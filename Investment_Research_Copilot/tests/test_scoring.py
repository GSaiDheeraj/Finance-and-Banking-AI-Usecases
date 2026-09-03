"""Unit tests for deterministic portfolio scoring."""
from __future__ import annotations

from portfolio_monitor.schemas import CashFlowReport, DriftLine, DriftReport, DriftStatus, RiskMetrics
from portfolio_monitor.scoring import score_portfolio


def _minimal_drift(breaches: int = 0) -> DriftReport:
    lines = [
        DriftLine(
            bucket="equity",
            actual_weight=0.62,
            target_weight=0.60,
            lower_band=0.55,
            upper_band=0.65,
            status=DriftStatus.WITHIN_BAND,
        )
    ]
    return DriftReport(lines=lines, n_breaches=breaches, active_share=0.02)


def _score(drift: DriftReport):
    return score_portfolio(
        drift=drift,
        risk_metrics=RiskMetrics(),
        limit_checks=[],
        cashflow=CashFlowReport(),
        mandate="balanced",
        risk_scan=None,
    )


def test_score_is_reproducible():
    drift = _minimal_drift()
    assert _score(drift).normalized_score == _score(drift).normalized_score


def test_more_drift_breaches_lower_score():
    assert _score(_minimal_drift(3)).normalized_score <= _score(_minimal_drift(0)).normalized_score
