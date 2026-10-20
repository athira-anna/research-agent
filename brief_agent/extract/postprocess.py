"""Field fixes, merging repeats within a source, and the per-source cap."""

from __future__ import annotations

import re
from collections import defaultdict

from rapidfuzz import fuzz

from ..plan.models import Plan
from .grounding import normalize
from .models import Claim

MAX_CLAIMS_PER_SOURCE = 15
SAME_STATEMENT = 92


def fix_fields(c: Claim, plan: Plan) -> None:
    qids = {q.id for q in plan.questions}
    mids = {m.id for m in plan.metrics}
    if c.question_id not in qids and c.question_id != "other":
        c.fixes.append(f"question_id '{c.question_id}' → other (not in plan)")
        c.question_id = "other"
    if c.value:
        v = c.value
        if v.metric_id not in mids and v.metric_id != "other":
            c.fixes.append(f"metric_id '{v.metric_id}' → other (not in plan)")
            v.metric_id = "other"
        if v.high < v.low:
            v.low, v.high = v.high, v.low
            c.fixes.append("low/high swapped")
    if c.causal and not (c.causal.cause.strip() and c.causal.effect.strip()):
        c.causal = None
        c.fixes.append("empty causal link removed")


def is_rate(c: Claim, plan: Plan) -> bool | None:
    """Is the claim's number a share/rate (True), a count/amount (False), or unknown (None)?"""
    if not c.value:
        return None
    m = next((m for m in plan.metrics if m.id == c.value.metric_id), None)
    if m and m.measure_type in ("share", "rate"):
        return True
    if m and m.measure_type in ("count", "amount"):
        return False
    if c.value.unit in ("count", "currency"):
        return False
    return None


def _numbers(text: str) -> list[str]:
    return sorted(re.findall(r"\d+(?:[.,]\d+)*", text))


def _same(a: Claim, b: Claim) -> bool:
    same_value = (a.value is None and b.value is None) or (
        a.value is not None and b.value is not None and abs(a.value.low - b.value.low) < 1e-9)
    if normalize(a.evidence.quote) == normalize(b.evidence.quote) and same_value:
        return True
    # Statements that differ in any number ("wave 1" vs "wave 2", "2023" vs "2024") are different claims.
    return same_value and _numbers(a.statement) == _numbers(b.statement) and \
        fuzz.token_set_ratio(normalize(a.statement), normalize(b.statement)) >= SAME_STATEMENT


def merge_repeats(claims: list[Claim]) -> tuple[list[Claim], list[tuple[Claim, str]]]:
    kept, dropped = [], []
    for c in claims:
        twin = next((k for k in kept if _same(k, c)), None)
        if twin:
            dropped.append((c, f"repeat of {twin.claim_id}"))
        else:
            kept.append(c)
    return kept, dropped


def _origin(c: Claim) -> str:
    return normalize(c.evidence.attributed_to) or c.evidence.block_id


def _spread_origins(claims: list[Claim]) -> list[Claim]:
    """Within a question, take one claim per origin before a second from any origin.
    Otherwise four country figures from one study can push out a different study's counter-evidence."""
    groups: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        groups[_origin(c)].append(c)
    out: list[Claim] = []
    while any(groups.values()):
        for g in groups.values():
            if g:
                out.append(g.pop(0))
    return out


def cap(claims: list[Claim], limit: int = MAX_CLAIMS_PER_SOURCE) -> tuple[list[Claim], list[Claim]]:
    """Plan questions before 'other'; within a question numbers first and origins spread; round-robin across questions."""
    if len(claims) <= limit:
        return claims, []
    by_q: dict[str, list[Claim]] = defaultdict(list)
    for c in claims:
        by_q[c.question_id].append(c)
    for q in by_q:
        by_q[q] = _spread_origins(sorted(by_q[q], key=lambda c: c.value is None))  # numbers first
    order = [q for q in dict.fromkeys(c.question_id for c in claims) if q != "other"] + \
            (["other"] if "other" in by_q else [])
    picked: list[Claim] = []
    while len(picked) < limit and any(by_q[q] for q in order):
        for q in order:
            if by_q[q] and len(picked) < limit:
                picked.append(by_q[q].pop(0))
    keep_ids = {c.claim_id for c in picked}
    return [c for c in claims if c.claim_id in keep_ids], [c for c in claims if c.claim_id not in keep_ids]
