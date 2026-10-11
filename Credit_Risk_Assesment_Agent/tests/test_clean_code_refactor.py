"""Regression tests for the clean-code refactor of the credit-risk agent.

Covers the extracted helpers and renamed logic paths to lock in
behavior preservation: severity mapping, score-to-grade, trend
trajectory, ratio period ordering, and validation scoring helpers.
"""

from credit_risk import benchmarks, llm_validation, scoring
from credit_risk.llm_validation import ConfidenceReport
from credit_risk.ratios import order_periods
from credit_risk.schemas import RiskBand


def test_severity_higher_worse_thresholds():
    thresholds = {"medium": 3.0, "high": 4.5, "critical": 6.0}
    assert scoring._sev_higher_worse(None, thresholds) == "low"
    assert scoring._sev_higher_worse(1.0, thresholds) == "low"
    assert scoring._sev_higher_worse(3.0, thresholds) == "medium"
    assert scoring._sev_higher_worse(4.5, thresholds) == "high"
    assert scoring._sev_higher_worse(9.0, thresholds) == "critical"


def test_severity_lower_worse_thresholds():
    thresholds = {"medium": 4.0, "high": 2.0, "critical": 1.0}
    assert scoring._sev_lower_worse(None, thresholds) == "low"
    assert scoring._sev_lower_worse(5.0, thresholds) == "low"
    assert scoring._sev_lower_worse(4.0, thresholds) == "medium"
    assert scoring._sev_lower_worse(2.0, thresholds) == "high"
    assert scoring._sev_lower_worse(0.5, thresholds) == "critical"


def test_severity_helpers_agree():
    lower_worse = {"medium": 1.5, "high": 1.2, "critical": 1.0}
    higher_worse = {"medium": 3.0, "high": 4.5, "critical": 6.0}
    assert (
        scoring._severity_for_threshold(1.6, lower_worse, higher_worse=False) == "low"
    )
    assert (
        scoring._severity_for_threshold(1.0, higher_worse, higher_worse=True) == "low"
    )


def test_grade_for_score_boundaries():
    assert scoring._grade_for_score(5.0).value == "AAA"
    assert scoring._grade_for_score(12.0).value == "AA"
    assert scoring._grade_for_score(100.0).value == "D"
    assert scoring._grade_for_score(999.0).value == "D"


def test_decide_hard_stop_and_bands():
    assert scoring._decide(RiskBand.INVESTMENT_GRADE, 0, True).value == "decline"
    assert scoring._decide(RiskBand.DEFAULT, 0, False).value == "decline"
    assert scoring._decide(RiskBand.DISTRESSED, 0, False).value == "decline"
    assert (
        scoring._decide(RiskBand.SPECULATIVE, 0, False).value
        == "refer_credit_committee"
    )
    assert (
        scoring._decide(RiskBand.SUB_INVESTMENT_GRADE, 0, False).value
        == "approve_with_conditions"
    )
    assert (
        scoring._decide(RiskBand.INVESTMENT_GRADE, 1, False).value
        == "approve_with_conditions"
    )
    assert scoring._decide(RiskBand.INVESTMENT_GRADE, 0, False).value == "approve"


def test_order_periods_year_sort():
    assert order_periods(["FY2023", "FY2021", "FY2022"]) == [
        "FY2021",
        "FY2022",
        "FY2023",
    ]
    assert order_periods(["FY2021", "FY2021"]) == ["FY2021"]


def test_overall_trajectory_counts():
    assert benchmarks._overall_trajectory(2, 0) == "deteriorating"
    assert benchmarks._overall_trajectory(0, 2) == "improving"
    assert benchmarks._overall_trajectory(1, 1) == "mixed"
    assert benchmarks._overall_trajectory(0, 0) == "stable"


def test_trend_direction_needs_two_points():
    assert benchmarks._trend_direction([None], True)[0] == "stable"
    assert benchmarks._trend_direction([1.0, 2.0], True)[0] == "improving"
    assert benchmarks._trend_direction([2.0, 1.0], True)[0] == "deteriorating"


def test_commentary_signal_keywords():
    assert benchmarks._commentary_signal(None) == "neutral"
    assert (
        benchmarks._commentary_signal("record growth and robust expansion")
        == "positive"
    )
    assert benchmarks._commentary_signal("covenant breach and default risk") == "risk"


def test_is_plausible_number_boundaries():
    assert llm_validation._is_plausible_number("1,234.5") is True
    assert llm_validation._is_plausible_number(None) is False
    assert llm_validation._is_plausible_number("not-a-number") is False
    assert llm_validation._is_plausible_number(1e16) is False


def test_classification_empty_output_fails():
    report = llm_validation.score_classification([], allowed_labels=["a"])
    assert isinstance(report, ConfidenceReport)
    assert report.confidence == 0.0
    assert report.failures


def test_extraction_empty_output_fails():
    report = llm_validation.score_extraction([])
    assert report.confidence == 0.0


def test_suggestion_empty_output_fails():
    report = llm_validation.score_suggestion([])
    assert report.confidence == 0.0


def test_retry_addendum_caps_failures():
    failures = [
        llm_validation.ValidationFailure(rule=f"rule-{i}", detail="d", penalty=0.01)
        for i in range(30)
    ]
    addendum = llm_validation.build_retry_addendum(failures)
    assert "rule-0" in addendum
    assert "rule-29" not in addendum


def test_validate_with_retry_returns_best():
    calls = {"count": 0}

    def fake_llm(addendum):
        calls["count"] += 1
        return [{"label": "x", "period": "FY2024", "value": 1.0}]

    def fake_score(output):
        return llm_validation.score_extraction(output, evidence_text="x")

    best, report, attempts = llm_validation.validate_with_retry(fake_llm, fake_score)
    assert attempts >= 1
    assert best is not None
    assert report.confidence >= 0.0
