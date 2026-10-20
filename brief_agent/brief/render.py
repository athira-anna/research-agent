"""Steps 6.1–6.5: build the brief as a fixed-layout document model, then render it as brief.html.
Pure code: every sentence comes from checked findings, except the (separately checked) bottom line."""

from __future__ import annotations

import html
import json
import re
from datetime import date
from pathlib import Path

from .wording import GAP_TITLES, LABEL_TEXT, headline, qualifiers, scope_text, value_text

# Document model: a list of (kind, payload). Inline text may contain **bold**, [n] citations and
# ⟦label:xyz⟧ badges; each renderer translates those.
Doc = list[tuple[str, object]]

MAX_SINGLE_ORIGIN = 10


class Citations:
    def __init__(self, claims: dict[str, dict], sources: dict[str, dict]):
        self.claims, self.sources = claims, sources
        self.order: list[str] = []

    def ref(self, claim_id: str) -> str:
        """One number per quoted passage: claims sharing a quote share a citation."""
        c = self.claims[claim_id]
        key = (c["evidence"]["block_id"], c["evidence"]["quote"])
        for i, cid in enumerate(self.order):
            o = self.claims[cid]
            if (o["evidence"]["block_id"], o["evidence"]["quote"]) == key:
                return f"[{i + 1}]"
        self.order.append(claim_id)
        return f"[{len(self.order)}]"

    def entries(self) -> list[dict]:
        out = []
        for i, cid in enumerate(self.order, start=1):
            c = self.claims[cid]
            s = self.sources.get(c["source_id"], {})
            out.append({"n": i, "claim_id": cid, "origin": c["evidence"].get("attributed_to") or "",
                        "quote": c["evidence"]["quote"], "block_id": c["evidence"]["block_id"],
                        "source_id": c["source_id"], "title": (s.get("meta") or {}).get("title") or s.get("url"),
                        "url": s.get("url"), "site": (s.get("meta") or {}).get("site_name")})
        return out


def publisher(source: dict) -> str:
    site = (source.get("meta") or {}).get("site_name")
    if site and site.lower() not in {"news", "home", "blog", "article", "press release", "newsroom"}:
        return site
    host = re.sub(r"^www\.", "", re.sub(r"^https?://", "", source.get("url") or "").split("/")[0])
    return host or source.get("source_id", "source")


def _claim_line(c: dict, cite: Citations, with_scope: bool = True) -> str:
    val = value_text(c.get("value"))
    sc = scope_text(c["scope"]) if with_scope else ""
    origin = c["origin"]
    if origin.startswith("source ") or origin == c["source_id"]:     # unattributed: the source's own voice
        origin = publisher(cite.sources.get(c["source_id"], {}))
    lead = f"**{origin}**" + (f" ({sc})" if sc else "")
    return f"{lead}: {c['statement']}" + (f" — {val}" if val and val not in c["statement"] else "") \
        + qualifiers(c) + f" {cite.ref(c['claim_id'])}"


# The brief's fixed layout: every run gets these sections, in this order, with these titles.
# An empty section stays, with its standard "none" line, so every brief reads the same way.
SECTIONS = [
    ("summary", "Summary", "No summary could be produced."),
    ("agreements", "Where sources agree", "No point is supported by two or more independent origins."),
    ("contradictions", "Contradictions and open disagreements", "No contradictions found among comparable claims."),
    ("context", "Different scopes and measures", "No figures that need this context."),
    ("outliers", "Single-origin claims", "No claims rest on a single origin."),
    ("gaps", "Gaps: what no source covers", "No gaps identified."),
    ("sources", "Sources", "No sources."),
]
SECTION_INTROS = {
    "agreements": "Points two or more independent origins support.",
    "contradictions": "Contested = sources contradict each other on the same scope. Unresolved = a difference "
                      "the sources don't let us settle (the reason is given).",
    "context": "Figures that look comparable but cover different places, groups or periods, measure different "
               "things, or mix forecasts with measured results. They can all be true at once.",
    "outliers": "Claims only one study or organisation makes, so they aren't corroborated.",
    "sources": "Every link given for this brief, whether it could be used, and the exact passages cited from it. "
               "Each numbered quote was checked to appear in the source and to support its claim; claims are "
               "not verified as true.",
}


def _finding(f: dict, cite: Citations) -> Doc:
    """Every finding card has the same fields, in the same order."""
    v = f["verdict"]
    doc: Doc = [("h3", f["label"]),
                ("p", f"⟦label:{v['label']}⟧ **{headline(v)}**"),
                ("p", f"**Strength:** {f['strength']['level']}")]
    by_id = {c["claim_id"]: c for c in f["claims"]}
    if v["label"] == "contested" and v.get("sides"):
        for side in v["sides"]:
            pos = side.get("position") or "Position"
            doc.append(("p", f"**{pos}** ({side['origins']} origin{'s' if side['origins'] != 1 else ''})"))
            doc.append(("ul", [_claim_line(by_id[c], cite) for c in side["claim_ids"] if c in by_id]))
    else:
        doc.append(("ul", [_claim_line(c, cite) for c in f["claims"]]))
    doc.append(("p", "**Why they may differ:** " + ("; ".join(f["likely_reasons"][:5]) or "—")))
    doc.append(("p", "**Caveats:** " + ("; ".join(f["strength"]["notes"]) or "none")))
    return doc


def _rank(f: dict) -> tuple:
    return ({"strong": 0, "moderate": 1, "weak": 2}.get(f["strength"]["level"], 3), -f["verdict"].get("origins", 1),
            f["question_id"])


# Which verdicts go in which section (2–4). Single-origin claims get their own compact list (section 5).
FINDING_SECTIONS = {
    "agreements": ("consensus",),
    "contradictions": ("contested", "unresolved"),
    "context": ("complementary", "different_measures", "not_comparable", "unrelated", "unjudged"),
}


def build_doc(run_dir: Path, analysis: dict, bottom_line: dict) -> tuple[Doc, list[dict]]:
    """The whole brief as a document model: fixed header, then every section of SECTIONS in order."""
    claims = {c["claim_id"]: c for c in json.loads((run_dir / "claims.json").read_text())["claims"]}
    sources = {s["source_id"]: s for s in json.loads((run_dir / "sources.json").read_text())}
    cite = Citations(claims, sources)
    # Clusters outside the plan's questions are findings like any other, marked as such.
    findings = sorted(analysis["findings"] + [f | {"label": f"{f['label']} (outside the research questions)"}
                                              for f in analysis["emerging"]], key=_rank)

    content: dict[str, Doc] = {"summary": _summary(bottom_line)}
    for sid, labels in FINDING_SECTIONS.items():
        content[sid] = [node for f in findings if f["verdict"]["label"] in labels for node in _finding(f, cite)]
    content["outliers"] = _outliers([f for f in findings if f["verdict"]["label"] == "single_origin"], cite)
    content["gaps"] = _gaps(analysis["gaps"])
    content["sources"] = _sources(analysis["sources"], cite)   # last: every citation has been numbered by now

    doc = _header(run_dir, analysis)
    for i, (sid, title, empty) in enumerate(SECTIONS, start=1):
        doc.append(("section", (sid, f"{i}. {title}")))
        body = content.get(sid) or []
        if sid in SECTION_INTROS and body:
            doc.append(("p", SECTION_INTROS[sid]))
        doc += body or [("p", f"_{empty}_")]
    return doc, cite.entries()


def _header(run_dir: Path, analysis: dict) -> Doc:
    used = sum(1 for s in analysis["sources"] if s["claims"])
    meta = f"Generated {date.today().isoformat()} · {used} of {len(analysis['sources'])} sources used · run `{run_dir.name}`"
    if analysis.get("plan_source") == "fallback":
        meta += " · research questions were generic because planning failed"
    return [("h1", f"Research brief: {analysis['topic']}"), ("p", meta),
            ("p", f"**Topic as understood:** {analysis['interpretation']}"),
            ("p", "**Assumptions:** " + (" ".join(analysis["assumptions"]) or "None stated.")),
            ("toc", [(sid, f"{i}. {title}") for i, (sid, title, _) in enumerate(SECTIONS, start=1)])]


def _summary(bottom_line: dict) -> Doc:
    doc: Doc = []
    if bottom_line.get("source") == "template":
        doc.append(("p", "_Auto-generated from the findings (the summary writer was unavailable or its bullets "
                         "failed the checks)._"))
    if bottom_line["bullets"]:
        doc.append(("ul", [b["text"] for b in bottom_line["bullets"]]))
    return doc


def _outliers(single: list[dict], cite: Citations) -> Doc:
    """A compact list rather than a card per finding: one card each would bury everything else."""
    if not single:
        return []
    doc: Doc = [("ul", [_claim_line(c, cite) for f in single[:MAX_SINGLE_ORIGIN] for c in f["claims"][:2]])]
    if len(single) > MAX_SINGLE_ORIGIN:
        doc.append(("p", f"_{len(single) - MAX_SINGLE_ORIGIN} more in analysis.json in the run folder._"))
    return doc


def _gaps(gaps: list[dict]) -> Doc:
    groups: dict[str, list[str]] = {}
    for g in gaps:
        groups.setdefault(g["type"], []).append(g["text"])
    doc: Doc = []
    for t in ("unanswered_question", "thin_question", "no_figures", "facet_gap", "not_read"):
        if t in groups:
            doc += [("p", f"**{GAP_TITLES[t]}**"), ("ul", groups[t])]
    return doc


def _sources(sources: list[dict], cite: Citations) -> Doc:
    """Each source with its status and the numbered quotes cited from it (the [n] citations link here)."""
    refs: dict[str, list[dict]] = {}
    for e in cite.entries():
        refs.setdefault(e["source_id"], []).append(e)
    items = []
    for src in sources:
        n = src["claims"]
        reason = "; ".join(src.get("reasons", []))[:160]
        status = "used" if n else "not used" + (f" — {reason}" if reason else "")
        read = f", {round(src['coverage'] * 100)}% read" if src.get("coverage") and src["coverage"] < 0.999 else ""
        items.append({"source_id": src["source_id"], "url": src["url"], "title": src.get("title") or src["url"],
                      "meta": f"{status} · {n} claim{'s' if n != 1 else ''}{read}",
                      "refs": refs.get(src["source_id"], [])})
    return [("sources", items)] if items else []


# --- renderers ---------------------------------------------------------------------------------

_BADGE = {"consensus": "agree", "contested": "disagree", "unresolved": "warn", "complementary": "info",
          "different_measures": "muted", "not_comparable": "muted", "single_origin": "muted", "unjudged": "muted",
          "unrelated": "muted"}


def _html_inline(text: str) -> str:
    t = html.escape(text)
    t = re.sub(r"⟦label:([a-z_]+)⟧", lambda m: f'<span class="badge {_BADGE.get(m[1], "muted")}">'
               f'{html.escape(LABEL_TEXT.get(m[1], m[1]))}</span>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    t = re.sub(r"(?<![\w*])_(.+?)_(?![\w*])", r"<em>\1</em>", t)
    t = re.sub(r"\[(\d+)\]", r'<a class="cite" href="#ref-\1">[\1]</a>', t)
    t = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r'<a href="\2">\1</a>', t)
    return t


CSS = """
:root{--bg:#fbfaf7;--fg:#1c1b19;--muted:#6b675f;--line:#e4e0d8;--card:#fff;--accent:#2f5d8a;
--agree:#2e7d4f;--disagree:#b3402f;--warn:#a86b00;--info:#2f5d8a}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#161513;--fg:#ece8e1;--muted:#a39e94;
--line:#33302b;--card:#1e1c19;--accent:#8fb4dc;--agree:#6fc28f;--disagree:#ec8a7a;--warn:#e0b25c;--info:#8fb4dc}}
:root[data-theme="dark"]{--bg:#161513;--fg:#ece8e1;--muted:#a39e94;--line:#33302b;--card:#1e1c19;--accent:#8fb4dc;
--agree:#6fc28f;--disagree:#ec8a7a;--warn:#e0b25c;--info:#8fb4dc}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);
font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:32px 16px 64px}
h1{font-size:1.9rem;line-height:1.25;margin:0 0 12px}h2{font-size:1.3rem;margin:40px 0 12px;padding-top:12px;
border-top:1px solid var(--line)}h3{font-size:1.05rem;margin:24px 0 6px}
p,li{color:var(--fg)}ul{padding-left:1.2rem}li{margin:4px 0}a{color:var(--accent)}
.badge{display:inline-block;font-size:.75rem;font-weight:600;padding:1px 8px;border-radius:999px;margin-right:6px;
border:1px solid currentColor}.agree{color:var(--agree)}.disagree{color:var(--disagree)}.warn{color:var(--warn)}
.info{color:var(--info)}.muted{color:var(--muted)}
ol.refs{font-size:.9rem;color:var(--muted)}ol.refs li{margin:6px 0}ol.refs li:target{background:var(--card);
outline:2px solid var(--accent)}a.cite{text-decoration:none;font-size:.85em}
code{font-size:.85em;background:var(--card);padding:0 4px;border-radius:4px}
.src{margin:14px 0 18px}.src h3{margin:0 0 2px;font-size:1rem}.src-meta{margin:0 0 6px;color:var(--muted);
font-size:.88rem}
nav.toc{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:.9rem;padding:12px 0;border-top:1px solid var(--line);
border-bottom:1px solid var(--line);margin:16px 0}nav.toc a{text-decoration:none}
section h3{margin-top:22px;padding:10px 0 0;border-top:1px dashed var(--line)}section h3:first-of-type{border-top:0}
"""


def to_html(doc: Doc) -> str:
    title = next((p for k, p in doc if k == "h1"), "Research brief")
    out, open_section = [], False
    for kind, payload in doc:
        if kind == "section":
            if open_section:
                out.append("</section>")
            out.append(f'<section id="{payload[0]}"><h2>{html.escape(payload[1])}</h2>')
            open_section = True
            continue
        if kind == "toc":
            out.append('<nav class="toc">' + "".join(f'<a href="#{sid}">{html.escape(t)}</a>' for sid, t in payload)
                       + "</nav>")
            continue
        if kind in ("h1", "h2", "h3"):
            out.append(f"<{kind}>{html.escape(payload)}</{kind}>")
        elif kind == "p":
            out.append(f"<p>{_html_inline(payload)}</p>")
        elif kind == "ul":
            out.append("<ul>" + "".join(f"<li>{_html_inline(x)}</li>" for x in payload) + "</ul>")
        elif kind == "sources":
            for src in payload:
                out.append(f'<div class="src"><h3>{html.escape(src["source_id"])} · <a href="{html.escape(src["url"])}" '
                           f'target="_blank" rel="noopener">{html.escape(src["title"][:120])}</a></h3>'
                           f'<p class="src-meta">{html.escape(src["meta"])}</p>')
                if src["refs"]:
                    out.append('<ol class="refs">' + "".join(
                        f'<li id="ref-{e["n"]}" value="{e["n"]}">'
                        f'{("<strong>" + html.escape(e["origin"]) + "</strong>: ") if e["origin"] else ""}'
                        f'“{html.escape(e["quote"])}” <code>{html.escape(e["block_id"])}</code></li>'
                        for e in src["refs"]) + "</ol>")
                out.append("</div>")
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<meta name="brief-sections" content="{",".join(sid for sid, _, _ in SECTIONS)}">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>"
            + "\n".join(out) + ("</section>" if open_section else "") + "</main></body></html>\n")


def write_brief(run_dir: Path, analysis: dict, bottom_line: dict) -> dict:
    """Writes run_dir/brief.html (the only output). Returns counts for the run log."""
    doc, citations = build_doc(run_dir, analysis, bottom_line)
    (run_dir / "brief.html").write_text(to_html(doc), encoding="utf-8")
    return {"citations": len(citations), "findings": len(analysis["findings"])}
