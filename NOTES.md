# Notes

## Approach
Google ADK runs a fixed pipeline: plan + fetch in parallel, then clean → extract → align → judge → brief. Each stage is a plain Python function. **Code decides what code can decide;** the LLM handles judgement, and code checks its output before use. Claims are checked three ways: their quote must exist in the source, their number's meaning must match its wording, and the quote must support the claim. Agreement is judged per scope dimension and by independent origin, and every run ends in a brief.

## What I'd do differently for production
- **Postgres schema** (runs, sources, blocks, claims, clusters, findings) instead of JSON files per run.
- **MCP tools** for crawling, extraction and other common steps, so other agents can reuse them.
- **Semantic search with embeddings** instead of keywords: paragraph selection, duplicate sources/topics, clustering.
- **Whole sources, chunked,** instead of the first ~40k most relevant characters, within a cost budget.
- **Translation** of non-English sources (today they're only flagged).
- **LLM-call caching and lower latency:** more parallelism, smaller models for easy steps.
- **Stronger prompt-injection defences:** today page text is labelled as untrusted data, the extraction model has no tools, claims must quote real text, and AI-directed text is flagged. Add a classifier and red-team tests.
- **More URL security:** today private addresses are blocked on every redirect, downloads are size-capped, and the browser is sandboxed. Add DNS pinning, reputation checks, allow/block lists, rate limits.
- **An evaluation set and stronger models.** The hand spot checks found the support check 17/19 correct, and 3 false "consensus" verdicts (fixed). Everything ran on Flash-Lite.

## Trade-offs for the 2-hour budget
- **~40k characters per source, one extraction call per source,** no chunking.
- **At most 15 claims kept per source.** An assumption, not tuned.
- **Keyword scoring, not embeddings;** no translation; JSON files; no LLM caching.
- **Over budget overall,** spent on correctness checks rather than features (`DECISIONS.log`).
