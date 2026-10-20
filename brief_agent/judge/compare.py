"""Step 5.1–5.2: facts about a pair of claims that code can decide on its own.

Scope is compared PER DIMENSION (population, geography, timeframe), never collapsed into one value:
same | nested | partial_overlap | disjoint | one_unspecified | both_unspecified.
"""

from __future__ import annotations

from rapidfuzz import fuzz

from ..extract.grounding import normalize
from ..plan.models import Plan

AGREE_MAX_DIFF = 0.10     # relative difference ≤10% → agree
CLOSE_MAX_DIFF = 0.25     # ≤25% → close (tension); above → disagree

DIMENSIONS = ("population", "geography", "timeframe")


def label_relation(a: str | None, b: str | None, broader: set[tuple[str, str]]) -> str:
    if not a and not b:
        return "both_unspecified"
    if not a or not b:
        return "one_unspecified"
    if a.lower() == b.lower():
        return "same"
    if (a, b) in broader or (b, a) in broader:
        return "nested"
    if "global" in (a.lower(), b.lower()):
        return "nested"        # any place is part of the global picture
    return "disjoint"


def time_relation(a: list | None, b: list | None) -> str:
    if not a and not b:
        return "both_unspecified"
    if not a or not b:
        return "one_unspecified"
    (s1, e1), (s2, e2) = a, b
    if (s1, e1) == (s2, e2):
        return "same"
    if (s1 <= s2 and e2 <= e1) or (s2 <= s1 and e1 <= e2):
        return "nested"
    if s1 <= e2 and s2 <= e1:
        return "partial_overlap"
    return "disjoint"


def scope_relations(sa: dict, sb: dict, broader: set[tuple[str, str]]) -> dict[str, str]:
    return {
        "population": label_relation(sa["population"], sb["population"], broader),
        "geography": label_relation(sa["geography"], sb["geography"], broader),
        "timeframe": time_relation(sa["timeframe"], sb["timeframe"]),
    }


def measure_relation(ca: dict, cb: dict, plan: Plan) -> str:
    va, vb = ca.get("value"), cb.get("value")
    if not va or not vb:
        return "n/a"
    ma, mb = va["metric_id"], vb["metric_id"]
    if ma == "other" and mb == "other":
        return "unknown"        # the LLM judges whether they measure the same thing
    if ma == mb:
        return "same_metric"
    conf = {m.id: set(m.confusable_with) for m in plan.metrics}
    if mb in conf.get(ma, set()) or ma in conf.get(mb, set()):
        return "confusable"
    if "other" in (ma, mb):
        return "unknown"
    return "different"


def modality_relation(ca: dict, cb: dict, sa: dict, sb: dict) -> str:
    a, b = ca["modality"], cb["modality"]
    if a == b == "observed" or a == b == "opinion":
        return "comparable"
    if a == b == "forecast":
        # forecasts are comparable only for the same horizon (or when neither states one)
        return "comparable" if sa["timeframe"] == sb["timeframe"] else "not_comparable"
    return "not_comparable"


# --- numbers ---------------------------------------------------------------------------------

def _forms(v: dict) -> dict[str, tuple[float, float, str]]:
    """Every form this value can be expressed in: level, absolute change, relative change."""
    unit = v["unit"]
    out: dict[str, tuple[float, float, str]] = {}
    if v["value_kind"] == "level":
        out["level"] = (v["low"], v["high"], unit)
        b = (v.get("baseline") or {}).get("number")
        if b is not None:
            out["absolute_change"] = (v["low"] - b, v["high"] - b,
                                      "percentage_points" if unit == "percent" else unit)
            if b:
                out["relative_change"] = ((v["low"] - b) / abs(b) * 100, (v["high"] - b) / abs(b) * 100, "percent")
    elif v["value_kind"] == "absolute_change":
        sign = -1 if v["direction"] == "decrease" and v["low"] > 0 else 1
        out["absolute_change"] = (sign * v["low"], sign * v["high"], unit)
    elif v["value_kind"] == "relative_change":
        sign = -1 if v["direction"] == "decrease" and v["low"] > 0 else 1
        out["relative_change"] = (sign * v["low"], sign * v["high"], "percent")
    return out


def _units_match(ua: str, ub: str) -> bool:
    if ua == ub:
        return True
    return {ua, ub} <= {"count", "other"}


def same_quantity(va: dict, vb: dict) -> bool:
    """Without a shared plan metric, two numbers are only comparable if they count the same thing
    ("jobs in Israel" vs "workload of exposed occupations" are not)."""
    a, b = normalize(va.get("of_what") or ""), normalize(vb.get("of_what") or "")
    if a and b and fuzz.token_set_ratio(a, b) < 50:
        return False          # clearly different things, even if extraction gave them the same metric
    if va["metric_id"] != "other" and va["metric_id"] == vb["metric_id"]:
        return True
    return bool(a and b) and fuzz.token_set_ratio(a, b) >= 80


def compare_numbers(va: dict | None, vb: dict | None) -> dict:
    """{result: agree|close|disagree|not_comparable, form, a, b, detail}. Code's call, never the LLM's."""
    if not va or not vb:
        return {"result": "not_comparable", "detail": "no number on one side"}
    if not same_quantity(va, vb):
        return {"result": "not_comparable",
                "detail": f"they count different things ({va.get('of_what') or '?'} vs {vb.get('of_what') or '?'})"}
    fa, fb = _forms(va), _forms(vb)
    for form in ("level", "absolute_change", "relative_change"):
        if form in fa and form in fb and _units_match(fa[form][2], fb[form][2]):
            (a1, a2, unit), (b1, b2, _) = fa[form], fb[form]
            base = {"form": form, "unit": unit, "a": [round(a1, 3), round(a2, 3)], "b": [round(b1, 3), round(b2, 3)]}
            if form != "level" and ((a1 > 0 and b2 < 0) or (a2 < 0 and b1 > 0)):
                return base | {"result": "disagree", "detail": "opposite directions", "direction_conflict": True}
            if a1 <= b2 and b1 <= a2:
                return base | {"result": "agree", "detail": "ranges overlap"}
            ma, mb = (a1 + a2) / 2, (b1 + b2) / 2
            diff = abs(ma - mb) / max(abs(ma), abs(mb), 1e-9)
            result = "agree" if diff <= AGREE_MAX_DIFF else "close" if diff <= CLOSE_MAX_DIFF else "disagree"
            return base | {"result": result, "detail": f"{diff:.0%} apart"}
    return {"result": "not_comparable", "detail": "can't be put in the same form (e.g. unknown points vs relative)"}
