"""Google ADK orchestration. The processing stages are plain Python functions (stages.py); ADK provides the
structure, the run log and the link to the finished brief:

    research_brief (SequentialAgent)
      ├─ intake          reads the chat message: topic + URLs (or an input file); checks the API key  (intake.py)
      ├─ plan_and_fetch (ParallelAgent)
      │    ├─ plan       stage 0: topic → research questions
      │    └─ fetch      stage 1: URLs → raw pages
      ├─ clean           stage 2: clean + triage
      ├─ extract         stage 3: claims + checks
      ├─ align           stage 4: shared scope terms, clusters, origins
      ├─ judge           stage 5: pair/cluster labels, strength, gaps
      ├─ bottom_line (LoopAgent, max 2 rounds)
      │    ├─ bottom_line_writer   LlmAgent: writes 3–5 bullets from the findings
      │    └─ bottom_line_checker  code: checks the bullets; feedback → retry, else template fallback
      └─ render          stage 6: brief.html in the run folder, linked from the chat (opens in a new tab)

The chat shows a short run log: a line as each stage starts, how many links were fetched and are usable
(with any dropped links and why), and the "Brief ready" link. Full details go to the terminal log.
The order is fixed, so code (not an LLM) decides it; the LLM is used only where language judgement is
needed, and its output is checked by code.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import AsyncGenerator, Awaitable, Callable
from pathlib import Path

from google.adk.agents import BaseAgent, LlmAgent, LoopAgent, ParallelAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
from pydantic import ConfigDict

from .brief.bottom_line import INSTRUCTION, MIN_BULLETS, BottomLineDraft, check_bullets, feedback_text, template_bullets
from .events import make_event
from .intake import IntakeAgent
from .llm import has_api_key
from .stages import STAGES, bottom_line_input_for

log = logging.getLogger("brief_agent")
StageFn = Callable[[dict], Awaitable[dict]]


# --- one pipeline stage -----------------------------------------------------------------------------

class StageAgent(BaseAgent):
    """Runs one plain-Python stage function and reports it in the chat: a "▶" line when it starts, then only
    what the user needs (link counts, dropped links, a stop reason, the brief link). Full details go to the
    terminal log. A stage error never crashes the run: it's recorded, and if the stage is essential, later
    stages are told to stop (the brief still explains what happened)."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    run_fn: StageFn
    title: str = ""
    essential: bool = True

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("intake_failed") or (state.get("stop") and self.name != "render"):
            return
        title = self.title or self.name
        log.info("%s: started", title)
        yield make_event(self, ctx, f"▶ {title}")

        t0 = time.monotonic()
        try:
            summary = await self.run_fn(dict(state))
        except Exception as e:  # noqa: BLE001
            log.exception("stage %s failed", self.name)
            summary = {"error": f"{type(e).__name__}: {e}"}
        log.info("%s: finished in %.1f s — %s", title, time.monotonic() - t0, summary.get("line") or summary.get("error"))
        for detail in summary.pop("details", []):
            log.info("    %s", detail)

        delta = dict(summary.pop("state", {}))   # values later agents need
        if summary.get("stop"):
            delta["stop"] = summary["stop"]
        elif summary.get("error") and self.essential:
            delta["stop"] = f"{self.name} failed: {summary['error'][:200]}"

        brief = summary.pop("brief", None)
        lines = [summary.pop("screen")] if summary.get("screen") else []
        if summary.get("error"):
            lines.append(f"✗ {title} failed: {summary['error'][:200]}")
        if delta.get("stop") and self.name != "render":
            lines.append(f"Stopping here: {delta['stop']}. The brief will say why.")
        if brief:
            lines.append(_brief_ready(brief))
        if lines or delta:   # a silent stage posts nothing (no empty rows in adk web)
            yield make_event(self, ctx, "\n".join(lines), delta)
        if brief and not brief.get("url") and ctx.artifact_service is not None:
            # Fallback when the local viewer couldn't start: attach the file (adk web offers it as a download).
            yield make_event(self, ctx, artifact_delta=await _publish(ctx, brief))


def _brief_ready(brief: dict) -> str:
    """The run's last chat message: "Brief ready", linked to the brief in a new browser tab (served by
    viewer.py), or the file path if the viewer couldn't start."""
    if brief.get("url"):
        return f'<a href="{brief["url"]}" target="_blank" rel="noopener"><strong>Brief ready</strong></a>'
    return f"**Brief ready:** `{brief['path']}`"


async def _publish(ctx: InvocationContext, brief: dict) -> dict:
    """Save the brief as an ADK artifact (only used when the local viewer couldn't start)."""
    data = Path(brief["path"]).read_bytes()
    version = await ctx.artifact_service.save_artifact(
        app_name=ctx.app_name, user_id=ctx.user_id, session_id=ctx.session.id, filename=brief["filename"],
        artifact=types.Part(inline_data=types.Blob(mime_type=brief["mime_type"], data=data,
                                                   display_name=brief["filename"])))
    return {brief["filename"]: version}


# --- the bottom line: LLM writer + code checker, in a LoopAgent ---------------------------------------

class DraftHandoff:
    """Passes the writer's draft (or its error) to the checker directly, per run (invocation), instead of
    through a chat event: the writer's raw JSON reply has no place in the user's chat log."""

    def __init__(self) -> None:
        self._entries: dict[str, dict] = {}

    def put(self, invocation_id: str, **entry) -> None:
        self._entries[invocation_id] = entry

    def take(self, invocation_id: str) -> dict:
        return self._entries.pop(invocation_id, {})


class GuardedWriter(BaseAgent):
    """Runs the LLM writer silently and hands its draft to the checker; if the model call fails (quota,
    outage), hands over the error instead of crashing the run. Skips the LLM entirely when there's nothing to
    summarise, no key, or the run is stopping. The only chat line is "▶ Writing the summary"."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    writer: BaseAgent   # the LlmAgent in production; tests pass a fake
    handoff: DraftHandoff
    needs_api_key: bool = True

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("intake_failed") or state.get("stop") or state.get("no_llm"):
            return
        run_dir = Path(state["run_dir"])
        if not (run_dir / "analysis.json").exists():
            return
        if self.needs_api_key and not has_api_key():
            # Skip before the SDK client is created: creating it without a key raises, and its half-built
            # client later logs "Task exception was never retrieved" tracebacks on shutdown.
            log.info("bottom line writer: skipped (no API key)")
            self.handoff.put(ctx.invocation_id, error="no API key")
            return
        if not state.get("bottom_line_rounds"):   # first round: announce it, and give the writer its input
            log.info("bottom line writer: started")
            yield make_event(self, ctx, "▶ Writing the summary", {"bottom_line_input": bottom_line_input_for(run_dir)})
        draft = None
        try:
            # The writer's events are not passed on: its reply is raw JSON. Its draft (output_key) is in each
            # event's state delta, so it's read from there and handed to the checker.
            async for event in self.writer.run_async(ctx):
                draft = (event.actions.state_delta or {}).get("bottom_line_draft", draft)
        except Exception as e:  # noqa: BLE001
            msg = f"{type(e).__name__}: {str(e)[:120]}"
            log.warning("bottom line writer failed (%s)", msg)
            self.handoff.put(ctx.invocation_id, error=msg)
            return
        log.info("bottom line writer: draft with %d bullets", len((draft or {}).get("bullets", [])))
        self.handoff.put(ctx.invocation_id, draft=draft)


class BottomLineChecker(BaseAgent):
    """Critic for the LoopAgent: accept valid bullets, otherwise give feedback for one retry, then fall back
    to template bullets. Posts one short chat line; the reasons go to the terminal log."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    handoff: DraftHandoff
    max_rounds: int = 2

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        state = ctx.session.state
        if state.get("intake_failed") or state.get("stop"):
            return
        path = Path(state["run_dir"]) / "analysis.json"
        if not path.exists():
            return
        handed = self.handoff.take(ctx.invocation_id)
        verdict, chat, log_line, delta = self._decide(state, json.loads(path.read_text()),
                                                      handed.get("draft"), handed.get("error"))
        log.info("bottom line: %s", log_line)
        yield make_event(self, ctx, chat, delta, escalate=verdict != "retry")

    def _decide(self, state: dict, analysis: dict, draft: dict | None,
                writer_error: str | None) -> tuple[str, str, str, dict]:
        """(verdict, chat line, log line, state delta); verdict is "accept", "template" or "retry"."""
        rounds = (state.get("bottom_line_rounds") or 0) + 1
        bullets, problems = check_bullets(draft.get("bullets", []), analysis) if draft else ([], ["no bullets were written"])
        template = {"source": "template", "bullets": template_bullets(analysis)}

        if state.get("no_llm") or writer_error:
            why = "no LLM" if state.get("no_llm") else f"writer unavailable: {writer_error}"
            return ("template", "✓ Summary: auto-generated from the findings", f"template ({why})",
                    {"bottom_line": template | {"dropped": []}, "bottom_line_rounds": rounds})
        if len(bullets) >= MIN_BULLETS:
            return ("accept", "✓ Summary written", f"accepted ({len(bullets)} bullets, round {rounds})",
                    {"bottom_line": {"source": "llm", "bullets": bullets, "dropped": problems},
                     "bottom_line_rounds": rounds})
        if rounds >= self.max_rounds:
            return ("template", "✓ Summary: auto-generated from the findings",
                    f"template fallback ({'; '.join(problems)[:150]})",
                    {"bottom_line": template | {"dropped": problems}, "bottom_line_rounds": rounds})
        return ("retry", "↻ Rewriting the summary",
                f"{len(problems)} problems, asking the writer for a rewrite: {problems[:5]}",
                {"bottom_line_feedback": feedback_text(problems), "bottom_line_rounds": rounds})


# --- assembly ---------------------------------------------------------------------------------------

def build_pipeline(stages: dict[str, StageFn], summary_model: str, writer: BaseAgent | None = None,
                   with_intake: bool = True) -> SequentialAgent:
    writer = writer or LlmAgent(
        name="bottom_line_writer", model=summary_model, instruction=INSTRUCTION,
        output_schema=BottomLineDraft, output_key="bottom_line_draft", include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0),
        description="Writes the brief's bottom line from checked findings.",
    )
    handoff = DraftHandoff()   # writer → checker, outside the chat
    steps: list[BaseAgent] = [IntakeAgent(name="intake", description="Reads topic + URLs from the message")] \
        if with_intake else []
    steps += [
        ParallelAgent(name="plan_and_fetch", sub_agents=[
            StageAgent(name="plan", run_fn=stages["plan"], title="Stage 0 · Research plan"),
            StageAgent(name="fetch", run_fn=stages["fetch"], title="Stage 1 · Fetch sources"),
        ]),
        StageAgent(name="clean", run_fn=stages["clean"], title="Stage 2 · Clean and triage"),
        StageAgent(name="extract", run_fn=stages["extract"], title="Stage 3 · Extract and check claims"),
        StageAgent(name="align", run_fn=stages["align"], title="Stage 4 · Align across sources"),
        StageAgent(name="judge", run_fn=stages["judge"], title="Stage 5 · Judge agreement and gaps"),
        LoopAgent(name="bottom_line", max_iterations=2, sub_agents=[
            GuardedWriter(name="bottom_line_guard", writer=writer, handoff=handoff,
                          needs_api_key=isinstance(writer, LlmAgent)),
            BottomLineChecker(name="bottom_line_checker", handoff=handoff),
        ]),
        StageAgent(name="render", run_fn=stages["render"], title="Stage 6 · Write the brief"),
    ]
    return SequentialAgent(
        name="research_brief",
        description="Send a topic and 3–5 URLs; get a research brief with consensus, contradictions, gaps "
                    "and citations (brief.html).",
        sub_agents=steps,
    )


def build_root_agent() -> SequentialAgent:
    """The agent `adk run` / `adk web` load (see agents/research_brief/agent.py)."""
    return build_pipeline(STAGES, os.environ.get("BRIEF_SUMMARY_MODEL", "gemini-flash-lite-latest"))
