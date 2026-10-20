"""The first agent of a run: turns the user's chat message into a topic, URLs and a fresh run folder,
or explains clearly why it can't start (no URLs, no API key)."""

from __future__ import annotations

import json
import logging
import re
from collections.abc import AsyncGenerator
from datetime import datetime
from pathlib import Path

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event

from .config import PROJECT_ROOT
from .events import make_event
from .llm import MISSING_KEY_HELP, has_api_key
from .plan.topic import TopicError, validate_topic

log = logging.getLogger("brief_agent")

USAGE = """Send a topic and 3–5 source URLs, for example:

    Topic: Employment effects of raising the minimum wage
    https://www.npr.org/2019/07/08/739607964/...
    https://www.epi.org/blog/most-minimum-wage-studies-have-found-little-or-no-job-loss/

or the path of an input file such as `inputs/minimum_wage.json`.
Options you can add to the message: `--no-llm` (no model calls), `--refresh` (ignore the fetch cache),
`--no-external` (don't use the reader/archive fallback services)."""

# Per-run state that must not leak from one request into the next in the same chat session.
RUN_STATE_KEYS = ("stop", "bottom_line", "bottom_line_draft", "bottom_line_feedback", "bottom_line_rounds",
                  "bottom_line_input", "intake_failed")

_URL = re.compile(r"https?://[^\s<>\"'`)\]]+")
_FLAGS = {"--no-llm": "no_llm", "--refresh": "refresh", "--no-external": "no_external"}
_REQUEST_WORDING = re.compile(
    r"^(?:please\s+)?(?:(?:i|we)\s+(?:need|want|would like)\s+(?:you\s+)?to\s+|can you\s+|could you\s+)?"
    r"(?:create|write|make|generate|prepare|produce|build|research)?\s*(?:me\s+)?(?:a\s+|an\s+)?"
    r"(?:research\s+)?(?:brief|report|summary)?\s*(?:on|about|for|of)?\s*:?\s*", re.I)
_SOURCES_TAIL = re.compile(r"\b(?:based on|using|from|with)?\s*(?:the\s+|these\s+|those\s+)?(?:following\s+|below\s+)?"
                           r"(?:sources?|urls?|links?|articles?)\s*(?:below)?\s*:?.*$", re.I | re.S)


# --- reading the message ----------------------------------------------------------------------------

def parse_request(text: str) -> tuple[str, list[str], dict]:
    """(topic, urls, flags) from a chat message: free text with URLs, `Topic:` lines, JSON, or an input-file path."""
    flags = {key: True for flag, key in _FLAGS.items() if flag in text}
    for flag in _FLAGS:
        text = text.replace(flag, " ")
    text = text.strip()
    data = _json_payload(text)
    if data is not None:
        return str(data.get("topic", "")).strip(), [str(u) for u in data.get("urls", [])], flags
    urls = [u.rstrip(".,;:") for u in _URL.findall(text)]
    return _topic_from(_URL.sub(" ", text)), list(dict.fromkeys(urls)), flags


def _json_payload(text: str) -> dict | None:
    """An input file named in the message (e.g. inputs/minimum_wage.json), or JSON pasted directly."""
    m = re.search(r"(\S+\.json)\b", text)
    if m:
        for base in (PROJECT_ROOT, Path.cwd()):
            path = (base / m.group(1)).resolve()
            if path.is_file():
                return json.loads(path.read_text())
    return json.loads(text) if text.startswith("{") else None


def _topic_from(rest: str) -> str:
    """The topic from what's left after removing URLs: a quoted phrase if there is one, otherwise the text
    without request wording ("I need to create a brief on ...", "based on sources: ...") and stray quotes."""
    quoted = [q.strip() for q in re.findall(r"[\"“”']([^\"“”']{3,})[\"“”']", rest) if len(q.split()) >= 2]
    if quoted:
        return quoted[0]
    rest = re.sub(r"(?im)^\s*(topic|urls?|sources?|links?)\s*:\s*", "", rest)
    topic = " ".join(ln.strip(" -*•\t") for ln in rest.splitlines() if ln.strip(" -*•\t"))
    without_tail = _SOURCES_TAIL.sub("", topic)
    if len(without_tail.split()) >= 2:
        topic = without_tail
    topic = _REQUEST_WORDING.sub("", topic, count=1)
    return re.sub(r"\s+", " ", re.sub(r"[\"“”,;:]+\s*$|^[\"“”,;:\s]+", "", topic)).strip(" .")


# --- the agent --------------------------------------------------------------------------------------

class IntakeAgent(BaseAgent):
    """Turns the user's message into a run: topic, URLs, a fresh run folder. Refuses clearly when it can't."""

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        text = "".join(p.text or "" for p in (ctx.user_content.parts if ctx.user_content else []))
        reset = {k: None for k in RUN_STATE_KEYS}
        try:
            topic, urls, flags = parse_request(text)
            topic = validate_topic(topic)
            if not urls:
                raise TopicError("no source URLs found in the message")
        except (TopicError, ValueError, OSError) as e:
            log.info("intake: can't start a run (%s)", e)
            yield make_event(self, ctx, f"Can't start a run: {e}.\n\n{USAGE}",
                             reset | {"stop": "invalid request", "intake_failed": True})
            return
        if not flags.get("no_llm") and not has_api_key():
            yield make_event(self, ctx, f"Can't start a run: {MISSING_KEY_HELP}",
                             reset | {"stop": "no API key", "intake_failed": True})
            return

        run_dir = PROJECT_ROOT / "runs" / datetime.now().strftime("%Y%m%d-%H%M%S")
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "input.json").write_text(json.dumps({"topic": topic, "urls": urls}, indent=2))
        log.info("intake: topic=%r, %d URLs, run folder %s", topic, len(urls), run_dir)
        options = f" · options: {', '.join(sorted(flags))}" if flags else ""
        yield make_event(self, ctx, f"**Research brief:** {topic} · {len(urls)} links{options}",
                         reset | {"topic": topic, "urls": urls, "run_dir": str(run_dir), "bottom_line_rounds": 0,
                                  "serve_brief": True, "no_llm": bool(flags.get("no_llm")),
                                  "refresh": bool(flags.get("refresh")),
                                  "no_external": bool(flags.get("no_external"))})
