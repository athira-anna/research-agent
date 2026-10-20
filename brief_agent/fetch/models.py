"""Data shapes for stage 1 (fetching)."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from enum import StrEnum


def url_hash(url: str) -> str:
    """Stable key linking a URL to its cache entry and to every cleaned/derived record."""
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


class Status(StrEnum):
    OK = "ok"
    PAYWALLED = "paywalled"
    JS_BLOCKED = "js_blocked"          # page is an empty JavaScript shell
    BLOCKED = "blocked"                # HTTP 401/403/429 refusal
    BOT_CHALLENGE = "bot_challenge"    # got a page, but it's a CAPTCHA / "are you human" check
    CONSENT_WALL = "consent_wall"      # got a page, but the readable text is a cookie-consent dialog
    EMPTY = "empty"                    # page loaded but has no readable text
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    UNSUPPORTED = "unsupported"        # video, image, etc.
    TOO_LARGE = "too_large"
    REJECTED_UNSAFE = "rejected_unsafe"
    ERROR = "error"


class Method(StrEnum):
    CACHE = "cache"
    HTTP = "http"
    BROWSER = "browser"
    READER = "reader"
    ARCHIVE = "archive"


@dataclass
class Outcome:
    """What one fetch method produced for one URL."""

    status: Status
    reason: str
    content: bytes | None = None
    content_type: str | None = None    # html | pdf | text
    final_url: str | None = None
    text_chars: int = 0
    detail: str | None = None          # e.g. "cloudflare" for a bot challenge
    archive_timestamp: str | None = None
    # True when the helper service itself failed (rate limit, outage) rather than telling us
    # something about the source. Such outcomes never decide the source's final status.
    service_failure: bool = False
    method: str | None = None          # filled in by the fetcher


@dataclass
class Attempt:
    method: str
    result: str                        # a Status value, or "unavailable" / "skipped"
    reason: str
    seconds: float


@dataclass
class SourceRecord:
    source_id: str
    url: str
    url_hash: str = ""
    final_url: str | None = None
    status: Status = Status.ERROR
    status_detail: str | None = None
    method: str | None = None
    content_type: str | None = None
    content_path: str | None = None
    text_preview_chars: int = 0
    archive_timestamp: str | None = None
    from_cache: bool = False
    attempts: list[Attempt] = field(default_factory=list)
    fetched_at: str | None = None

    def __post_init__(self) -> None:
        self.url_hash = self.url_hash or url_hash(self.url)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> SourceRecord:
        d = dict(d)
        d["status"] = Status(d["status"])
        d["attempts"] = [Attempt(**a) for a in d.get("attempts", [])]
        return cls(**d)
