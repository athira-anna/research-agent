"""Stage 0: topic → research plan (questions, standard metrics, subject/question terms).

    validate topic → reuse cached plan? → LLM draft → checks → (one retry with the problems) → fallback

The model sees only the topic, never the sources: questions taken from the sources would always be
"answered", which would make gap detection meaningless.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from ..llm import LLM, PLANNER_FALLBACK_MODELS, PLANNER_MODEL, LLMError, generate_json_with_fallback
from .models import Facet, Metric, Plan, PlanDraft, Question, SubjectTerms
from .topic import topic_key, validate_topic
from .validate import check_plan, is_usable

PLAN_VERSION = 1   # bump when the prompt or schema changes, so old cached plans aren't reused


class PlanFileError(ValueError):
    pass


SYSTEM = """You plan research briefs. Given a topic, decide what a useful analyst brief on it must answer.
You do NOT see any sources and must not answer the questions yourself.

Return:
- interpretation: ONE sentence restating the topic. No facts, no claims, no answers.
- assumptions: at most 2, and only about scope the topic itself leaves open (region, time frame,
  definition). Prefer leaving scope open ("any country") over choosing one. If the topic is ambiguous,
  name the ambiguity instead of silently resolving it. Use an empty list if nothing needs assuming.
- subject_terms: core = the topic's key terms; synonyms = alternative words and phrasings people use for
  the WHOLE topic (e.g. for "AI and job losses": automation, job displacement, layoffs, unemployment).
- metrics: the standard ways to measure what the quantitative questions ask about. Each has a
  lowercase snake_case id (its standard name), a one-sentence definition, measure_type, unit, when it is
  the right lens (useful_for), and confusable_with = ids of metrics in this list that readers often mix
  up with it (e.g. "share of jobs exposed to AI" vs "share of jobs lost to AI"). Include such easily
  confused alternatives when they exist; they prevent false contradictions later.
- questions: 5 to 8. Each has an angle; cover a spread of angles (scale, causes, affected_groups,
  forecasts, disagreements, responses). quantitative questions must list metric_ids from your metrics.
  facets = the breakdowns that matter for that question (dimension: population, geography, timeframe,
  metric or other), with a few example values. question_terms = words that signal text answering THAT
  question specifically (e.g. "retraining", "universal basic income" for a responses question); do not
  repeat subject_terms.

Rules for questions:
- Neutral wording with no built-in answer: "Has AI caused net job losses in software?",
  not "Why has AI destroyed software jobs?".
- No duplicates. Two questions are duplicates if the SAME EVIDENCE would answer both, however
  differently they are worded: "What % of jobs has AI eliminated?" and "How large are AI-driven job losses
  so far?" are duplicates; "...so far?" and "...by 2030?" are not (evidence vs forecast).
  Before answering, compare every pair of questions and merge any duplicates."""


def _prompt(topic: str) -> str:
    return f"Topic: {topic}"


def _retry_prompt(topic: str, draft: PlanDraft, problems: list[str]) -> str:
    listed = "\n".join(f"- {p}" for p in problems)
    return (f"Topic: {topic}\n\nYour previous plan had these problems:\n{listed}\n\n"
            f"Previous plan:\n{draft.model_dump_json(indent=1)}\n\nReturn a corrected, complete plan.")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _from_draft(topic: str, draft: PlanDraft, model: str) -> Plan:
    data = draft.model_dump()
    return Plan(plan_version=PLAN_VERSION, source="llm", model=model, created_at=_now(),
                topic=topic, topic_key=topic_key(topic), **data)


def fallback_plan(topic: str, reason: str) -> Plan:
    """Same schema as an LLM plan and goes through the same checks."""
    t = topic
    templates = [
        ("scale", "quantitative", f"What is the current scale of {t}, according to available figures?", ["reported_magnitude"]),
        ("causes", "qualitative", f"What are the main causes or drivers of {t}?", []),
        ("affected_groups", "qualitative", f"Who is affected by {t}, and how differently?", []),
        ("forecasts", "qualitative", f"What do forecasts or projections say about {t}?", []),
        ("disagreements", "qualitative", f"Where do sources or experts disagree about {t}?", []),
        ("responses", "qualitative", f"What responses or policies are proposed regarding {t}?", []),
    ]
    plan = Plan(
        plan_version=PLAN_VERSION, source="fallback", model=None, created_at=_now(),
        topic=topic, topic_key=topic_key(topic),
        interpretation="Generic research plan (automatic planning failed); scope left open.",
        assumptions=[],
        subject_terms=SubjectTerms(core=[], synonyms=[]),
        metrics=[Metric(id="reported_magnitude", label="Reported magnitude (any measure)",
                        definition="Any figure a source reports for the size of the topic.",
                        measure_type="other", unit="other",
                        useful_for="Collecting any figure sources report.", confusable_with=[])],
        questions=[Question(text=text, angle=angle, kind=kind, why_it_matters="Generic angle for any topic.",
                            metric_ids=metrics,
                            facets=[Facet(dimension="population", name="group"),
                                    Facet(dimension="geography", name="region"),
                                    Facet(dimension="timeframe", name="period")],
                            question_terms=[])
                   for angle, kind, text, metrics in templates],
        fixes=[f"fallback plan used: {reason}"],
    )
    checked, _ = check_plan(plan)
    return checked


async def make_plan(topic: str, llm: LLM | None) -> Plan:
    """LLM draft → checks → one retry with the problems → fallback. Never raises for LLM trouble.

    Models are tried in order (preferred planner model first, then cheaper ones) before giving up.
    """
    topic = validate_topic(topic)
    if llm is None:
        return fallback_plan(topic, "LLM disabled")
    models = [PLANNER_MODEL, *PLANNER_FALLBACK_MODELS]
    try:
        draft, model, notes = await generate_json_with_fallback(
            llm, models, system=SYSTEM, prompt=_prompt(topic), schema=PlanDraft)
    except LLMError as e:
        return fallback_plan(topic, str(e))
    plan, problems = check_plan(_from_draft(topic, draft, model))
    plan.fixes[:0] = notes
    if not problems:
        return plan

    try:
        # Retry on the model that worked, so the feedback goes to the same model.
        draft2, _, _ = await generate_json_with_fallback(
            llm, [model], system=SYSTEM, prompt=_retry_prompt(topic, draft, problems), schema=PlanDraft)
        plan2, _ = check_plan(_from_draft(topic, draft2, model))
        plan2.fixes[:0] = notes + [f"retried once; first draft had: {'; '.join(problems)}"]
        if is_usable(plan2):
            return plan2
    except LLMError:
        pass
    if is_usable(plan):
        plan.fixes.append(f"retry failed; using first draft with fixes ({'; '.join(problems)})")
        return plan
    return fallback_plan(topic, f"plan unusable after retry: {'; '.join(problems)}")


def load_plan_file(path: Path, topic: str) -> Plan:
    """An analyst-written or edited plan. Must be for the same topic; goes through the same checks."""
    try:
        plan = Plan.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError) as e:
        raise PlanFileError(f"can't read plan file {path}: {e}") from e
    if not plan.topic:
        raise PlanFileError(f"plan file {path} has no 'topic'")
    if topic_key(plan.topic) != topic_key(topic):
        raise PlanFileError(f"plan file is for topic '{plan.topic}', but this run's topic is '{topic}'")
    plan.source, plan.topic, plan.topic_key = "user", topic, topic_key(topic)
    plan.plan_version, plan.created_at = PLAN_VERSION, plan.created_at or _now()
    plan, _ = check_plan(plan)
    if not is_usable(plan):
        raise PlanFileError(f"plan file has only {len(plan.questions)} usable questions; need at least 5")
    return plan


async def get_plan(topic: str, run_dir: Path, llm: LLM | None, *, replan: bool = False,
                   plan_file: Path | None = None) -> tuple[Plan, str]:
    """Returns (plan, how) where how is 'reused', 'new', 'replaced' or 'user'. Writes run_dir/plan.json."""
    topic = validate_topic(topic)
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "plan.json"

    if plan_file:
        plan, how = load_plan_file(plan_file, topic), "user"
    else:
        how = "new"
        if path.exists():
            try:
                cached = Plan.model_validate(json.loads(path.read_text()))
            except ValueError:
                cached = None
            # A fallback plan usually means a temporary failure (quota, outage): never reuse it.
            if (not replan and cached and cached.topic_key == topic_key(topic)
                    and cached.plan_version == PLAN_VERSION and cached.source != "fallback"):
                return cached, "reused"
            how = "replaced"
        plan = await make_plan(topic, llm)

    if path.exists():
        shutil.copy(path, run_dir / "plan.prev.json")
    path.write_text(plan.model_dump_json(indent=2))
    return plan, how


def load_run_plan(run_dir: Path) -> Plan | None:
    path = run_dir / "plan.json"
    if not path.exists():
        return None
    try:
        return Plan.model_validate(json.loads(path.read_text()))
    except ValueError:
        return None
