"""Step 4b-1½: does the claim's number mean what the quote's wording says?

"rose from 5% to 10%" contains 10, but "a 10% relative increase" is a misreading (it's +5 points, or +100%).
We parse the common number phrasings in the quote, list every reading consistent with them, and
check the claim against that list. Code never asks a model here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .grounding import number_in_quote, numbers_in
from .models import Baseline, Value

_N = r"-?\d[\d,]*(?:\.\d+)?"
_PCT = r"\s*(?:%|percent|per cent)"
_UP = r"rose|rise|risen|rising|increased?|increasing|grew|grown|growing|jumped|climbed|gained|up"
_DOWN = r"fell|fallen|falling|dropped|declined?|declining|decreased?|decreasing|shrank|shrunk|slid|cut|reduced|lost|down"
_HEDGE = r"(?:about|around|nearly|almost|roughly|some|over|more than|less than|just over|just under|approximately|up to|as much as|at least)\s+"

FROM_TO = re.compile(rf"from\s+(?P<a>{_N})(?P<pa>{_PCT})?(?:\s+(?:in|at|during)\s+(?:\d{{4}}|the \w+))?"
                     rf"\s+to\s+(?P<b>{_N})(?P<pb>{_PCT})?")
PP = re.compile(rf"(?P<n>{_N})\s*(?:percentage points?|pp\b|points?\b)")
CHANGE_PCT = re.compile(rf"\b(?P<verb>{_UP}|{_DOWN})\s+(?:by\s+)?(?:{_HEDGE})?(?P<n>{_N}){_PCT}(?!\s+of\b)")
NOUN_PCT = re.compile(rf"(?P<n>{_N}){_PCT}\s+(?P<noun>rise|increase|jump|growth|gain|drop|fall|decline|decrease|cut|"
                      rf"reduction|loss)")
MULT = re.compile(r"\b(?P<m>doubled|tripled|trebled|quadrupled|halved)\b")
LEVEL_OF = re.compile(rf"(?P<n>{_N}){_PCT}\s+of\b")
# "cut 15% of roles" is both a level (15% of roles) and a relative cut (roles −15%).
_SHARE_VERB = re.compile(r"\b(cut|cuts|eliminat\w*|replac\w*|displac\w*|automat\w*|los[et]\w*|shed\w*|remov\w*|reduc\w*|add\w*|creat\w*|gain\w*)\b(?:\W+\w+){0,4}\W*$")
_SHARE_DOWN = re.compile(r"^(cut|eliminat|replac|displac|automat|los|shed|remov|reduc)")

_MULT = {"doubled": (100, "increase"), "tripled": (200, "increase"), "trebled": (200, "increase"),
         "quadrupled": (300, "increase"), "halved": (50, "decrease")}
_DOWN_RE = re.compile(rf"^(?:{_DOWN}|drop|fall|decline|decrease|cut|reduction|loss)$")


def _f(s: str) -> float:
    return float(s.replace(",", ""))


@dataclass
class Reading:
    value_kind: str
    unit: str                 # percent | percentage_points | nonpct (count, currency, other)
    low: float
    baseline: float | None = None
    direction: str | None = None


@dataclass
class Parse:
    readings: list[Reading] = field(default_factory=list)
    weak: list[float] = field(default_factory=list)      # numbers whose only reading comes from "N% of"
    ambiguous: list[tuple[float, str]] = field(default_factory=list)   # (number, direction)
    literal: Reading | None = None
    spans: list[tuple[int, int]] = field(default_factory=list)


def parse(quote_norm: str, is_rate: bool | None) -> Parse:
    """is_rate: the claim's metric is a share/rate (True), a count/amount (False), or unknown (None)."""
    p = Parse()

    def free(m) -> bool:
        if any(m.start() < e and s < m.end() for s, e in p.spans):
            return False
        p.spans.append((m.start(), m.end()))
        return True

    for m in FROM_TO.finditer(quote_norm):
        if not free(m):
            continue
        a, b = _f(m["a"]), _f(m["b"])
        pct = bool(m["pa"] or m["pb"])
        d = "increase" if b > a else "decrease" if b < a else "none"
        lit = Reading("level", "percent" if pct else "nonpct", b, baseline=a, direction=d)
        p.literal = p.literal or lit
        p.readings += [lit, Reading("absolute_change", "percentage_points" if pct else "nonpct", b - a, direction=d)]
        if a:
            p.readings.append(Reading("relative_change", "percent", (b - a) / a * 100, direction=d))
    for m in PP.finditer(quote_norm):
        if free(m):
            p.readings.append(Reading("absolute_change", "percentage_points", _f(m["n"])))
    for rx, word in ((CHANGE_PCT, "verb"), (NOUN_PCT, "noun")):
        for m in rx.finditer(quote_norm):
            if not free(m):
                continue
            d = "decrease" if _DOWN_RE.match(m[word]) else "increase"
            if is_rate is False:   # "jobs fell 10%": a count changes relatively
                p.readings.append(Reading("relative_change", "percent", _f(m["n"]), direction=d))
            else:                  # "unemployment rose 10%": points or relative? the text doesn't say
                p.ambiguous.append((_f(m["n"]), d))
    for m in MULT.finditer(quote_norm):
        if free(m):
            n, d = _MULT[m["m"]]
            r = Reading("relative_change", "percent", n, direction=d)
            p.readings.append(r)
            p.literal = p.literal or r
    for m in LEVEL_OF.finditer(quote_norm):
        if free(m):
            n = _f(m["n"])
            p.readings.append(Reading("level", "percent", n))
            verb = _SHARE_VERB.search(quote_norm[:m.start()])
            if verb:
                d = "decrease" if _SHARE_DOWN.match(verb.group(1)) else "increase"
                p.readings.append(Reading("relative_change", "percent", n, direction=d))
            p.weak.append(n)
    return p


def _digit_numbers(text: str) -> list[float]:
    return [_f(m) for m in re.findall(_N, text)]


def _close(a: float, b: float) -> bool:
    return abs(abs(a) - abs(b)) <= max(0.05, abs(b) * 0.01)


def _unit_ok(claim_unit: str, reading_unit: str) -> bool:
    if reading_unit == "nonpct":
        return claim_unit not in ("percent", "percentage_points")
    return claim_unit == reading_unit


def _outside_spans(quote_norm: str, spans) -> str:
    out, last = [], 0
    for s, e in sorted(spans):
        out.append(quote_norm[last:s])
        last = e
    out.append(quote_norm[last:])
    return " ".join(out)


def check_value(v: Value, quote_norm: str, is_rate: bool | None) -> tuple[str, list[str]]:
    """Check (and, where the quote allows, fix) `v` in place. Returns (status, fixes).

    status: consistent | consistent_unpatterned | downgraded_ambiguous | rewritten_from_quote | misread | not_in_quote
    """
    fixes: list[str] = []
    # A fraction word recorded as a decimal with a percent unit ("two-thirds" → 0.67%) is off by 100x.
    if v.unit == "percent" and 0 < abs(v.low) <= 1 and abs(v.high) <= 1 \
            and not any(_close(v.low, n) and n <= 1 for n in _digit_numbers(quote_norm)) \
            and number_in_quote(v.low * 100, quote_norm):
        old = f"{v.low:g}"
        v.low, v.high = round(v.low * 100, 2), round(v.high * 100, 2)
        fixes.append(f"value {old} percent → {v.low:g} percent (fraction recorded as a decimal)")
    p = parse(quote_norm, is_rate)

    for r in p.readings:
        if r.value_kind == v.value_kind and _close(v.low, r.low) and _unit_ok(v.unit, r.unit):
            if r.direction in ("increase", "decrease") and v.direction != r.direction:
                fixes.append(f"direction {v.direction} → {r.direction} (from the quote)")
                v.direction = r.direction
            return "consistent", fixes

    for n, d in p.ambiguous:
        if _close(v.low, n):
            if v.value_kind != "unknown":
                fixes.append(f"value_kind {v.value_kind} → unknown (the quote doesn't say points or relative)")
                v.value_kind = "unknown"
            if v.direction != d:
                fixes.append(f"direction {v.direction} → {d} (from the quote)")
                v.direction = d
            return "downgraded_ambiguous", fixes

    # A number outside every recognised phrase: its meaning is left to the LLM support check.
    if number_in_quote(v.low, _outside_spans(quote_norm, p.spans)):
        return "consistent_unpatterned", fixes

    if p.literal and _relates_to_literal(v.low, p.literal, quote_norm, p):
        lit, old = p.literal, f"{v.value_kind} {v.low:g} {v.unit}"
        v.value_kind, v.low, v.high, v.direction = lit.value_kind, lit.low, lit.low, lit.direction or v.direction
        if lit.unit == "nonpct":
            v.unit = v.unit if v.unit not in ("percent", "percentage_points") else "other"
        else:
            v.unit = lit.unit
        if lit.baseline is not None:
            v.baseline = Baseline(number=lit.baseline, period=v.baseline.period if v.baseline else "")
            fixes.append(f"value {old} → {lit.value_kind} {lit.low:g} with baseline {lit.baseline:g} (literal reading of the quote)")
        else:
            fixes.append(f"value {old} → {lit.value_kind} {lit.low:g} {v.unit} (literal reading of the quote)")
        return "rewritten_from_quote", fixes

    if not number_in_quote(v.low, quote_norm):
        return "not_in_quote", fixes

    # Only a weak pattern ("N% of ...") disagrees: not proof of a misread, so downgrade instead of dropping.
    if any(_close(v.low, n) for n in p.weak) and not any(
            _close(v.low, r.low) for r in p.readings if r.low not in p.weak):
        if v.value_kind != "unknown":
            fixes.append(f"value_kind {v.value_kind} → unknown (quote wording only loosely fits)")
            v.value_kind = "unknown"
        return "downgraded_ambiguous", fixes
    return ("misread" if p.readings or p.ambiguous else "consistent_unpatterned"), fixes


def _relates_to_literal(low: float, lit: Reading, quote_norm: str, p: Parse) -> bool:
    """Is the claim's number plainly a (mis)reading of the literal phrase? e.g. 10 or 5 for "from 5% to 10%",
    or 2 / 100 for "doubled"."""
    candidates = numbers_in(quote_norm) + [r.low for r in p.readings]
    if lit.value_kind == "relative_change" and lit.baseline is None:      # doubled / halved / ...
        candidates += [1 + lit.low / 100 if lit.direction == "increase" else 1 - lit.low / 100]
    return any(_close(low, x) for x in candidates)
