"""
Shared construction helpers used by BOTH constructors (deterministic + LLM).

NO LLM. Turns a set of `SelectedPosition`s into the same artefacts the monitoring engine
produces — an asset-class sleeve allocation and a full `RiskMetrics` — by reusing the
deterministic allocation/risk code. This keeps the constructed portfolio's expected metrics
computed exactly the same way the live portfolio's metrics are, so the two are comparable.
"""
from __future__ import annotations

from typing import List

from ..allocation import compute_allocation
from ..marketdata import get_histories, get_price_history, get_quotes
from ..reference import benchmark_symbol
from ..risk import compute_risk_metrics
from ..schemas import AllocationBucket, Holding, RiskMetrics, SelectedPosition


def positions_to_holdings(positions: List[SelectedPosition]) -> List[Holding]:
    return [
        Holding(
            symbol=p.symbol, name=p.name, asset_class=p.asset_class, sector=p.sector,
            region=p.region, country=p.country, quantity=(p.shares or 0.0), price=p.price,
            market_value=(p.shares * p.price if (p.shares and p.price) else p.amount),
            price_source="input",
        )
        for p in positions
    ]


def _bucketise(pairs) -> List[AllocationBucket]:
    totals: dict = {}
    for name, w in pairs:
        totals[name] = totals.get(name, 0.0) + w
    return sorted([AllocationBucket(name=k, weight=round(v, 6)) for k, v in totals.items()],
                  key=lambda b: b.weight, reverse=True)


def sleeve_allocation(positions: List[SelectedPosition]) -> List[AllocationBucket]:
    return _bucketise((p.asset_class.value, p.target_weight) for p in positions)


def cap_allocation(positions: List[SelectedPosition]) -> List[AllocationBucket]:
    """By market-cap tier, over the EQUITY sleeve only (debt/cash/alts are not cap-classified)."""
    from ..schemas import AssetClass
    return _bucketise((p.market_cap.value, p.target_weight)
                      for p in positions if p.asset_class == AssetClass.EQUITY)


def country_allocation(positions: List[SelectedPosition]) -> List[AllocationBucket]:
    return _bucketise(((p.country or "Unclassified"), p.target_weight) for p in positions)


def compute_expected_metrics(
    positions: List[SelectedPosition], mandate: str, base_currency: str = "USD"
) -> RiskMetrics:
    """Expected forward-looking risk metrics for a constructed portfolio (historical proxy)."""
    holdings = positions_to_holdings(positions)
    allocation = compute_allocation(holdings, base_currency)
    symbols = [p.symbol for p in positions]
    histories = get_histories(symbols)
    quotes = get_quotes(symbols)
    bench_hist = get_price_history(benchmark_symbol(mandate))
    return compute_risk_metrics(holdings, allocation, histories, bench_hist, quotes)
