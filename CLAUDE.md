# Multi-source research brief agent (competition build)

Given a topic and 3–5 URLs, the agent fetches the sources, extracts claims, reasons across sources
(consensus / contradictions / outliers / gaps), and writes a structured brief with citations.
Time budget: ~2 hours. Finish something good rather than something ambitious and half-done.

## AI-workflow logging (part of the submission)

- Every prompt is auto-logged to `ai-log/prompt_log.md`, and transcripts to `ai-log/transcripts/`,
  by hooks in `.claude/settings.json`. Don't edit those files by hand.
- After each milestone (a stage working, a design decision made, an approach dropped), run the
  `log-prompts` skill to update the curated `prompts.md`.
- When something is tried and fails, add a line to `DECISIONS.log`: `tried X → failed because Y → did Z`.
  It feeds the Loom walkthrough and NOTES.md.
