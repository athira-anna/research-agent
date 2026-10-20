# Stages 4–5: manual spot check of consensus / contested verdicts

Runs: `inputs/minimum_wage.json` and `inputs/ai_jobs.json`, all LLM steps on `gemini-flash-lite-latest`.
Every `consensus` and `contested` verdict was checked by hand against the claims and quotes.

| Verdict | Run | Correct? | Cause | Fix |
|---|---|---|---|---|
| consensus | ai-jobs | ❌ | "41% of jobs" vs "a quarter to half of the workload" agreed because the ranges overlapped | Numbers must count the same thing (`of_what`); otherwise `different_measures` |
| consensus | ai-jobs (re-run) | ❌ | The LLM judged "60% of jobs" and "workload" as agreeing | Code-determined different quantities override the LLM → `different_measures` |
| consensus | minimum-wage | ❌ | Catch-all cluster; the LLM called it "agree" because nothing conflicted | LLM-only agreement needs a shared subject; otherwise `unrelated` |
| contested | both | n/a | None produced | n/a |

**Result: 3 of 3 consensus verdicts were false positives, and all were fixed with regression tests.** No consensus or contested verdicts remain in the final runs.

**What the final briefs say instead** (spot-checked):
- AI jobs: exposure estimates are `complementary` (by place / part vs whole, 9 origins); job-creation vs displacement figures are `different_measures`.
- Minimum wage: CBO is one origin repeated by NPR and Reason (`single_origin`, "repeated by 2 sources"), and EPI's review is a separate single origin. The rest is `unresolved` (unspecified scope).

**Known limitation:** the minimum-wage brief doesn't label CBO's projected job losses vs EPI's "little or no job loss" evidence as a contradiction.
- Flash-Lite clustered them apart, and under our rules a forecast vs measured evidence is "not comparable" anyway.
- The bottom line does place them next to each other.
- Surfacing "projection vs evidence" tensions explicitly would be the next improvement to the judging rules.
