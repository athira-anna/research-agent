"""Plan schema. `PlanDraft` is what the LLM must return (strict enums); `Plan` is the saved,
checked plan (lenient types, so a hand-edited plan can be loaded and fixed rather than rejected)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

FACET_DIMENSIONS = ("population", "geography", "timeframe", "metric", "other")
ANGLES = ("scale", "causes", "affected_groups", "forecasts", "disagreements", "responses", "other")
MEASURE_TYPES = ("share", "rate", "count", "amount", "change", "index", "ratio", "other")
UNITS = ("percent", "percentage_points", "count", "currency", "index_points", "years", "other")
KINDS = ("quantitative", "qualitative")

FacetDimension = Literal["population", "geography", "timeframe", "metric", "other"]
Angle = Literal["scale", "causes", "affected_groups", "forecasts", "disagreements", "responses", "other"]
MeasureType = Literal["share", "rate", "count", "amount", "change", "index", "ratio", "other"]
Unit = Literal["percent", "percentage_points", "count", "currency", "index_points", "years", "other"]
Kind = Literal["quantitative", "qualitative"]


# --- what the LLM returns ------------------------------------------------------------

class FacetDraft(BaseModel):
    dimension: FacetDimension
    name: str = Field(description="Short name of the breakdown, e.g. 'sector', 'region'.")
    examples: list[str] = Field(description="A few example values; may be empty.")


class MetricDraft(BaseModel):
    id: str = Field(description="Standard name, lowercase snake_case, e.g. 'job_loss_rate'.")
    label: str
    definition: str = Field(description="One sentence: exactly what is measured.")
    measure_type: MeasureType
    unit: Unit
    useful_for: str = Field(description="One line: when this metric is the right lens.")
    confusable_with: list[str] = Field(description="IDs of other metrics in this plan a reader might mix it up with.")


class QuestionDraft(BaseModel):
    text: str = Field(description="A neutral question with no built-in answer.")
    angle: Angle
    kind: Kind
    why_it_matters: str
    metric_ids: list[str] = Field(description="IDs from the metrics list; required for quantitative questions.")
    facets: list[FacetDraft]
    question_terms: list[str] = Field(description="Words/phrases that signal text answering THIS question; "
                                                  "not general topic words.")


class SubjectTerms(BaseModel):
    core: list[str] = Field(description="The topic's key terms.")
    synonyms: list[str] = Field(description="Synonyms and common alternative phrasings for the whole topic.")


class PlanDraft(BaseModel):
    interpretation: str = Field(description="One sentence restating the topic. No facts, no answers.")
    assumptions: list[str] = Field(description="At most 2, only about scope the topic leaves open.")
    subject_terms: SubjectTerms
    metrics: list[MetricDraft]
    questions: list[QuestionDraft]


# --- the saved plan ------------------------------------------------------------------

class Facet(BaseModel):
    dimension: str
    name: str
    examples: list[str] = []


class Metric(BaseModel):
    id: str
    label: str = ""
    definition: str = ""
    measure_type: str = "other"
    unit: str = "other"
    useful_for: str = ""
    confusable_with: list[str] = []


class Question(BaseModel):
    id: str = ""
    text: str
    angle: str = "other"
    kind: str = "qualitative"
    why_it_matters: str = ""
    metric_ids: list[str] = []
    facets: list[Facet] = []
    question_terms: list[str] = []


class Plan(BaseModel):
    plan_version: int = 0
    source: Literal["llm", "fallback", "user"] = "llm"
    model: str | None = None
    created_at: str | None = None
    topic: str = ""
    topic_key: str = ""
    interpretation: str = ""
    assumptions: list[str] = []
    subject_terms: SubjectTerms = SubjectTerms(core=[], synonyms=[])
    metrics: list[Metric] = []
    questions: list[Question] = []
    fixes: list[str] = []     # what the checks changed, for debugging and the brief

    def all_subject_terms(self) -> list[str]:
        return list(dict.fromkeys(self.subject_terms.core + self.subject_terms.synonyms))
