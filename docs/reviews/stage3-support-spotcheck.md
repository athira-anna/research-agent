# Stage 3 support check: manual spot check

Run: `runs/stage2-dev` (Wikipedia "Technological unemployment" + r/cscareerquestions), topic "AI and job losses",
extraction and support check both by `gemini-flash-lite-latest` (Flash was overloaded; this key has no Pro quota).
Each verdict was compared by hand with the quote and its block.

| Verdicts checked | Correct | Wrong | Notes |
|---|---|---|---|
| 17 kept claims (`support: full`) | 15 | 2 | |
| 2 claims dropped by the checker | 2 | 0 | |
| **Total** | **17 / 19 (≈ 89%)** | **2 (≈ 11%)** | |

**Correct catch worth noting:** "17 employees out of around 100 were let go…" was dropped as `number_misread`. The "100" came from a different block than the quote. Grounding alone would have kept it.

**Errors:**
1. "41% of jobs in Israel were at risk of being automated **within the next two decades**" kept as `observed`. It's a forecast. *Fixed in code:* "within the next", "in the coming" and "by the 2030s" were added to the future markers, so this is now flagged for the checker.
2. "There were around 100 employees" changed `observed` → `opinion` without the checker flagging modality. *Fixed in code:* modality changes now need an explicit `modality` flag.

**Problems found in the same review (not checker verdicts):**
- **Firms vs workers:** "41% of bosses" / "49% of firms" were tagged with a workers' job-loss metric, and the checker passed them. *Fixed in code:* what the number counts (`of_what`) is compared with what the metric counts.
- **Cap and counter-evidence:** the cap kept 4 country figures from one Oxford/Citi study and dropped the OECD's "only 9% at risk". *Fixed:* within a question, the cap takes one claim per origin before a second from any origin.
- **Decade timeframe:** "by the early 2030s" with years 2030–2033 was cleared because 2033 isn't written in the text. *Fixed:* a decade covers its years.
- **Fraction words:** "half of all white-collar workers" = 50% was dropped as `number_not_in_quote`. *Fixed:* common fraction words (half, a third, one in five…) and "x5" multipliers are recognised.

**Caveats:** 19 verdicts is a small sample, from one cheap model, on one topic. Treat ~11% as a rough figure, not a measured error rate.
