"""Load settings from .env.local (then .env) in the working directory and the project root.

Variables already set in the shell win, and empty values are ignored, so a blank
`GOOGLE_API_KEY=` in the file doesn't hide a key exported in your shell.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILES = (".env.local", ".env")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def load_env(directory: Path | None = None) -> list[Path]:
    """`adk web` may be started from another folder, so the project root is always checked too."""
    loaded = []
    dirs = [directory] if directory else list(dict.fromkeys([Path.cwd(), PROJECT_ROOT]))
    for path in (d / name for d in dirs for name in ENV_FILES):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key, value = key.strip().removeprefix("export ").strip(), value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            if value and key not in os.environ:
                os.environ[key] = value
        loaded.append(path)
    return loaded
