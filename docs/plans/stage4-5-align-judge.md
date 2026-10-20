# Stages 4 + 5: Align claims across sources, then judge them

## Why these stages exist

This is "the hard part" in the problem statement: **reasoning across sources, not stitching summaries together**. After stage 3 we have up to 15 checked claims per source, each with a question, scope, number meaning, modality, certainty and origin. Now:

- **Stage 4 (align):** figure out which claims, *from different sources*, are about **the same thing**, and put their scopes into shared terms so they can be compared.
- **Stage 5 (judge):** for each group, decide whether the sources **agree, contradict each other, or only seem to** (different scope, different measure, forecast vs fact). Then find **what nobody covers** (gaps).

**Main principle: code decides what code can decide, and the LLM only does what needs judgment.**
- **Code:** comparing numbers, checking scope overlap, counting independent origins, applying the labelling rules, finding gaps.
- **LLM:** standardising messy scope wording ("tech workers" = "software developers"?), grouping claims that say the same thing in different words, and judging agreement *in words*.
- **Code checks the LLM's labels,** so the LLM can never call something a contradiction that the rules forbid (e.g. US vs global, or a forecast vs a measured result).

**Input:** `claims.json`, `plan.json`, `sources.json`. In the test run that's 18 claims; at most 5 sources × 15 = 75.
**Output:** `alignment.json` (stage 4) and `analysis.json` (stage 5). The brief (stage 6) is written from `analysis.json`.

---

# Stage 4: Align

### Step 4.1: Group by question
Claims are already tagged with a plan question (or `other`), so stage 4 works **one question at a time**. That keeps each LLM call small (≤ ~25 claims) and focused. `other` is handled as its own group.

### Step 4.2: Put scopes into shared terms ("canonical" scope)
Each claim keeps the source's own wording (`raw`), and gets a standard version (`canonical`) next to it.

| Dimension | How | Example |
|---|---|---|
| **Timeframe** | **Code.** Years from `start`/`end`; decades ("2030s") → 2030–2039; empty → `unspecified` | "in 2024" → 2024–2024 |
| **Geography** | **Code first** (alias table: US / U.S. / United States / America → `US`; EU-28 → `EU`; "worldwide" → `global`), **then the LLM** for the rest | "the 28 member states" → `EU` |
| **Population** | **LLM**, using the plan's facet examples as preferred labels | "junior developers", "entry-level software engineers" → `software (entry-level)` |
| **Metric** | Mostly settled already: `value.metric_id` from stage 3. The LLM only labels claims without a number. | n/a |

- The LLM returns a **mapping table** (raw wording → canonical label), not rewritten claims. That makes it auditable, and the same wording always gets the same label.
- Also returned: **broader / narrower** relations between labels (`software (entry-level)` ⊂ `software` ⊂ `all workers`; `US` ⊂ `global`). Stage 5 needs these to tell "different scope" from "overlapping scope".
- Empty stays **`unspecified`**. It's never guessed (same principle as stage 3).

### Step 4.3: Group claims about the same thing ("clusters")
- A **cluster** = claims that answer **the same specific sub-question**, whether they agree or not.
  - Example: "share of jobs at high risk of automation", with Frey & Osborne 47% (US), Bruegel 54% (EU), OECD 9% (OECD average).
  - Each cluster gets a **neutral label**: the sub-question, never a conclusion.
- **One LLM call per question** (the same call as 4.2) returns clusters: `{cluster_id, label, claim_ids}`.
- **Code checks the result:**
  - Every claim is in exactly one cluster: missing claims become single-claim clusters; claims listed twice stay in their first cluster.
  - Unknown IDs are dropped.
  - Clusters may mix sources (that's the point), and may also hold several claims from one source.
- **Clustering ignores agreement,** so a contradiction can't be hidden by splitting the two sides into separate clusters. The prompt says this explicitly.

### Step 4.4: Origins (who is actually saying it)
- **Origin** = `attributed_to` (standardised: lowercase, "the" removed, near-identical names merged, e.g. "McKinsey" / "McKinsey Global Institute") or, if empty, the **source** itself.
- Sources marked as **duplicates in stage 2** count as one origin with their original.
- So "3 news sites repeat one McKinsey figure" = **1 origin, 3 sources**.

### Stage 4 output: `alignment.json`
```json
{ "questions": { "q1": {
    "scope_map": { "population": {"junior developers": "software (entry-level)"},
                   "geography":  {"the 28 member states": "EU"} },
    "scope_hierarchy": [["software (entry-level)", "software"], ["US", "global"]],
    "clusters": [ { "cluster_id": "q1-k1", "label": "Share of jobs at high risk of automation",
                    "claim_ids": ["s1-c001", "s1-c002", "s2-c004"] } ] } },
  "claims": { "s1-c001": { "canonical_scope": {"population": "all workers", "geography": "US",
                                               "timeframe": [2013, 2013]},
                           "origin": "frey & osborne" } } }
```

---

# Stage 5: Judge

### Step 5.1: Compare claims pair by pair inside each cluster (code)
For every pair of claims **from different origins** in a cluster, code works out four facts.

#### Scope: one relation per dimension (not one combined value)
Scope is compared **separately for population, geography and timeframe**. (The metric is handled by the measure relation below.) Each dimension gets its own relation, and the pair keeps all three:

| Relation | Meaning | Population / geography | Timeframe |
|---|---|---|---|
| `same` | Same canonical label | `software` vs `software` | 2024 vs 2024 |
| `nested` | One is part of the other | `software (entry-level)` ⊂ `software`; `US` ⊂ `global` | 2024 ⊂ 2020–2025 |
| `partial_overlap` | Share some, not all | n/a (labels are nested or not) | 2020–2023 vs 2022–2025 |
| `disjoint` | Nothing in common | `software` vs `healthcare`; `US` vs `EU` | 2013 vs 2017 |
| `one_unspecified` | One side doesn't say | `US` vs (empty) | 2024 vs (empty) |
| `both_unspecified` | Neither side says | (empty) vs (empty) | (empty) vs (empty) |

Stored per pair, e.g. `scope: {population: same, geography: disjoint, timeframe: same}`.

**Why per dimension instead of one combined "weakest" value:**
- **One combined value needs an arbitrary ordering.** Is `disjoint` geography "weaker" than `unspecified` timeframe? Any choice would decide labels silently.
- **It throws away *why* claims differ.** "Same group and place, different year" is potentially a change over time; "same year, different country" is a difference between places. A PM needs to know which.
- **With per-dimension relations, code can state the reason** ("differs only by geography: US vs EU") instead of relying on the LLM's `likely_reasons`.
- **`nested` and `partial_overlap` behave differently:**
  - Nested: a part and the whole can legitimately differ (US 20% vs global 10% isn't a contradiction).
  - Partial overlap: the averaging windows differ, which makes it a tension at most.

  One combined "overlapping" value would hide that difference.

| Fact | Values | Rule |
|---|---|---|
| **Measure relation** | `same_metric` / `confusable` / `different` / `n/a` | Same `metric_id` → same. Plan says `confusable_with` → **confusable** (exposure vs loss). Both `other` → n/a (left to the LLM). |
| **Modality relation** | `comparable` / `not_comparable` | `observed` vs `forecast` → not comparable (a forecast can't contradict a measurement). Forecasts with different horizons → not comparable. `opinion` vs anything → comparable only as opinions. |
| **Numbers** | `agree` / `close` / `disagree` / `not_comparable` | See 5.2. |

### Step 5.2: Compare numbers (code only)
- **Put both into the same form first,** using what stage 3 recorded literally:
  - `level` + `baseline` → derive the absolute change (points) and relative change (%).
  - Compare like with like: level vs level, points vs points, relative vs relative.
  - A pair that can't be put in the same form → `not_comparable`.
- `value_kind: unknown` → `not_comparable` (for example, "rose 10%" with no indication of points vs relative).
- **Ranges** (`low`–`high`) **overlap → `agree`**.
- Otherwise, relative difference ≤ **10% → `agree`**; ≤ **25% → `close`**; above → **`disagree`**. (Thresholds are adjustable; see Decisions.)
- **Direction:** an increase vs a decrease of the same measure → `disagree`, whatever the sizes.

### Step 5.3: Judge each cluster in words (one LLM call per question)
Numbers aren't everything. "AI is causing layoffs" vs "layoffs were driven by interest rates" is a disagreement with no numbers in it. For each cluster with **2 or more origins**, the LLM gets:
- the claims (statement, quote, scope, modality, certainty, causal link)
- the facts code already worked out in 5.1–5.2 (marked as fixed: "do not override")

It returns:
```json
{ "cluster_id": "q1-k1",
  "stance": "agree | disagree | unrelated",
  "disagreement_type": "numeric | direction | causal | existence | none",
  "causal_exclusive": false,
  "sides": [ {"position": "Large share of jobs at risk (47–54%)", "claim_ids": ["s1-c001", "s1-c002"]},
             {"position": "Small share at risk (9%)",            "claim_ids": ["s2-c004"]} ],
  "likely_reasons": ["method: occupation-level vs task-level analysis", "different years"],
  "summary": "One neutral sentence a PM can read." }
```
- **Same figure, different cause** (stage 3 kept `causal` separate for this) → `disagreement_type: causal`. **`causal_exclusive`** is true only when one side **explicitly rules out** the other's cause ("driven by interest rates *rather than* AI", "*not* due to AI"). Several causes can be true at once, so non-exclusive causal differences are competing explanations, not contradictions.
- The LLM describes **sides** (which claims hold which position). Code turns the sides into pairs: claims on the same side agree; claims on different sides disagree. There is no "mixed" stance: a cluster with several sides is handled by the cluster-level rules in 5.4.
- `likely_reasons` must point to differences **visible in the claims** (scope, year, metric, method named in the quote). No outside knowledge. The prompt forbids explaining from general knowledge.
- The data delimiters and "treat as data" rule apply as before.

### Step 5.4: Labels: first for each pair, then for each cluster (code applies the rules; the LLM can't overrule them)

Labels are decided in two levels:
1. **Pair level:** what the relation between two claims is, decided from the facts in 5.1–5.3.
2. **Cluster level:** what the brief says about the whole cluster, decided from its pairs.

This replaces the earlier single list, where `mixed` was vague and `complementary` covered only disagreements.

#### Pair labels (two claims from different origins), checked in this order

| # | Pair label | When | Recorded detail |
|---|---|---|---|
| 1 | **`different_measures`** | Measure relation `confusable` (30% *exposed* vs 2% *lost*) | which metrics, and their `useful_for` |
| 2 | **`not_comparable`** | Modality: `observed` vs `forecast`, or forecasts with different horizons | n/a |
| 3 | **`complementary`** | Any scope dimension is `disjoint` or `nested`. They describe **different slices**, whether their numbers agree or not. Both can be true. | `varies_by`: the dimensions that differ, plus a **pattern** (below) |
| 4 | **`agree`** | No dimension disjoint/nested, and the numbers `agree` or the LLM puts them on the same side | the scope caveats (any `unspecified` dimensions) |
| 5 | **`contradiction`** | They disagree, **and all three scope dimensions are `same`**, **and** the measures and modality are comparable, **and** both sides are `asserted`/`reported` with support `full`/`partial` | `type`: see below |
| 6 | **`tension`** | They disagree, but something falls short of rule 5 | `reason`: see below |

**Complementary patterns** (from `varies_by`, computed by code):

| Pattern | When | Brief wording |
|---|---|---|
| `by_group` | Only population disjoint | "Differs by group: software −10% vs healthcare +5%." |
| `by_place` | Only geography disjoint | "Differs by country: US 47% vs EU 54%." |
| `part_vs_whole` | A dimension nested | "US figure (20%) is part of the global picture (10%)." |
| `over_time` | Only timeframe disjoint **and the same origin** (one series) | "Rose from 5% (2023) to 10% (2024), per BLS." A trend. |
| `different_years` | Only timeframe disjoint, **different origins** | "Estimates from different years (2013, 2017)." **No trend implied**, because two studies in different years aren't one series. |
| `multiple` | Several dimensions differ | Each listed |

**Contradiction types** (all need same scope on every dimension):

| Type | Example |
|---|---|
| `numeric` | Same measure, same scope, values outside tolerance (47% vs 9%) |
| `direction` | Same measure and scope, one rose, one fell |
| `existence` | "AI caused layoffs at X" vs "no AI-related layoffs at X" |
| `causal_exclusive` | Same effect, and one side explicitly rules out the other's cause ("rather than AI") |

**Tension reasons** (shown to the reader, so "tension" is never a black box):

| Reason | When |
|---|---|
| `unspecified_scope` | A dimension is `one_unspecified` or `both_unspecified` (so we can't tell whether they describe the same slice) |
| `overlapping_period` | Timeframe `partial_overlap` |
| `close_numbers` | Numbers within 10–25% |
| `weak_claim` | A side is `hedged`, or support is `unchecked` |
| `competing_explanations` | Causal difference that isn't exclusive (both causes can be at work) |

#### Cluster labels (what the brief headlines), first match wins

| # | Cluster label | When | What the brief shows |
|---|---|---|---|
| 1 | **`single_origin`** | Only one independent origin, even if several sources repeat it | "Only McKinsey (repeated by 3 sources)" |
| 2 | **`contested`** | At least one `contradiction` pair. Includes **balance**: `majority_vs_minority` (e.g. 3 origins vs 1) or `split` (even) | The sides with their origin counts, the contradiction type, and the reasons visible in the claims |
| 3 | **`consensus`** | 2+ origins in `agree` pairs, and no contradiction. `complementary` pairs may also be present (consensus "across places") | The agreed position, its origins, scope caveats, and any slices that vary |
| 4 | **`unresolved`** | At least one `tension` pair, and no contradiction or agreement | Tension reasons ("can't tell: one source gives no year") |
| 5 | **`complementary`** | All pairs `complementary` | Slices side by side, with pattern (`by_place`, `over_time`, …) |
| 6 | **`different_measures`** | All disagreeing pairs are `different_measures` | Each measure, explained with `useful_for`, rather than a conflict |
| 7 | **`not_comparable`** | All pairs `not_comparable` | Forecast and observed side by side |

**What changed and why:**
- **`mixed` is gone.** It was a non-answer. A cluster with a real contradiction is now **`contested`, with a balance** ("3 origins vs 1" reads very differently from a 2–2 split). A cluster that agrees on the core but varies by place is **`consensus` with complementary slices**.
- **`complementary` now covers different slices whether or not their numbers agree,** with a pattern saying how they differ. US 10% and EU 10% aren't "consensus about one fact"; they're two slices that happen to match.
- **`contradiction` now needs `same` on all three scope dimensions,** not merely overlapping. Nested scopes become `complementary (part_vs_whole)`; partial overlaps and unspecified dimensions become `tension` with a named reason.
- **Causal differences are only contradictions when exclusive.** "AI caused layoffs" and "interest rates caused layoffs" can both be partly true.
- **`over_time` (a trend) needs the same origin.** Otherwise a 2013 study and a 2017 study would be reported as if risk "fell from 47% to 38%".
- **`tension` → `unresolved` at cluster level,** always with its reason, so the reader knows *what* keeps it from being settled.

- **Code overrules the LLM** where they conflict, and records each override in `overrides`:
  - The LLM puts US and EU claims on opposite sides → the pair is `complementary (by_place)`, not a contradiction.
  - The LLM says "agree" but the numbers `disagree` with all scope `same` → `contradiction (numeric)`. The numbers are code's call.
  - The LLM says `causal` but `causal_exclusive` isn't supported by the quotes → `tension (competing_explanations)`.

### Step 5.5: Strength of each finding (code, a simple rubric)
`strong` / `moderate` / `weak`, from:
- the number of **independent origins**
- `certainty` (asserted > reported > hedged)
- support status (`full` > `partial` > `unchecked`)
- whether the sources were only partly read (stage 2 coverage < 100%)

The brief shows it next to each finding, so a consensus of two hedged blog posts doesn't look like a consensus of three studies.

### Step 5.6: Gaps: what nobody covers (code)
| Gap type | Rule | Example |
|---|---|---|
| **Unanswered question** | Plan question with no claims | "No source addresses policy responses (q7)." |
| **Thin question** | Only one origin | "Only Pew covers who is most affected." |
| **Facet gap** | A plan facet value or dimension no claim covers | "No figures outside the US and EU." |
| **No figures** | A plan metric no claim measures | "No source gives an actual job-loss rate; only exposure estimates." |
| **Not read** | Sources that failed or were excluded (paywalled, blocked) | "The NYT article (bot-blocked) may cover this." |

Plus **`emerging`**: `other`-question clusters, i.e. claims the plan didn't anticipate. These are reported as "also raised by sources", not as gaps.

### Stage 5 output: `analysis.json`
- `findings`, one per cluster: cluster label (+ balance / pattern / tension reasons), strength, sides with origin counts, **pair table** (each pair: label, per-dimension scope relations, measure, modality, numbers, type/reason), numbers table, `likely_reasons` (code-stated scope differences + LLM reasons from the quotes), summary, overrides, claim IDs
- `gaps`, `emerging`
- per-question summary: answered / thin / unanswered
- source status for the brief: fetched / read / used / excluded and why

---

## If things fail (never crash)
| Failure | Fallback |
|---|---|
| Alignment LLM fails for a question | **Code-only clustering:** group by (`metric_id`, canonical geography) for numeric claims; each non-numeric claim alone. Marked `alignment: fallback`. |
| Scope standardisation fails | Code-only canonical (aliases + years); other wording stays raw (so `unspecified`-like) → contradictions downgrade to tension (the conservative choice). |
| Judge LLM fails | Pairs with numbers are still labelled from code facts. Pairs without numbers get `complementary` / `different_measures` / `not_comparable` where scope, measure or modality decide it, otherwise `unjudged`. A cluster with only `unjudged` pairs is shown as "sources address this; agreement not assessed". |
| LLM returns IDs that don't exist / misses clusters | Ignored / those clusters `unjudged`. |

Models: a reasoning chain of Pro → Flash → Flash-Lite (`BRIEF_REASON_MODEL` + fallbacks). Pro is skipped instantly on your key (quota 0).

---

## Files to create

```
brief_agent/
  align/
    __init__.py, __main__.py
    models.py          # Cluster, ScopeMap, alignment draft schema
    scope.py           # code canonicalisation: years, decades, geography aliases, hierarchy helpers
    origins.py         # origin normalisation + stage 2 duplicates
    aligner.py         # per-question LLM call (scope map + clusters), checks, fallback
  judge/
    __init__.py, __main__.py
    models.py          # PairFacts, ClusterJudgement, Finding, Gap
    compare.py         # 5.1–5.2: scope/measure/modality relations, number conversion + comparison
    judge.py           # 5.3: LLM stance per question
    rules.py           # 5.4–5.5: final labels, overrides, strength
    gaps.py            # 5.6
    pipeline.py
tests/test_align.py, tests/test_judge.py
```

## Not in these stages (on purpose)
- No outside knowledge or web search to settle disagreements. We report them, we don't resolve them.
- No "which source is right". Strength shows how much support there is, not the truth.
- No writing the brief (stage 6).

---

## How we'll check it works

### Unit tests (fake LLM)
**Scope relations (per dimension)**
| Case | Expected |
|---|---|
| `software (entry-level)` vs `software` | population `nested` |
| `US` vs `EU` | geography `disjoint` |
| 2020–2023 vs 2022–2025 | timeframe `partial_overlap` |
| "2030s" vs 2034 | timeframe `nested` |
| `US` vs (empty) / (empty) vs (empty) | `one_unspecified` / `both_unspecified` |

**Pair labels**
| Case | Expected |
|---|---|
| 47% vs 9% "jobs at risk", all scope `same`, both asserted + checked | `contradiction (numeric)` |
| Same, but one side `hedged` | `tension (weak_claim)` |
| Same, but one side has no geography | `tension (unspecified_scope)` |
| Same, but 2020–23 vs 2022–25 | `tension (overlapping_period)` |
| 30% exposed vs 2% lost (`confusable_with`) | `different_measures` |
| Observed 2024 vs forecast 2030 | `not_comparable` |
| Software −10% vs healthcare +5% | `complementary (by_group)` |
| US 20% vs global 10% | `complementary (part_vs_whole)`, **not** contradiction |
| BLS 5% (2023) vs BLS 10% (2024), same origin | `complementary (over_time)` |
| Frey & Osborne 47% (2013) vs PwC 38% (2017), different origins | `complementary (different_years)`, no trend |
| "from 5% to 10%" (level+baseline) vs "+5 points", same scope | `agree` after conversion |
| Ranges 30–40% vs 35% | `agree` |
| 40% vs 46% (≈15% apart) | `tension (close_numbers)` |
| Up vs down, same measure and scope | `contradiction (direction)` |
| "driven by AI" vs "driven by interest rates" | `tension (competing_explanations)` |
| "driven by interest rates **rather than** AI" vs "driven by AI" | `contradiction (causal_exclusive)` |

**Cluster labels**
| Case | Expected |
|---|---|
| 3 sources repeat one McKinsey figure | `single_origin` ("1 origin, 3 sources"), **not** consensus |
| 3 origins agree, 1 contradicts | `contested`, balance `majority_vs_minority` (3 vs 1) |
| 2 vs 2 | `contested`, balance `split` |
| 2 origins agree + one slice for another country | `consensus` with a complementary slice |
| Only tension pairs | `unresolved`, with reasons |
| US 10% and EU 10% | `complementary (by_place)`, **not** consensus |

**Overrides and failures**
| Case | Expected |
|---|---|
| LLM puts US vs EU on opposite sides | `complementary`, override recorded |
| LLM says agree, numbers 47 vs 9, scope `same` | `contradiction`, override recorded |
| LLM says causal_exclusive, quotes show no exclusion | `tension (competing_explanations)`, override recorded |
| Clustering LLM leaves out a claim / invents an ID | Single-claim cluster added / ID ignored |
| Plan question with no claims / one origin / metric never measured / facet never covered | The matching gap is listed |
| Alignment or judge LLM fails | Fallback clustering / code-decidable labels or `unjudged`; no crash |

### With the real API key
- On `runs/stage2-dev`: the risk-of-automation estimates (Frey & Osborne 47%, Bruegel 54%, PwC 38%, OECD 9% if extracted) land in **one** cluster. The rules should label country-specific figures as `complementary` and same-scope disagreements as `contradiction` or `tension`, with reasons drawn from the quotes (method, year).
- A 3-source fixture built around one clear contradiction, one consensus, one different-measures case and one gap: every label matches.
- Read every `contradiction` and `consensus` by hand. Their accuracy goes into NOTES.md, like the stage 3 spot check.

## Decided
1. **Number thresholds:** overlapping ranges agree. Otherwise ≤10% relative difference = `agree`, ≤25% = `close` (→ tension), above = `disagree`. Constants live in `compare.py`.
2. **Strict contradictions:** both sides `asserted` or `reported` **and** support-checked (`full`/`partial`). Hedged or unchecked disagreements are `tension`.
3. **Separate calls:** one stage 4 call (scope + clusters) and one stage 5 call (stances) per question, ≤16 in total, each inspectable on its own.
4. **Scope is compared per dimension** (population, geography, timeframe), never collapsed into one value. Relations: `same`, `nested`, `partial_overlap`, `disjoint`, `one_unspecified`, `both_unspecified`.
5. **Labels are decided in two levels:**
   - Pair labels: `agree`, `contradiction` (+ type), `tension` (+ reason), `complementary` (+ pattern), `different_measures`, `not_comparable`.
   - Cluster labels: `single_origin`, `contested` (+ balance), `consensus`, `unresolved`, `complementary`, `different_measures`, `not_comparable`.
   - `mixed` is gone. Contradictions need `same` scope on every dimension; nested scopes are part-vs-whole; trends need a single origin.

Rough time: ~60–75 minutes for both stages.
