"""
Deterministic allocation: price the book, then break it down by dimension.

No LLM. Each holding is valued at ``price * quantity``, with the price taken
in priority order: a manual price on the input, then a live market-data
price, then a per-share cost basis as a last resort (flagged). Weights and
breakdowns are pure sums, so the same inputs always yield the same allocation.
"""

from __future__ import annotations

from .logconf import get_logger
from .marketdata import Quote
from .reference import classify_market_cap
from .schemas import (
    AllocationBreakdown,
    AllocationBucket,
    AssetClass,
    Holding,
    InstrumentType,
    MarketCap,
)

_log = get_logger()

_CAP_ENUM = {
    "large_cap": MarketCap.LARGE_CAP,
    "mid_cap": MarketCap.MID_CAP,
    "small_cap": MarketCap.SMALL_CAP,
}
_INSTRUMENT_FROM_QUOTE_TYPE = {
    "EQUITY": InstrumentType.STOCK,
    "ETF": InstrumentType.ETF,
    "MUTUALFUND": InstrumentType.MUTUAL_FUND,
}
_EQUITY_CLASSES = frozenset({AssetClass.EQUITY, AssetClass.REAL_ESTATE})
_DEBT_CLASSES = frozenset({AssetClass.FIXED_INCOME})
_UNCLASSIFIED = "Unclassified"


def price_holdings(
    holdings: list[Holding], quotes: dict[str, Quote] | None = None
) -> list[Holding]:
    """Fill price / market_value / price_source on each holding.

    Priority: manual ``price`` on the input, then the market-data quote, then
    the per-share cost basis. Holdings with no obtainable price keep
    ``market_value=None`` and are flagged ``missing``.

    Mutates the given holdings in place (stamping classification and weights
    downstream) and returns the same list for chaining.
    """
    active_quotes = quotes or {}
    for holding in holdings:
        _resolve_price(holding, active_quotes)
        _enrich_from_quote(holding, active_quotes)
    return holdings


def compute_allocation(holdings: list[Holding], base_currency: str = "USD") -> AllocationBreakdown:
    """Build the full allocation breakdown across all dimensions from priced holdings."""
    priced = [holding for holding in holdings if holding.market_value is not None]
    total_market_value = sum(holding.market_value for holding in priced)
    all_market_value = sum((holding.market_value or 0.0) for holding in holdings)
    unpriced_symbols = [holding.symbol for holding in holdings if holding.market_value is None]

    totals = _accumulate_totals(priced)
    _stamp_weights(priced, total_market_value)

    if unpriced_symbols:
        _log.warning(
            "allocation: %d unpriced holding(s): %s", len(unpriced_symbols), unpriced_symbols
        )
    coverage = _coverage_ratio(total_market_value, all_market_value, bool(holdings))
    return AllocationBreakdown(
        total_market_value=round(total_market_value, 2),
        base_currency=base_currency,
        by_asset_class=_bucket_list(totals["asset_class"], total_market_value),
        by_sector=_bucket_list(totals["sector"], total_market_value),
        by_region=_bucket_list(totals["region"], total_market_value),
        by_country=_bucket_list(totals["country"], total_market_value),
        by_market_cap=_bucket_list(totals["market_cap"], total_market_value),
        by_currency=_bucket_list(totals["currency"], total_market_value),
        by_position=_bucket_list(totals["position"], total_market_value),
        equity_debt=_equity_debt_weights(totals["equity_debt"], total_market_value),
        priced_coverage=round(coverage, 4),
        unpriced_symbols=unpriced_symbols,
    )


def _coverage_ratio(priced_total: float, overall_total: float, has_holdings: bool) -> float:
    """Fraction of market value that could be priced (1.0 when there is nothing to price)."""
    if overall_total:
        return priced_total / overall_total
    if not has_holdings:
        return 1.0
    return 0.0


def _bucket_weights(buckets: list[AllocationBucket]) -> dict[str, float]:
    """Name → weight mapping for one allocation dimension."""
    return {bucket.name: bucket.weight for bucket in buckets}


def asset_class_weights(allocation: AllocationBreakdown) -> dict[str, float]:
    return _bucket_weights(allocation.by_asset_class)


def sector_weights(allocation: AllocationBreakdown) -> dict[str, float]:
    return _bucket_weights(allocation.by_sector)


def position_weights(allocation: AllocationBreakdown) -> dict[str, float]:
    return _bucket_weights(allocation.by_position)


def _resolve_price(holding: Holding, quotes: dict[str, Quote]) -> None:
    if holding.price is not None and holding.price > 0:
        holding.price_source = "input"
    else:
        quote = quotes.get(holding.symbol.upper())
        if quote and quote.price and quote.price > 0:
            holding.price = quote.price
            holding.price_source = "marketdata"
        elif holding.cost_basis and holding.quantity:
            holding.price = holding.cost_basis / holding.quantity
            holding.price_source = "cost_basis"
        else:
            holding.price = None
            holding.price_source = "missing"
    holding.market_value = holding.price * holding.quantity if holding.price is not None else None


def _enrich_from_quote(holding: Holding, quotes: dict[str, Quote]) -> None:
    quote = quotes.get(holding.symbol.upper())
    if not quote:
        return
    if not holding.country and quote.country:
        holding.country = quote.country
    if holding.market_cap == MarketCap.UNKNOWN and quote.market_cap:
        holding.market_cap = _CAP_ENUM.get(classify_market_cap(quote.market_cap), MarketCap.UNKNOWN)
    if holding.instrument_type == InstrumentType.OTHER and quote.quote_type:
        holding.instrument_type = _INSTRUMENT_FROM_QUOTE_TYPE.get(
            quote.quote_type.upper(), InstrumentType.OTHER
        )


def _accumulate_totals(priced: list[Holding]) -> dict[str, dict[str, float]]:
    totals: dict[str, dict[str, float]] = {
        "asset_class": {},
        "sector": {},
        "region": {},
        "country": {},
        "market_cap": {},
        "currency": {},
        "position": {},
        "equity_debt": {"equity": 0.0, "debt": 0.0, "cash": 0.0, "other": 0.0},
    }
    for holding in priced:
        market_value = holding.market_value or 0.0
        _add(totals["asset_class"], holding.asset_class.value, market_value)
        _add(totals["sector"], holding.sector or _UNCLASSIFIED, market_value)
        _add(totals["region"], holding.region.value, market_value)
        _add(totals["country"], holding.country or _UNCLASSIFIED, market_value)
        _add(totals["currency"], holding.currency, market_value)
        _add(totals["position"], holding.symbol, market_value)
        if holding.asset_class == AssetClass.EQUITY:
            _add(totals["market_cap"], holding.market_cap.value, market_value)
        _add(totals["equity_debt"], _equity_debt_bucket(holding.asset_class), market_value)
    return totals


def _add(totals: dict[str, float], key: str, market_value: float) -> None:
    totals[key] = totals.get(key, 0.0) + market_value


def _equity_debt_bucket(asset_class: AssetClass) -> str:
    if asset_class in _EQUITY_CLASSES:
        return "equity"
    if asset_class in _DEBT_CLASSES:
        return "debt"
    if asset_class == AssetClass.CASH:
        return "cash"
    return "other"


def _weight(value: float, grand_total: float, ndigits: int = 6) -> float:
    """Share of `value` in `grand_total` (0.0 when there is nothing to share)."""
    if not grand_total:
        return 0.0
    return round(value / grand_total, ndigits)


def _stamp_weights(priced: list[Holding], total_market_value: float) -> None:
    for holding in priced:
        holding.weight = _weight(holding.market_value or 0.0, total_market_value)


def _bucket_list(totals: dict[str, float], grand_total: float) -> list[AllocationBucket]:
    buckets = [
        AllocationBucket(
            name=name,
            market_value=round(market_value, 2),
            weight=_weight(market_value, grand_total),
        )
        for name, market_value in totals.items()
    ]
    return sorted(buckets, key=lambda bucket: bucket.market_value, reverse=True)


def _equity_debt_weights(
    equity_debt_totals: dict[str, float], grand_total: float
) -> dict[str, float]:
    if not grand_total:
        return {}
    return {name: _weight(value, grand_total) for name, value in equity_debt_totals.items()}
