"""Quick rule-based check: did we get the real page, or a paywall / bot wall / JS shell?

No LLM here: rules are fast, free, predictable, and every verdict comes with a reason
that ends up in the source's `attempts` log.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass

import trafilatura
from trafilatura.settings import use_config

from .models import Status

# trafilatura's default refuses to return anything under 250 chars; a short news brief is still content.
TRAFILATURA_CONFIG = use_config()
TRAFILATURA_CONFIG.set("DEFAULT", "MIN_EXTRACTED_SIZE", "10")
TRAFILATURA_CONFIG.set("DEFAULT", "MIN_OUTPUT_SIZE", "10")

# Cookie-consent managers (Cookiebot, OneTrust, Didomi, Usercentrics, ...) inject huge dialogs that text
# extractors can mistake for the article. They're removed before extraction.
_LOWER = "translate({a},'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz')"
_CONSENT_WORDS = ("onetrust", "cookiebot", "cookie", "consent", "didomi", "usercentrics", "truste", "sp_message",
                  "gdpr", "cmp-container", "qc-cmp")
CONSENT_PRUNE_XPATH = "//*[" + " or ".join(
    f"contains({_LOWER.format(a=attr)},'{w}')" for w in _CONSENT_WORDS for attr in ("@id", "@class")) + "]"
_CONSENT_TEXT = re.compile(r"cookie|storage duration|consent|tracking|third[- ]party|vendor|opt[- ]out|"
                           r"privacy (?:policy|settings)|legitimate interest|personali[sz]ed ads", re.I)

# Under this, a page *might* be a JS shell or a block page, so stage 1 tries the next method.
MIN_TEXT_CHARS = 500
# If every method only ever finds a short page with no paywall/challenge signs, accept it
# (marked "short") and let stage 2 decide. Below this it really is empty.
SHORT_PAGE_MIN_CHARS = 150
# Paywall signals only count when the page also has little text: many sites ship the
# full article in the HTML (for search engines) and hide it with CSS. We can use that.
PAYWALL_MAX_TEXT_CHARS = 1500


@dataclass
class Verdict:
    status: Status
    reason: str
    text_chars: int
    detail: str | None = None


# --- bot challenge signals ---------------------------------------------------------

# Strong: enough on their own.
_CHALLENGE_TITLES = [
    (re.compile(r"just a moment\.\.\.|attention required! \| cloudflare", re.I), "cloudflare"),
    (re.compile(r"^\s*access denied\s*$", re.I), "akamai"),
    (re.compile(r"^\s*(pardon our interruption|are you a robot\??)\s*$", re.I), "generic"),
]

# Weak: only count when the page also has little readable text, because normal pages
# load these too (e.g. a reCAPTCHA in a comment form, Cloudflare's bot-management script).
_CHALLENGE_MARKERS = [
    ("cloudflare", ["/cdn-cgi/challenge-platform", "cf-turnstile", "cf_chl_"]),
    ("recaptcha", ["g-recaptcha", "google.com/recaptcha"]),
    ("hcaptcha", ["h-captcha", "hcaptcha.com"]),
    ("datadome", ["captcha-delivery.com", "datadome"]),
    ("perimeterx", ["px-captcha", "press &amp; hold", "press & hold"]),
    ("imperva", ["_incapsula_resource", "incapsula incident"]),
    ("akamai", ["reference #", "errors.edgesuite.net"]),
]
_CHALLENGE_PHRASES = [
    "verify you are human",
    "verifying you are human",
    "are you a robot",
    "unusual traffic from your network",
    "checking your browser",
    "enable cookies to continue",
    "complete the security check",
]

# --- paywall signals ---------------------------------------------------------------

_NOT_FREE_RE = re.compile(r'"isAccessibleForFree"\s*:\s*"?false', re.I)
_PAYWALL_PHRASES = [
    "subscribe to continue",
    "subscribe to read",
    "already a subscriber",
    "this article is for subscribers",
    "for subscribers only",
    "to continue reading",
    "create a free account to continue",
    "you have reached your free article limit",
    "you've reached your limit of free articles",
]

# --- JavaScript shell signals ------------------------------------------------------

_NOSCRIPT_JS_RE = re.compile(r"<noscript[^>]*>[^<]*(enable|turn on)\s+javascript", re.I)
_EMPTY_MOUNT_RE = re.compile(r'<div\s+id="(root|app|__next|__nuxt)"[^>]*>\s*</div>', re.I)

_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def extract_text(html: str) -> str:
    return trafilatura.extract(html, include_comments=False, include_tables=True,
                               config=TRAFILATURA_CONFIG, prune_xpath=[CONSENT_PRUNE_XPATH]) or ""


def looks_like_consent(text: str) -> bool:
    """Text that is mostly cookie/consent language (≥ 4 such terms per 100 words)."""
    words = len(text.split())
    return words > 0 and len(_CONSENT_TEXT.findall(text)) / words >= 0.04


def _title(html: str) -> str:
    m = _TITLE_RE.search(html[:20000])
    return m.group(1).strip() if m else ""


def _strong_challenge(html: str, headers: Mapping[str, str]) -> str | None:
    if headers.get("cf-mitigated", "").lower() == "challenge":
        return "cloudflare"
    title = _title(html)
    for pattern, provider in _CHALLENGE_TITLES:
        if pattern.search(title):
            return provider
    return None


def _weak_challenge(html_lower: str) -> str | None:
    for provider, markers in _CHALLENGE_MARKERS:
        if any(m in html_lower for m in markers):
            return provider
    if any(p in html_lower for p in _CHALLENGE_PHRASES):
        return "generic"
    return None


def assess_html(html: str, headers: Mapping[str, str] | None = None) -> Verdict:
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    lower = html.lower()
    text = extract_text(html)
    text_chars = len(text)
    low_text = text_chars < MIN_TEXT_CHARS

    provider = _strong_challenge(html, headers)
    if provider:
        return Verdict(Status.BOT_CHALLENGE, f"bot challenge page ({provider})", text_chars, provider)
    if low_text:
        provider = _weak_challenge(lower)
        if provider:
            return Verdict(Status.BOT_CHALLENGE, f"bot challenge markers ({provider}) and only {text_chars} chars of text", text_chars, provider)

    if text_chars and looks_like_consent(text):
        return Verdict(Status.CONSENT_WALL, f"readable text is a cookie-consent dialog ({text_chars} chars)", text_chars)

    if text_chars < PAYWALL_MAX_TEXT_CHARS:
        if _NOT_FREE_RE.search(html):
            return Verdict(Status.PAYWALLED, f'page metadata says "isAccessibleForFree": false; {text_chars} chars of text', text_chars)
        phrase = next((p for p in _PAYWALL_PHRASES if p in lower), None)
        if phrase:
            return Verdict(Status.PAYWALLED, f'paywall phrase "{phrase}"; {text_chars} chars of text', text_chars)

    if low_text:
        if _NOSCRIPT_JS_RE.search(html) or _EMPTY_MOUNT_RE.search(html):
            return Verdict(Status.JS_BLOCKED, f"JavaScript app shell; only {text_chars} chars of text", text_chars)
        return Verdict(Status.EMPTY, f"too little text ({text_chars} chars)", text_chars)

    return Verdict(Status.OK, f"{text_chars} chars of text", text_chars)


def assess_text(text: str) -> Verdict:
    """Same idea for plain text (e.g. the reader service's markdown output)."""
    lower = text.lower()
    chars = len(text.strip())
    if chars and looks_like_consent(text):
        return Verdict(Status.CONSENT_WALL, f"text is a cookie-consent dialog ({chars} chars)", chars)
    if chars < MIN_TEXT_CHARS:
        provider = _weak_challenge(lower)
        if provider:
            return Verdict(Status.BOT_CHALLENGE, f"bot challenge text ({provider})", chars, provider)
    if chars < PAYWALL_MAX_TEXT_CHARS:
        phrase = next((p for p in _PAYWALL_PHRASES if p in lower), None)
        if phrase:
            return Verdict(Status.PAYWALLED, f'paywall phrase "{phrase}"; {chars} chars of text', chars)
    if chars < MIN_TEXT_CHARS:
        return Verdict(Status.EMPTY, f"too little text ({chars} chars)", chars)
    return Verdict(Status.OK, f"{chars} chars of text", chars)
