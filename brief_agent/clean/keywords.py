"""Topic keywords and simple matching, shared by paragraph selection and the relevance fallback."""

from __future__ import annotations

import re

_STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "onto", "about", "over", "under", "between", "of", "on",
    "in", "to", "by", "at", "as", "is", "are", "was", "were", "be", "its", "it", "this", "that", "these",
    "those", "what", "how", "why", "when", "who", "which", "does", "do", "a", "an", "or", "vs", "versus",
    "impact", "effect", "effects", "role", "state", "future",
}


def topic_keywords(topic: str, extra: list[str] | None = None) -> list[str]:
    """Content words of the topic (acronyms like "AI" kept), plus any synonyms from the plan."""
    words = []
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]*", topic):
        lw = w.lower()
        if lw in _STOPWORDS:
            continue
        if len(lw) >= 3 or w.isupper():
            words.append(lw)
    for phrase in extra or []:
        words.append(phrase.lower().strip())
    return list(dict.fromkeys(words))


def _pattern(kw: str) -> re.Pattern:
    # Short words (AI, US) must match exactly; longer ones match inflections (jobs → job, losses → loss).
    if len(kw) <= 3:
        return re.compile(rf"\b{re.escape(kw)}(s|es)?\b", re.I)   # "job" also matches "jobs"
    stem = kw[:-2] if kw.endswith("es") and len(kw) > 5 else kw[:-1] if kw.endswith("s") else kw
    return re.compile(rf"\b{re.escape(stem)}\w*\b", re.I)


def keyword_hits(text: str, keywords: list[str]) -> tuple[int, int]:
    """(distinct keywords found, total occurrences)."""
    distinct = total = 0
    for kw in keywords:
        n = len(_pattern(kw).findall(text))
        if n:
            distinct += 1
            total += n
    return distinct, total
