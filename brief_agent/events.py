"""The one helper every agent uses to post to the run log / session state."""

from __future__ import annotations

from google.adk.agents import BaseAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event, EventActions
from google.genai import types


def make_event(agent: BaseAgent, ctx: InvocationContext, text: str = "", state_delta: dict | None = None,
               escalate: bool = False, artifact_delta: dict | None = None) -> Event:
    """An event from `agent`. Empty text → no chat message (state/control only)."""
    return Event(author=agent.name, invocation_id=ctx.invocation_id,
                 content=types.Content(role="model", parts=[types.Part(text=text)]) if text else None,
                 actions=EventActions(state_delta=state_delta or {}, escalate=escalate or None,
                                      artifact_delta=artifact_delta or {}))
