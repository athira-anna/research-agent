"""Multi-source research brief agent."""

from .config import load_env

# Before any submodule reads its settings (e.g. llm.TRIAGE_MODEL at import time).
load_env()
