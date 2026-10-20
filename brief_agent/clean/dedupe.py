"""Step 4: mark duplicate paragraphs across sources. Nothing is ever removed.

Runs on cleaned blocks only, so shared site boilerplate (already stripped) can't cause false matches.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict

from .models import CleanSource, Duplicate

SHINGLE_WORDS = 5
MIN_BLOCK_CHARS = 60          # very short blocks ("Read more", a date line) say nothing about copying
BLOCK_MATCH = 0.8             # share of a block's shingles found in one earlier block
SOURCE_DUPLICATE = 0.5        # share of a source's text that is copied → source-level duplicate

_WORD = re.compile(r"[a-z0-9]+")


def shingles(text: str) -> set[int]:
    words = _WORD.findall(text.lower())
    if len(words) < SHINGLE_WORDS:
        return {hash(" ".join(words))} if words else set()
    return {hash(" ".join(words[i:i + SHINGLE_WORDS])) for i in range(len(words) - SHINGLE_WORDS + 1)}


def _eligible(block) -> bool:
    return block.kind in ("paragraph", "list", "table") and len(block.text) >= MIN_BLOCK_CHARS


def mark_duplicates(sources: list[CleanSource]) -> None:
    """Compare each source with all earlier ones (in input order); annotate blocks and sources in place."""
    index: dict[int, set[str]] = defaultdict(set)      # shingle → block IDs seen so far
    block_size: dict[str, int] = {}

    for src in sources:
        eligible = [b for b in src.blocks if _eligible(b)]
        own: list[tuple[str, set[int]]] = []
        copied_chars = total_chars = 0
        copied_from: Counter[str] = Counter()

        for b in eligible:
            sh = shingles(b.text)
            own.append((b.id, sh))
            total_chars += len(b.text)
            hits: Counter[str] = Counter()
            for s in sh:
                hits.update(index.get(s, ()))
            if hits and sh:
                best_id, n = hits.most_common(1)[0]
                if n / len(sh) >= BLOCK_MATCH or n / max(block_size[best_id], 1) >= BLOCK_MATCH:
                    b.duplicate_of = best_id
                    copied_chars += len(b.text)
                    copied_from[best_id.split("-")[0]] += len(b.text)

        if total_chars and copied_chars / total_chars >= SOURCE_DUPLICATE:
            unique = sum(1 for b in eligible if not b.duplicate_of)
            src.duplicate = Duplicate(of=copied_from.most_common(1)[0][0],
                                      overlap=round(copied_chars / total_chars, 2), unique_blocks=unique)

        # Index after comparing, so a source never matches itself.
        for block_id, sh in own:
            block_size[block_id] = len(sh)
            for s in sh:
                index[s].add(block_id)
