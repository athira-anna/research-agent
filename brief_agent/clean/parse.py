"""Stage 2, step 1: parse HTML, PDF and reader-service output into raw text blocks + metadata.

(Named "parse" so it isn't confused with brief_agent/extract/, which is stage 3 claim extraction.)"""

from __future__ import annotations

import io
import re
import statistics
from collections import Counter

import trafilatura
from lxml import etree

from ..fetch.detect import CONSENT_PRUNE_XPATH, TRAFILATURA_CONFIG
from .models import RawBlock

MIN_CONTENT_CHARS = 150


# --- HTML ---------------------------------------------------------------------------

def _cell_text(el) -> str:
    return " ".join("".join(el.itertext()).split())


def _table_to_markdown(table) -> str:
    rows = [[_cell_text(c) for c in row.findall("cell")] for row in table.findall("row")]
    rows = [r for r in rows if any(r)]
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * width]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def _xml_to_blocks(xml: str) -> tuple[list[RawBlock], dict]:
    root = etree.fromstring(xml.encode("utf-8"))
    meta = {k: root.get(k) for k in ("title", "author", "date", "sitename", "hostname", "language")}
    main = root.find("main")
    blocks: list[RawBlock] = []
    for el in (main if main is not None else []):
        tag = el.tag
        if tag == "head":
            blocks.append(RawBlock("heading", _cell_text(el)))
        elif tag == "table":
            blocks.append(RawBlock("table", _table_to_markdown(el)))
        elif tag == "list":
            items = [_cell_text(i) for i in el.iter("item")]
            blocks.append(RawBlock("list", "\n".join(f"- {i}" for i in items if i)))
        elif tag in ("p", "quote", "code"):
            blocks.append(RawBlock("paragraph", _cell_text(el)))
        else:
            text = _cell_text(el)
            if text:
                blocks.append(RawBlock("paragraph", text))
    return [b for b in blocks if b.text.strip()], meta


def parse_html(html: str) -> tuple[list[RawBlock], dict]:
    kwargs = dict(output_format="xml", include_tables=True, include_comments=False, include_links=False,
                  with_metadata=True, config=TRAFILATURA_CONFIG, prune_xpath=[CONSENT_PRUNE_XPATH],
                  # Don't guess a date from years mentioned in the text; only real page metadata.
                  date_extraction_params={"extensive_search": False, "original_date": True})
    xml = trafilatura.extract(html, **kwargs)
    blocks, meta = _xml_to_blocks(xml) if xml else ([], {})
    if sum(len(b.text) for b in blocks) < MIN_CONTENT_CHARS:
        xml = trafilatura.extract(html, favor_recall=True, **kwargs)
        if xml:
            blocks, meta = _xml_to_blocks(xml)
    return blocks, meta


# --- PDF ----------------------------------------------------------------------------

_DIGITS = re.compile(r"\d+")
_SENTENCE_END = re.compile(r"[.!?:;\"”)\]]$")


def _looks_like_heading(line: str) -> bool:
    if len(line) > 80 or _SENTENCE_END.search(line) or len(line.split()) > 12:
        return False
    return bool(re.match(r"^(\d+(\.\d+)*\.?\s+)?[A-Z][^a-z]*$|^(\d+(\.\d+)*\.?\s+)[A-Z]", line)) or line.isupper()


def parse_pdf(data: bytes) -> tuple[list[RawBlock], dict]:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [(page.extract_text() or "").splitlines() for page in reader.pages]

    # Running headers/footers: the same line (ignoring page numbers) on most pages.
    repeated: set[str] = set()
    if len(pages) >= 3:
        counts: Counter[str] = Counter()
        for lines in pages:
            counts.update({_DIGITS.sub("#", ln.strip()) for ln in lines if ln.strip()})
        repeated = {ln for ln, n in counts.items() if n >= max(3, len(pages) * 0.5)}

    lengths = [len(ln.strip()) for lines in pages for ln in lines if ln.strip()]
    typical = statistics.median(lengths) if lengths else 80

    blocks: list[RawBlock] = []
    for page_no, lines in enumerate(pages, start=1):
        buf = ""

        def flush():
            nonlocal buf
            if buf.strip():
                blocks.append(RawBlock("paragraph", buf.strip(), page_no))
            buf = ""

        for raw in lines:
            line = raw.strip()
            if not line:
                flush()
                continue
            if _DIGITS.sub("#", line) in repeated or _DIGITS.fullmatch(line):
                continue
            if not buf and _looks_like_heading(line):
                blocks.append(RawBlock("heading", line, page_no))
                continue
            if buf.endswith("-") and line[:1].islower():
                buf = buf[:-1] + line          # re-join a word split at the line end
            else:
                buf = f"{buf} {line}" if buf else line
            # A short line that ends a sentence usually ends the paragraph.
            if _SENTENCE_END.search(line) and len(line) < typical * 0.8:
                flush()
        flush()

    info = reader.metadata or {}
    date = getattr(info, "creation_date", None)
    meta = {"title": (info.get("/Title") or None), "author": (info.get("/Author") or None),
            "date": date.date().isoformat() if date else None, "pages": len(pages)}
    return blocks, meta


# --- reader service (Jina markdown) ------------------------------------------------

_MD_IMAGE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HEADER = re.compile(r"^(Title|URL Source|Published Time|Markdown Content|Warning):\s*(.*)$")


def _mostly_links(line: str) -> bool:
    links = _MD_LINK.findall(line)
    if len(links) < 2:
        return False
    plain = _MD_LINK.sub("", line)
    return len(re.sub(r"[\s|•·\-*,]", "", plain)) < 0.3 * len(line)


def parse_reader(text: str) -> tuple[list[RawBlock], dict]:
    meta: dict = {}
    lines = text.splitlines()
    body_start = 0
    for i, line in enumerate(lines[:20]):
        m = _HEADER.match(line)
        if m:
            key, value = m.groups()
            if key == "Title":
                meta["title"] = value.strip() or None
            elif key == "Published Time":
                meta["date"] = value.strip()[:10] or None
            body_start = i + 1
            if key == "Markdown Content":
                break

    blocks: list[RawBlock] = []
    para: list[str] = []
    table: list[str] = []
    items: list[str] = []

    def flush():
        nonlocal para, table, items
        if para:
            blocks.append(RawBlock("paragraph", " ".join(para)))
        if table:
            blocks.append(RawBlock("table", "\n".join(table)))
        if items:
            blocks.append(RawBlock("list", "\n".join(items)))
        para, table, items = [], [], []

    for raw in lines[body_start:]:
        line = raw.strip()
        if not line or set(line) <= set("-=_*"):
            flush()
            continue
        if _mostly_links(line):
            continue  # navigation menus, tag clouds
        line = _MD_LINK.sub(r"\1", _MD_IMAGE.sub("", line)).strip()
        if not line:
            continue
        if line.startswith("#"):
            flush()
            blocks.append(RawBlock("heading", line.lstrip("#").strip()))
        elif line.startswith("|"):
            if para or items:
                flush()
            table.append(line)
        elif re.match(r"^([-*+]|\d+\.)\s+", line):
            if para or table:
                flush()
            items.append("- " + re.sub(r"^([-*+]|\d+\.)\s+", "", line))
        else:
            if table or items:
                flush()
            para.append(line)
    flush()
    return [b for b in blocks if b.text.strip()], meta
