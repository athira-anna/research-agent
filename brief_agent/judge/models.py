"""Schema for the stage 5 LLM call: stance per cluster, expressed as sides."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class Side(BaseModel):
    position: str = Field(description="One neutral sentence stating this side's position.")
    claim_ids: list[str]


class ClusterStance(BaseModel):
    cluster_id: str
    stance: Literal["agree", "disagree", "unrelated"]
    disagreement_type: Literal["numeric", "direction", "causal", "existence", "none"]
    causal_exclusive: bool = Field(description="True only if one side explicitly rules out the other's cause.")
    sides: list[Side]
    likely_reasons: list[str] = Field(description="Differences visible in the claims (scope, year, method, "
                                                  "measure). No outside knowledge.")
    summary: str = Field(description="One neutral sentence a product manager can read.")


class JudgeResult(BaseModel):
    judgements: list[ClusterStance]
