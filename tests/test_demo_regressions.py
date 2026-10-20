"""Regression tests for bugs found in the first real multi-source run (Wikipedia, IMF, Goldman Sachs, WEF)."""

from brief_agent.clean.parse import parse_html
from brief_agent.extract.grounding import normalize
from brief_agent.extract.models import Value
from brief_agent.extract.numbers import check_value
from brief_agent.fetch.detect import assess_html, assess_text, looks_like_consent
from brief_agent.fetch.models import Status
from brief_agent.judge.compare import label_relation

COOKIE_ROWS = "".join(f"<tr><td>cookie_{i}</td><td>Used to track user's interaction with embedded content. "
                      f"Maximum Storage Duration: 180 days. Type: HTTP Cookie</td></tr>" for i in range(60))
ARTICLE = ("<article><h1>Future of Jobs 2025</h1>" + "".join(
    f"<p>Paragraph {i}: shifting trends are projected to create 170 million new jobs by 2030 while displacing "
    f"92 million others, according to employers surveyed across many economies and industries.</p>" for i in range(5))
    + "</article>")


def test_consent_dialog_is_pruned_and_article_kept():
    html = (f"<html><body><div id='CybotCookiebotDialog'><table>{COOKIE_ROWS}</table></div>{ARTICLE}"
            f"</body></html>")
    assert assess_html(html).status == Status.OK
    blocks, _ = parse_html(html)
    text = " ".join(b.text for b in blocks)
    assert "170 million" in text and "Storage Duration" not in text


def test_text_that_is_only_cookie_language_is_a_consent_wall():
    text = " ".join(f"cookie_{i} Used to track interaction. Maximum Storage Duration: Session. Type: HTTP Cookie"
                    for i in range(40))
    assert looks_like_consent(text)
    assert assess_text(text).status == Status.CONSENT_WALL
    html = f"<html><body><div><table>{COOKIE_ROWS}</table></div></body></html>"
    assert assess_html(html).status == Status.CONSENT_WALL


def test_normal_article_mentioning_cookies_once_is_not_a_consent_wall():
    assert not looks_like_consent("The firm uses cookies on its website. " + "Jobs data for 2024 show change. " * 40)


def test_two_thirds_recorded_as_decimal_percent_is_fixed():
    v = Value(raw="", low=0.6666, high=0.6666, unit="percent", value_kind="level", metric_id="other",
              of_what="", baseline=None, direction="none")
    status, fixes = check_value(v, normalize("Roughly two-thirds of U.S. occupations are exposed to automation"), None)
    assert abs(v.low - 66.66) < 0.01 and "fraction recorded as a decimal" in fixes[0]


def test_real_small_percent_is_left_alone():
    v = Value(raw="", low=0.5, high=0.5, unit="percent", value_kind="level", metric_id="other",
              of_what="", baseline=None, direction="none")
    check_value(v, normalize("Only 0.5% of jobs were lost, about half the forecast"), None)
    assert v.low == 0.5


def test_any_place_is_part_of_global():
    assert label_relation("Israel", "global", set()) == "nested"
    assert label_relation("US", "OECD countries", {("US", "OECD countries")}) == "nested"


def test_same_source_named_and_unattributed_claims_are_not_independent():
    from tests.test_judge import cluster, mk, val
    US = dict(geo="global", pop=None, tf=(2030, 2030))
    v = cluster([mk("a", "s4", value=val(22), origin="World Economic Forum", **US),
                 mk("b", "s4", value=val(22.5), **US)])
    assert v["label"] == "single_origin"


def test_overlapping_numbers_about_different_things_are_not_agreement():
    from brief_agent.judge.compare import compare_numbers
    jobs = {"low": 41, "high": 41, "unit": "percent", "value_kind": "level", "metric_id": "other",
            "of_what": "jobs in Israel", "baseline": None, "direction": "none"}
    workload = jobs | {"low": 25, "high": 50, "of_what": "workload of exposed occupations"}
    assert compare_numbers(jobs, workload)["result"] == "not_comparable"
    assert compare_numbers(jobs, jobs | {"low": 42, "high": 42, "of_what": "jobs in Israel"})["result"] == "agree"


def test_same_metric_but_different_things_not_comparable():
    from brief_agent.judge.compare import compare_numbers
    jobs = {"low": 41, "high": 41, "unit": "percent", "value_kind": "level", "metric_id": "share_of_jobs_exposed",
            "of_what": "jobs", "baseline": None, "direction": "none"}
    assert compare_numbers(jobs, jobs | {"low": 25, "high": 50, "of_what": "workload"})["result"] == "not_comparable"


def test_numbers_counting_different_things_are_different_measures_even_if_llm_agrees():
    from tests.test_judge import label_pair, mk, val
    agree = {"stance": "agree", "disagreement_type": "none", "causal_exclusive": False,
             "sides": [{"position": "x", "claim_ids": ["a", "b"]}]}
    a = mk("a", value=val(60) | {"of_what": "jobs"}, origin="IMF", geo="US", pop="all", tf=(2024, 2024))
    b = mk("b", "s2", value=val(25, high=50) | {"of_what": "workload"}, origin="GS", geo="US", pop="all", tf=(2024, 2024))
    p = label_pair(a, b, agree)
    assert p["label"] == "different_measures" and p["overrides"]


def test_llm_only_agreement_between_unrelated_statements_does_not_count():
    from tests.test_judge import label_pair, mk
    agree = {"stance": "agree", "disagreement_type": "none", "causal_exclusive": False,
             "sides": [{"position": "x", "claim_ids": ["a", "b"]}]}
    a = mk("a", origin="NPR", geo="US", pop="all", tf=(2024, 2024))
    b = mk("b", "s2", origin="COWS", geo="US", pop="all", tf=(2024, 2024))
    a[0]["statement"] = "The current federal minimum wage is $7.25."
    b[0]["statement"] = "Recent increases raised earnings for low-wage workers in Wisconsin without causing job loss."
    assert label_pair(a, b, agree)["label"] == "unrelated"
    b[0]["statement"] = "The federal minimum wage currently stands at $7.25 an hour."
    assert label_pair(a, b, agree)["label"] == "agree"
