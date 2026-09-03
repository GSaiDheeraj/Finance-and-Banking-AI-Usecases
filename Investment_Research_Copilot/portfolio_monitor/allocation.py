"""
Deterministic allocation: price the book, then break it down by dimension.

NO LLM. Each holding is valued at `price * quantity`, where the price is taken in priority
order: a manual price on the input > a live market-data price > derived from cost basis (as
a last resort, flagged). Weights and the asset-class / sector / region / currency / position
breakdowns are then pure sums, so the same inputs always yield the same allocation.
"""
from __future__ import annotations

from typing import Dict, List, Optional

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

_CAP_ENUM = {"large_cap": MarketCap.LARGE_CAP, "mid_cap": MarketCap.MID_CAP,
             "small_cap": MarketCap.SMALL_CAP}
_INSTRUMENT_FROM_QT = {"EQUITY": InstrumentType.STOCK, "ETF": InstrumentType.ETF,
                       "MUTUALFUND": InstrumentType.MUTUAL_FUND}
# Which asset classes count as 'equity' vs 'debt' for the equity/debt exposure view.
_EQUITY_CLASSES = {AssetClass.EQUITY, AssetClass.REAL_ESTATE}
_DEBT_CLASSES = {AssetClass.FIXED_INCOME}


def price_holdings(
    holdings: List[Holding], quotes: Optional[Dict[str, Quote]] = None
) -> List[Holding]:
    """Fill price / market_value / price_source on each holding (in place, returns the list).

    Priority: manual `price` on input > market-data quote > derived per-share cost basis.
    A holding with no obtainable price is left with market_value=None and flagged 'missing'.
    """
    quotes = quotes or {}
    for h in holdings:
        if h.price is not None and h.price > 0:
            h.price_source = "input"
        else:
            q = quotes.get(h.symbol.upper())
            if q and q.price and q.price > 0:
                h.price = q.price
                h.price_source = "marketdata"
            elif h.cost_basis and h.quantity:
                h.price = h.cost_basis / h.quantity      # last-resort: book price per share
                h.price_source = "cost_basis"
            else:
                h.price = None
                h.price_source = "missing"
        h.market_value = (h.price * h.quantity) if (h.price is not None) else None
        # enrich classification from the quote when the input didn't carry it
        q = quotes.get(h.symbol.upper())
        if q:
            if not h.country and q.country:
                h.country = q.country
            if h.market_cap == MarketCap.UNKNOWN and q.market_cap:
                h.market_cap = _CAP_ENUM.get(classify_market_cap(q.market_cap), MarketCap.UNKNOWN)
            if h.instrument_type == InstrumentType.OTHER and q.quote_type:
                h.instrument_type = _INSTRUMENT_FROM_QT.get(q.quote_type.upper(), InstrumentType.OTHER)
    return holdings


def _bucket_list(totals: Dict[str, float], grand_total: float) -> List[AllocationBucket]:
    out = [
        AllocationBucket(
            name=name, market_value=round(mv, 2),
            weight=round(mv / grand_total, 6) if grand_total else 0.0,
        )
        for name, mv in totals.items()
    ]
    return sorted(out, key=lambda b: b.market_value, reverse=True)


def compute_allocation(
    holdings: List[Holding], base_currency: str = "USD"
) -> AllocationBreakdown:
    """Build the full allocation breakdown across all dimensions from priced holdings."""
    priced = [h for h in holdings if h.market_value is not None]
    total = sum(h.market_value for h in priced)
    all_mv = sum((h.market_value or 0.0) for h in holdings)
    unpriced = [h.symbol for h in holdings if h.market_value is None]

    by_ac: Dict[str, float] = {}
    by_sector: Dict[str, float] = {}
    by_region: Dict[str, float] = {}
    by_country: Dict[str, float] = {}
    by_cap: Dict[str, float] = {}
    by_ccy: Dict[str, float] = {}
    by_pos: Dict[str, float] = {}
    eq_debt: Dict[str, float] = {"equity": 0.0, "debt": 0.0, "cash": 0.0, "other": 0.0}

    for h in priced:
        mv = h.market_value
        by_ac[h.asset_class.value] = by_ac.get(h.asset_class.value, 0.0) + mv
        by_sector[h.sector or "Unclassified"] = by_sector.get(h.sector or "Unclassified", 0.0) + mv
        by_region[h.region.value] = by_region.get(h.region.value, 0.0) + mv
        by_country[h.country or "Unclassified"] = by_country.get(h.country or "Unclassified", 0.0) + mv
        by_ccy[h.currency] = by_ccy.get(h.currency, 0.0) + mv
        by_pos[h.symbol] = by_pos.get(h.symbol, 0.0) + mv
        if h.asset_class == AssetClass.EQUITY:
            by_cap[h.market_cap.value] = by_cap.get(h.market_cap.value, 0.0) + mv
        # equity / debt / cash bucketing
        if h.asset_class in _EQUITY_CLASSES:
            eq_debt["equity"] += mv
        elif h.asset_class in _DEBT_CLASSES:
            eq_debt["debt"] += mv
        elif h.asset_class == AssetClass.CASH:
            eq_debt["cash"] += mv
        else:
            eq_debt["other"] += mv

    # Stamp each holding's weight for downstream concentration checks.
    for h in priced:
        h.weight = round(h.market_value / total, 6) if total else 0.0

    coverage = (total / all_mv) if all_mv else (1.0 if not holdings else 0.0)
    if unpriced:
        _log.warning("allocation: %d unpriced holding(s): %s", len(unpriced), unpriced)

    eq_debt_w = {k: round(v / total, 6) for k, v in eq_debt.items() if total} if total else {}
    return AllocationBreakdown(
        total_market_value=round(total, 2),
        base_currency=base_currency,
        by_asset_class=_bucket_list(by_ac, total),
        by_sector=_bucket_list(by_sector, total),
        by_region=_bucket_list(by_region, total),
        by_country=_bucket_list(by_country, total),
        by_market_cap=_bucket_list(by_cap, total),
        by_currency=_bucket_list(by_ccy, total),
        by_position=_bucket_list(by_pos, total),
        equity_debt=eq_debt_w,
        priced_coverage=round(coverage, 4),
        unpriced_symbols=unpriced,
    )


def asset_class_weights(allocation: AllocationBreakdown) -> Dict[str, float]:
    return {b.name: b.weight for b in allocation.by_asset_class}


def sector_weights(allocation: AllocationBreakdown) -> Dict[str, float]:
    return {b.name: b.weight for b in allocation.by_sector}


def position_weights(allocation: AllocationBreakdown) -> Dict[str, float]:
    return {b.name: b.weight for b in allocation.by_position}
