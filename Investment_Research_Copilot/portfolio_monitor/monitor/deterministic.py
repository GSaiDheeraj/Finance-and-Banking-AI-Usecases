"""
Deterministic (templated) monitoring briefing.

NO LLM, NO network. Assembles a readable briefing purely from the deterministic facts — the
allocation, drift, cash-flow report, risk metrics, alerts and score. Recommended actions are
the rule-based `recommended_action`s from the alerts. This is the offline baseline the
LLM briefing is compared against, and it is fully reproducible.
"""
from __future__ import annotations

from typing import List

from ..schemas import (
    AllocationBreakdown,
    CashFlowReport,
    DriftReport,
    MonitoringBriefing,
    PortfolioRiskScore,
    RebalancePlan,
    RiskMetrics,
    Severity,
)

_SEV_RANK = {Severity.INFO: 0, Severity.LOW: 1, Severity.MEDIUM: 2,
             Severity.HIGH: 3, Severity.CRITICAL: 4}


def build_briefing(
    portfolio_name: str,
    allocation: AllocationBreakdown,
    drift: DriftReport,
    rebalance: RebalancePlan,
    cashflow: CashFlowReport,
    risk_metrics: RiskMetrics,
    score: PortfolioRiskScore,
    alerts: list,
) -> MonitoringBriefing:
    m = risk_metrics
    exec_summary = (
        f"{portfolio_name}: health {score.health_band.value.upper()} "
        f"(risk score {score.normalized_score:.0f}/100), market value "
        f"{allocation.total_market_value:,.0f} {allocation.base_currency} across "
        f"{m.n_holdings} holdings. {drift.n_breaches} allocation sleeve(s) outside tolerance; "
        f"{sum(1 for a in alerts if _SEV_RANK.get(a.severity,0) >= 3)} high-severity alert(s)."
    )

    alloc_bits = [
        f"{b.name} {b.weight:.0%}" for b in allocation.by_asset_class
    ]
    breached = [l for l in drift.lines if l.status.value.startswith("breach")]
    alloc_commentary = (
        f"Asset-class mix: {', '.join(alloc_bits)}. "
        + (rebalance.summary if rebalance.trades else "All sleeves within tolerance.")
    )
    if breached:
        alloc_commentary += " Breached: " + "; ".join(
            f"{l.bucket} {l.actual_weight:.0%} vs {l.target_weight:.0%}" for l in breached) + "."

    risk_bits = []
    if m.volatility_annual is not None:
        risk_bits.append(f"volatility {m.volatility_annual:.1%}")
    if m.max_drawdown is not None:
        risk_bits.append(f"max drawdown {m.max_drawdown:.1%}")
    if m.var_95_1d_pct is not None:
        risk_bits.append(f"1-day 95% VaR {m.var_95_1d_pct:.1%}")
    if m.concentration_hhi is not None:
        risk_bits.append(f"HHI {m.concentration_hhi:.2f} (≈{m.effective_n:.0f} effective names)")
    if m.beta is not None:
        risk_bits.append(f"beta {m.beta:.2f}")
    risk_commentary = ("Risk profile: " + ", ".join(risk_bits) + "."
                       if risk_bits else "Risk metrics unavailable (no price history).")
    if m.data_coverage < 1.0:
        risk_commentary += f" (Data coverage {m.data_coverage:.0%}.)"

    cf = cashflow
    cashflow_commentary = (
        f"Income: {cf.total_actual_income:,.0f} actual vs {cf.total_expected_income:,.0f} "
        f"expected (net {cf.net_variance:+,.0f}); {cf.n_flags} variance flag(s)."
        if cf.variances else "No transactions ledger supplied — cash-flow variance not assessed."
    )

    # recommended actions: dedup the rule-based actions on high/medium alerts, top first
    actions: List[str] = []
    for a in sorted(alerts, key=lambda x: _SEV_RANK.get(x.severity, 0), reverse=True):
        if a.recommended_action and a.recommended_action not in actions \
                and _SEV_RANK.get(a.severity, 0) >= 2:
            actions.append(a.recommended_action)
    if not actions:
        actions = ["No action required — portfolio within policy and risk limits."]

    watch = [a.title for a in alerts if a.severity == Severity.MEDIUM][:6]

    return MonitoringBriefing(
        method="deterministic",
        executive_summary=exec_summary,
        allocation_commentary=alloc_commentary,
        risk_commentary=risk_commentary,
        cashflow_commentary=cashflow_commentary,
        recommended_actions=actions[:8],
        watch_items=watch,
    )
