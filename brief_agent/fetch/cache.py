"""On-disk cache of successful fetches, keyed by a hash of the URL (never the URL itself)."""

from __future__ import annotations

import json
from pathlib import Path

from .models import SourceRecord, Status, url_hash

_EXT = {"html": "html", "pdf": "pdf", "text": "txt"}


class Cache:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _key(self, url: str) -> str:
        return url_hash(url)

    def load(self, url: str) -> tuple[SourceRecord, bytes] | None:
        meta = self.root / f"{self._key(url)}.json"
        if not meta.exists():
            return None
        try:
            record = SourceRecord.from_dict(json.loads(meta.read_text()))
            content = (self.root / f"{self._key(url)}.{_EXT[record.content_type]}").read_bytes()
        except (OSError, ValueError, KeyError, TypeError):
            return None  # broken cache entry: just fetch again
        return record, content

    def save(self, record: SourceRecord, content: bytes) -> None:
        # Only successes are cached; failures are often temporary and get retried next run.
        if record.status != Status.OK or record.content_type not in _EXT:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        key = self._key(record.url)
        (self.root / f"{key}.{_EXT[record.content_type]}").write_bytes(content)
        (self.root / f"{key}.json").write_text(json.dumps(record.to_dict(), indent=2))


def extension(content_type: str) -> str:
    return _EXT.get(content_type, "bin")
