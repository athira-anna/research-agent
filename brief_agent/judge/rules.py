"""Step 5.4–5.5: labels in two levels (pair, then cluster), applied by code. The LLM's stance is one input;
code overrules it where scope, measure, modality or numbers decide, and records every override."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from ..extract.grounding import normalize
from .compare import DIMENSIONS

_EXCLUSION = re.compile(r"\b(rather than|instead of|not (?:due to|because of|caused by|driven by|the result of)"
                        r"|no evidence|unrelated to|nothing to do with|not ai\b|not the cause|isn't the cause)")
_UNSPEC = ("one_unspecified", "both_unspecified")


SAME_SUBJECT_MIN = 45


def _same_subject(ca: dict, cb: dict) -> bool:
    return fuzz.token_set_ratio(normalize(ca["statement"]), normalize(cb["statement"])) >= SAME_SUBJECT_MIN


def is_weak(c: dict) -> bool:
    return c["certainty"] == "hedged" or c.get("support", {}).get("status") not in ("full", "partial")


def _pattern(varies: list[str], scope: dict, same_origin: bool) -> str:
    if len(varies) > 1:
        return "multiple"
    d = varies[0]
    if scope[d] == "nested":
        return "part_vs_whole"
    if d == "population":
        return "by_group"
    if d == "geography":
        return "by_place"
    return "over_time" if same_origin else "different_years"


def _llm_relation(stance: dict | None, ca: dict, cb: dict) -> tuple[str | None, str]:
    """What the LLM's stance says about this pair: ("agree" | "disagree" | None, disagreement type).
    Same side → agree; different sides → disagree."""
    if not stance or stance["stance"] == "unrelated":
        return None, "none"
    side = {cid: i for i, s in enumerate(stance["sides"]) for cid in s["claim_ids"]}
    sa, sb = side.get(ca["claim_id"]), side.get(cb["claim_id"])
    if sa is not None and sb is not None:
        return ("agree" if sa == sb else "disagree"), stance["disagreement_type"]
    return ("agree" if stance["stance"] == "agree" else None), stance["disagreement_type"]


def pair_label(ca: dict, cb: dict, facts: dict, stance: dict | None, same_origin: bool) -> dict:
    """The label for one pair of claims, by the rules in order: measure → modality → scope → numbers / LLM
    stance → contradiction only if nothing weakens it. Every time code overrules the LLM, it's recorded."""
    scope, numbers = facts["scope"], facts["numbers"]
    out = {"label": None, "varies_by": [], "overrides": [], "caveats": [],
           "unspecified": [d for d in DIMENSIONS if scope[d] in _UNSPEC]}
    llm_rel, llm_type = _llm_relation(stance, ca, cb)

    # 1–2: measure and modality decide before anything else.
    if facts["measure"] in ("confusable", "different"):
        if llm_rel == "disagree":
            out["overrides"].append("LLM saw a disagreement; the claims measure different things")
        return out | {"label": "different_measures", "detail": facts["measure"]}
    if numbers.get("detail", "").startswith("they count different things"):
        if llm_rel:
            out["overrides"].append(f"LLM judged them {llm_rel}; {numbers['detail']}")
        return out | {"label": "different_measures", "detail": numbers["detail"]}
    if facts["modality"] == "not_comparable":
        return out | {"label": "not_comparable", "detail": f"{ca['modality']} vs {cb['modality']}"}

    # 3: different slices (any dimension disjoint or nested) can both be true.
    varies = [d for d in DIMENSIONS if scope[d] in ("disjoint", "nested")]
    if varies:
        if llm_rel == "disagree":
            out["overrides"].append(f"LLM put them on opposite sides; scopes differ ({', '.join(varies)}) → complementary")
        return out | {"label": "complementary", "varies_by": varies, "pattern": _pattern(varies, scope, same_origin)}

    # 4: numbers are code's call; the LLM covers what numbers can't (causes, existence).
    n = numbers["result"]
    if n in ("agree", "close", "disagree"):
        if n == "agree" and llm_rel == "disagree" and llm_type in ("causal", "existence"):
            rel, dtype = "disagree", llm_type          # same figure, different cause / existence
        elif n == "agree":
            rel, dtype = "agree", None
            if llm_rel == "disagree":
                out["overrides"].append("LLM saw a disagreement; the numbers agree")
        elif n == "close":
            rel, dtype = "close", None
        else:
            rel, dtype = "disagree", "direction" if numbers.get("direction_conflict") else "numeric"
            if llm_rel == "agree":
                out["overrides"].append(f"LLM saw agreement; the numbers disagree ({numbers.get('detail')})")
    else:
        rel, dtype = llm_rel, llm_type if llm_type != "none" else "existence"

    if rel is None:
        return out | {"label": "unjudged"}
    if rel == "agree" and n not in ("agree", "close", "disagree") and not _same_subject(ca, cb):
        # Agreement from the LLM alone, between statements about different things ("no conflict" ≠ agreement).
        out["overrides"].append("LLM judged them in agreement; the statements are about different things")
        return out | {"label": "unrelated"}
    if rel == "agree":
        if out["unspecified"]:
            out["caveats"].append(f"unspecified {', '.join(out['unspecified'])}")
        return out | {"label": "agree"}
    if rel == "close":
        return out | {"label": "tension", "reason": "close_numbers", "reasons": ["close_numbers"]}

    # 5–6: a disagreement. Contradiction only if nothing weakens it.
    return out | _disagreement(ca, cb, dtype, scope, stance, out)


def _disagreement(ca: dict, cb: dict, dtype: str, scope: dict, stance: dict | None, out: dict) -> dict:
    """Contradiction (strict) or tension with named reasons. A causal difference only contradicts when one
    side explicitly rules out the other's cause, as the quotes show."""
    reasons = []
    if dtype == "causal":
        exclusive = bool(stance and stance.get("causal_exclusive"))
        quotes = normalize(ca["evidence"]["quote"] + " " + cb["evidence"]["quote"])
        if exclusive and not _EXCLUSION.search(quotes):
            out["overrides"].append("LLM said one cause rules out the other; the quotes don't say so")
            exclusive = False
        if not exclusive:
            reasons.append("competing_explanations")
        dtype = "causal_exclusive" if exclusive else dtype
    if "partial_overlap" in scope.values():
        reasons.append("overlapping_period")
    if out["unspecified"]:
        reasons.append("unspecified_scope")
    if is_weak(ca) or is_weak(cb):
        reasons.append("weak_claim")
    if reasons:
        return {"label": "tension", "reason": reasons[0], "reasons": reasons, "disagreement_type": dtype}
    return {"label": "contradiction", "type": dtype}


# --- cluster level ---------------------------------------------------------------------------

def _components(claim_ids: list[str], agree_pairs: list[tuple[str, str]]) -> list[set[str]]:
    parent = {c: c for c in claim_ids}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b in agree_pairs:
        parent[find(a)] = find(b)
    groups: dict[str, set[str]] = {}
    for c in claim_ids:
        groups.setdefault(find(c), set()).add(c)
    return list(groups.values())


def _independent_origins(claims: list[dict], origin_of: dict[str, str]) -> set[str]:
    """Origins, with a source's unattributed claims folded into that source's attributed origin (if it has one)."""
    named = {}
    for c in claims:
        o = origin_of[c["claim_id"]]
        if not o.startswith("source:"):
            named.setdefault(c["source_id"], set()).add(o)
    out = set()
    for c in claims:
        o = origin_of[c["claim_id"]]
        if o.startswith("source:") and named.get(c["source_id"]):
            continue   # same source already counted through its named origin
        out.add(o)
    return out


def cluster_label(claims: list[dict], pairs: list[dict], origin_of: dict[str, str], stance: dict | None) -> dict:
    ids = [c["claim_id"] for c in claims]
    origins = _independent_origins(claims, origin_of)
    sources = {c["source_id"] for c in claims}
    cross = [p for p in pairs if not p["same_origin"]]
    labels = [p["label"] for p in cross]

    if len(origins) == 1:
        return {"label": "single_origin", "origins": 1, "sources": len(sources)}

    if "contradiction" in labels:
        involved = {c for p in cross if p["label"] == "contradiction" for c in (p["a"], p["b"])}
        if stance and stance["sides"]:
            groups = [set(s["claim_ids"]) & set(ids) for s in stance["sides"]]
            positions = [s["position"] for s in stance["sides"]]
        else:
            groups = _components(ids, [(p["a"], p["b"]) for p in cross if p["label"] == "agree"])
            positions = [None] * len(groups)
        sides = [{"position": pos, "claim_ids": sorted(g), "origins": len({origin_of[c] for c in g})}
                 for g, pos in zip(groups, positions) if g & involved]
        counts = sorted((s["origins"] for s in sides), reverse=True)
        balance = "split" if len(counts) > 1 and counts[0] == counts[1] else "majority_vs_minority"
        types = sorted({p["type"] for p in cross if p["label"] == "contradiction"})
        return {"label": "contested", "balance": balance, "sides": sides, "contradiction_types": types,
                "origins": len(origins), "sources": len(sources)}
    if "agree" in labels:
        by_id = {c["claim_id"]: c for c in claims}
        agreeing = _independent_origins([by_id[c] for p in cross if p["label"] == "agree" for c in (p["a"], p["b"])],
                                        origin_of)
        slices = sorted({p["pattern"] for p in cross if p["label"] == "complementary"})
        return {"label": "consensus", "origins": len(agreeing), "sources": len(sources),
                "complementary_slices": slices}
    if "tension" in labels:
        reasons = sorted({r for p in cross if p["label"] == "tension" for r in p.get("reasons", [])})
        return {"label": "unresolved", "reasons": reasons, "origins": len(origins), "sources": len(sources)}
    for lab in ("complementary", "different_measures", "not_comparable"):
        if lab in labels:
            extra = {"patterns": sorted({p["pattern"] for p in cross if p["label"] == "complementary"})} \
                if lab == "complementary" else {}
            return {"label": lab, "origins": len(origins), "sources": len(sources)} | extra
    if labels and set(labels) <= {"unrelated", "unjudged"} and "unrelated" in labels:
        return {"label": "unrelated", "origins": len(origins), "sources": len(sources)}
    return {"label": "unjudged", "origins": len(origins), "sources": len(sources)}


def strength(label: dict, claims: list[dict], coverage_of: dict[str, float]) -> dict:
    n = label.get("origins", 1)
    score = 2 if n >= 3 else 1 if n == 2 else 0
    notes = []
    if all(c["certainty"] in ("asserted", "reported") for c in claims):
        score += 1
    if any(c["certainty"] == "hedged" for c in claims):
        notes.append("some claims are hedged")
    if any(c.get("support", {}).get("status") not in ("full", "partial") for c in claims):
        score -= 1
        notes.append("some claims weren't support-checked")
    if any(coverage_of.get(c["source_id"], 1.0) < 0.999 for c in claims):
        notes.append("some sources were only partly read")
    if n >= 2 and len({c["source_id"] for c in claims}) == 1:
        notes.append("all origins reach us through one source (a secondary summary)")
    level = "strong" if score >= 3 else "moderate" if score == 2 else "weak"
    if label["label"] == "single_origin" and level == "strong":
        level = "moderate"
    return {"level": level, "notes": notes}
