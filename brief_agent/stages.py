"""The pipeline's seven stages as plain async functions over the session state, in pipeline order.
The ADK agents in agents.py run them; each stage's real work lives in its own package (plan/, fetch/, …).

Each returns {"line": one-line summary, "details": [lines for the terminal log], ...} with optional keys:
"screen" (the short result shown in the chat), "stop" (later stages are skipped), "state" (values later
agents need) and "brief" (the finished brief: path, and URL when the local viewer serves it).
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from .align import align as align_claims
from .brief.bottom_line import llm_input, template_bullets
from .brief.render import write_brief
from .clean import clean_sources
from .extract import extract_claims
from .fetch import FetchConfig, fetch_sources
from .judge import judge as judge_findings
from .llm import GeminiLLM
from .plan import get_plan
from .viewer import brief_url

_LLM = GeminiLLM()


def _llm(st: dict) -> GeminiLLM | None:
    return None if st.get("no_llm") else _LLM


# --- stage 0: research plan -------------------------------------------------------------------------

async def plan(st: dict) -> dict:
    p, how = await get_plan(st["topic"], Path(st["run_dir"]), _llm(st), plan_file=st.get("plan_file"))
    line = f"{len(p.questions)} research questions ({p.source}{', ' + p.model if p.model else ''}; {how})"
    if p.source == "fallback" and p.fixes:
        line += f" — {p.fixes[0][:140]}"
    return {"line": line, "details": [f"{q.id} [{q.angle}] {q.text}" for q in p.questions]}


# --- stage 1: fetch ---------------------------------------------------------------------------------

async def fetch(st: dict) -> dict:
    recs = await fetch_sources(st["urls"], Path(st["run_dir"]),
                               FetchConfig(refresh=st.get("refresh", False),
                                           allow_external=not st.get("no_external", False)))
    fetched = sum(r.status.value == "ok" for r in recs)
    dropped = [f"- dropped: {r.url} — {_status_text(r)}" for r in recs if r.status.value != "ok"]
    return {"line": f"{fetched}/{len(recs)} sources fetched",
            "screen": "\n".join([f"{fetched} of {len(recs)} links fetched", *dropped]),
            "details": [f"{r.source_id}: {_status_text(r)}{f' via {r.method}' if r.method else ''} — {r.url}"
                        for r in recs]}


def _status_text(record) -> str:
    detail = f" ({record.status_detail})" if record.status_detail else ""
    return record.status.value.replace("_", " ") + detail


# --- stage 2: clean and triage ----------------------------------------------------------------------

async def clean(st: dict) -> dict:
    srcs = await clean_sources(Path(st["run_dir"]), st["topic"], _llm(st))
    use = Counter(s.usability.status for s in srcs)
    usable = use.get("usable", 0) + use.get("partial", 0)
    # Links dropped here (fetched fine, but off-topic, a list of headlines, a duplicate, …); fetch failures
    # were already reported by the fetch stage.
    dropped = [f"- dropped: {s.url} — {'; '.join(s.usability.reasons)[:120]}" for s in srcs
               if s.usability.status == "excluded" and s.fetch.get("status") == "ok"]
    out = {"line": f"usable {use.get('usable', 0)}, partial {use.get('partial', 0)}, excluded {use.get('excluded', 0)}",
           "screen": "\n".join([f"{usable} of {len(srcs)} links usable", *dropped]),
           "details": [f"{s.source_id}: {s.usability.status}"
                       + (f" — {'; '.join(s.usability.reasons)[:140]}" if s.usability.reasons else "")
                       + (f" ({round(s.coverage * 100)}% read)" if s.coverage and s.coverage < 0.999 else "")
                       for s in srcs]}
    if not usable:
        out["stop"] = "no usable sources"
    return out


# --- stage 3: extract and check claims --------------------------------------------------------------

async def extract(st: dict) -> dict:
    if st.get("no_llm"):
        return {"stop": "claim extraction needs an LLM (--no-llm was given)", "line": "skipped (no LLM)"}
    claims, sources = await extract_claims(Path(st["run_dir"]), st["topic"], _llm(st))
    return extract_summary(claims, sources)


def extract_summary(claims: list, sources: list[dict]) -> dict:
    """Progress for stage 3 that says when extraction FAILED, not just "0 claims"."""
    tried = [s for s in sources if (s.get("extraction") or {}).get("status") in ("ok", "failed")]
    failed = [s for s in tried if s["extraction"]["status"] == "failed"]
    out = {"line": f"{len(claims)} checked claims", "details": [_extraction_text(s) for s in sources]}
    if failed:
        reason = failed[0]["extraction"].get("reason", "").split("; ")[-1][:140]
        out["line"] += f"; extraction failed for {len(failed)}/{len(tried)} sources ({reason})"
        if len(failed) == len(tried):
            out["stop"] = f"claim extraction failed for every source ({reason})"
    return out


def _extraction_text(source: dict) -> str:
    e = source.get("extraction") or {}
    reasons = ", ".join(f"{k} {v}" for k, v in (e.get("dropped_by_reason") or {}).items())
    return (f"{source['source_id']}: {e.get('status')}, {e.get('claims', 0)} claims kept, "
            f"{e.get('dropped', 0)} dropped" + (f" ({reasons})" if reasons else ""))


# --- stage 4: align across sources ------------------------------------------------------------------

async def align(st: dict) -> dict:
    a = await align_claims(Path(st["run_dir"]), _llm(st))
    n = sum(len(q["clusters"]) for q in a["questions"].values())
    return {"line": f"{n} clusters of related claims across {len(a['questions'])} questions",
            "details": [f"{cl['cluster_id']}: {cl['label']} ({len(cl['claim_ids'])} claims)"
                        for q in a["questions"].values() for cl in q["clusters"]]}


# --- stage 5: judge agreement and gaps --------------------------------------------------------------

async def judge(st: dict) -> dict:
    a = await judge_findings(Path(st["run_dir"]), _llm(st))
    findings = a["findings"] + a["emerging"]
    labels = Counter(f["verdict"]["label"] for f in findings)
    verdicts = ", ".join(f"{n} {lab.replace('_', ' ')}" for lab, n in labels.most_common()) or "no findings"
    return {"line": f"{verdicts}; {len(a['gaps'])} gaps",
            "details": [f"{f['cluster_id']}: {f['verdict']['label'].replace('_', ' ')} — {f['label']}" for f in findings]
                       + [f"gap: {g['text'][:120]}" for g in a["gaps"][:5]]}


def bottom_line_input_for(run_dir: Path) -> str:
    """The checked findings, in the compact form the bottom-line writer reads."""
    return llm_input(json.loads((run_dir / "analysis.json").read_text()))


# --- stage 6: write the brief -----------------------------------------------------------------------

async def render(st: dict) -> dict:
    """Writes brief.html with the checked bottom line (or template bullets if there is none). Works for runs
    that stopped early too: the brief then explains why, from whatever the earlier stages left."""
    run_dir = Path(st["run_dir"])
    analysis_path = run_dir / "analysis.json"
    if not analysis_path.exists():
        analysis_path.write_text(json.dumps(empty_analysis(st), indent=2))
    analysis = json.loads(analysis_path.read_text())
    bottom = st.get("bottom_line") or {"source": "template", "bullets": template_bullets(analysis)}
    if st.get("stop"):
        bottom = {"source": "template", "bullets": [{"text": f"The run stopped early: {st['stop']}. "
                                                             "See the sources section for details.", "finding_ids": []}]}
    for name, empty in (("claims.json", {"claims": []}), ("sources.json", [])):
        if not (run_dir / name).exists():
            (run_dir / name).write_text(json.dumps(empty))
    out = write_brief(run_dir, analysis, bottom)
    return {"line": f"{out['findings']} findings, {out['citations']} citations",
            "brief": {"path": str(run_dir / "brief.html"), "filename": "brief.html", "mime_type": "text/html",
                      "url": brief_url(run_dir) if st.get("serve_brief") else None}}


def empty_analysis(st: dict) -> dict:
    """An analysis with no findings, for a run that stopped before stage 5: lists the sources as far as they got."""
    run_dir = Path(st["run_dir"])
    sources = []
    if (run_dir / "sources.json").exists():
        for s in json.loads((run_dir / "sources.json").read_text()):
            sources.append({"source_id": s["source_id"], "url": s["url"], "title": (s.get("meta") or {}).get("title"),
                            "fetch": s["fetch"]["status"], "processing": s["processing"]["status"],
                            "usability": s["usability"]["status"], "reasons": s["usability"]["reasons"],
                            "extraction": (s.get("extraction") or {}).get("status"), "claims": 0,
                            "coverage": s.get("coverage")})
    elif (run_dir / "sources_raw.json").exists():
        for s in json.loads((run_dir / "sources_raw.json").read_text()):
            sources.append({"source_id": s["source_id"], "url": s["url"], "title": None, "fetch": s["status"],
                            "processing": None, "usability": "excluded", "reasons": [f"fetch: {s['status']}"],
                            "extraction": None, "claims": 0, "coverage": None})
    return {"topic": st["topic"], "interpretation": st["topic"], "assumptions": [], "plan_source": "n/a",
            "findings": [], "emerging": [], "gaps": [], "per_question": {}, "judge_notes": {}, "sources": sources}


STAGES = {"plan": plan, "fetch": fetch, "clean": clean, "extract": extract, "align": align, "judge": judge,
          "render": render}
