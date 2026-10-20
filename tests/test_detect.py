from pathlib import Path

import pytest

from brief_agent.fetch.detect import assess_html, assess_text
from brief_agent.fetch.models import Status

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str) -> str:
    return (FIXTURES / name).read_text()


@pytest.mark.parametrize("fixture, expected, detail", [
    ("normal_article.html", Status.OK, None),
    # False-alarm check: a real article that loads reCAPTCHA + Cloudflare scripts must stay ok.
    ("article_with_recaptcha_form.html", Status.OK, None),
    ("paywalled.html", Status.PAYWALLED, None),
    ("js_shell.html", Status.JS_BLOCKED, None),
    ("cloudflare_challenge.html", Status.BOT_CHALLENGE, "cloudflare"),
    ("datadome_challenge.html", Status.BOT_CHALLENGE, "datadome"),
])
def test_fixture_labels(fixture, expected, detail):
    v = assess_html(load(fixture))
    assert v.status == expected, v.reason
    if detail:
        assert v.detail == detail


def test_cloudflare_header_alone_marks_challenge():
    v = assess_html(load("normal_article.html"), {"cf-mitigated": "challenge"})
    assert v.status == Status.BOT_CHALLENGE
    assert v.detail == "cloudflare"


def test_empty_page():
    v = assess_html("<html><head><title>Hi</title></head><body><p>Hello.</p></body></html>")
    assert v.status == Status.EMPTY


def test_reader_text():
    assert assess_text("Title: x\n\n" + "Real article text. " * 60).status == Status.OK
    assert assess_text("Just a moment... Verify you are human").status == Status.BOT_CHALLENGE
    assert assess_text("Intro paragraph. Subscribe to continue reading.").status == Status.PAYWALLED
