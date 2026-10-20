"""Stage 5: alignment.json + claims → analysis.json (findings with two-level labels, strength, gaps).

    per cluster: pairwise facts (code) → LLM stance (per question) → pair labels (code rules)
                 → cluster label → strength
    then: gaps, emerging claims, per-question summary, source status
"""

from __future__ import annotations

import asyncio
import json
from itertools import combinations
from pathlib import Path

from ..align.scope import GEO_HIERARCHY, broader_closure
from ..llm import LLM, LLMError
from ..plan.planner import load_run_plan
from .compare import compare_numbers, measure_relation, modality_relation, scope_relations
from .gaps import find_gaps
from .judge import cluster_payload, judge_question
from .rules import cluster_label, pair_label, strength

MAX_PARALLEL_CALLS = 4


def _pairs(claims: list[dict], acl: dict, plan, broader: set) -> list[dict]:
    out = []
    for ca, cb in combinations(claims, 2):
        a, b = ca["claim_id"], cb["claim_id"]
        sa, sb = acl[a]["canonical_scope"], acl[b]["canonical_scope"]
        # Two claims from one source are only independent if BOTH cite different origins explicitly;
        # an unattributed claim is the source's own voice and may be the same organisation.
        unattributed = acl[a]["origin"].startswith("source:") or acl[b]["origin"].startswith("source:")
        same_origin = acl[a]["origin"] == acl[b]["origin"] or (ca["source_id"] == cb["source_id"] and unattributed)
        facts = {"scope": scope_relations(sa, sb, broader), "measure": measure_relation(ca, cb, plan),
                 "modality": modality_relation(ca, cb, sa, sb),
                 "numbers": compare_numbers(ca.get("value"), cb.get("value"))}
        out.append({"a": a, "b": b, "same_origin": same_origin, "facts": facts})
    return out


def _code_reasons(pairs: list[dict], acl: dict) -> list[str]:
    """Scope differences stated by code, one line per dimension: 'varies by geography: US, EU, Israel'."""
    values: dict[str, list[str]] = {}
    for p in pairs:
        for d in p.get("varies_by", []):
            for cid in (p["a"], p["b"]):
                v = acl[cid]["canonical_scope"][d]
                if d == "timeframe" and v:
                    v = "–".join(map(str, sorted(set(v))))
                if v and v not in values.setdefault(d, []):
                    values[d].append(v)
    return [f"varies by {d}: {', '.join(map(str, vs))}" for d, vs in values.items()]


async def judge(run_dir: Path, llm: LLM | None) -> dict:
    claims = {c["claim_id"]: c for c in json.loads((run_dir / "claims.json").read_text())["claims"]}
    alignment = json.loads((run_dir / "alignment.json").read_text())
    sources = json.loads((run_dir / "sources.json").read_text())
    plan = load_run_plan(run_dir)
    acl = alignment["claims"]
    origin_of = {cid: v["origin"] for cid, v in acl.items()}
    coverage_of = {s["source_id"]: s.get("coverage") or 1.0 for s in sources}

    clusters = _clusters_with_pairs(alignment, claims, acl, plan, _broader_relations(alignment))
    stances, judge_notes = await _llm_stances(clusters, llm, acl, origin_of)
    findings = [_finding(cl, stances.get(cl["cluster_id"]), acl, origin_of, coverage_of) for cl in clusters]

    analysis = {
        "topic": plan.topic, "interpretation": plan.interpretation, "assumptions": plan.assumptions,
        "plan_source": plan.source,
        "findings": [f for f in findings if f["question_id"] != "other"],
        "emerging": [f for f in findings if f["question_id"] == "other"],
        "gaps": find_gaps(plan, list(claims.values()), alignment, sources),
        "per_question": _per_question(plan, findings), "judge_notes": judge_notes,
        "sources": [_source_summary(s) for s in sources],
    }
    (run_dir / "analysis.json").write_text(json.dumps(analysis, indent=2, ensure_ascii=False))
    return analysis


def _broader_relations(alignment: dict) -> set[tuple[str, str]]:
    """Every narrower → broader relation: built-in places plus what stage 4 found, transitively closed."""
    hierarchy = set(map(tuple, alignment.get("geo_hierarchy", []))) | GEO_HIERARCHY
    for q in alignment["questions"].values():
        hierarchy |= {tuple(h) for h in q.get("hierarchy", [])}
    return broader_closure(hierarchy)


def _clusters_with_pairs(alignment: dict, claims: dict, acl: dict, plan, broader: set) -> list[dict]:
    """Each cluster with its member claims and the code-computed facts for every pair (step 5.1–5.2)."""
    clusters = []
    for qid, q in alignment["questions"].items():
        for cl in q["clusters"]:
            members = [claims[c] for c in cl["claim_ids"] if c in claims]
            clusters.append({"qid": qid, **cl, "members": members, "pairs": _pairs(members, acl, plan, broader)})
    return clusters


async def _llm_stances(clusters: list[dict], llm: LLM | None, acl: dict,
                       origin_of: dict) -> tuple[dict[str, dict], dict[str, str]]:
    """Step 5.3: one LLM call per question, for clusters with 2+ origins. Returns ({cluster_id: stance}, notes)."""
    sem = asyncio.Semaphore(MAX_PARALLEL_CALLS)
    notes: dict[str, str] = {}

    async def one_question(qid: str) -> dict[str, dict]:
        todo = [cl for cl in clusters
                if cl["qid"] == qid and len({origin_of[c["claim_id"]] for c in cl["members"]}) > 1]
        if not todo:
            return {}
        if llm is None:
            notes[qid] = "LLM disabled"
            return {}
        try:
            async with sem:
                stances, model = await judge_question(
                    llm, [cluster_payload(cl, cl["members"], acl, cl["pairs"]) for cl in todo])
            notes[qid] = f"judged by {model}"
            return stances
        except LLMError as e:
            notes[qid] = f"judge LLM unavailable ({str(e)[:120]}); code-decidable labels only"
            return {}

    stances: dict[str, dict] = {}
    for result in await asyncio.gather(*(one_question(q) for q in dict.fromkeys(cl["qid"] for cl in clusters))):
        stances.update(result)
    return stances, notes


def _finding(cl: dict, stance: dict | None, acl: dict, origin_of: dict, coverage_of: dict) -> dict:
    """Step 5.4–5.5 for one cluster: pair labels → cluster verdict → strength, plus what the brief shows."""
    by_id = {c["claim_id"]: c for c in cl["members"]}
    labelled = []
    for p in cl["pairs"]:
        lab = pair_label(by_id[p["a"]], by_id[p["b"]], p["facts"], stance, p["same_origin"])
        if p["same_origin"] and not (lab["label"] == "complementary" and lab.get("pattern") == "over_time"):
            continue   # same-origin pairs only matter as a trend within one series
        labelled.append(p | lab)
    verdict = cluster_label(cl["members"], labelled, origin_of, stance)
    llm_reasons = [r for r in (stance["likely_reasons"] if stance else []) if len(r.split()) >= 3]  # not bare "scope"
    return {
        "cluster_id": cl["cluster_id"], "question_id": cl["qid"], "label": cl["label"],
        "verdict": verdict, "strength": strength(verdict, cl["members"], coverage_of),
        # The LLM's own sentence, kept for inspection; the brief headlines code-written wording from the verdict.
        "llm_summary": stance["summary"] if stance else None,
        "likely_reasons": _code_reasons(labelled, acl) + llm_reasons,
        "pairs": labelled,
        "overrides": [f"{p['a']}↔{p['b']}: {o}" for p in labelled for o in p["overrides"]],
        "claims": [{"claim_id": c["claim_id"], "source_id": c["source_id"], "origin": acl[c["claim_id"]]["label"],
                    "statement": c["statement"], "value": c.get("value"),
                    "scope": acl[c["claim_id"]]["canonical_scope"], "modality": c["modality"],
                    "certainty": c["certainty"], "quote": c["evidence"]["quote"],
                    "block_id": c["evidence"]["block_id"]} for c in cl["members"]],
    }


def _per_question(plan, findings: list[dict]) -> dict:
    out = {}
    for q in plan.questions:
        qf = [f for f in findings if f["question_id"] == q.id]
        origins = {c["origin"] for f in qf for c in f["claims"]}
        status = "unanswered" if not qf else "thin" if len(origins) == 1 else "answered"
        out[q.id] = {"text": q.text, "status": status, "findings": [f["cluster_id"] for f in qf],
                     "origins": len(origins)}
    return out


def _source_summary(s: dict) -> dict:
    """What the brief needs to know about each source, from its stage 1–3 status blocks."""
    usability = s.get("usability") or {}
    extraction = s.get("extraction") or {}
    return {"source_id": s["source_id"], "url": s["url"], "title": (s.get("meta") or {}).get("title"),
            "fetch": s.get("fetch", {}).get("status"), "processing": (s.get("processing") or {}).get("status"),
            "usability": usability.get("status"), "reasons": usability.get("reasons", []),
            "extraction": extraction.get("status"), "claims": extraction.get("claims", 0),
            "coverage": s.get("coverage")}
