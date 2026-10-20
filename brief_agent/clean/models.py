"""Data shapes for stage 2 (cleaning and triage)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Literal

ProcessingStatus = Literal["ok", "empty", "failed", "skipped"]
UsabilityStatus = Literal["usable", "partial", "excluded"]
BlockKind = Literal["paragraph", "heading", "table", "list", "footnote"]


@dataclass
class RawBlock:
    """A piece of text straight out of an extractor, before IDs and cleanup."""

    kind: BlockKind
    text: str
    page: int | None = None


@dataclass
class Block:
    id: str                            # "s1-p014": the citation unit
    kind: BlockKind
    text: str
    section: str | None = None         # nearest heading above it
    page: int | None = None            # PDFs only
    duplicate_of: str | None = None    # block ID in an earlier source


@dataclass
class Processing:
    status: ProcessingStatus
    reason: str = ""


@dataclass
class Usability:
    status: UsabilityStatus = "usable"
    reasons: list[str] = field(default_factory=list)   # why excluded / partial
    notes: list[str] = field(default_factory=list)     # caveats that don't change usability


@dataclass
class Relevance:
    label: Literal["high", "partial", "none"]
    page_kind: str | None
    reason: str
    checked: bool                      # False → keyword fallback, the LLM didn't confirm


@dataclass
class Duplicate:
    of: str
    overlap: float
    unique_blocks: int


@dataclass
class CleanSource:
    source_id: str
    url: str
    url_hash: str
    final_url: str | None
    raw_path: str | None
    raw_sha256: str | None
    fetch: dict
    processing: Processing
    usability: Usability = field(default_factory=Usability)
    meta: dict = field(default_factory=dict)
    relevance: Relevance | None = None
    duplicate: Duplicate | None = None
    blocks: list[Block] = field(default_factory=list)
    selected_ids: list[str] = field(default_factory=list)
    coverage: float = 0.0
    stats: dict = field(default_factory=dict)

    def exclude(self, reason: str) -> None:
        self.usability.status = "excluded"
        self.usability.reasons.append(reason)

    def mark_partial(self, reason: str) -> None:
        if self.usability.status == "usable":
            self.usability.status = "partial"
        self.usability.reasons.append(reason)

    def to_dict(self) -> dict:
        return asdict(self)
