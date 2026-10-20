"""Step 6: fit a long source into the extraction budget by keeping its most relevant blocks."""

from __future__ import annotations

import math

from .keywords import keyword_hits
from .models import Block

BUDGET_CHARS = 40_000
ALWAYS_KEEP_INTRO = 2
TABLE_OVERFLOW = 1.1
DENSITY_BASE_CHARS = 300   # paragraphs up to this length aren't penalised for length   # tables beside selected text may push slightly past the budget: numbers are evidence


def _score(block: Block, keywords: list[str], question_terms: list[str]) -> float:
    """Subject terms: is this about the topic? Question terms: does it answer one of the plan's questions?"""
    distinct, total = keyword_hits(block.text, keywords)
    if block.section:
        s_distinct, _ = keyword_hits(block.section, keywords)
        distinct += 0.5 * s_distinct
    score = distinct * 3 + min(total, 10)
    if question_terms:
        q_distinct, q_total = keyword_hits(block.text, question_terms)
        score += q_distinct * 4 + min(q_total, 5)
    return score


def select_blocks(blocks: list[Block], keywords: list[str], budget: int = BUDGET_CHARS,
                  question_terms: list[str] | None = None) -> tuple[list[str], float]:
    """Return (selected block IDs in original order, coverage of the source's unique text).

    `keywords` are the subject terms (topic words + the plan's synonyms); `question_terms` come from
    the plan's individual questions and add a bonus.
    """
    question_terms = question_terms or []
    eligible = [b for b in blocks if b.kind != "heading" and not b.duplicate_of]
    total = sum(len(b.text) for b in eligible)
    if total == 0:
        return [], 0.0

    if total <= budget:
        chosen = {b.id for b in eligible}
    else:
        chosen: set[str] = set()
        used = 0
        for b in [b for b in eligible if b.kind == "paragraph"][:ALWAYS_KEEP_INTRO]:
            chosen.add(b.id)
            used += len(b.text)
        # Every block is scored wherever it sits, so a key paragraph mid-article isn't missed.
        scores = {b.id: _score(b, keywords, question_terms) for b in eligible}
        # Rank by relevance per character (damped), not raw hits: otherwise long, loosely related
        # paragraphs beat short ones that are entirely on topic.
        rank = {i: sc / max(1.0, math.sqrt(len(b.text) / DENSITY_BASE_CHARS))
                for b in eligible for i, sc in [(b.id, scores[b.id])]}
        ranked = sorted((b for b in eligible if b.id not in chosen), key=lambda b: -rank[b.id])  # stable: ties keep order
        # Article text first; footnotes (references) only fill whatever budget is left.
        for b in sorted(ranked, key=lambda b: b.kind == "footnote"):
            if scores[b.id] <= 0:
                continue
            if used + len(b.text) <= budget:
                chosen.add(b.id)
                used += len(b.text)
        selected_sections = {b.section for b in eligible if b.id in chosen and b.section}
        for b in eligible:
            if (b.kind == "table" and b.id not in chosen and b.section in selected_sections
                    and used + len(b.text) <= budget * TABLE_OVERFLOW):
                chosen.add(b.id)
                used += len(b.text)

        # Leftover budget: the blocks nearest to what we picked, so extraction sees surrounding context.
        pos = {b.id: i for i, b in enumerate(blocks)}
        picked = [pos[i] for i in chosen]
        rest = [b for b in eligible if b.id not in chosen and b.kind != "footnote"]
        for b in sorted(rest, key=lambda b: min(abs(pos[b.id] - p) for p in picked) if picked else pos[b.id]):
            if used + len(b.text) <= budget:
                chosen.add(b.id)
                used += len(b.text)

    # Coverage of the article itself; reference lists don't count.
    body = [b for b in eligible if b.kind != "footnote"] or eligible
    coverage = sum(len(b.text) for b in body if b.id in chosen) / sum(len(b.text) for b in body)
    # Headings of sections we kept go along as context (not counted in the budget).
    sections = {b.section for b in blocks if b.id in chosen and b.section}
    for b in blocks:
        if b.kind == "heading" and b.text in sections:
            chosen.add(b.id)
    return [b.id for b in blocks if b.id in chosen], round(coverage, 3)
