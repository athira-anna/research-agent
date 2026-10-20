"""Step 4: is the quote really in the source, and is the claim's number really in the quote?"""

from __future__ import annotations

import re
import unicodedata

from rapidfuzz import fuzz

FUZZY_MIN = 90
MIN_QUOTE_CHARS, MAX_QUOTE_CHARS = 20, 600

_FOOTNOTE = re.compile(r"\[(?:\d{1,3}|[a-z]|note \d+|citation needed)\]", re.I)
_NUMBER = re.compile(r"(?<![a-wyz0-9_.])-?\d[\d,]*(?:\.\d+)?")   # "x5" (five times) counts as 5
_SCALE_WORDS = re.compile(r"(?<=\d)\s?(?:bn|mn|m|k|tn)\b|\b(?:thousand|million|billion|trillion)\b")


def normalize(text: str) -> str:
    """Lowercase; unify quotes, dashes and spaces; drop footnote markers and table pipes."""
    t = unicodedata.normalize("NFKC", text).lower()
    t = t.replace("­", "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"[‐‑‒–—−]", "-", t)
    t = _FOOTNOTE.sub("", t)
    t = re.sub(r"\|?-{3,}\|?", " ", t).replace("|", " ")
    t = " ".join(t.split())
    return t.strip(" \"'.,;:")


def match_quote(quote: str, block_text: str) -> tuple[str, float] | None:
    """('exact'|'fuzzy', score) if the quote is in the block, else None."""
    q, b = normalize(quote), normalize(block_text)
    if not q:
        return None
    if q in b:
        return "exact", 100.0
    score = fuzz.partial_ratio(q, b)
    return ("fuzzy", round(score, 1)) if score >= FUZZY_MIN else None


def best_score(quote: str, blocks: list[dict]) -> float:
    q = normalize(quote)
    return round(max((fuzz.partial_ratio(q, normalize(b["text"])) for b in blocks), default=0.0), 1)


_FRACTIONS = {"half": 50, "a third": 100 / 3, "one third": 100 / 3, "two thirds": 200 / 3, "two-thirds": 200 / 3,
              "a quarter": 25, "one quarter": 25, "three quarters": 75, "three-quarters": 75, "a fifth": 20,
              "one fifth": 20, "a tenth": 10, "one tenth": 10}
_WORD_N = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
           "ten": 10}
_IN_N = re.compile(r"\b(\w+|\d+) (?:in|out of) (\w+|\d+)\b")


def numbers_in(text: str) -> list[float]:
    """Numbers written in digits, plus common fraction words ("half" → 50 and 0.5, "one in five" → 20)."""
    out = []
    for m in _NUMBER.findall(text):
        try:
            out.append(float(m.replace(",", "")))
        except ValueError:
            pass
    for word, pct in _FRACTIONS.items():
        if re.search(rf"\b{word}\b", text):
            out += [pct, pct / 100]
    for a, b in _IN_N.findall(text):
        x = _WORD_N.get(a, int(a) if a.isdigit() else None)
        y = _WORD_N.get(b, int(b) if b.isdigit() else None)
        if x and y and x < y:
            out += [100 * x / y, x / y]
    return out


def number_in_quote(value: float, quote_norm: str) -> bool:
    """True if `value` appears in the quote, allowing written scales ("$4.2bn" may be recorded as 4.2e9)."""
    tokens = numbers_in(quote_norm)
    scales = [1.0]
    if _SCALE_WORDS.search(quote_norm):
        scales += [1e3, 1e6, 1e9, 1e12]
    for t in tokens:
        for s in scales:
            if abs(abs(value) - abs(t) * s) <= max(1e-9, abs(t * s) * 0.005):
                return True
    return False
