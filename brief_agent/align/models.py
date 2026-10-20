"""Schema for the stage 4 LLM call: scope mapping table, hierarchy, clusters."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ScopeMapping(BaseModel):
    dimension: Literal["population", "geography"]
    raw: str = Field(description="The wording exactly as it appears in the claims.")
    canonical: str = Field(description="Short standard label; the same meaning always gets the same label.")


class Broader(BaseModel):
    narrower: str = Field(description="A canonical label…")
    broader: str = Field(description="…that is part of this canonical label, e.g. 'software (entry-level)' ⊂ 'software'.")


class ClusterDraft(BaseModel):
    label: str = Field(description="Neutral description of the specific sub-question; never a conclusion.")
    claim_ids: list[str]


class AlignDraft(BaseModel):
    mappings: list[ScopeMapping]
    hierarchy: list[Broader]
    clusters: list[ClusterDraft]
