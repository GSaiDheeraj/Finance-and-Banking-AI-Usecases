"""
Deterministic allocation drift vs the Investment Policy Statement, plus a plan.

No LLM. Given actual asset-class weights and the mandate's strategic asset
allocation (targets plus a tolerance band from ``reference``), each sleeve is
classified as within-band / overweight / underweight / breached, and trades
are proposed to bring breached sleeves back to target. Every line and trade
carries a rationale string so the agent always states why.
"""

from __future__ import annotations

from .logconf import get_logger
from .reference import TRANSACTION_COST_BPS, target_allocation, tolerance_band
from .schemas import (
    DriftReport,
    DriftStatus,
    PolicyLine,
    RebalancePlan,
    RebalanceTrade,
)

_log = get_logger()

BREACH_STATUSES = frozenset({DriftStatus.BREACH_OVER, DriftStatus.BREACH_UNDER})

_STATUS_RATIONALE = {
    DriftStatus.WITHIN_BAND: "On target; no action.",
    DriftStatus.OVERWEIGHT: "Above target but inside the tolerance band — monitor.",
    DriftStatus.UNDERWEIGHT: "Below target but inside the tolerance band — monitor.",
    DriftStatus.BREACH_OVER: "Above the upper tolerance band — trim to rebalance.",
    DriftStatus.BREACH_UNDER: "Below the lower tolerance band — add to rebalance.",
}


def compute_drift(actual_weights: dict[str, float], mandate: str) -> DriftReport:
    """Compare actual asset-class weights to the mandate's SAA and classify each sleeve."""
    targets = target_allocation(mandate)
    band = tolerance_band(mandate)
    sleeves = _all_sleeves(targets, actual_weights)

    lines = [_policy_line(sleeve, actual_weights, targets, band, mandate) for sleeve in sleeves]
    n_breaches = sum(1 for line in lines if line.status in BREACH_STATUSES)
    active_share = round(0.5 * sum(abs(line.drift) for line in lines), 6)

    report = DriftReport(
        mandate=mandate,
        lines=lines,
        n_breaches=n_breaches,
        active_share=active_share,
        within_tolerance=(n_breaches == 0),
    )
    _log.info(
        "drift: mandate=%s breaches=%d active_share=%.3f",
        mandate,
        n_breaches,
        report.active_share,
    )
    return report


def build_rebalance_plan(drift: DriftReport, total_market_value: float) -> RebalancePlan:
    """Propose trades moving each breached sleeve back to its target weight.

    Sleeves inside their band are left alone to avoid needless turnover. Trade
    value is signed: positive means buy, negative means sell.
    """
    trades = [
        _rebalance_trade(line, total_market_value)
        for line in drift.lines
        if line.status in BREACH_STATUSES
    ]
    total_buy = sum(trade.trade_value for trade in trades if trade.trade_value > 0)
    total_sell = sum(-trade.trade_value for trade in trades if trade.trade_value < 0)

    turnover = (total_sell / total_market_value) if total_market_value else 0.0
    estimated_cost = (total_buy + total_sell) * (TRANSACTION_COST_BPS / 10_000.0)
    if trades:
        summary = (
            f"{len(trades)} sleeve(s) breached tolerance — sell {total_sell:,.0f}, "
            f"buy {total_buy:,.0f} (one-way turnover {turnover:.1%}); "
            f"est. cost {estimated_cost:,.0f} at {TRANSACTION_COST_BPS:.0f} bps."
        )
    else:
        summary = "All sleeves within tolerance — no rebalancing required."

    return RebalancePlan(
        trades=trades,
        total_buy_value=round(total_buy, 2),
        total_sell_value=round(total_sell, 2),
        turnover_pct=round(turnover, 6),
        est_transaction_cost=round(estimated_cost, 2),
        summary=summary,
    )


def _all_sleeves(targets: dict[str, float], actual_weights: dict[str, float]) -> list[str]:
    sleeves = list(targets.keys())
    for sleeve in actual_weights:
        if sleeve not in sleeves:
            sleeves.append(sleeve)
    return sleeves


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


def _sleeve_band(target: float, band: float) -> tuple[float, float]:
    if target > 0:
        return max(0.0, target - band), min(1.0, target + band)
    # Off-policy sleeve: the band itself acts as the cap.
    return 0.0, band


def _policy_line(
    sleeve: str,
    actual_weights: dict[str, float],
    targets: dict[str, float],
    band: float,
    mandate: str,
) -> PolicyLine:
    target = float(targets.get(sleeve, 0.0))
    actual = float(actual_weights.get(sleeve, 0.0))
    lower, upper = _sleeve_band(target, band)
    status = _classify(actual, target, lower, upper)
    if target == 0.0 and actual > 0:
        rationale = (
            f"Held {actual:.1%} in '{sleeve}', which is not part of the "
            f"{mandate} policy. {_STATUS_RATIONALE[status]}"
        )
    else:
        rationale = (
            f"Actual {actual:.1%} vs target {target:.1%} "
            f"(band {lower:.1%}–{upper:.1%}). {_STATUS_RATIONALE[status]}"
        )
    return PolicyLine(
        dimension="asset_class",
        bucket=sleeve,
        target_weight=round(target, 6),
        actual_weight=round(actual, 6),
        lower_band=round(lower, 6),
        upper_band=round(upper, 6),
        drift=round(actual - target, 6),
        status=status,
        rationale=rationale,
    )


def _rebalance_trade(line: PolicyLine, total_market_value: float) -> RebalanceTrade:
    weight_delta = line.target_weight - line.actual_weight  # positive means buy
    trade_value = weight_delta * total_market_value
    action = "buy" if trade_value > 0 else "sell"
    band_edge = "upper" if line.status == DriftStatus.BREACH_OVER else "lower"
    rationale = (
        f"{line.bucket}: {line.actual_weight:.1%} breached the {band_edge} band "
        f"({line.upper_band:.1%}/{line.lower_band:.1%}); "
        f"{action} {abs(trade_value):,.0f} ({abs(weight_delta):.1%} of the book) "
        f"to return to the {line.target_weight:.1%} target."
    )
    return RebalanceTrade(
        bucket=line.bucket,
        action=action,
        current_weight=line.actual_weight,
        target_weight=line.target_weight,
        current_value=round(line.actual_weight * total_market_value, 2),
        trade_value=round(trade_value, 2),
        trade_pct=round(abs(weight_delta), 6),
        rationale=rationale,
    )
