"""Step 2: raw pieces → typed, numbered blocks with their section; drop leftover boilerplate."""

from __future__ import annotations

import re
import unicodedata

from .models import Block, RawBlock

_FOOTNOTE_SECTIONS = re.compile(
    r"^(references|notes|footnotes|sources|citations|bibliography|works cited|endnotes)$", re.I)

# Leftover single-line boilerplate. Only checked on short paragraph/list/heading blocks;
# tables and footnotes are never filtered.
_BOILERPLATE = re.compile(
    r"^(share (this|on)\b|sign up for|subscribe to our newsletter|advertisement$|advertisements?$|"
    r"accept (all )?cookies|we use cookies|follow us on|skip to (main )?content|read more\b|"
    r"related (articles|stories|content)$|all rights reserved|©|copyright \d{4}|click here to\b|"
    r"recommended for you$|most popular$|trending now$|back to top$)",
    re.I,
)
_BOILERPLATE_MAX_CHARS = 140


def _normalize(text: str, keep_lines: bool) -> str:
    text = unicodedata.normalize("NFC", text).replace(" ", " ").replace("​", "")
    if keep_lines:  # tables and lists: keep one row/item per line
        return "\n".join(" ".join(ln.split()) for ln in text.splitlines() if ln.strip())
    return " ".join(text.split())


def _is_boilerplate(kind: str, text: str) -> bool:
    if kind in ("table", "footnote") or len(text) > _BOILERPLATE_MAX_CHARS:
        return False
    first = text.lstrip("- ").strip()
    return bool(_BOILERPLATE.match(first))


def build_blocks(source_id: str, raw: list[RawBlock]) -> list[Block]:
    """Headings stay separate blocks (never merged into the next paragraph); each block records its section."""
    out: list[Block] = []
    section: str | None = None
    in_footnotes = False
    for rb in raw:
        text = _normalize(rb.text, keep_lines=rb.kind in ("table", "list"))
        if not text or _is_boilerplate(rb.kind, text):
            continue
        kind = rb.kind
        if kind == "heading":
            section = text
            in_footnotes = bool(_FOOTNOTE_SECTIONS.match(text.strip(" :")))
        elif in_footnotes:
            kind = "footnote"
        if out and out[-1].text == text and out[-1].kind == kind:
            continue  # repeated element (e.g. a heading rendered twice)
        for piece in _split(kind, text):
            out.append(Block(id=f"{source_id}-p{len(out) + 1:03d}", kind=kind, text=piece,
                             section=section if kind != "heading" else None, page=rb.page))
    return out


LIST_CHUNK_CHARS = 1500


def _split(kind: str, text: str) -> list[str]:
    """One block per reference in a footnote list (each is its own citation); long lists in chunks."""
    if "\n" not in text or kind not in ("footnote", "list"):
        return [text]
    items = [ln for ln in text.splitlines() if ln.strip()]
    if kind == "footnote":
        return items
    if len(text) <= LIST_CHUNK_CHARS * 1.5:
        return [text]
    chunks, cur = [], []
    for item in items:
        if cur and sum(len(i) for i in cur) + len(item) > LIST_CHUNK_CHARS:
            chunks.append("\n".join(cur))
            cur = []
        cur.append(item)
    return chunks + (["\n".join(cur)] if cur else [])


def content_chars(blocks: list[Block]) -> int:
    return sum(len(b.text) for b in blocks if b.kind != "heading")


def has_sentence(blocks: list[Block]) -> bool:
    return any(b.kind in ("paragraph", "list", "table") and re.search(r"[a-zA-Z]{3,}.*[.!?]", b.text)
               for b in blocks)


def to_markdown(blocks: list[Block]) -> str:
    parts = []
    for b in blocks:
        if b.kind == "heading":
            parts.append(f"## {b.text}")
        else:
            tag = f"<!-- {b.id}{f' p.{b.page}' if b.page else ''}{' dup:' + b.duplicate_of if b.duplicate_of else ''} -->"
            parts.append(f"{tag}\n{b.text}")
    return "\n\n".join(parts) + "\n"
