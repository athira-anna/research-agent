"""Step 3: check a plan in code. The same checks run on LLM, fallback and hand-edited plans.

Returns the fixed plan plus `problems`: things worth sending back to the model in a retry.
Fixes are always applied (and recorded in `plan.fixes`), so a plan is usable even if the retry fails.
"""

from __future__ import annotations

import re

from ..clean.keywords import topic_keywords
from .models import ANGLES, FACET_DIMENSIONS, KINDS, MEASURE_TYPES, UNITS, Plan

MIN_QUESTIONS, MAX_QUESTIONS = 5, 8
MAX_ASSUMPTIONS, MAX_ASSUMPTION_CHARS = 2, 200
MAX_SUBJECT_TERMS, MAX_QUESTION_TERMS = 40, 10
DUPLICATE_JACCARD = 0.6

_QUESTION_STOPWORDS = {
    "what", "which", "who", "whom", "whose", "how", "why", "when", "where", "is", "are", "was", "were", "do",
    "does", "did", "has", "have", "had", "the", "a", "an", "of", "in", "on", "to", "for", "and", "or", "by",
    "with", "from", "so", "far", "far?", "this", "that", "these", "those", "there", "their", "its", "be",
    "been", "much", "many", "large", "big", "extent", "degree", "level", "levels", "due", "driven",
}


def _snake(s: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", s.lower())).strip("_")


def _terms(items: list[str]) -> list[str]:
    return list(dict.fromkeys(" ".join(t.lower().split()) for t in items if t and t.strip()))


def _content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w.rstrip("s") for w in words if w not in _QUESTION_STOPWORDS and len(w) > 1}


def _same_question(a, b) -> bool:
    # Different angles never count as duplicates: "...so far?" (scale) and "...by 2030?" (forecasts)
    # share most of their words but need different evidence.
    if a.angle != b.angle:
        return False
    if a.metric_ids and set(a.metric_ids) == set(b.metric_ids):
        return True
    wa = _content_words(a.text) | set(a.question_terms)
    wb = _content_words(b.text) | set(b.question_terms)
    return bool(wa and wb) and len(wa & wb) / len(wa | wb) >= DUPLICATE_JACCARD


def check_plan(plan: Plan) -> tuple[Plan, list[str]]:
    """Check and fix a plan. Returns (fixed copy, problems worth sending back to the model in a retry);
    every fix is also recorded in plan.fixes."""
    plan = plan.model_copy(deep=True)
    problems: list[str] = []
    fixes = plan.fixes
    _check_assumptions(plan, problems, fixes)
    metric_ids = _check_metrics(plan, problems, fixes)
    _check_subject_terms(plan)
    subject = set(plan.all_subject_terms())
    questions = [q for q in plan.questions if _check_question(q, metric_ids, subject, problems, fixes)]
    questions = _remove_reworded_duplicates(questions, problems, fixes)
    if len(questions) > MAX_QUESTIONS:
        fixes.append(f"kept the first {MAX_QUESTIONS} of {len(questions)} questions")
        problems.append(f"{len(questions)} questions; need {MIN_QUESTIONS}–{MAX_QUESTIONS}")
        questions = questions[:MAX_QUESTIONS]
    if len(questions) < MIN_QUESTIONS:
        problems.append(f"only {len(questions)} distinct questions; need {MIN_QUESTIONS}–{MAX_QUESTIONS}")
    for i, q in enumerate(questions, start=1):
        q.id = f"q{i}"
    plan.questions = questions
    return plan, problems


def _check_assumptions(plan: Plan, problems: list[str], fixes: list[str]) -> None:
    """Few and short (the prompt asks for scope-only assumptions; code enforces the limits)."""
    assumptions = [" ".join(a.split()) for a in plan.assumptions if a and a.strip()]
    if len(assumptions) > MAX_ASSUMPTIONS:
        fixes.append(f"kept {MAX_ASSUMPTIONS} of {len(assumptions)} assumptions")
        problems.append(f"{len(assumptions)} assumptions; at most {MAX_ASSUMPTIONS}, only about scope the topic leaves open")
    plan.assumptions = [a[:MAX_ASSUMPTION_CHARS] for a in assumptions[:MAX_ASSUMPTIONS]]
    plan.interpretation = " ".join(plan.interpretation.split())


def _check_metrics(plan: Plan, problems: list[str], fixes: list[str]) -> set[str]:
    """Unique snake_case IDs, known types/units, valid confusable_with references. Returns the metric IDs."""
    metrics, ids = [], set()
    for m in plan.metrics:
        mid = _snake(m.id or m.label)
        if not mid:
            fixes.append("dropped a metric with no id")
            continue
        if mid in ids:
            problems.append(f"metric id '{mid}' is used twice")
            fixes.append(f"dropped duplicate metric '{mid}'")
            continue
        if mid != m.id:
            fixes.append(f"metric id '{m.id}' → '{mid}'")
        ids.add(mid)
        m.id = mid
        m.measure_type = m.measure_type if m.measure_type in MEASURE_TYPES else "other"
        m.unit = m.unit if m.unit in UNITS else "other"
        metrics.append(m)
    for m in metrics:
        m.confusable_with = [c for c in dict.fromkeys(_snake(c) for c in m.confusable_with) if c in ids and c != m.id]
    plan.metrics = metrics
    return ids


def _check_subject_terms(plan: Plan) -> None:
    """Topic words always present; lowercased, de-duplicated, capped."""
    core = _terms(plan.subject_terms.core)
    core += [w for w in topic_keywords(plan.topic) if w not in core]
    synonyms = [t for t in _terms(plan.subject_terms.synonyms) if t not in core]
    plan.subject_terms.core = core[:MAX_SUBJECT_TERMS]
    plan.subject_terms.synonyms = synonyms[:max(0, MAX_SUBJECT_TERMS - len(plan.subject_terms.core))]


def _check_question(q, metric_ids: set[str], subject: set[str], problems: list[str], fixes: list[str]) -> bool:
    """Fix one question in place. Returns False if it should be dropped (no text)."""
    q.text = " ".join(q.text.split())
    if not q.text:
        return False
    q.angle = q.angle if q.angle in ANGLES else "other"
    q.kind = q.kind if q.kind in KINDS else "qualitative"
    for f in q.facets:
        if f.dimension not in FACET_DIMENSIONS:
            fixes.append(f"facet '{f.name}': dimension '{f.dimension}' → 'other'")
            f.dimension = "other"
    known = [_snake(i) for i in q.metric_ids if _snake(i) in metric_ids]
    if len(known) < len(q.metric_ids):
        unknown = sorted(set(q.metric_ids) - set(known))
        problems.append(f"question '{q.text[:60]}' refers to unknown metric ids {unknown}")
    q.metric_ids = list(dict.fromkeys(known))
    q.question_terms = [t for t in _terms(q.question_terms) if t not in subject][:MAX_QUESTION_TERMS]
    if q.kind == "quantitative" and not q.metric_ids:
        problems.append(f"quantitative question '{q.text[:60]}' has no metrics")
        fixes.append(f"'{q.text[:60]}' switched to qualitative (no metrics)")
        q.kind = "qualitative"
    return True


def _remove_reworded_duplicates(questions: list, problems: list[str], fixes: list[str]) -> list:
    """The prompt is the main defence against reworded duplicates; this is the safety net."""
    kept = []
    for q in questions:
        twin = next((k for k in kept if _same_question(k, q)), None)
        if twin:
            problems.append(f"'{twin.text[:70]}' and '{q.text[:70]}' ask the same thing")
            fixes.append(f"removed duplicate question '{q.text[:70]}'")
        else:
            kept.append(q)
    return kept


def is_usable(plan: Plan) -> bool:
    return len(plan.questions) >= MIN_QUESTIONS
