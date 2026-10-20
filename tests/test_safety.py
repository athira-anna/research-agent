"""Safety tests. DNS is faked, so no real network requests are made."""

import asyncio
import gzip

import httpx
import pytest

from brief_agent.fetch import safety
from brief_agent.fetch.methods import _outcome_from_error, safe_get
from brief_agent.fetch.models import Status

FAKE_DNS = {
    "localhost": ["127.0.0.1", "::1"],
    "public.example": ["93.184.215.14"],
    "sneaky.example": ["10.0.0.5"],
    "mapped.example": ["::ffff:192.168.1.1"],
}


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    async def resolve(host):
        if host in FAKE_DNS:
            return FAKE_DNS[host]
        try:  # IP literals resolve to themselves
            safety.ipaddress.ip_address(host)
            return [host]
        except ValueError:
            raise safety.socket.gaierror("unknown host")
    monkeypatch.setattr(safety, "_resolve", resolve)


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize("url", [
    "http://localhost:8080/admin",
    "http://127.0.0.1/",
    "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/",
    "http://192.168.1.1/",
    "http://[::1]/",
    "http://sneaky.example/",
    "http://mapped.example/",
    "file:///etc/passwd",
    "ftp://public.example/file",
    "javascript:alert(1)",
    "data:text/html,<h1>hi</h1>",
    "not a url",
])
def test_rejects_unsafe(url):
    with pytest.raises(safety.UnsafeURL):
        run(safety.check_url(url))


def test_allows_public():
    run(safety.check_url("https://public.example/article"))


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)


def _get(handler, url="https://public.example/", **kw):
    async def go():
        async with _client(handler) as c:
            return await safe_get(c, url, **kw)
    return run(go())


def test_redirect_to_localhost_is_rejected():
    requested = []

    def handler(req):
        requested.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://localhost/admin"})

    with pytest.raises(safety.UnsafeURL):
        _get(handler)
    assert requested == ["https://public.example/"]  # never connected to localhost


def test_too_many_redirects():
    with pytest.raises(safety.UnsafeURL, match="redirects"):
        _get(lambda req: httpx.Response(302, headers={"location": "https://public.example/again"}))


def test_large_body_is_cut_off():
    big = b"<html>" + b"a" * (safety.MAX_HTML_BYTES + 1)
    with pytest.raises(safety.TooLarge):
        _get(lambda req: httpx.Response(200, headers={"content-type": "text/html"}, content=big))


def test_lying_content_length_rejected_early():
    with pytest.raises(safety.TooLarge, match="Content-Length"):
        _get(lambda req: httpx.Response(200, headers={"content-type": "text/html",
                                                      "content-length": str(50 * 1024 * 1024)}, content=b"x"))


def test_gzip_bomb_counts_unzipped_size():
    bomb = gzip.compress(b"\0" * (safety.MAX_HTML_BYTES + 1024))
    assert len(bomb) < 100_000  # tiny on the wire
    with pytest.raises(safety.TooLarge):
        _get(lambda req: httpx.Response(200, headers={"content-type": "text/html",
                                                      "content-encoding": "gzip"}, content=bomb))


def test_slow_drip_hits_total_time_limit():
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            while True:
                await asyncio.sleep(0.2)
                yield b"a"

    with pytest.raises(TimeoutError):
        _get(lambda req: httpx.Response(200, headers={"content-type": "text/html"}, stream=Drip()),
             total_timeout=1)
    assert _outcome_from_error(TimeoutError()).status == Status.TIMEOUT
