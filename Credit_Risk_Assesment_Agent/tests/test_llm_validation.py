"""Offline tests for credit_risk.llm_validation (no network)."""

from credit_risk import llm_validation as lv


def test_classification_percent_correct():
    items = [
        {"standardised_label": "revenue", "label": "Turnover"},
        {"standardised_label": "made_up", "label": "Turnover"},
    ]
    rep = lv.score_classification(
        items, allowed_labels=["revenue", "ebit"], evidence_text="turnover for the year"
    )
    assert rep.checks_run == 6
    assert 0 < rep.percent_correct < 100
    assert any("unknown_label" in f.rule for f in rep.failures)
    assert rep.confidence < 1.0


def test_extraction_confidence_and_retry_addendum():
    rows = [
        {"label": "", "period": "", "value": "n/a"},
        {"label": "", "period": "FY2023", "value": "n/a"},
    ]
    rep = lv.score_extraction(rows, evidence_text="revenue FY2023")
    assert rep.confidence < lv.CONFIDENCE_THRESHOLD
    add = lv.build_retry_addendum(rep.failures)
    assert "extraction." in add and "PREVIOUS ATTEMPT" in add


def test_validate_with_retry_reinvokes_with_failures():
    calls = []

    def llm_fn(addendum: str):
        calls.append(addendum)
        if not addendum:
            return [{"label": "", "period": "", "value": "junk"}]
        return [{"label": "Revenue", "period": "FY2023", "value": 100.0}]

    def score_fn(out):
        return lv.score_extraction(out, evidence_text="revenue FY2023")

    best, report, attempts = lv.validate_with_retry(llm_fn, score_fn, max_attempts=3)
    assert attempts == 2
    assert "PREVIOUS ATTEMPT" in calls[1]
    assert report.passed
    assert best[0]["label"] == "Revenue"


def test_suggestion_empty_fails():
    rep = lv.score_suggestion([], must_acknowledge_risk=True)
    assert rep.confidence == 0.0 and not rep.passed
