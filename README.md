# Multi-source research brief agent

Give it a topic and 3–5 links. It reads the pages, pulls out claims, and writes one HTML brief: where the sources agree, where they differ, what nobody covered, and the exact quote behind each claim.

You need Python 3.11+ and a Gemini API key.

## 1. Set it up

From this folder:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp -n .env.example .env.local
```

`cp -n` leaves an existing `.env.local` alone, so it will not wipe a key you already saved.

Open `.env.local` and set:

```bash
GOOGLE_API_KEY=your-key-here
```

Get a key at https://aistudio.google.com/apikey. The other lines in `.env.local` can stay as they are.

## 2. Run it

Start the browser chat:

```bash
source .venv/bin/activate
adk web agents
```

Open http://localhost:8000 and pick **research_brief**.

Send one of these:

```text
inputs/minimum_wage.json
```

```text
Topic: Employment effects of raising the minimum wage
https://www.npr.org/2019/07/08/739607964/-15-minimum-wage-would-boost-17-million-workers-cut-1-3-million-jobs-cbo-says
https://reason.com/2019/07/08/cbo-says-15-federal-minimum-wage-will-cost-1-3-million-jobs/
https://www.epi.org/blog/most-minimum-wage-studies-have-found-little-or-no-job-loss/
```

```text
I need a brief on "How AI will change the job market" based on these sources: URL1, URL2, URL3
```

Put the topic in quotes when you write a sentence. You can also add a flag in the same message:

| Flag | What it does |
|---|---|
| `--no-llm` | Skip model calls. Stops after the pages are cleaned. |
| `--refresh` | Fetch the pages again and ignore the cache. |
| `--no-external` | Stay on this machine. Skip the reader and archive fallbacks. |

While it runs, the chat shows one line per stage, how many links were fetched, and any link that was dropped and why. The terminal where you started `adk web` has the full log.

When it finishes, click **Brief ready**. The brief opens in a new tab from `runs/<timestamp>/brief.html`. A small local viewer on http://127.0.0.1:8766 serves that file. ADK's own window would only offer it as a download.

If `GOOGLE_API_KEY` is missing, the agent stops and tells you to add it. `--no-llm` is the one message that still runs without a key.

To chat in the terminal instead of the browser:

```bash
adk run agents/research_brief
```

## 3. What the run does

The order is fixed. Plan and fetch run at the same time. The rest follow in order.

| Step | What it does |
|---|---|
| Intake | Reads the topic and links from your message. Checks the API key. |
| Plan | Turns the topic into research questions. It does not read the sources. |
| Fetch | Downloads each page: plain HTTP, then a browser, then a reader service, then a web archive. |
| Clean | Turns each page into numbered blocks, drops menus, and keeps the most relevant text. |
| Extract | Pulls claims. Checks that each quote is real and that it supports the claim. |
| Align | Groups claims about the same question and counts who is behind each one. |
| Judge | Labels agreement, disagreement, and gaps. |
| Bottom line | Writes a short summary. Code checks it, then a template is used if the check fails. |
| Render | Writes `brief.html` and posts the **Brief ready** link. |

A model that is unavailable (for example Pro on a free key) is skipped for the rest of that run. The brief says which models ran.

## Run one stage at a time

Use the same folder for every command so each stage can see the last one's files.

```bash
R=runs/my-run
T="AI and job losses"

python -m brief_agent.plan    --run-dir $R --topic "$T"
python -m brief_agent.fetch   --run-dir $R URL1 URL2 URL3
python -m brief_agent.clean   --run-dir $R --topic "$T"
python -m brief_agent.extract --run-dir $R --topic "$T"
python -m brief_agent.align   --run-dir $R
python -m brief_agent.judge   --run-dir $R
python -m brief_agent.brief   --run-dir $R
```

| Stage | Command | Useful flags | Output |
|---|---|---|---|
| Plan | `python -m brief_agent.plan --run-dir $R --topic "$T"` | `--replan`, `--plan-file mine.json`, `--no-llm` | `plan.json` |
| Fetch | `python -m brief_agent.fetch --run-dir $R URL1 URL2 URL3` | `--file urls.txt`, `--refresh`, `--no-external` | `sources_raw.json`, `raw/` |
| Clean | `python -m brief_agent.clean --run-dir $R --topic "$T"` | `--no-llm` | `sources.json`, `clean/` |
| Extract | `python -m brief_agent.extract --run-dir $R --topic "$T"` | `-v` | `claims.json`, `dropped_claims.json` |
| Align | `python -m brief_agent.align --run-dir $R` | `--no-llm` | `alignment.json` |
| Judge | `python -m brief_agent.judge --run-dir $R` | `--no-llm` | `analysis.json` |
| Brief | `python -m brief_agent.brief --run-dir $R` | `--no-llm` | `brief.html` |

Fetch, clean, and extract need the folder from the stage before them. The brief needs `analysis.json` from judge.

## Settings

Settings come from `.env.local` in this folder. A value already set in your shell wins.

| Variable | Purpose |
|---|---|
| `GOOGLE_API_KEY` | Gemini. Required for a full brief. |
| `BRIEF_PLANNER_MODEL`, `BRIEF_PLANNER_FALLBACK_MODELS` | Research plan. Default: Pro, then Flash, then Flash-Lite. |
| `BRIEF_EXTRACT_MODEL`, `BRIEF_EXTRACT_FALLBACK_MODELS` | Claim extraction. Default: Flash, then Flash-Lite. |
| `BRIEF_SUPPORT_MODEL` | Checks that a quote supports its claim. Default: Flash-Lite. |
| `BRIEF_REASON_MODEL`, `BRIEF_REASON_FALLBACK_MODELS` | Align and judge. Default: Pro, then Flash, then Flash-Lite. |
| `BRIEF_TRIAGE_MODEL` | Is this page on topic? Default: Flash-Lite. |
| `BRIEF_SUMMARY_MODEL` | Bottom-line writer. Default: Flash-Lite. |
| `BRIEF_AGENT_UA` | User-Agent for plain HTTP. Wikipedia wants contact info in it. |
| `JINA_API_KEY` | Optional. Higher limits for the reader-service fallback. |
| `BRIEF_VIEW_PORT` | Port for the brief viewer. Default 8766. The next free port is used if that one is taken. |

## Sample and tests

`inputs/minimum_wage.json` is a ready-made run: two articles repeat one CBO study, and cbo.gov is blocked by a bot wall. A finished example is in `docs/sample-briefs/`.

```bash
pytest
```

Tests that call Gemini run only when `GOOGLE_API_KEY` is set.

Notes on the design: `NOTES.md`. What was tried and changed: `DECISIONS.log`. Stage plans: `docs/plans/`.
