"""A tiny local web server so the brief opens in a browser tab.

ADK's dev UI deliberately turns HTML artifacts into downloads, so instead the chat links to
http://127.0.0.1:<port>/<run-id>/brief.html served from here. It listens on localhost only and serves
nothing but runs/<run-id>/brief.html files: no other paths, no directory listings.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import PROJECT_ROOT

log = logging.getLogger("brief_agent")
RUNS_DIR = PROJECT_ROOT / "runs"
_PATH = re.compile(r"^/([A-Za-z0-9_.-]+)/brief\.html$")
_lock = threading.Lock()
_base_url: str | None = None


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (http.server API)
        m = _PATH.match(self.path.split("?", 1)[0])
        file = (RUNS_DIR / m.group(1) / "brief.html").resolve() if m else None
        if not file or file.parent.parent != RUNS_DIR.resolve() or not file.is_file():
            self.send_error(404)
            return
        body = file.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:   # keep the terminal for the run log
        pass


def ensure_viewer() -> str | None:
    """Start the viewer once per process; return its base URL, or None if no port could be opened."""
    global _base_url
    with _lock:
        if _base_url:
            return _base_url
        port = int(os.environ.get("BRIEF_VIEW_PORT", "8766"))
        for p in range(port, port + 10):
            try:
                server = ThreadingHTTPServer(("127.0.0.1", p), _Handler)
            except OSError:
                continue
            threading.Thread(target=server.serve_forever, daemon=True, name="brief-viewer").start()
            _base_url = f"http://127.0.0.1:{p}"
            log.info("brief viewer serving runs/*/brief.html at %s", _base_url)
            return _base_url
        log.warning("brief viewer: no free port in %d–%d", port, port + 9)
        return None


def brief_url(run_dir: Path) -> str | None:
    base = ensure_viewer()
    if not base or run_dir.resolve().parent != RUNS_DIR.resolve():
        return None
    return f"{base}/{run_dir.name}/brief.html"
