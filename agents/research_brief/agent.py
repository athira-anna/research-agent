"""Entry point for Google ADK:

    adk web agents                      # browser UI; pick "research_brief"
    adk run agents/research_brief       # terminal chat

Send a topic and 3–5 URLs (or an input file such as inputs/minimum_wage.json). The run log appears as
messages, one per stage, and the finished brief is published as the artifact `brief.html`.
"""

import sys
from pathlib import Path

# The pipeline lives in the brief_agent package at the project root, two levels up.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from brief_agent.agents import build_root_agent  # noqa: E402

root_agent = build_root_agent()
