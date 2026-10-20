"""Stage 6: render the brief (Markdown, HTML, JSON) from analysis.json; the bottom line is LLM-written and code-checked."""

from .render import write_brief

__all__ = ["write_brief"]
