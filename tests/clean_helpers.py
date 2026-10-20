"""Shared helpers for stage 2 tests: fake LLMs, a tiny PDF writer, and a fake stage 1 run folder."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from brief_agent.fetch.models import url_hash
from brief_agent.llm import LLMError


class FakeLLM:
    """Returns a fixed answer (or raises) and records every prompt it was given."""

    def __init__(self, answer: dict | None = None, error: str | None = None):
        self.answer = answer or {"relevance": "high", "page_kind": "article", "reason": "about the topic"}
        self.error = error
        self.calls: list[dict] = []

    async def generate_json(self, *, model, system, prompt, schema):
        self.calls.append({"model": model, "system": system, "prompt": prompt})
        if self.error:
            raise LLMError(self.error)
        return schema.model_validate(self.answer)


class FakeGenaiClient:
    """Stands in for google.genai.Client: always returns the same raw text."""

    def __init__(self, text: str):
        self.calls = 0

        async def generate_content(**_):
            self.calls += 1
            return SimpleNamespace(text=text)

        self.aio = SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))


def make_pdf(pages: list[list[str]]) -> bytes:
    """Minimal valid PDF with one text line per entry (Helvetica, top to bottom)."""
    objs: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_id = 3 + 2 * len(pages)
    for i, lines in enumerate(pages):
        ops = ["BT /F1 11 Tf 14 TL 50 780 Td"]
        for line in lines:
            esc = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            ops.append(f"({esc}) Tj T*" if line else "T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] /Contents {4 + 2 * i} 0 R "
                    f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>".encode())
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for n, body in enumerate(objs, start=1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_run(tmp_path: Path, sources: list[dict]) -> Path:
    """Write a fake stage 1 run: each source is {url, html|pdf|text|status}."""
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    records = []
    for i, s in enumerate(sources, start=1):
        sid = f"s{i}"
        rec = {"source_id": sid, "url": s["url"], "url_hash": url_hash(s["url"]), "final_url": s["url"],
               "status": s.get("status", "ok"), "status_detail": s.get("status_detail"), "method": "http",
               "content_type": None, "content_path": None, "from_cache": False, "archive_timestamp": None,
               "fetched_at": "2026-10-20T00:00:00+00:00", "attempts": []}
        for kind, ext in (("html", "html"), ("pdf", "pdf"), ("text", "txt")):
            if kind in s:
                path = raw_dir / f"{sid}.{ext}"
                data = s[kind]
                path.write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
                rec["content_type"], rec["content_path"] = kind, str(path)
        records.append(rec)
    (tmp_path / "sources_raw.json").write_text(json.dumps(records))
    return tmp_path


def article(title: str, paragraphs: list[str], extra: str = "") -> str:
    body = "".join(f"<p>{p}</p>" for p in paragraphs)
    return (f"<!doctype html><html><head><meta charset='utf-8'><title>{title}</title></head><body>"
            f"<nav><a href='/'>Home</a> <a href='/news'>News</a> <a href='/tech'>Tech</a></nav>"
            f"<article><h1>{title}</h1>{body}{extra}</article>"
            f"<footer>Share this article. Sign up for our newsletter. © 2026 Example News</footer></body></html>")
