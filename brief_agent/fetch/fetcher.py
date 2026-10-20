"""Stage 1: fetch every URL in parallel, each through its own fallback chain.

    safety check → cache → http → browser → reader → archive

Never raises for a bad URL: every failure becomes a status plus an `attempts` log.
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import httpx

from . import safety
from .cache import Cache, extension
from .detect import SHORT_PAGE_MIN_CHARS
from .methods import Browser, fetch_archive, fetch_http, fetch_reader, make_client
from .models import Attempt, Method, Outcome, SourceRecord, Status

PER_URL_TIMEOUT_S = 90

# Stop the chain: retrying with another method won't change these.
_FINAL = {Status.NOT_FOUND, Status.UNSUPPORTED, Status.TOO_LARGE, Status.REJECTED_UNSAFE}
# When everything fails, report the most informative status we saw.
_FAILURE_RANK = [Status.PAYWALLED, Status.BOT_CHALLENGE, Status.CONSENT_WALL, Status.BLOCKED, Status.JS_BLOCKED,
                 Status.EMPTY, Status.TIMEOUT, Status.TOO_LARGE, Status.ERROR]


@dataclass
class FetchConfig:
    allow_external: bool = True     # reader + archive send the URL to third parties
    use_cache: bool = True
    refresh: bool = False
    cache_dir: Path = Path("cache")
    per_url_timeout: float = PER_URL_TIMEOUT_S


def _short_but_clean(seen: list[Outcome]) -> Outcome | None:
    """A short article looks "empty" to every method. If nothing else is wrong, keep the longest copy."""
    if any(o.status in (Status.PAYWALLED, Status.BOT_CHALLENGE, Status.CONSENT_WALL, Status.BLOCKED) for o in seen):
        return None
    candidates = [o for o in seen if o.status == Status.EMPTY and o.content
                  and o.text_chars >= SHORT_PAGE_MIN_CHARS]
    return max(candidates, key=lambda o: o.text_chars, default=None)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Fetcher:
    def __init__(self, config: FetchConfig, run_dir: Path) -> None:
        self.config = config
        self.run_dir = run_dir
        self.cache = Cache(config.cache_dir)
        self.browser = Browser()
        self.client: httpx.AsyncClient | None = None

    async def fetch_all(self, urls: list[str]) -> list[SourceRecord]:
        (self.run_dir / "raw").mkdir(parents=True, exist_ok=True)
        async with make_client() as client:
            self.client = client
            try:
                records = await asyncio.gather(
                    *(self._fetch_one(f"s{i}", url) for i, url in enumerate(urls, start=1)))
            finally:
                await self.browser.close()
        (self.run_dir / "sources_raw.json").write_text(
            json.dumps([r.to_dict() for r in records], indent=2))
        return list(records)

    async def _fetch_one(self, source_id: str, url: str) -> SourceRecord:
        record = SourceRecord(source_id=source_id, url=url)
        try:
            async with asyncio.timeout(self.config.per_url_timeout):
                await self._chain(record)
        except TimeoutError:
            record.status = Status.TIMEOUT
            record.attempts.append(Attempt("all", Status.TIMEOUT, f"gave up after {self.config.per_url_timeout:.0f}s overall", self.config.per_url_timeout))
        except Exception as e:  # noqa: BLE001 - stage 1 never crashes the pipeline
            record.status = Status.ERROR
            record.attempts.append(Attempt("all", Status.ERROR, f"unexpected {type(e).__name__}: {e}", 0.0))
        record.fetched_at = record.fetched_at or _now()
        return record

    async def _chain(self, record: SourceRecord) -> None:
        url = record.url

        t = time.monotonic()
        try:
            await safety.check_url(url)
        except safety.UnsafeURL as e:
            record.status = Status.REJECTED_UNSAFE
            record.attempts.append(Attempt("safety", Status.REJECTED_UNSAFE, str(e), round(time.monotonic() - t, 2)))
            return

        if self.config.use_cache and not self.config.refresh:
            hit = self.cache.load(url)
            if hit:
                cached, content = hit
                self._accept(record, cached.method or Method.CACHE, Outcome(
                    Status.OK, "from cache", content, cached.content_type, cached.final_url,
                    cached.text_preview_chars, archive_timestamp=cached.archive_timestamp))
                record.from_cache = True
                record.fetched_at = cached.fetched_at
                record.attempts.append(Attempt(Method.CACHE, Status.OK, f"cached copy from {cached.fetched_at}", 0.0))
                return

        steps = [(Method.HTTP, lambda: fetch_http(self.client, url)),
                 (Method.BROWSER, lambda: self.browser.fetch(url))]
        if self.config.allow_external:
            steps += [(Method.READER, lambda: fetch_reader(self.client, url)),
                      (Method.ARCHIVE, lambda: fetch_archive(self.client, url))]

        seen: list[Outcome] = []
        for method, run in steps:
            if method == Method.BROWSER and seen and seen[-1].status in (Status.PAYWALLED, Status.TIMEOUT):
                # A browser sees the same paywall, and a server that timed out once is unlikely to
                # render in time; save the time budget for the reader/archive.
                record.attempts.append(Attempt(method, "skipped", f"previous result was {seen[-1].status}", 0.0))
                continue
            t = time.monotonic()
            out = await run()
            secs = round(time.monotonic() - t, 2)
            result = "unavailable" if out.service_failure else out.status
            record.attempts.append(Attempt(method, result, out.reason, secs))
            if out.status == Status.OK:
                self._accept(record, method, out)
                self.cache.save(record, out.content)
                return
            if out.final_url and not record.final_url:
                record.final_url = out.final_url
            if result != "unavailable":
                out.method = method
                seen.append(out)
            if out.status in _FINAL and result != "unavailable":
                break  # e.g. the browser saw a real 404: a reader returning the "page not found" text isn't a success

        short = _short_but_clean(seen)
        if short:
            short.detail = "short"
            short.reason = f"accepted short page: {short.reason}"
            self._accept(record, short.method, short)
            record.attempts.append(Attempt("accept", Status.OK, short.reason, 0.0))
            self.cache.save(record, short.content)
            return

        worst = min(seen, key=lambda o: _FAILURE_RANK.index(o.status) if o.status in _FAILURE_RANK else len(_FAILURE_RANK)) \
            if seen else Outcome(Status.ERROR, "no method produced a result")
        # Final statuses (not_found, unsafe, ...) always win: nothing else was tried after them.
        final = next((o for o in seen if o.status in _FINAL), worst)
        record.status = final.status
        record.status_detail = final.detail

    def _accept(self, record: SourceRecord, method: str, out: Outcome) -> None:
        path = self.run_dir / "raw" / f"{record.source_id}.{extension(out.content_type)}"
        path.write_bytes(out.content)
        record.status = Status.OK
        record.status_detail = out.detail
        record.method = str(method)
        record.content_type = out.content_type
        record.content_path = str(path)
        record.final_url = out.final_url or record.url
        record.text_preview_chars = out.text_chars
        record.archive_timestamp = out.archive_timestamp
        record.fetched_at = _now()


async def fetch_sources(urls: list[str], run_dir: Path, config: FetchConfig | None = None) -> list[SourceRecord]:
    return await Fetcher(config or FetchConfig(), run_dir).fetch_all(urls)
