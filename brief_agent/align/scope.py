"""Code-side scope standardisation: timeframes from years/decades, geography aliases, and a built-in
place hierarchy. Wording that code can't map is left to the LLM (aligner.py)."""

from __future__ import annotations

import re

from ..extract.grounding import normalize

GEO_ALIASES = {
    "US": ["us", "u.s", "u.s.", "usa", "u.s.a", "united states", "united states of america", "america", "american"],
    "UK": ["uk", "u.k", "u.k.", "united kingdom", "britain", "great britain", "british"],
    "EU": ["eu", "european union", "eu-28", "eu28", "eu-27", "eu27", "the european union's 28 member states",
           "28 member states", "the 28 member states", "european union member states", "europe"],
    "OECD countries": ["oecd", "oecd countries", "21 oecd countries", "oecd members", "oecd member countries"],
    "global": ["global", "globally", "world", "worldwide", "the world", "international", "around the world",
               "all countries"],
}
_ALIAS = {a: canon for canon, aliases in GEO_ALIASES.items() for a in aliases}

# narrower → broader (built in); the LLM adds more per question.
GEO_HIERARCHY = {("US", "global"), ("UK", "global"), ("EU", "global"), ("OECD countries", "global"),
                 ("US", "OECD countries"), ("UK", "OECD countries"), ("Canada", "OECD countries"),
                 ("Japan", "OECD countries"), ("Germany", "OECD countries"), ("France", "OECD countries"),
                 ("Israel", "OECD countries"), ("Australia", "OECD countries"), ("South Korea", "OECD countries"),
                 ("Germany", "EU"), ("France", "EU"), ("Italy", "EU"), ("Spain", "EU")}


def canon_geography(raw: str) -> str | None:
    n = normalize(raw).strip(" .")
    if not n:
        return None
    return _ALIAS.get(n) or _ALIAS.get(re.sub(r"^the ", "", n))


def canon_timeframe(tf: dict) -> tuple[int, int] | None:
    """(start, end) years, or None when the source gives no time. "2030s" → (2030, 2039)."""
    start, end = tf.get("start"), tf.get("end")
    if start or end:
        s, e = start or end, end or start
        return (min(s, e), max(s, e))
    decades = re.findall(r"\b(\d{3})0s\b", tf.get("raw") or "")
    if decades:
        years = [int(d) * 10 for d in decades]
        return (min(years), max(years) + 9)
    years = [int(y) for y in re.findall(r"\b(19\d\d|20\d\d)\b", tf.get("raw") or "")]
    return (min(years), max(years)) if years else None


def broader_closure(pairs: set[tuple[str, str]]) -> set[tuple[str, str]]:
    """Transitive closure of narrower→broader pairs."""
    closure = set(pairs)
    changed = True
    while changed:
        changed = False
        for a, b in list(closure):
            for c, d in list(closure):
                if b == c and (a, d) not in closure and a != d:
                    closure.add((a, d))
                    changed = True
    return closure
