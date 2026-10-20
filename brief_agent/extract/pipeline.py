"""Stage 3: sources.json + plan.json → claims.json, dropped_claims.json, and an `extraction` block per source.

Per source (all sources in parallel, with a concurrency limit):
    LLM extraction → field fixes → grounding (quote in block) → number meaning → consistency checks
    → merge repeats → LLM support check → weaken/drop → cap at 15
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from pathlib import Path

from ..llm import (EXTRACT_FALLBACK_MODELS, EXTRACT_MODEL, LLM, SUPPORT_MODEL, LLMError,
                   generate_json_with_fallback)
from ..plan.models import Plan
from ..plan.planner import load_run_plan
from .grounding import MAX_QUOTE_CHARS, MIN_QUOTE_CHARS, best_score, match_quote, normalize, number_in_quote
from .models import Claim, ExtractionDraft
from .numbers import check_value
from .postprocess import cap, fix_fields, is_rate, merge_repeats
from .prompt import MAX_CLAIMS_FROM_MODEL, SYSTEM, build_prompt
from .support import apply_verdict, consistency_checks, llm_support_check

MAX_PARALLEL_CALLS = 4


class Dropped(list):
    """Dropped claims with their reason, written to dropped_claims.json for debugging."""

    def add(self, claim: Claim | dict, source_id: str, reason: str, detail: str = "") -> None:
        data = claim.model_dump() if isinstance(claim, Claim) else claim
        self.append({"source_id": source_id, "reason": reason, "detail": detail, "claim": data})


def _ground(c: Claim, blocks: dict[str, dict], all_blocks: list[dict]) -> tuple[str | None, str]:
    """Steps 4 checks on the quote. Returns (drop reason or None, detail)."""
    q = c.evidence.quote.strip()
    if len(q) < MIN_QUOTE_CHARS:
        return "quote_too_short", f"{len(q)} chars"
    if len(q) > MAX_QUOTE_CHARS:
        return "quote_too_long", f"{len(q)} chars"
    cited = blocks.get(c.evidence.block_id)
    hit = match_quote(q, cited["text"]) if cited else None
    if hit:
        c.grounding = {"method": hit[0], "score": hit[1]}
        return None, ""
    for b in all_blocks:
        hit = match_quote(q, b["text"])
        if hit:
            c.grounding = {"method": "moved", "score": hit[1], "moved_from": c.evidence.block_id}
            c.fixes.append(f"block_id {c.evidence.block_id} → {b['id']} (quote found there)")
            c.evidence.block_id = b["id"]
            return None, ""
    return "quote_not_found", f"best match {best_score(q, all_blocks)}"


async def _extract_source(src: dict, topic: str, plan: Plan, llm: LLM,
                          sem: asyncio.Semaphore) -> tuple[list[Claim], dict, Dropped]:
    """One source, end to end. Returns (kept claims, the source's `extraction` status, its dropped claims).
    Each source keeps its own drop list: sources run in parallel, so a shared list would mix up the counts."""
    dropped = Dropped()
    if src["usability"]["status"] == "excluded":
        return [], {"status": "skipped", "reason": "; ".join(src["usability"]["reasons"]) or "excluded"}, dropped
    sent = [b for b in src["blocks"] if b["id"] in set(src["selected_ids"]) and not b.get("duplicate_of")]
    if not any(b["kind"] != "heading" for b in sent):
        return [], {"status": "skipped", "reason": "no selected text"}, dropped

    try:
        async with sem:
            draft, model, notes = await generate_json_with_fallback(
                llm, [EXTRACT_MODEL, *EXTRACT_FALLBACK_MODELS], system=SYSTEM,
                prompt=build_prompt(topic, plan, src, sent), schema=ExtractionDraft)
    except LLMError as e:
        return [], {"status": "failed", "reason": str(e)[:300], "claims": 0, "dropped": 0}, dropped

    sid = src["source_id"]
    by_id = {b["id"]: b for b in src["blocks"]}
    metric_defs = {m.id: f"{m.label}: {m.definition} ({m.measure_type}, {m.unit})" for m in plan.metrics}
    flags: dict[str, list[str]] = {}
    checked: list[Claim] = []
    for i, d in enumerate(draft.claims[:MAX_CLAIMS_FROM_MODEL], start=1):
        c = Claim(**d.model_dump(), claim_id=f"{sid}-c{i:03d}", source_id=sid)
        claim_flags = _check_claim(c, plan, by_id, src["blocks"], metric_defs, dropped)
        if claim_flags is not None:
            checked.append(c)
            flags[c.claim_id] = claim_flags

    claims, repeats = merge_repeats(checked)
    for c, why in repeats:
        dropped.add(c, sid, "repeat", why)
    claims, support_note = await _support_check(claims, flags, by_id, metric_defs, llm, sem, dropped)
    claims, over = cap(claims)
    for c in over:
        dropped.add(c, sid, "over_cap")
    return claims, _status(model, claims, dropped, len(draft.claims), [*notes, support_note]), dropped


def _check_claim(c: Claim, plan: Plan, by_id: dict, blocks: list[dict], metric_defs: dict,
                 dropped: Dropped) -> list[str] | None:
    """Steps 4–4b-1 for one claim: field fixes, quote grounding, number meaning, consistency checks.
    Returns the flags for the LLM support check, or None if the claim was dropped."""
    fix_fields(c, plan)
    reason, detail = _ground(c, by_id, blocks)
    if reason:
        dropped.add(c, c.source_id, reason, detail)
        return None
    quote_norm = normalize(c.evidence.quote)
    if c.value:
        status, fixes = check_value(c.value, quote_norm, is_rate(c, plan))
        c.number_check = status
        c.fixes += fixes
        if status not in ("misread", "not_in_quote") and c.value.high != c.value.low \
                and not number_in_quote(c.value.high, quote_norm):
            status = "not_in_quote"   # a range: both ends must be in the quote
        if status in ("misread", "not_in_quote"):
            dropped.add(c, c.source_id, "number_misread" if status == "misread" else "number_not_in_quote",
                        f"{c.value.value_kind} {c.value.low:g} {c.value.unit} vs quote")
            return None
    return consistency_checks(c, quote_norm, normalize(by_id[c.evidence.block_id]["text"]), metric_defs)


async def _support_check(claims: list[Claim], flags: dict, by_id: dict, metric_defs: dict, llm: LLM,
                         sem: asyncio.Semaphore, dropped: Dropped) -> tuple[list[Claim], str]:
    """Step 4b-2: does each quote support its claim? Weaken or drop per the verdicts. If the check itself is
    unavailable, claims are kept as "unchecked" rather than lost. Returns (survivors, note)."""
    if not claims:
        return claims, ""
    items = [(c, by_id[c.evidence.block_id]["text"], flags[c.claim_id]) for c in claims]
    note = ""
    try:
        async with sem:
            verdicts = await llm_support_check(llm, [SUPPORT_MODEL, *EXTRACT_FALLBACK_MODELS], items, metric_defs)
    except LLMError as e:
        verdicts, note = {}, f"support check unavailable ({str(e)[:120]}); claims kept as unchecked"
    survivors = []
    for c in claims:
        v = verdicts.get(c.claim_id)
        if v is None:
            c.support = {"status": "unchecked", "reason": note or "no verdict returned"}
            survivors.append(c)
        elif reason := apply_verdict(c, v):
            dropped.add(c, c.source_id, reason, v["reason"])
        else:
            survivors.append(c)
    return survivors, note


def _status(model: str, claims: list[Claim], dropped: Dropped, returned: int, notes: list[str]) -> dict:
    """The `extraction` status block stored on the source."""
    reason = "; ".join(n for n in notes if n)
    if not claims and not dropped:
        reason = (reason + "; " if reason else "") + "no claims about the topic found"
    return {"status": "ok", "model": model, "claims": len(claims), "dropped": len(dropped),
            "returned_by_model": returned,
            "dropped_by_reason": dict(Counter(d["reason"] for d in dropped)),
            "support": dict(Counter(c.support.get("status", "unchecked") for c in claims)),
            "reason": reason}


async def extract_claims(run_dir: Path, topic: str, llm: LLM) -> tuple[list[Claim], list[dict]]:
    sources = json.loads((run_dir / "sources.json").read_text())
    plan = load_run_plan(run_dir)
    if plan is None:
        raise FileNotFoundError(f"{run_dir / 'plan.json'} not found; run stage 0 first")
    sem = asyncio.Semaphore(MAX_PARALLEL_CALLS)
    results = await asyncio.gather(*(_extract_source(s, topic, plan, llm, sem) for s in sources))

    claims: list[Claim] = []
    dropped = Dropped()
    for src, (src_claims, status, src_dropped) in zip(sources, results):
        src["extraction"] = status
        claims += src_claims
        dropped += src_dropped
    (run_dir / "sources.json").write_text(json.dumps(sources, indent=2, ensure_ascii=False))
    (run_dir / "claims.json").write_text(json.dumps(
        {"topic": topic, "topic_key": plan.topic_key, "claims": [c.model_dump() for c in claims]},
        indent=2, ensure_ascii=False))
    (run_dir / "dropped_claims.json").write_text(json.dumps(dropped, indent=2, ensure_ascii=False))
    return claims, sources
