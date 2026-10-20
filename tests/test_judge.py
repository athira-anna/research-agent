"""Stages 4 + 5 tests: per-dimension scope, number comparison, pair and cluster labels, overrides,
alignment checks/fallbacks, origins, gaps, and an end-to-end run with fake LLMs."""

import asyncio
import copy
import json
import re

import pytest

from brief_agent.align.aligner import _check_clusters, align
from brief_agent.align.models import AlignDraft
from brief_agent.align.origins import assign_origins
from brief_agent.align.scope import GEO_HIERARCHY, broader_closure, canon_geography, canon_timeframe
from brief_agent.judge.compare import compare_numbers, scope_relations, time_relation
from brief_agent.judge.models import JudgeResult
from brief_agent.judge.pipeline import _pairs, judge
from brief_agent.judge.rules import cluster_label, pair_label
from brief_agent.llm import LLMError
from brief_agent.plan.models import Plan
from brief_agent.plan.validate import check_plan
from tests.test_plan import draft as plan_draft

TOPIC = "AI and job losses"
PLAN, _ = check_plan(Plan(topic=TOPIC, topic_key="ai and job losses", plan_version=1, **plan_draft()))
BROADER = broader_closure(GEO_HIERARCHY | {("software (entry-level)", "software")})


def run(coro):
    return asyncio.run(coro)


def val(low, kind="level", unit="percent", metric="job_loss_rate", baseline=None, direction="increase", high=None):
    return {"raw": "", "low": low, "high": high if high is not None else low, "unit": unit, "value_kind": kind,
            "metric_id": metric, "of_what": "workers", "baseline": {"number": baseline, "period": ""} if baseline is not None else None,
            "direction": direction}


def mk(cid, sid="s1", value=None, pop=None, geo=None, tf=None, modality="observed", certainty="asserted",
       support="full", origin=None, causal=None, quote="Some quoted text from the source about jobs.", q="q1"):
    claim = {"claim_id": cid, "source_id": sid, "question_id": q, "statement": f"statement {cid}",
             "modality": modality, "certainty": certainty, "causal": causal, "value": value,
             "scope": {"population": pop or "", "geography": geo or "",
                       "timeframe": {"raw": "", "start": tf[0] if tf else None, "end": tf[1] if tf else None},
                       "metric": ""},
             "evidence": {"block_id": f"{sid}-p001", "quote": quote, "attributed_to": origin or ""},
             "support": {"status": support}}
    acl = {"canonical_scope": {"population": pop, "geography": geo, "timeframe": list(tf) if tf else None},
           "origin": f"org:{origin}" if origin else f"source:{sid}", "label": origin or sid}
    return claim, acl


def label_pair(a, b, stance=None):
    (ca, aa), (cb, ab) = a, b
    acl = {ca["claim_id"]: aa, cb["claim_id"]: ab}
    [p] = _pairs([ca, cb], acl, PLAN, BROADER)
    return pair_label(ca, cb, p["facts"], stance, p["same_origin"])


# --- scope per dimension ------------------------------------------------------------------------

@pytest.mark.parametrize("a, b, dim, expected", [
    ("software (entry-level)", "software", "population", "nested"),
    ("US", "EU", "geography", "disjoint"),
    ("US", "global", "geography", "nested"),
    ("US", None, "geography", "one_unspecified"),
    (None, None, "geography", "both_unspecified"),
    ("software", "Software", "population", "same"),
])
def test_label_relations(a, b, dim, expected):
    sa = {"population": None, "geography": None, "timeframe": [2024, 2024]}
    sb = dict(sa)
    sa[dim], sb[dim] = a, b
    assert scope_relations(sa, sb, BROADER)[dim] == expected


@pytest.mark.parametrize("a, b, expected", [
    ([2020, 2023], [2022, 2025], "partial_overlap"),
    ([2030, 2039], [2034, 2034], "nested"),
    ([2013, 2013], [2017, 2017], "disjoint"),
    ([2024, 2024], [2024, 2024], "same"),
    (None, [2024, 2024], "one_unspecified"),
])
def test_time_relations(a, b, expected):
    assert time_relation(a, b) == expected


def test_canonical_scope_helpers():
    assert canon_geography("the United States") == "US" and canon_geography("U.S.") == "US"
    assert canon_geography("European Union") == "EU" and canon_geography("Narnia") is None
    assert canon_timeframe({"raw": "by the early 2030s", "start": None, "end": None}) == (2030, 2039)
    assert canon_timeframe({"raw": "", "start": 2024, "end": None}) == (2024, 2024)


# --- numbers --------------------------------------------------------------------------------------

def test_level_with_baseline_agrees_with_points():
    assert compare_numbers(val(10, baseline=5), val(5, kind="absolute_change", unit="percentage_points"))["result"] == "agree"


def test_ranges_overlap_agree():
    assert compare_numbers(val(30, high=40), val(35))["result"] == "agree"


@pytest.mark.parametrize("a, b, expected", [(40, 42, "agree"), (40, 46, "close"), (47, 9, "disagree")])
def test_number_thresholds(a, b, expected):
    assert compare_numbers(val(a), val(b))["result"] == expected


def test_opposite_directions_disagree():
    r = compare_numbers(val(2, kind="relative_change", direction="increase"),
                        val(2, kind="relative_change", direction="decrease"))
    assert r["result"] == "disagree" and r["direction_conflict"]


def test_unknown_kind_not_comparable():
    assert compare_numbers(val(10, kind="unknown"), val(10))["result"] == "not_comparable"


# --- pair labels -------------------------------------------------------------------------------------

US24 = dict(geo="US", pop="software", tf=(2024, 2024))


def test_numeric_contradiction():
    p = label_pair(mk("a", value=val(47), origin="Frey", **US24), mk("b", "s2", value=val(9), origin="OECD", **US24))
    assert p["label"] == "contradiction" and p["type"] == "numeric"


def test_hedged_side_is_tension():
    p = label_pair(mk("a", value=val(47), origin="Frey", **US24),
                   mk("b", "s2", value=val(9), origin="OECD", certainty="hedged", **US24))
    assert (p["label"], p["reason"]) == ("tension", "weak_claim")


def test_unchecked_side_is_tension():
    p = label_pair(mk("a", value=val(47), origin="Frey", **US24),
                   mk("b", "s2", value=val(9), origin="OECD", support="unchecked", **US24))
    assert p["label"] == "tension" and "weak_claim" in p["reasons"]


def test_unspecified_geography_is_tension():
    p = label_pair(mk("a", value=val(47), origin="Frey", **US24),
                   mk("b", "s2", value=val(9), origin="OECD", pop="software", tf=(2024, 2024)))
    assert (p["label"], p["reason"]) == ("tension", "unspecified_scope")


def test_overlapping_period_is_tension():
    p = label_pair(mk("a", value=val(47), origin="Frey", geo="US", pop="software", tf=(2020, 2023)),
                   mk("b", "s2", value=val(9), origin="OECD", geo="US", pop="software", tf=(2022, 2025)))
    assert (p["label"], p["reason"]) == ("tension", "overlapping_period")


def test_confusable_metrics_are_different_measures():
    p = label_pair(mk("a", value=val(30, metric="ai_exposure_share"), origin="IMF", **US24),
                   mk("b", "s2", value=val(2, metric="job_loss_rate"), origin="BLS", **US24))
    assert p["label"] == "different_measures"


def test_observed_vs_forecast_not_comparable():
    p = label_pair(mk("a", value=val(5), origin="BLS", **US24),
                   mk("b", "s2", value=val(20), origin="WEF", modality="forecast", geo="US", pop="software", tf=(2030, 2030)))
    assert p["label"] == "not_comparable"


def test_by_group():
    p = label_pair(mk("a", value=val(10), origin="BLS", pop="software", geo="US", tf=(2024, 2024)),
                   mk("b", "s2", value=val(5), origin="CDC", pop="healthcare", geo="US", tf=(2024, 2024)))
    assert (p["label"], p["pattern"]) == ("complementary", "by_group")


def test_part_vs_whole_is_not_contradiction():
    p = label_pair(mk("a", value=val(20), origin="BLS", pop="software", geo="US", tf=(2024, 2024)),
                   mk("b", "s2", value=val(10), origin="ILO", pop="software", geo="global", tf=(2024, 2024)))
    assert (p["label"], p["pattern"]) == ("complementary", "part_vs_whole")


def test_same_origin_years_is_a_trend_different_origins_is_not():
    same = label_pair(mk("a", value=val(5), origin="BLS", pop="software", geo="US", tf=(2023, 2023)),
                      mk("b", "s2", value=val(10), origin="BLS", pop="software", geo="US", tf=(2024, 2024)))
    diff = label_pair(mk("a", value=val(47), origin="Frey", pop="software", geo="US", tf=(2013, 2013)),
                      mk("b", "s2", value=val(38), origin="PwC", pop="software", geo="US", tf=(2017, 2017)))
    assert same["pattern"] == "over_time" and diff["pattern"] == "different_years"


def test_conversion_agrees():
    p = label_pair(mk("a", value=val(10, baseline=5), origin="BLS", **US24),
                   mk("b", "s2", value=val(5, kind="absolute_change", unit="percentage_points"), origin="Indeed", **US24))
    assert p["label"] == "agree"


def test_close_numbers_tension():
    p = label_pair(mk("a", value=val(40), origin="A", **US24), mk("b", "s2", value=val(46), origin="B", **US24))
    assert (p["label"], p["reason"]) == ("tension", "close_numbers")


def test_direction_contradiction():
    p = label_pair(mk("a", value=val(2, kind="relative_change"), origin="A", **US24),
                   mk("b", "s2", value=val(2, kind="relative_change", direction="decrease"), origin="B", **US24))
    assert (p["label"], p["type"]) == ("contradiction", "direction")


def causal_stance(exclusive):
    return {"stance": "disagree", "disagreement_type": "causal", "causal_exclusive": exclusive,
            "sides": [{"position": "AI", "claim_ids": ["a"]}, {"position": "rates", "claim_ids": ["b"]}],
            "likely_reasons": [], "summary": ""}


def test_competing_explanations():
    a = mk("a", origin="Reuters", causal={"cause": "AI", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by AI adoption, executives said.", **US24)
    b = mk("b", "s2", origin="Fed", causal={"cause": "interest rates", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by higher interest rates, economists argue.", **US24)
    p = label_pair(a, b, causal_stance(False))
    assert (p["label"], p["reason"]) == ("tension", "competing_explanations")


def test_exclusive_causal_contradiction():
    a = mk("a", origin="Reuters", causal={"cause": "AI", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by AI adoption, executives said.", **US24)
    b = mk("b", "s2", origin="Fed", causal={"cause": "interest rates", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by interest rates rather than AI, economists argue.", **US24)
    p = label_pair(a, b, causal_stance(True))
    assert (p["label"], p["type"]) == ("contradiction", "causal_exclusive")


# --- overrides -------------------------------------------------------------------------------------

def test_llm_opposite_sides_for_us_vs_eu_overridden():
    stance = {"stance": "disagree", "disagreement_type": "numeric", "causal_exclusive": False,
              "sides": [{"position": "x", "claim_ids": ["a"]}, {"position": "y", "claim_ids": ["b"]}]}
    p = label_pair(mk("a", value=val(47), origin="Frey", geo="US", pop="software", tf=(2024, 2024)),
                   mk("b", "s2", value=val(54), origin="Bruegel", geo="EU", pop="software", tf=(2024, 2024)), stance)
    assert p["label"] == "complementary" and p["overrides"]


def test_llm_agree_but_numbers_disagree_overridden():
    stance = {"stance": "agree", "disagreement_type": "none", "causal_exclusive": False,
              "sides": [{"position": "x", "claim_ids": ["a", "b"]}]}
    p = label_pair(mk("a", value=val(47), origin="Frey", **US24), mk("b", "s2", value=val(9), origin="OECD", **US24), stance)
    assert p["label"] == "contradiction" and "numbers disagree" in p["overrides"][0]


def test_llm_exclusive_without_exclusion_words_overridden():
    a = mk("a", origin="Reuters", causal={"cause": "AI", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by AI adoption, executives said.", **US24)
    b = mk("b", "s2", origin="Fed", causal={"cause": "interest rates", "effect": "layoffs"},
           quote="Tech layoffs in 2024 were driven by higher interest rates, economists argue.", **US24)
    p = label_pair(a, b, causal_stance(True))
    assert p["label"] == "tension" and p["overrides"]


# --- cluster labels ---------------------------------------------------------------------------------

def cluster(items, stance=None):
    claims = [c for c, _ in items]
    acl = {c["claim_id"]: a for c, a in items}
    pairs = []
    for p in _pairs(claims, acl, PLAN, BROADER):
        by = {c["claim_id"]: c for c in claims}
        lab = pair_label(by[p["a"]], by[p["b"]], p["facts"], stance, p["same_origin"])
        if p["same_origin"] and not (lab["label"] == "complementary" and lab.get("pattern") == "over_time"):
            continue
        pairs.append(p | lab)
    return cluster_label(claims, pairs, {c: a["origin"] for c, a in acl.items()}, stance)


def test_one_origin_repeated_is_single_origin():
    v = cluster([mk(f"c{i}", f"s{i}", value=val(30), origin="McKinsey", **US24) for i in range(1, 4)])
    assert v["label"] == "single_origin" and v["sources"] == 3


def test_majority_vs_minority():
    items = [mk("a", "s1", value=val(47), origin="Frey", **US24), mk("b", "s2", value=val(48), origin="PwC", **US24),
             mk("c", "s3", value=val(46), origin="Bruegel", **US24), mk("d", "s4", value=val(9), origin="OECD", **US24)]
    v = cluster(items)
    assert v["label"] == "contested" and v["balance"] == "majority_vs_minority"
    assert sorted(s["origins"] for s in v["sides"]) == [1, 3]


def test_split():
    items = [mk("a", "s1", value=val(47), origin="A", **US24), mk("b", "s2", value=val(48), origin="B", **US24),
             mk("c", "s3", value=val(9), origin="C", **US24), mk("d", "s4", value=val(9.5), origin="D", **US24)]
    assert cluster(items)["balance"] == "split"


def test_consensus_with_complementary_slice():
    items = [mk("a", "s1", value=val(10), origin="A", **US24), mk("b", "s2", value=val(10.5), origin="B", **US24),
             mk("c", "s3", value=val(30), origin="C", geo="EU", pop="software", tf=(2024, 2024))]
    v = cluster(items)
    assert v["label"] == "consensus" and v["complementary_slices"] == ["by_place"]


def test_only_tension_is_unresolved():
    items = [mk("a", "s1", value=val(47), origin="A", **US24),
             mk("b", "s2", value=val(9), origin="B", pop="software", tf=(2024, 2024))]
    v = cluster(items)
    assert v["label"] == "unresolved" and "unspecified_scope" in v["reasons"]


def test_same_value_different_places_is_complementary_not_consensus():
    items = [mk("a", "s1", value=val(10), origin="A", geo="US", pop="software", tf=(2024, 2024)),
             mk("b", "s2", value=val(10), origin="B", geo="EU", pop="software", tf=(2024, 2024))]
    assert cluster(items)["label"] == "complementary"


# --- stage 4 checks -----------------------------------------------------------------------------------

def test_cluster_checks_missing_and_unknown_ids():
    claims = [mk("a")[0], mk("b")[0], mk("c")[0]]
    draft = AlignDraft(mappings=[], hierarchy=[], clusters=[{"label": "x", "claim_ids": ["a", "zzz", "a"]}])
    clusters, fixes = _check_clusters(draft, "q1", claims)
    assert [cl["claim_ids"] for cl in clusters] == [["a"], ["b"], ["c"]]
    assert any("unknown claim id zzz" in f for f in fixes) and any("listed twice" in f for f in fixes)


def test_origins_merge_name_variants_and_duplicate_sources():
    claims = [mk("a", "s1", origin="McKinsey")[0], mk("b", "s2", origin="McKinsey Global Institute")[0],
              mk("c", "s3")[0], mk("d", "s4")[0]]
    sources = [{"source_id": f"s{i}"} for i in range(1, 4)] + [{"source_id": "s4", "duplicate": {"of": "s3"}}]
    o = assign_origins(claims, sources)
    assert o["a"]["origin"] == o["b"]["origin"]
    assert o["c"]["origin"] == o["d"]["origin"] == "source:s3"


# --- end to end with fake LLMs ---------------------------------------------------------------------------

class FakeLLM:
    def __init__(self, fail_align=False, fail_judge=False, stance=None):
        self.fail_align, self.fail_judge, self.stance = fail_align, fail_judge, stance

    async def generate_json(self, *, model, system, prompt, schema):
        ids = re.findall(r'"claim_id": "([^"]+)"', prompt)
        if schema is AlignDraft:
            if self.fail_align:
                raise LLMError("down")
            return AlignDraft(mappings=[{"dimension": "geography", "raw": "the States", "canonical": "US"}],
                              hierarchy=[], clusters=[{"label": "Share of jobs lost to AI", "claim_ids": ids}])
        if schema is JudgeResult:
            if self.fail_judge:
                raise LLMError("down")
            cids = re.findall(r'"cluster_id": "([^"]+)"', prompt)
            return JudgeResult(judgements=[copy.deepcopy(self.stance or {}) | {"cluster_id": cid} for cid in cids])
        raise AssertionError(schema)


def make_run(tmp_path, claims):
    (tmp_path / "plan.json").write_text(PLAN.model_dump_json())
    (tmp_path / "claims.json").write_text(json.dumps({"topic": TOPIC, "claims": claims}))
    sources = [{"source_id": f"s{i}", "url": f"https://s{i}.example", "fetch": {"status": "ok"},
                "extraction": {"status": "ok"}, "coverage": 1.0} for i in range(1, 5)]
    sources.append({"source_id": "s5", "url": "https://paywalled.example", "fetch": {"status": "paywalled"}})
    (tmp_path / "sources.json").write_text(json.dumps(sources))
    return tmp_path


def e2e_claims():
    return [mk("s1-c001", "s1", value=val(47), origin="Frey", geo="the States", pop="software", tf=(2024, 2024))[0],
            mk("s2-c001", "s2", value=val(48), origin="PwC", geo="US", pop="software", tf=(2024, 2024))[0],
            mk("s3-c001", "s3", value=val(9), origin="OECD", geo="US", pop="software", tf=(2024, 2024))[0]]


def test_end_to_end_contested_and_gaps(tmp_path):
    stance = {"stance": "disagree", "disagreement_type": "numeric", "causal_exclusive": False,
              "sides": [{"position": "~half at risk", "claim_ids": ["s1-c001", "s2-c001"]},
                        {"position": "few at risk", "claim_ids": ["s3-c001"]}],
              "likely_reasons": ["occupation-level vs task-level method"], "summary": "Sources disagree."}
    run_dir = make_run(tmp_path, e2e_claims())
    alignment = run(align(run_dir, FakeLLM()))
    assert alignment["claims"]["s1-c001"]["canonical_scope"]["geography"] == "US"   # LLM mapping, then alias table
    a = run(judge(run_dir, FakeLLM(stance=stance)))
    [f] = a["findings"]
    assert f["verdict"]["label"] == "contested" and f["verdict"]["balance"] == "majority_vs_minority"
    types = {g["type"] for g in a["gaps"]}
    assert {"unanswered_question", "not_read", "no_figures"} <= types
    assert (run_dir / "analysis.json").exists()


def test_end_to_end_llm_failures_fall_back(tmp_path):
    run_dir = make_run(tmp_path, e2e_claims())
    alignment = run(align(run_dir, FakeLLM(fail_align=True)))
    assert alignment["questions"]["q1"]["status"] == "fallback"
    a = run(judge(run_dir, FakeLLM(fail_judge=True)))
    [f] = a["findings"]
    assert f["verdict"]["label"] == "contested"          # numbers alone decide this one
    assert "unavailable" in a["judge_notes"]["q1"]
