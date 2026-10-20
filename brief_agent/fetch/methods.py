"""The four ways we try to get a page: plain HTTP, headless browser, reader service, web archive.

Each method returns an `Outcome`; the fallback order lives in fetcher.py.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin

import httpx

from . import safety
from .detect import assess_html, assess_text
from .models import Outcome, Status

# Plain HTTP identifies itself honestly. A spoofed Chrome User-Agent from a non-browser client is
# itself a bot signal (Wikipedia returns 403 for it). Sites that insist on a real browser get one in method 2.
AGENT_USER_AGENT = os.environ.get(
    "BRIEF_AGENT_UA", "BriefAgent/0.1 (multi-source research brief tool; +https://github.com/)")
# Used only by the headless browser, which really is Chrome, so the User-Agent matches its fingerprint.
BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": AGENT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,application/pdf;q=0.8,*/*;q=0.7",
    "Accept-Language": "en-US,en;q=0.9",
}

HTTP_TIMEOUT_S = 15
BROWSER_TIMEOUT_S = 25
BROWSER_CHALLENGE_WAIT_MS = 5000
READER_TIMEOUT_S = 25
ARCHIVE_TIMEOUT_S = 25


# --- safe streaming GET (shared by http, reader and archive) -----------------------

@dataclass
class RawResponse:
    url: str
    status_code: int
    headers: httpx.Headers
    body: bytes
    kind: str  # html | pdf | text | other


def _kind(content_type: str, first_bytes: bytes) -> str:
    ct = content_type.split(";")[0].strip().lower()
    if ct == "application/pdf" or first_bytes.startswith(b"%PDF"):
        return "pdf"
    if ct in ("text/html", "application/xhtml+xml") or (not ct and first_bytes.lstrip()[:1] == b"<"):
        return "html"
    if ct in ("text/plain", "text/markdown"):
        return "text"
    return "other"


async def safe_get(client: httpx.AsyncClient, url: str, *, headers: dict | None = None,
                   total_timeout: float = HTTP_TIMEOUT_S) -> RawResponse:
    """GET with an SSRF check on every hop, a size cap on decompressed bytes, and a hard total time limit.

    Raises safety.UnsafeURL, safety.TooLarge, TimeoutError or httpx errors.
    """
    async with asyncio.timeout(total_timeout):  # total time, so a slow-drip server can't stall us
        current = url
        for _ in range(safety.MAX_REDIRECTS + 1):
            await safety.check_url(current)
            async with client.stream("GET", current, headers=headers, timeout=total_timeout) as resp:
                if resp.is_redirect:
                    current = urljoin(str(resp.url), resp.headers.get("location", ""))
                    continue
                declared = resp.headers.get("content-length")
                limit = safety.max_bytes_for("pdf" if "pdf" in resp.headers.get("content-type", "") else None)
                if declared and declared.isdigit() and int(declared) > limit:
                    raise safety.TooLarge(f"Content-Length {int(declared):,} bytes > limit {limit:,}")
                chunks, size = [], 0
                async for chunk in resp.aiter_bytes():  # already decompressed: zip bombs count at full size
                    size += len(chunk)
                    if size > limit:
                        raise safety.TooLarge(f"body exceeded {limit:,} bytes")
                    chunks.append(chunk)
                body = b"".join(chunks)
                return RawResponse(str(resp.url), resp.status_code, resp.headers, body,
                                   _kind(resp.headers.get("content-type", ""), body[:1024]))
        raise safety.UnsafeURL(f"more than {safety.MAX_REDIRECTS} redirects")


def decode_html(body: bytes, headers: httpx.Headers) -> str:
    m = re.search(r"charset=([\w-]+)", headers.get("content-type", ""), re.I) or \
        re.search(rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I)
    charset = m.group(1) if m else "utf-8"
    if isinstance(charset, bytes):
        charset = charset.decode("ascii", "ignore")
    try:
        return body.decode(charset, errors="replace")
    except LookupError:
        return body.decode("utf-8", errors="replace")


def make_client() -> httpx.AsyncClient:
    # Redirects are followed by hand in safe_get so each hop gets the safety check.
    return httpx.AsyncClient(follow_redirects=False, headers=DEFAULT_HEADERS,
                             timeout=httpx.Timeout(HTTP_TIMEOUT_S), http2=False)


def _outcome_from_response(r: RawResponse) -> Outcome:
    if r.status_code in (404, 410):
        return Outcome(Status.NOT_FOUND, f"HTTP {r.status_code}", final_url=r.url)
    if r.status_code >= 400:
        if r.kind == "html":
            v = assess_html(decode_html(r.body, r.headers), r.headers)
            if v.status == Status.BOT_CHALLENGE:
                return Outcome(v.status, f"HTTP {r.status_code}, {v.reason}", final_url=r.url, detail=v.detail)
        if r.status_code in (401, 403, 429, 451):
            return Outcome(Status.BLOCKED, f"HTTP {r.status_code}", final_url=r.url)
        return Outcome(Status.ERROR, f"HTTP {r.status_code}", final_url=r.url)

    if r.kind == "pdf":
        if not r.body.startswith(b"%PDF"):
            return Outcome(Status.ERROR, "said PDF but body is not a PDF", final_url=r.url)
        return Outcome(Status.OK, f"PDF, {len(r.body):,} bytes", r.body, "pdf", r.url)
    if r.kind == "text":
        v = assess_text(decode_html(r.body, r.headers))
        return Outcome(v.status, v.reason, r.body if v.status in (Status.OK, Status.EMPTY) else None, "text", r.url, v.text_chars, v.detail)
    if r.kind == "html":
        v = assess_html(decode_html(r.body, r.headers), r.headers)
        return Outcome(v.status, v.reason, r.body if v.status in (Status.OK, Status.EMPTY) else None, "html", r.url, v.text_chars, v.detail)
    return Outcome(Status.UNSUPPORTED, f"content type '{r.headers.get('content-type', '?')}'", final_url=r.url)


def _outcome_from_error(e: BaseException) -> Outcome:
    if isinstance(e, safety.UnsafeURL):
        return Outcome(Status.REJECTED_UNSAFE, str(e))
    if isinstance(e, safety.TooLarge):
        return Outcome(Status.TOO_LARGE, str(e))
    if isinstance(e, (TimeoutError, httpx.TimeoutException)):
        return Outcome(Status.TIMEOUT, "timed out")
    return Outcome(Status.ERROR, f"{type(e).__name__}: {e}")


# --- method 1: plain HTTP ----------------------------------------------------------

async def fetch_http(client: httpx.AsyncClient, url: str) -> Outcome:
    for attempt in range(2):  # one retry, for server errors and dropped connections only
        try:
            r = await safe_get(client, url)
        except (httpx.ConnectError, httpx.RemoteProtocolError, httpx.ReadError) as e:
            if attempt == 0:
                await asyncio.sleep(1)
                continue
            return _outcome_from_error(e)
        except Exception as e:  # noqa: BLE001 - every failure becomes a status
            return _outcome_from_error(e)
        if r.status_code >= 500 and attempt == 0:
            await asyncio.sleep(1)
            continue
        return _outcome_from_response(r)
    return Outcome(Status.ERROR, "unreachable")


# --- method 2: headless browser ----------------------------------------------------

class Browser:
    """One shared Chromium, started on first use; a fresh private context per URL."""

    def __init__(self) -> None:
        self._pw = None
        self._browser = None
        self._lock = asyncio.Lock()
        self._host_ok: dict[str, bool] = {}

    async def _ensure(self):
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                # Playwright turns Chromium's sandbox off by default; we turn it back on.
                self._browser = await self._pw.chromium.launch(headless=True, chromium_sandbox=True)
        return self._browser

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def _allowed(self, url: str) -> bool:
        if not url.startswith(("http://", "https://")):
            return True  # data:, blob: and about: don't touch the network
        host = httpx.URL(url).host
        if host not in self._host_ok:
            try:
                await safety.check_url(url)
                self._host_ok[host] = True
            except safety.UnsafeURL:
                self._host_ok[host] = False
        return self._host_ok[host]

    async def fetch(self, url: str) -> Outcome:
        try:
            async with asyncio.timeout(BROWSER_TIMEOUT_S):
                return await self._fetch(url)
        except Exception as e:  # noqa: BLE001
            return _outcome_from_error(e)

    async def _fetch(self, url: str) -> Outcome:
        browser = await self._ensure()
        context = await browser.new_context(user_agent=BROWSER_USER_AGENT, accept_downloads=False,
                                            service_workers="block", permissions=[])
        try:
            async def route(r):
                if r.request.resource_type in ("image", "media", "font"):
                    await r.abort()
                elif await self._allowed(r.request.url):
                    await r.continue_()
                else:
                    await r.abort("blockedbyclient")

            await context.route("**/*", route)
            page = await context.new_page()
            response = await page.goto(url, wait_until="domcontentloaded",
                                       timeout=(BROWSER_TIMEOUT_S - 8) * 1000)
            if response is None:
                return Outcome(Status.ERROR, "no response from page")
            if response.status in (404, 410):
                return Outcome(Status.NOT_FOUND, f"HTTP {response.status}", final_url=page.url)
            try:
                await page.wait_for_load_state("networkidle", timeout=6000)
            except Exception:  # noqa: BLE001 - busy pages never go idle; use what we have
                pass

            headers = await response.all_headers()
            html = await page.content()
            v = assess_html(html, headers)
            if v.status == Status.BOT_CHALLENGE:
                # Some challenges (e.g. Cloudflare "Just a moment...") clear once their JS has run.
                # We wait once; we never try to solve a CAPTCHA.
                await page.wait_for_timeout(BROWSER_CHALLENGE_WAIT_MS)
                html = await page.content()
                v = assess_html(html)
                if v.status == Status.OK:
                    v.reason += " (challenge cleared after waiting)"
            if not await self._allowed(page.url):
                return Outcome(Status.REJECTED_UNSAFE, f"page ended up on non-public host: {page.url}")
            body = html.encode("utf-8")
            if len(body) > safety.MAX_HTML_BYTES:
                return Outcome(Status.TOO_LARGE, f"rendered HTML is {len(body):,} bytes")
            if v.status != Status.OK and response.status in (401, 403, 429) and v.status != Status.BOT_CHALLENGE:
                return Outcome(Status.BLOCKED, f"HTTP {response.status}; {v.reason}", final_url=page.url)
            return Outcome(v.status, v.reason, body if v.status in (Status.OK, Status.EMPTY) else None, "html",
                           page.url, v.text_chars, v.detail)
        finally:
            await context.close()


# --- method 3: reader service ------------------------------------------------------

_READER_TARGET_ERROR_RE = re.compile(r"Target URL returned error (\d{3})")


async def fetch_reader(client: httpx.AsyncClient, url: str) -> Outcome:
    """Jina Reader fetches the page on its side and returns clean text. Sends the URL to a third party."""
    headers = {"Accept": "text/plain", "X-Return-Format": "markdown"}
    if key := os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    try:
        r = await safe_get(client, "https://r.jina.ai/" + url, headers=headers, total_timeout=READER_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        out = _outcome_from_error(e)
        out.reason = f"reader service: {out.reason}"
        out.service_failure = True
        return out
    if r.status_code >= 400:
        return Outcome(Status.ERROR, f"reader service returned HTTP {r.status_code}", service_failure=True)
    text = r.body.decode("utf-8", errors="replace")
    if m := _READER_TARGET_ERROR_RE.search(text[:2000]):
        code = int(m.group(1))
        if code in (404, 410):
            return Outcome(Status.NOT_FOUND, f"reader: target returned HTTP {code}")
        if code in (401, 403, 429, 451):
            return Outcome(Status.BLOCKED, f"reader: target returned HTTP {code}")
    v = assess_text(text)
    return Outcome(v.status, v.reason, r.body if v.status in (Status.OK, Status.EMPTY) else None, "text", url, v.text_chars, v.detail)


# --- method 4: Internet Archive ----------------------------------------------------

async def fetch_archive(client: httpx.AsyncClient, url: str) -> Outcome:
    """Fetch the closest Wayback Machine snapshot. Soft paywalls are often absent in archived copies."""
    try:
        async with asyncio.timeout(ARCHIVE_TIMEOUT_S):
            lookup = await safe_get(client, "https://archive.org/wayback/available?url=" + quote(url, safe=""),
                                    headers={"Accept": "application/json"}, total_timeout=ARCHIVE_TIMEOUT_S)
            if lookup.status_code != 200:
                why = "rate-limited" if lookup.status_code == 429 else f"HTTP {lookup.status_code}"
                return Outcome(Status.ERROR, f"archive.org lookup {why}", service_failure=True)
            snap = json.loads(lookup.body).get("archived_snapshots", {}).get("closest") or {}
            if not snap.get("available") or not str(snap.get("status", "200")).startswith("2"):
                return Outcome(Status.NOT_FOUND, "no archived copy", service_failure=True)
            ts = snap["timestamp"]
            # "id_" asks for the original page without the Wayback toolbar.
            r = await safe_get(client, f"https://web.archive.org/web/{ts}id_/{url}", total_timeout=ARCHIVE_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        out = _outcome_from_error(e)
        out.reason = f"archive: {out.reason}"
        out.service_failure = out.status != Status.REJECTED_UNSAFE
        return out
    out = _outcome_from_response(r)
    out.archive_timestamp = ts
    out.final_url = url
    if out.status == Status.OK:
        out.reason += f" (archived copy from {ts[:4]}-{ts[4:6]}-{ts[6:8]})"
    return out
