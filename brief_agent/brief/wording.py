"""Plain-English wording for labels, patterns, reasons and numbers. No raw codes reach the reader."""

from __future__ import annotations

PATTERNS = {
    "by_group": "differs by group",
    "by_place": "differs by place",
    "part_vs_whole": "one figure is part of a larger whole",
    "over_time": "a change over time within one series",
    "different_years": "estimates from different years (not a trend)",
    "multiple": "differs on several dimensions",
}
TENSION_REASONS = {
    "unspecified_scope": "at least one source doesn't say which place, group or period it covers",
    "overlapping_period": "the periods covered only partly overlap",
    "close_numbers": "the figures are close but more than 10% apart",
    "weak_claim": "a claim is hedged or couldn't be support-checked",
    "competing_explanations": "different causes are offered, and both may be at work",
}
CONTRADICTION_TYPES = {
    "numeric": "different figures for the same thing",
    "direction": "one says it rose, the other that it fell",
    "existence": "one says it happened, the other that it didn't",
    "causal_exclusive": "one explicitly rules out the other's cause",
}
GAP_TITLES = {
    "unanswered_question": "Questions no source answers",
    "thin_question": "Questions only one origin answers",
    "facet_gap": "Places, groups or breakdowns nobody covers",
    "no_figures": "Measures with no figures",
    "not_read": "Sources that couldn't be used",
}
LABEL_TEXT = {
    "consensus": "Sources agree",
    "contested": "Sources disagree",
    "unresolved": "Unresolved",
    "complementary": "Different slices of the picture",
    "different_measures": "Different measures, don't compare directly",
    "not_comparable": "Not directly comparable",
    "single_origin": "Single origin",
    "unjudged": "Addressed, agreement not assessed",
    "unrelated": "Related points, not directly comparable",
}


def headline(verdict: dict) -> str:
    """Plain-English headline for a cluster verdict, e.g. 'Sources disagree: 3 origins vs 1'."""
    lab = verdict["label"]
    text = LABEL_TEXT.get(lab, lab)
    if lab == "contested":
        counts = sorted((s["origins"] for s in verdict.get("sides", [])), reverse=True)
        balance = "evenly split" if verdict.get("balance") == "split" else " vs ".join(map(str, counts)) + " origins"
        kinds = "; ".join(CONTRADICTION_TYPES.get(t, t) for t in verdict.get("contradiction_types", []))
        return f"{text}: {balance}" + (f" ({kinds})" if kinds else "")
    if lab == "consensus":
        extra = f"; also {', '.join(PATTERNS[p] for p in verdict['complementary_slices'])}" \
            if verdict.get("complementary_slices") else ""
        return f"{text} ({verdict.get('origins')} independent origins){extra}"
    if lab == "unresolved":
        return f"{text}: " + "; ".join(TENSION_REASONS.get(r, r) for r in verdict.get("reasons", []))
    if lab == "complementary":
        return f"{text}: " + ", ".join(PATTERNS.get(p, p) for p in verdict.get("patterns", []))
    if lab == "single_origin":
        n = verdict.get("sources", 1)
        return f"{text}" + (f" (repeated by {n} sources)" if n > 1 else "")
    return text


def _num(x: float) -> str:
    ax = abs(x)
    for div, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if ax >= div:
            return f"{x / div:,.3g} {word}"
    return f"{x:,.0f}" if ax >= 100 or float(x).is_integer() else f"{x:,.3g}"


def value_text(v: dict | None) -> str:
    """'47%', '10% (from 5%)', '+5 points', '+100% (relative)', '100 million'."""
    if not v:
        return ""
    low, high, unit, kind = v["low"], v["high"], v["unit"], v["value_kind"]
    rng = _num(low) if low == high else f"{_num(low)}–{_num(high)}"
    sign = "−" if v.get("direction") == "decrease" else "+" if v.get("direction") == "increase" else ""
    if unit == "percent":
        rng += "%"
    elif unit == "percentage_points":
        rng += " points"
    elif unit == "currency":
        rng += " (currency)"
    if kind == "level":
        base = (v.get("baseline") or {}).get("number")
        if base is not None:
            return f"{rng} (from {_num(base)}{'%' if unit == 'percent' else ''})"
        return rng
    if kind == "absolute_change":
        return f"{sign}{rng}"
    if kind == "relative_change":
        return f"{sign}{rng} (relative)"
    return f"{sign}{rng} (points or relative unclear)"


def scope_text(scope: dict) -> str:
    parts = [p for p in (scope.get("geography"), scope.get("population")) if p]
    tf = scope.get("timeframe")
    if tf:
        parts.append(str(tf[0]) if tf[0] == tf[1] else f"{tf[0]}–{tf[1]}")
    return ", ".join(parts)


def qualifiers(claim: dict) -> str:
    q = []
    if claim["modality"] == "forecast":
        q.append("forecast")
    if claim["modality"] == "opinion":
        q.append("opinion")
    if claim["certainty"] == "hedged":
        q.append("hedged")
    return f" _({', '.join(q)})_" if q else ""
