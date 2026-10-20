"""Stage 2: sources_raw.json (stage 1) → sources.json with clean blocks and three separate statuses.

    fetch       did we get the page?            (copied from stage 1)
    processing  did we get clean text out of it?
    usability   should later stages use it?     (usable / partial / excluded + reasons + notes)

Every source is written out, including failed and excluded ones; nothing is silently dropped.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

from ..fetch.detect import looks_like_consent
from ..llm import LLM
from .blocks import build_blocks, content_chars, has_sentence, to_markdown
from .dedupe import mark_duplicates
from .parse import MIN_CONTENT_CHARS, parse_html, parse_pdf, parse_reader
from .keywords import topic_keywords
from .models import CleanSource, Processing
from .relevance import check_relevance, looks_like_injection
from .select import select_blocks

_FETCH_FIELDS = ("status", "status_detail", "method", "content_type", "from_cache", "archive_timestamp",
                 "fetched_at", "attempts")


def _process(raw: dict) -> CleanSource:
    """Steps 1–3 for one source: extract, build blocks, metadata."""
    src = CleanSource(
        source_id=raw["source_id"], url=raw["url"], url_hash=raw.get("url_hash", ""),
        final_url=raw.get("final_url"), raw_path=raw.get("content_path"), raw_sha256=None,
        fetch={k: raw.get(k) for k in _FETCH_FIELDS}, processing=Processing("skipped"),
    )
    if raw["status"] != "ok":
        detail = f" ({raw['status_detail']})" if raw.get("status_detail") else ""
        src.processing = Processing("skipped", f"fetch failed: {raw['status']}{detail}")
        src.exclude(f"fetch: {raw['status']}{detail}")
        return src

    try:
        data = Path(raw["content_path"]).read_bytes()
        src.raw_sha256 = hashlib.sha256(data).hexdigest()
        kind = raw.get("content_type")
        if kind == "pdf":
            raw_blocks, meta = parse_pdf(data)
        elif kind == "text":
            raw_blocks, meta = parse_reader(data.decode("utf-8", errors="replace"))
        else:
            raw_blocks, meta = parse_html(data.decode("utf-8", errors="replace"))
    except Exception as e:  # noqa: BLE001 - a broken file excludes one source, not the run
        src.processing = Processing("failed", f"{type(e).__name__}: {e}")
        src.exclude("processing failed")
        return src

    src.blocks = build_blocks(src.source_id, raw_blocks)
    date = meta.get("date")
    # A PDF's creation date is often when the file was generated, not when the work was published.
    date_conf = ("file_metadata" if kind == "pdf" else "page_metadata") if date else "unknown"
    if not date and raw.get("archive_timestamp"):
        ts = raw["archive_timestamp"]
        date, date_conf = f"{ts[:4]}-{ts[4:6]}-{ts[6:8]}", "archive_snapshot"
    src.meta = {"title": meta.get("title"), "site_name": meta.get("sitename") or meta.get("hostname"),
                "author": meta.get("author"), "published_date": date, "date_confidence": date_conf,
                "language": meta.get("language")}
    src.stats = {"chars": content_chars(src.blocks), "blocks": len(src.blocks),
                 "pages": meta.get("pages"),
                 "by_kind": {k: sum(1 for b in src.blocks if b.kind == k)
                             for k in ("paragraph", "heading", "table", "list", "footnote")}}

    if looks_like_consent(" ".join(b.text for b in src.blocks)):
        src.processing = Processing("empty", "text is a cookie-consent dialog, not the page content")
        src.exclude("processing: consent_wall (cookie-consent dialog instead of content)")
        return src

    if src.stats["chars"] < MIN_CONTENT_CHARS or not has_sentence(src.blocks):
        why = "scanned PDF, no text layer" if kind == "pdf" and src.stats["chars"] == 0 else \
            f"only {src.stats['chars']} chars of text after cleaning"
        src.processing = Processing("empty", why)
        src.exclude(f"processing: empty ({why})")
        return src

    src.processing = Processing("ok", f"{len(src.blocks)} blocks, {src.stats['chars']:,} chars")
    if raw.get("status_detail") == "short":
        src.usability.notes.append("short page")
    if date_conf == "unknown":
        src.usability.notes.append("publication date unknown")
    return src


def _apply_relevance(src: CleanSource) -> None:
    r = src.relevance
    if r.page_kind == "error_or_challenge":
        src.exclude("bot_challenge: page is an error or challenge page")
    elif r.page_kind == "index_or_listing":
        src.exclude("not_an_article: page is a list of headlines or links")
    elif r.label == "none":
        src.exclude(f"off_topic: {r.reason}")
    elif r.label == "partial":
        src.mark_partial(f"only partly about the topic: {r.reason}")
    if not r.checked:
        src.usability.notes.append("relevance not confirmed by LLM (keyword fallback)")


async def clean_sources(run_dir: Path, topic: str, llm: LLM | None = None,
                        extra_keywords: list[str] | None = None) -> list[CleanSource]:
    from ..plan.planner import load_run_plan  # local import: plan.validate imports clean.keywords

    raw_records = json.loads((run_dir / "sources_raw.json").read_text())
    sources = [_process(r) for r in raw_records]
    plan = load_run_plan(run_dir)
    # Subject terms (is it about the topic?) and question terms (does it answer a question?) stay separate.
    keywords = topic_keywords(topic, (extra_keywords or []) + (plan.all_subject_terms() if plan else []))
    question_terms = list(dict.fromkeys(t for q in plan.questions for t in q.question_terms)) if plan else []
    ok = [s for s in sources if s.processing.status == "ok"]

    mark_duplicates(ok)

    results = await asyncio.gather(
        *(check_relevance(llm, topic, s.meta.get("title"), s.blocks, keywords) for s in ok))
    for src, rel in zip(ok, results):
        src.relevance = rel
        _apply_relevance(src)
        if looks_like_injection(src.blocks):
            src.usability.notes.append("contains text addressed to an AI (possible prompt injection); treated as data")
        if src.duplicate:
            d = src.duplicate
            if d.unique_blocks == 0:
                src.exclude(f"duplicate of {d.of} (no unique content; data kept)")
            else:
                src.mark_partial(f"overlaps {d.of} by {d.overlap:.0%}; only {d.unique_blocks} unique blocks used")
        if src.usability.status != "excluded":
            src.selected_ids, src.coverage = select_blocks(src.blocks, keywords, question_terms=question_terms)
            if src.coverage < 0.999:
                src.usability.notes.append(f"{src.coverage:.0%} of the text read (most relevant parts)")

    _write(run_dir, sources)
    return sources


def _write(run_dir: Path, sources: list[CleanSource]) -> None:
    (run_dir / "sources.json").write_text(json.dumps([s.to_dict() for s in sources], indent=2, ensure_ascii=False))
    clean_dir = run_dir / "clean"
    clean_dir.mkdir(exist_ok=True)
    for s in sources:
        if s.blocks:
            header = f"# {s.meta.get('title') or s.url}\n\n<!-- {s.source_id} {s.url} url_hash={s.url_hash[:12]} -->\n\n"
            (clean_dir / f"{s.source_id}.md").write_text(header + to_markdown(s.blocks), encoding="utf-8")
