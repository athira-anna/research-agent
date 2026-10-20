"""Stage 0 tests. Fake LLMs only: no API key needed."""

import asyncio
import copy
import json

import pytest

from brief_agent.clean.blocks import build_blocks
from brief_agent.clean.keywords import topic_keywords
from brief_agent.clean.models import RawBlock
from brief_agent.clean.select import select_blocks
from brief_agent.llm import GeminiLLM, LLMError
from brief_agent.plan.models import Plan
from brief_agent.plan.planner import PLAN_VERSION, PlanFileError, fallback_plan, get_plan, make_plan
from brief_agent.plan.topic import TopicError, topic_key
from brief_agent.plan.validate import check_plan, is_usable
from tests.clean_helpers import FakeGenaiClient

TOPIC = "AI and job losses"


def run(coro):
    return asyncio.run(coro)


def q(text, angle, kind="qualitative", metric_ids=(), terms=()):
    return {"text": text, "angle": angle, "kind": kind, "why_it_matters": "matters",
            "metric_ids": list(metric_ids), "question_terms": list(terms),
            "facets": [{"dimension": "population", "name": "sector", "examples": ["software"]}]}


def draft(questions=None, **over):
    d = {
        "interpretation": "Effects of AI adoption on employment levels.",
        "assumptions": ["Covers any country; the topic names no region."],
        "subject_terms": {"core": ["ai", "job losses"], "synonyms": ["automation", "unemployment", "layoffs"]},
        "metrics": [
            {"id": "job_loss_rate", "label": "Job loss rate", "definition": "Share of workers whose jobs were cut.",
             "measure_type": "share", "unit": "percent", "useful_for": "realised losses",
             "confusable_with": ["ai_exposure_share"]},
            {"id": "ai_exposure_share", "label": "Exposure", "definition": "Share of jobs exposed to AI.",
             "measure_type": "share", "unit": "percent", "useful_for": "potential impact",
             "confusable_with": ["job_loss_rate"]},
        ],
        "questions": questions or [
            q("What share of jobs has been lost to AI so far?", "scale", "quantitative", ["job_loss_rate"], ["redundancies"]),
            q("What mechanisms link AI adoption to job cuts?", "causes", terms=["task automation"]),
            q("Which groups of workers are most affected?", "affected_groups", terms=["demographics"]),
            q("What share of jobs will be lost to AI by 2030?", "forecasts", "quantitative", ["job_loss_rate"], ["projections"]),
            q("Where do economists disagree about net employment effects?", "disagreements", terms=["debate"]),
            q("What policies are proposed to support displaced workers?", "responses", terms=["retraining", "universal basic income"]),
        ],
    }
    d.update(over)
    return d


class SeqLLM:
    """Returns the given answers in order; an Exception instance is raised instead."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    async def generate_json(self, *, model, system, prompt, schema):
        self.calls.append({"model": model, "prompt": prompt})
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return schema.model_validate(copy.deepcopy(a))


# --- valid plan ------------------------------------------------------------------------

def test_valid_plan(tmp_path):
    plan, how = run(get_plan(TOPIC, tmp_path, SeqLLM(draft())))
    assert how == "new" and plan.source == "llm" and plan.plan_version == PLAN_VERSION
    assert [x.id for x in plan.questions] == [f"q{i}" for i in range(1, 7)]
    assert plan.topic_key == "ai and job losses"
    saved = Plan.model_validate(json.loads((tmp_path / "plan.json").read_text()))
    assert saved.questions[0].metric_ids == ["job_loss_rate"]
    assert saved.metrics[0].confusable_with == ["ai_exposure_share"]


# --- duplicates -----------------------------------------------------------------------

def test_reworded_duplicate_named_in_retry_then_removed():
    qs = draft()["questions"] + [q("How large are AI-driven job losses so far?", "scale", "quantitative",
                                   ["job_loss_rate"], ["redundancies"])]
    bad = draft(questions=qs)
    llm = SeqLLM(bad, bad)                      # the retry doesn't fix it
    plan = run(make_plan(TOPIC, llm))
    assert len(llm.calls) == 2
    assert "ask the same thing" in llm.calls[1]["prompt"]
    assert len(plan.questions) == 6
    assert not any("How large are AI-driven" in x.text for x in plan.questions)
    assert any("removed duplicate question" in f for f in plan.fixes)


def test_so_far_vs_by_2030_is_not_a_duplicate():
    plan, problems = check_plan(Plan(topic=TOPIC, **draft()))
    texts = [x.text for x in plan.questions]
    assert "What share of jobs has been lost to AI so far?" in texts
    assert "What share of jobs will be lost to AI by 2030?" in texts
    assert problems == []


# --- metrics ----------------------------------------------------------------------------

def test_unknown_metric_removed_and_quantitative_without_metrics_switched():
    qs = draft()["questions"]
    qs[0] = q("What share of jobs has been lost to AI so far?", "scale", "quantitative", ["made_up_metric"])
    plan, problems = check_plan(Plan(topic=TOPIC, **draft(questions=qs)))
    assert any("unknown metric ids ['made_up_metric']" in p for p in problems)
    assert any("has no metrics" in p for p in problems)
    assert plan.questions[0].metric_ids == [] and plan.questions[0].kind == "qualitative"


def test_metric_ids_normalised_and_duplicates_dropped():
    d = draft()
    d["metrics"][1]["id"] = "Job Loss Rate"          # becomes job_loss_rate → duplicate
    d["metrics"].append({**d["metrics"][0], "id": "AI-Exposure (share)", "measure_type": "weird", "unit": "furlongs"})
    plan, problems = check_plan(Plan(topic=TOPIC, **d))
    ids = [m.id for m in plan.metrics]
    assert ids == ["job_loss_rate", "ai_exposure_share"]
    assert any("used twice" in p for p in problems)
    assert plan.metrics[1].measure_type == "other" and plan.metrics[1].unit == "other"


# --- terms and assumptions --------------------------------------------------------------

def test_question_terms_dont_repeat_subject_terms_and_topic_words_added():
    qs = draft()["questions"]
    qs[1] = q("What mechanisms link AI adoption to job cuts?", "causes", terms=["Automation", "task automation"])
    d = draft(questions=qs, subject_terms={"core": ["artificial intelligence"], "synonyms": ["automation"]})
    plan, _ = check_plan(Plan(topic=TOPIC, **d))
    assert plan.questions[1].question_terms == ["task automation"]
    for w in topic_keywords(TOPIC):
        assert w in plan.subject_terms.core


def test_assumptions_capped():
    d = draft(assumptions=["Any country.", "x" * 500, "Third.", "Fourth."])
    plan, problems = check_plan(Plan(topic=TOPIC, **d))
    assert len(plan.assumptions) == 2 and len(plan.assumptions[1]) <= 200
    assert any("assumptions" in p for p in problems)


# --- failures and fallback --------------------------------------------------------------

def test_invalid_json_gives_fallback_with_same_schema(monkeypatch):
    llm = GeminiLLM()
    llm._client = FakeGenaiClient("Sure! {not json")
    monkeypatch.setattr("brief_agent.llm.RETRY_DELAY_S", 0)
    plan = run(make_plan(TOPIC, llm))
    assert plan.source == "fallback"
    rechecked, problems = check_plan(plan)
    assert problems == [] and is_usable(rechecked)
    llm_plan, _ = check_plan(Plan(topic=TOPIC, **draft()))
    assert set(plan.model_dump()) == set(llm_plan.model_dump())
    assert all(q.angle != "other" for q in plan.questions)
    assert plan.questions[0].metric_ids == ["reported_magnitude"]


def test_too_few_questions_after_retry_gives_fallback():
    few = draft(questions=draft()["questions"][:3])
    llm = SeqLLM(few, few)
    plan = run(make_plan(TOPIC, llm))
    assert "need 5–8" in llm.calls[1]["prompt"]
    assert plan.source == "fallback"


def test_model_chain_uses_next_model(monkeypatch):
    monkeypatch.setattr("brief_agent.plan.planner.PLANNER_MODEL", "pro")
    monkeypatch.setattr("brief_agent.plan.planner.PLANNER_FALLBACK_MODELS", ["flash", "lite"])

    class Chain(SeqLLM):
        async def generate_json(self, *, model, system, prompt, schema):
            self.calls.append({"model": model, "prompt": prompt})
            if model == "pro":
                raise LLMError("pro: 429 RESOURCE_EXHAUSTED")
            return schema.model_validate(copy.deepcopy(draft()))

    llm = Chain()
    plan = run(make_plan(TOPIC, llm))
    assert plan.model == "flash" and plan.source == "llm"
    assert [c["model"] for c in llm.calls] == ["pro", "flash"]
    assert any("pro unavailable" in f for f in plan.fixes)


# --- topic ---------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["", "   ", "x" * 1000])
def test_bad_topic_errors_before_llm(tmp_path, bad):
    llm = SeqLLM(draft())
    with pytest.raises(TopicError):
        run(get_plan(bad, tmp_path, llm))
    assert llm.calls == []


@pytest.mark.parametrize("variant", ["AI & Job-Losses!", "ai  and job losses", "AI and job losses.", " AI AND JOB—LOSSES "])
def test_topic_key_normalisation(variant):
    assert topic_key(variant) == "ai and job losses"


# --- cache ----------------------------------------------------------------------------------

def test_cache_reused_for_same_topic_different_punctuation(tmp_path):
    run(get_plan(TOPIC, tmp_path, SeqLLM(draft())))
    llm = SeqLLM(draft())
    plan, how = run(get_plan("AI & Job-Losses!", tmp_path, llm))
    assert how == "reused" and llm.calls == []


def test_cache_different_topic_makes_new_plan(tmp_path):
    run(get_plan(TOPIC, tmp_path, SeqLLM(draft())))
    plan, how = run(get_plan("Remote work and productivity", tmp_path, SeqLLM(draft())))
    assert how == "replaced" and plan.topic == "Remote work and productivity"
    prev = json.loads((tmp_path / "plan.prev.json").read_text())
    assert prev["topic"] == TOPIC


def test_cache_old_version_and_fallback_not_reused(tmp_path):
    run(get_plan(TOPIC, tmp_path, SeqLLM(draft())))
    data = json.loads((tmp_path / "plan.json").read_text())
    data["plan_version"] = PLAN_VERSION - 1
    (tmp_path / "plan.json").write_text(json.dumps(data))
    _, how = run(get_plan(TOPIC, tmp_path, SeqLLM(draft())))
    assert how == "replaced"

    run(get_plan(TOPIC, tmp_path, None, replan=True))          # writes a fallback plan
    llm = SeqLLM(draft())
    plan, how = run(get_plan(TOPIC, tmp_path, llm))
    assert how == "replaced" and plan.source == "llm" and len(llm.calls) == 1


# --- plan file -----------------------------------------------------------------------------

def test_plan_file_for_other_topic_is_an_error(tmp_path):
    f = tmp_path / "mine.json"
    f.write_text(json.dumps({"topic": "Remote work", **draft()}))
    with pytest.raises(PlanFileError, match="Remote work"):
        run(get_plan(TOPIC, tmp_path / "run", None, plan_file=f))


def test_plan_file_bad_facet_dimension_fixed(tmp_path):
    d = draft()
    d["questions"][0]["facets"][0]["dimension"] = "industry"
    f = tmp_path / "mine.json"
    f.write_text(json.dumps({"topic": "ai & job losses", **d}))
    plan, how = run(get_plan(TOPIC, tmp_path / "run", None, plan_file=f))
    assert how == "user" and plan.source == "user"
    assert plan.questions[0].facets[0].dimension == "other"
    assert any("'industry' → 'other'" in x for x in plan.fixes)


# --- stage 2 integration -----------------------------------------------------------------

FILLER = ("Notes on regional rail timetables, platform upgrades, ticket machines and station "
          "accessibility works scheduled across the network this season. ")


def test_plan_subject_terms_select_synonym_paragraph():
    raw = [RawBlock("paragraph", f"Bulletin {i}. " + FILLER * 3) for i in range(60)]
    raw.insert(30, RawBlock("paragraph", "Unemployment rose as automation spread through clerical work."))
    blocks = build_blocks("s1", raw)
    target = next(b.id for b in blocks if b.text.startswith("Unemployment"))
    budget = 4 * len(blocks[0].text) + 10

    without, _ = select_blocks(blocks, topic_keywords(TOPIC), budget=budget)
    plan, _ = check_plan(Plan(topic=TOPIC, **draft()))
    with_plan, _ = select_blocks(blocks, topic_keywords(TOPIC, plan.all_subject_terms()), budget=budget)
    assert target not in without
    assert target in with_plan


def test_question_terms_outrank_subject_only():
    a = "Automation is spreading through back-office work in many large firms this year."
    b = "Automation is spreading, and firms are funding retraining for affected staff this year."
    raw = [RawBlock("paragraph", f"Bulletin {i}. " + FILLER * 3) for i in range(40)]
    raw.insert(20, RawBlock("paragraph", a))
    raw.insert(35, RawBlock("paragraph", b))
    blocks = build_blocks("s1", raw)
    plan, _ = check_plan(Plan(topic=TOPIC, **draft()))
    q_terms = [t for x in plan.questions for t in x.question_terms]
    budget = 2 * len(blocks[0].text) + max(len(a), len(b)) + 10   # intro + exactly one more
    selected, _ = select_blocks(blocks, topic_keywords(TOPIC, plan.all_subject_terms()), budget=budget,
                                question_terms=q_terms)
    by_text = {x.text: x.id for x in blocks}
    assert by_text[b] in selected and by_text[a] not in selected
