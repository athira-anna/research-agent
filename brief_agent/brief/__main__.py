"""Stage 6 on its own: write brief.html for a run folder that has analysis.json (from
`python -m brief_agent.judge` or an ADK run). Uses the same ADK agents as the full pipeline: the bottom-line
writer/checker loop, then rendering.   python -m brief_agent.brief --run-dir runs/<id> [--no-llm]"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from google.adk.runners import InMemoryRunner
from google.genai import types

from ..agents import build_pipeline
from ..stages import bottom_line_input_for, render


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m brief_agent.brief", description=__doc__)
    p.add_argument("--run-dir", type=Path, required=True)
    p.add_argument("--no-llm", action="store_true", help="template bottom line, no LLM")
    args = p.parse_args()
    topic = json.loads((args.run_dir / "analysis.json").read_text())["topic"]

    async def reuse(_state: dict) -> dict:
        return {"line": "reused from the run folder"}

    async def judge_state(_state: dict) -> dict:
        return {"line": "reused analysis.json", "state": {"bottom_line_input": bottom_line_input_for(args.run_dir)}}

    stages = {n: reuse for n in ("plan", "fetch", "clean", "extract", "align")} | {"judge": judge_state,
                                                                                    "render": render}
    root = build_pipeline(stages, os.environ.get("BRIEF_SUMMARY_MODEL", "gemini-flash-lite-latest"),
                          with_intake=False)

    async def go() -> None:
        runner = InMemoryRunner(agent=root, app_name="research_brief")
        session = await runner.session_service.create_session(
            app_name="research_brief", user_id="analyst",
            state={"topic": topic, "urls": [], "run_dir": str(args.run_dir.resolve()), "no_llm": args.no_llm})
        msg = types.Content(role="user", parts=[types.Part(text="Rewrite the brief")])
        async for ev in runner.run_async(user_id="analyst", session_id=session.id, new_message=msg):
            if ev.author.startswith(("bottom_line", "render")) and ev.content and ev.content.parts:
                text = "".join(x.text or "" for x in ev.content.parts)
                if text:
                    print(f"[{ev.author}] {text}")

    asyncio.run(go())


if __name__ == "__main__":
    main()
