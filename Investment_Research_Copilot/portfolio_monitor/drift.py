"""
Deterministic allocation drift vs the Investment Policy Statement, and a rebalancing plan.

NO LLM. Given the actual asset-class weights and the mandate's strategic asset allocation
(target weights + a tolerance band from `reference.py`), this module classifies each sleeve
as within-band / overweight / underweight / breached, and proposes the trades that bring the
breached sleeves back to target. Every line and every trade carries a `rationale` string so
the agent always states *why* — the explainability requirement.
"""
from __future__ import annotations

from typing import Dict, List

from .logconf import get_logger
from .reference import (
    TRANSACTION_COST_BPS,
    target_allocation,
    tolerance_band,
)
from .schemas import (
    DriftReport,
    DriftStatus,
    PolicyLine,
    RebalancePlan,
    RebalanceTrade,
)

_log = get_logger()


def _classify(actual: float, target: float, lower: float, upper: float) -> DriftStatus:
    if actual > upper:
        return DriftStatus.BREACH_OVER
    if actual < lower:
        return DriftStatus.BREACH_UNDER
    if actual > target:
        return DriftStatus.OVERWEIGHT
    if actual < target:
        return DriftStatus.UNDERWEIGHT
    return DriftStatus.WITHIN_BAND


_STATUS_RATIONALE = {
    DriftStatus.WITHIN_BAND: "On target; no action.",
    DriftStatus.OVERWEIGHT: "Above target but inside the tolerance band — monitor.",
    DriftStatus.UNDERWEIGHT: "Below target but inside the tolerance band — monitor.",
    DriftStatus.BREACH_OVER: "Above the upper tolerance band — trim to rebalance.",
    DriftStatus.BREACH_UNDER: "Below the lower tolerance band — add to rebalance.",
}


def compute_drift(actual_weights: Dict[str, float], mandate: str) -> DriftReport:
    """Compare actual asset-class weights to the mandate's SAA and classify each sleeve."""
    targets = target_allocation(mandate)
    band = tolerance_band(mandate)

    # Union of sleeves: every target sleeve, plus any held sleeve not in the policy.
    sleeves = list(targets.keys())
    for s in actual_weights:
        if s not in sleeves:
            sleeves.append(s)

    lines: List[PolicyLine] = []
    n_breaches = 0
    sum_abs_drift = 0.0
    for sleeve in sleeves:
        target = float(targets.get(sleeve, 0.0))
        actual = float(actual_weights.get(sleeve, 0.0))
        lower = max(0.0, target - band)
        upper = min(1.0, target + band) if target > 0 else band   # off-policy sleeve: band as cap
        status = _classify(actual, target, lower, upper)
        drift = actual - target
        sum_abs_drift += abs(drift)
        if status in (DriftStatus.BREACH_OVER, DriftStatus.BREACH_UNDER):
            n_breaches += 1
        rationale = (
            f"Actual {actual:.1%} vs target {target:.1%} (band {lower:.1%}–{upper:.1%}). "
            + _STATUS_RATIONALE[status]
        )
        if target == 0.0 and actual > 0:
            rationale = (f"Held {actual:.1%} in '{sleeve}', which is not part of the "
                         f"{mandate} policy. " + _STATUS_RATIONALE[status])
        lines.append(PolicyLine(
            dimension="asset_class", bucket=sleeve, target_weight=round(target, 6),
            actual_weight=round(actual, 6), lower_band=round(lower, 6),
            upper_band=round(upper, 6), drift=round(drift, 6), status=status,
            rationale=rationale,
        ))

    report = DriftReport(
        mandate=mandate, lines=lines, n_breaches=n_breaches,
        active_share=round(0.5 * sum_abs_drift, 6),
        within_tolerance=(n_breaches == 0),
    )
    _log.info("drift: mandate=%s breaches=%d active_share=%.3f",
              mandate, n_breaches, report.active_share)
    return report


def build_rebalance_plan(drift: DriftReport, total_market_value: float) -> RebalancePlan:
    """Propose trades that move each BREACHED sleeve back to its target weight.

    A sleeve inside its band is left alone (no needless turnover). Trade value is signed:
    positive = buy, negative = sell. Each trade explains which band it breached and by how
    much it moves the sleeve.
    """
    trades: List[RebalanceTrade] = []
    total_buy = total_sell = 0.0

    for line in drift.lines:
        if line.status not in (DriftStatus.BREACH_OVER, DriftStatus.BREACH_UNDER):
            continue
        delta_w = line.target_weight - line.actual_weight        # +ve => need to buy
        trade_value = delta_w * total_market_value
        action = "buy" if trade_value > 0 else "sell"
        if trade_value > 0:
            total_buy += trade_value
        else:
            total_sell += -trade_value
        rationale = (
            f"{line.bucket}: {line.actual_weight:.1%} breached the "
            f"{'upper' if line.status == DriftStatus.BREACH_OVER else 'lower'} band "
            f"({line.upper_band:.1%}/{line.lower_band:.1%}); "
            f"{action} {abs(trade_value):,.0f} ({abs(delta_w):.1%} of the book) to return to "
            f"the {line.target_weight:.1%} target."
        )
        trades.append(RebalanceTrade(
            bucket=line.bucket, action=action,
            current_weight=line.actual_weight, target_weight=line.target_weight,
            current_value=round(line.actual_weight * total_market_value, 2),
            trade_value=round(trade_value, 2),
            trade_pct=round(abs(delta_w), 6), rationale=rationale,
        ))

    turnover = (total_sell / total_market_value) if total_market_value else 0.0
    est_cost = (total_buy + total_sell) * (TRANSACTION_COST_BPS / 10_000.0)
    if trades:
        summary = (f"{len(trades)} sleeve(s) breached tolerance — sell {total_sell:,.0f}, "
                   f"buy {total_buy:,.0f} (one-way turnover {turnover:.1%}); "
                   f"est. cost {est_cost:,.0f} at {TRANSACTION_COST_BPS:.0f} bps.")
    else:
        summary = "All sleeves within tolerance — no rebalancing required."

    return RebalancePlan(
        trades=trades, total_buy_value=round(total_buy, 2),
        total_sell_value=round(total_sell, 2), turnover_pct=round(turnover, 6),
        est_transaction_cost=round(est_cost, 2), summary=summary,
    )
