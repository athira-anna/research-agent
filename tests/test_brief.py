"""Stage 6 (brief) and ADK orchestration tests. No API calls: fake stages and a fake bottom-line writer."""

import asyncio
import json
import re

import pytest
from google.adk.agents import BaseAgent
from google.adk.events import Event, EventActions
from google.adk.runners import InMemoryRunner
from google.genai import types

from brief_agent.agents import build_pipeline
from brief_agent.stages import render as render_stage
from brief_agent.brief.bottom_line import check_bullets, template_bullets
from brief_agent.brief.render import write_brief
from brief_agent.brief.wording import headline, value_text


def run(coro):
    return asyncio.run(coro)


def claim(cid, sid, low, origin, quote, unit="percent"):
    return {"claim_id": cid, "source_id": sid, "question_id": "q1", "statement": f"{origin} says {low:g}% of jobs are at risk.",
            "modality": "observed", "certainty": "asserted", "causal": None,
            "value": {"raw": "", "low": low, "high": low, "unit": unit, "value_kind": "level", "metric_id": "other",
                      "of_what": "jobs", "baseline": None, "direction": "none"},
            "scope": {"population": "", "geography": "US", "timeframe": {"raw": "", "start": 2024, "end": 2024}, "metric": ""},
            "evidence": {"block_id": f"{sid}-p001", "quote": quote, "attributed_to": origin}, "support": {"status": "full"}}


def finding(fid, label, claims, **verdict):
    return {"cluster_id": fid, "question_id": "q1", "label": "Share of jobs at risk",
            "verdict": {"label": label, "origins": len({c["evidence"]["attributed_to"] for c in claims}),
                        "sources": len({c["source_id"] for c in claims}), **verdict},
            "strength": {"level": "moderate", "notes": []}, "likely_reasons": ["different methods named in the quotes"],
            "llm_summary": None, "pairs": [], "overrides": [],
            "claims": [{"claim_id": c["claim_id"], "source_id": c["source_id"], "origin": c["evidence"]["attributed_to"],
                        "statement": c["statement"], "value": c["value"],
                        "scope": {"population": None, "geography": "US", "timeframe": [2024, 2024]},
                        "modality": c["modality"], "certainty": c["certainty"], "quote": c["evidence"]["quote"],
                        "block_id": c["evidence"]["block_id"]} for c in claims]}


CLAIMS = [claim("s1-c001", "s1", 47, "Frey", "Frey and Osborne estimated that 47% of US jobs are at high risk."),
          claim("s2-c001", "s2", 48, "PwC", "PwC found that 48% of US jobs are at high risk of automation."),
          claim("s3-c001", "s3", 9, "OECD", "The OECD found only 9% of US jobs are at high risk of automation.")]


def analysis():
    contested = finding("q1-k1", "contested", CLAIMS, balance="majority_vs_minority",
                        sides=[{"position": "About half at risk", "claim_ids": ["s1-c001", "s2-c001"], "origins": 2},
                               {"position": "Few at risk", "claim_ids": ["s3-c001"], "origins": 1}],
                        contradiction_types=["numeric"])
    return {"topic": "AI and job losses", "interpretation": "Effects of AI on jobs.", "assumptions": [],
            "plan_source": "llm", "findings": [contested], "emerging": [],
            "gaps": [{"type": "unanswered_question", "question_id": "q2", "text": "No source addresses: who is affected?"}],
            "per_question": {}, "judge_notes": {"q1": "judged by test"},
            "sources": [{"source_id": f"s{i}", "url": f"https://s{i}.example/a", "title": f"Source {i}", "fetch": "ok",
                         "processing": "ok", "usability": "usable", "reasons": [], "extraction": "ok", "claims": 1,
                         "coverage": 1.0} for i in (1, 2, 3)] +
                       [{"source_id": "s4", "url": "https://paywall.example/x", "title": None, "fetch": "paywalled",
                         "processing": "skipped", "usability": "excluded", "reasons": ["fetch: paywalled"],
                         "extraction": "skipped", "claims": 0, "coverage": None}]}


def make_run(tmp_path):
    a = analysis()
    (tmp_path / "analysis.json").write_text(json.dumps(a))
    (tmp_path / "claims.json").write_text(json.dumps({"claims": CLAIMS}))
    (tmp_path / "sources.json").write_text(json.dumps([{"source_id": s["source_id"], "url": s["url"],
                                                        "meta": {"title": s["title"]}} for s in a["sources"]]))
    (tmp_path / "dropped_claims.json").write_text(json.dumps([{"reason": "quote_not_found"}]))
    return tmp_path, a


# --- bottom line checks --------------------------------------------------------------------------

@pytest.mark.parametrize("bullet, ok", [
    ({"text": "Sources disagree on how many US jobs are at risk: 47% and 48% vs 9%.", "finding_ids": ["q1-k1"]}, True),
    ({"text": "Sources disagree: 60% of jobs are at risk.", "finding_ids": ["q1-k1"]}, False),     # number not in finding
    ({"text": "Sources disagree on job risk.", "finding_ids": ["q9-k9"]}, False),                   # unknown id
    ({"text": "Sources disagree on job risk.", "finding_ids": []}, False),                          # no id
    ({"text": "Sources agree that about half of jobs are at risk.", "finding_ids": ["q1-k1"]}, False),  # label mismatch
    ({"text": "No source covers who is affected.", "finding_ids": ["gap-1"]}, True),
    ({"text": "Source s3 notes that 9% of US jobs are at high risk.", "finding_ids": ["q1-k1"]}, False),  # internal ID
])
def test_bottom_line_checks(bullet, ok):
    valid, problems = check_bullets([bullet], analysis())
    assert bool(valid) == ok, problems


def test_template_bottom_line_has_no_llm_text():
    bullets = template_bullets(analysis())
    assert bullets[0]["finding_ids"] == ["q1-k1"] and "Sources disagree" in bullets[0]["text"]
    assert any(b["finding_ids"] == ["gap-1"] for b in bullets)


# --- rendering (brief.html is the only output) ---------------------------------------------------------

def _visible(html: str) -> str:
    """Reader-visible text of the page (no tags, no <style>)."""
    html = re.sub(r"<style>.*?</style>", " ", html, flags=re.S)
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def test_only_html_is_written_with_working_citations_and_wording(tmp_path):
    run_dir, a = make_run(tmp_path)
    write_brief(run_dir, a, {"source": "llm", "bullets": [{"text": "Sources disagree: 47% vs 9%.", "finding_ids": ["q1-k1"]}]})
    assert (run_dir / "brief.html").exists()
    assert not (run_dir / "brief.md").exists() and not (run_dir / "brief.json").exists()
    page = (run_dir / "brief.html").read_text()
    # every citation link points at an Evidence entry, and every Evidence quote is a real claim quote
    links = set(re.findall(r'href="#ref-(\d+)"', page))
    targets = set(re.findall(r'<li id="ref-(\d+)"', page))
    assert links and links == targets
    quotes = {c["evidence"]["quote"] for c in CLAIMS}
    shown = re.findall(r'<li id="ref-\d+"[^>]*>.*?“(.*?)”', page)
    assert shown and all(q in quotes for q in shown)
    text = _visible(page)
    for code in ("majority_vs_minority", "part_vs_whole", "unspecified_scope", "⟦"):
        assert code not in text.split("7. Sources")[0]
    assert "Sources disagree: 2 vs 1 origins" in text
    assert "paywall.example" in page and "fetch: paywalled" in text          # unused source with its reason
    assert "How this brief was made" not in text and "Evidence" not in text
    assert "prefers-color-scheme" in page


def test_value_wording():
    v = {"low": 10, "high": 10, "unit": "percent", "value_kind": "level", "baseline": {"number": 5}, "direction": "increase"}
    assert value_text(v) == "10% (from 5%)"
    assert value_text(v | {"value_kind": "absolute_change", "unit": "percentage_points", "baseline": None, "low": 5,
                           "high": 5}) == "+5 points"
    assert value_text(v | {"low": 1e8, "high": 1e8, "unit": "count", "baseline": None}) == "100 million"
    assert headline({"label": "unresolved", "reasons": ["weak_claim"]}).startswith("Unresolved: a claim is hedged")


def test_every_brief_has_the_same_sections_in_the_same_order(tmp_path):
    from brief_agent.brief.render import SECTIONS
    ids = [sid for sid, _, _ in SECTIONS]
    titles = [f"{i}. {t}" for i, (_, t, _) in enumerate(SECTIONS, start=1)]
    (tmp_path / "full").mkdir()
    (tmp_path / "empty").mkdir()
    full_dir, a = make_run(tmp_path / "full")
    write_brief(full_dir, a, {"source": "llm", "bullets": [{"text": "Sources disagree.", "finding_ids": ["q1-k1"]}]})
    empty_dir, e = make_run(tmp_path / "empty")
    write_brief(empty_dir, e | {"findings": [], "emerging": [], "gaps": []}, {"source": "template", "bullets": []})

    for d in (full_dir, empty_dir):
        page = (d / "brief.html").read_text()
        assert re.findall(r'<section id="([a-z]+)">', page) == ids
        assert re.findall(r"<h2>(.*?)</h2>", page) == titles
        assert page.count("<section") == page.count("</section>")
        assert "Topic as understood:" in _visible(page) and "Assumptions:" in _visible(page)
    empty = _visible((empty_dir / "brief.html").read_text())
    assert "No contradictions found among comparable claims." in empty
    assert "No gaps identified." in empty and "Assumptions: None stated." in empty


def test_every_finding_card_has_the_same_fields(tmp_path):
    run_dir, a = make_run(tmp_path)
    write_brief(run_dir, a, {"source": "llm", "bullets": []})
    card = _visible((run_dir / "brief.html").read_text()).split("Share of jobs at risk", 1)[1].split("4. ", 1)[0]
    for field in ("Strength:", "Why they may differ:", "Caveats:"):
        assert field in card


# --- ADK orchestration -----------------------------------------------------------------------------

class FakeWriter(BaseAgent):
    bullets: list = []

    async def _run_async_impl(self, ctx):
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    actions=EventActions(state_delta={"bottom_line_draft": {"bullets": self.bullets}}))


class FailingWriter(BaseAgent):
    async def _run_async_impl(self, ctx):
        raise ValueError("No API key was provided.")
        yield  # pragma: no cover


def stages(tmp_path, order, fail=None, stop_at=None):
    def stage(name):
        async def fn(state):
            order.append(name)
            if name == fail:
                raise RuntimeError("boom")
            out = {"line": name, "details": [f"{name} detail"]}
            if name == stop_at:
                out["stop"] = "no usable sources"
            if name == "judge":
                make_run(tmp_path)
                out["state"] = {"bottom_line_input": "findings"}
            return out
        return fn
    s = {n: stage(n) for n in ("plan", "fetch", "clean", "extract", "align", "judge")}
    s["render"] = render_stage
    return s


def pipeline(tmp_path, order=None, writer=None, **kw):
    return build_pipeline(stages(tmp_path, order if order is not None else [], **kw), "unused",
                          writer or FakeWriter(name="w"), with_intake=False)


async def drive(root, tmp_path, message="go", state=None):
    runner = InMemoryRunner(agent=root, app_name="t")
    session = await runner.session_service.create_session(
        app_name="t", user_id="u",
        state=state if state is not None else {"topic": "AI and job losses", "urls": [], "run_dir": str(tmp_path)})
    lines, artifacts = [], {}
    async for ev in runner.run_async(user_id="u", session_id=session.id,
                                     new_message=types.Content(role="user", parts=[types.Part(text=message)])):
        if ev.content and ev.content.parts:
            lines.append(f"[{ev.author}] " + "".join(p.text or "" for p in ev.content.parts))
        artifacts.update(ev.actions.artifact_delta or {})
    saved = await runner.artifact_service.load_artifact(app_name="t", user_id="u", session_id=session.id,
                                                        filename="brief.html") if artifacts else None
    return lines, artifacts, saved


def test_pipeline_runs_all_stages_with_a_short_chat_log(tmp_path, caplog):
    order = []
    good = [{"text": "Sources disagree: 47% vs 9% of US jobs at risk.", "finding_ids": ["q1-k1"]},
            {"text": "No source covers who is affected.", "finding_ids": ["gap-1"]}]
    with caplog.at_level("INFO", logger="brief_agent"):
        lines, artifacts, saved = run(drive(pipeline(tmp_path, order, FakeWriter(name="w", bullets=good)), tmp_path))
    assert set(order[:2]) == {"plan", "fetch"} and order[2:] == ["clean", "extract", "align", "judge"]
    for title in ("Stage 0 · Research plan", "Stage 3 · Extract and check claims", "Stage 6 · Write the brief"):
        assert any(line.endswith(f"▶ {title}") for line in lines)
    assert not any("detail" in line for line in lines)                  # details only in the terminal log
    assert "extract detail" in caplog.text and "bottom line: accepted" in caplog.text
    assert any("Brief ready" in line for line in lines)
    # without the local viewer (no intake → no serve_brief), the brief is attached as a fallback download
    assert artifacts == {"brief.html": 0} and b"Research brief" in saved.inline_data.data
    assert "Auto-generated" not in (tmp_path / "brief.html").read_text()


def test_brief_link_opens_in_a_new_tab_from_the_local_viewer(tmp_path, monkeypatch):
    import urllib.request
    from brief_agent import viewer
    monkeypatch.setattr(viewer, "RUNS_DIR", tmp_path.parent)
    state = {"topic": "AI and job losses", "urls": [], "run_dir": str(tmp_path), "serve_brief": True}
    lines, artifacts, _ = run(drive(pipeline(tmp_path), tmp_path, state=state))
    link = next(line for line in lines if "Brief ready" in line)
    url = re.search(r'href="([^"]+)" target="_blank"', link).group(1)
    assert not artifacts                                                  # no download chip
    with urllib.request.urlopen(url) as r:
        assert r.headers["Content-Type"].startswith("text/html") and b"Research brief" in r.read()
    # the viewer serves briefs only: nothing else on disk, no listings
    for bad in ("/", f"/{tmp_path.name}/", f"/{tmp_path.name}/claims.json", "/../etc/passwd"):
        try:
            urllib.request.urlopen(url.split(f"/{tmp_path.name}/")[0] + bad)
            raise AssertionError(f"served {bad}")
        except urllib.error.HTTPError as e:
            assert e.code == 404


def test_fetch_and_clean_show_counts_and_dropped_links():
    import asyncio as _a
    from types import SimpleNamespace
    from brief_agent import stages as st

    async def fake_fetch(urls, run_dir, config):
        ok = SimpleNamespace(status=SimpleNamespace(value="ok"), status_detail=None, method="http", url="https://a",
                             source_id="s1")
        bad = SimpleNamespace(status=SimpleNamespace(value="bot_challenge"), status_detail="datadome", method=None,
                              url="https://b", source_id="s2")
        return [ok, bad]
    import brief_agent.stages
    orig = brief_agent.stages.fetch_sources
    brief_agent.stages.fetch_sources = fake_fetch
    try:
        out = _a.run(st.fetch({"urls": ["https://a", "https://b"], "run_dir": "/tmp"}))
    finally:
        brief_agent.stages.fetch_sources = orig
    assert out["screen"].splitlines() == ["1 of 2 links fetched", "- dropped: https://b — bot challenge (datadome)"]


def test_bad_bottom_line_retries_then_falls_back(tmp_path, caplog):
    bad = [{"text": "Sources agree 99% of jobs vanish.", "finding_ids": ["q1-k1"]}]
    with caplog.at_level("INFO", logger="brief_agent"):
        run(drive(pipeline(tmp_path, writer=FakeWriter(name="w", bullets=bad)), tmp_path))
    assert "asking the writer for a rewrite" in caplog.text and "template fallback" in caplog.text
    assert "Auto-generated" in (tmp_path / "brief.html").read_text()


def test_stop_gate_skips_later_stages_but_still_writes_a_brief(tmp_path):
    order = []
    lines, _, _ = run(drive(pipeline(tmp_path, order, stop_at="clean"), tmp_path))
    assert "extract" not in order and any("Stopping here: no usable sources" in line for line in lines)
    assert "The run stopped early: no usable sources" in (tmp_path / "brief.html").read_text()


def test_stage_exception_does_not_crash_the_run(tmp_path):
    lines, _, _ = run(drive(pipeline(tmp_path, fail="extract"), tmp_path))
    assert any("✗ Stage 3 · Extract and check claims failed: RuntimeError: boom" in line for line in lines)
    assert (tmp_path / "brief.html").exists()


def test_writer_error_goes_straight_to_template(tmp_path, caplog):
    with caplog.at_level("INFO", logger="brief_agent"):
        lines, _, _ = run(drive(pipeline(tmp_path, writer=FailingWriter(name="w")), tmp_path))
    assert "bottom line writer failed (ValueError" in caplog.text
    assert "rewrite" not in caplog.text and "template (writer unavailable" in caplog.text


def test_extract_line_says_when_extraction_failed():
    from brief_agent.stages import extract_summary
    failed = {"source_id": "s1", "extraction": {"status": "failed",
                                                "reason": "gemini-flash-lite-latest unavailable (no GOOGLE_API_KEY / GEMINI_API_KEY set)"}}
    out = extract_summary([], [failed, failed, {"source_id": "s3", "extraction": {"status": "skipped"}}])
    assert "extraction failed for 2/2 sources" in out["line"] and "no GOOGLE_API_KEY" in out["line"]
    assert out["stop"].startswith("claim extraction failed for every source")
    ok = extract_summary([1, 2], [{"source_id": "s2", "extraction": {"status": "ok"}}, failed])
    assert "failed for 1/2" in ok["line"] and "stop" not in ok


# --- intake: the chat message that starts a run (adk web / adk run) -------------------------------

from brief_agent.intake import parse_request  # noqa: E402


def test_parse_topic_line_and_urls():
    topic, urls, flags = parse_request("Topic: AI and job losses\nhttps://a.example/x.\nhttps://b.example/y --no-llm")
    assert topic == "AI and job losses" and urls == ["https://a.example/x", "https://b.example/y"]
    assert flags == {"no_llm": True}


def test_parse_free_text_json_and_input_file(tmp_path):
    t, u, _ = parse_request("Effects of the minimum wage https://a.example/1 https://b.example/2")
    assert t == "Effects of the minimum wage" and len(u) == 2
    t, u, _ = parse_request('{"topic": "AI", "urls": ["https://a.example"]}')
    assert t == "AI" and u == ["https://a.example"]
    t, u, _ = parse_request("inputs/minimum_wage.json")
    assert t.startswith("Employment effects") and len(u) == 5


@pytest.mark.parametrize("message, topic", [
    # a real message sent through adk web
    ('i need to create a brief on "How AI will change the job market" based on sources : "https://a.example/1", '
     '"https://b.example/2"', "How AI will change the job market"),
    ("Can you write a research brief about remote work and productivity using these sources: "
     "https://a.example/1 https://b.example/2", "remote work and productivity"),
    ("Please create a brief on the four-day work week\nhttps://a.example/1", "the four-day work week"),
    ("AI and job losses https://a.example/1", "AI and job losses"),
])
def test_parse_natural_requests(message, topic):
    t, urls, _ = parse_request(message)
    assert t == topic and urls


def test_intake_refuses_a_message_without_urls_and_runs_nothing(tmp_path):
    order = []
    root = build_pipeline(stages(tmp_path, order), "unused", FakeWriter(name="w"))
    lines, artifacts, _ = run(drive(root, tmp_path, message="hello", state={}))
    assert order == [] and not artifacts
    assert any("Can't start a run: no source URLs" in line and "Topic:" in line for line in lines)


def test_intake_refuses_without_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    order = []
    root = build_pipeline(stages(tmp_path, order), "unused", FakeWriter(name="w"))
    lines, _, _ = run(drive(root, tmp_path, message="Topic: AI\nhttps://a.example", state={}))
    assert order == [] and any("GOOGLE_API_KEY is not set" in line for line in lines)


def test_root_agent_loads_for_adk():
    import importlib
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents"))
    mod = importlib.import_module("research_brief.agent")
    assert mod.root_agent.name == "research_brief"
    assert [a.name for a in mod.root_agent.sub_agents][:2] == ["intake", "plan_and_fetch"]



# --- the writer's raw output (found in a real adk web run) ----------------------------------------------

class ChattyWriter(BaseAgent):
    """Like the real LlmAgent: posts its JSON reply as content AND sets the draft through state."""
    bullets: list = []

    async def _run_async_impl(self, ctx):
        yield Event(author=self.name, invocation_id=ctx.invocation_id,
                    content=types.Content(role="model", parts=[types.Part(text=json.dumps({"bullets": self.bullets}))]),
                    actions=EventActions(state_delta={"bottom_line_draft": {"bullets": self.bullets}}))


def test_writer_json_is_not_shown_but_still_used(tmp_path):
    good = [{"text": "Sources disagree: 47% vs 9% of US jobs at risk [q1-k1].", "finding_ids": ["q1-k1"]},
            {"text": "No source covers who is affected (gap-1).", "finding_ids": ["gap-1"]}]
    lines, _, _ = run(drive(pipeline(tmp_path, writer=ChattyWriter(name="w", bullets=good)), tmp_path))
    assert not any('"bullets"' in line for line in lines)
    assert any("✓ Summary written" in line for line in lines)
    page = (tmp_path / "brief.html").read_text()
    assert "Sources disagree: 47% vs 9% of US jobs at risk." in page
    assert "[q1-k1]" not in page and "(gap-1)" not in page


def test_ids_in_bullet_text_are_removed():
    valid, _ = check_bullets([{"text": "Sources disagree on 47% vs 9% [q1-k1, gap-1].", "finding_ids": ["q1-k1"]}],
                             analysis())
    assert valid[0]["text"] == "Sources disagree on 47% vs 9%."
