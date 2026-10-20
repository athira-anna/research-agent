"""Claim schema. `ClaimDraft` is what the LLM returns; `Claim` adds what code works out
(IDs, grounding, number check, support verdict, fixes)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Modality = Literal["observed", "forecast", "opinion"]
Certainty = Literal["asserted", "hedged", "reported"]
ValueKind = Literal["level", "absolute_change", "relative_change", "unknown"]
Unit = Literal["percent", "percentage_points", "count", "currency", "index_points", "years", "other"]
Direction = Literal["increase", "decrease", "none", "unknown"]


class Timeframe(BaseModel):
    raw: str = Field(description="Time words exactly as the source gives them; empty if the source gives none.")
    start: int | None = Field(description="First year, only if the source states it.")
    end: int | None = Field(description="Last year, only if the source states it.")


class Scope(BaseModel):
    population: str = Field(description="Group/sector/population in the source's words; empty if not stated.")
    geography: str = Field(description="Place in the source's words; empty if not stated.")
    timeframe: Timeframe
    metric: str = Field(description="What is measured, in the source's words; empty if not stated.")


class Baseline(BaseModel):
    number: float
    period: str


class Value(BaseModel):
    raw: str = Field(description="The number phrase exactly as written, e.g. 'rose from 5% to 10%'.")
    low: float
    high: float = Field(description="Same as low unless the source gives a range.")
    unit: Unit
    value_kind: ValueKind = Field(description="What the number literally is in the text. 'from 5% to 10%' is a "
                                              "level of 10 with baseline 5. Use unknown if the text is ambiguous.")
    metric_id: str = Field(description="A metric id from the plan, or 'other'.")
    of_what: str = Field(description="What the number is a share/count of, in the source's words.")
    baseline: Baseline | None = Field(description="Starting value, only if the text states it.")
    direction: Direction


class Causal(BaseModel):
    cause: str
    effect: str


class Evidence(BaseModel):
    block_id: str = Field(description="ID of the block the quote is copied from, e.g. 's1-p014'.")
    quote: str = Field(description="One continuous span copied word for word from that block, 20-300 characters.")
    attributed_to: str = Field(description="Who the source says made the claim (study, organisation, company); "
                                           "empty if the source cites no one.")


class ClaimDraft(BaseModel):
    statement: str = Field(description="One checkable statement; says no more than the quote.")
    question_id: str = Field(description="A question id from the plan (q1, q2, ...) or 'other'.")
    modality: Modality
    certainty: Certainty
    causal: Causal | None = Field(description="Only if the text explicitly states a cause.")
    value: Value | None = Field(description="Only if the claim contains a number.")
    scope: Scope
    evidence: Evidence


class ExtractionDraft(BaseModel):
    claims: list[ClaimDraft]


class Claim(ClaimDraft):
    claim_id: str = ""
    source_id: str = ""
    grounding: dict = {}          # {method: exact|fuzzy|moved, score, moved_from}
    number_check: str | None = None
    support: dict = {}            # {status: full|partial|unchecked, reason, unsupported_parts}
    fixes: list[str] = []


# --- support check (step 4b-2) ---------------------------------------------------------

UnsupportedPart = Literal["core_statement", "causal_link", "number_meaning", "metric", "scope", "certainty",
                          "modality", "attribution"]


class SupportVerdict(BaseModel):
    claim_id: str
    support: Literal["full", "partial", "none"]
    unsupported_parts: list[UnsupportedPart]
    correct_modality: Modality
    reason: str = Field(description="One sentence.")


class SupportResult(BaseModel):
    verdicts: list[SupportVerdict]
