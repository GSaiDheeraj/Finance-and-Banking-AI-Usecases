"""
Deterministic portfolio risk metrics + risk-limit checks.

NO LLM. Given priced holdings (weights) and daily price history (from `marketdata`), this
module assembles annualised volatility, max drawdown, 1-day 95% historical VaR, beta and
tracking error vs the mandate benchmark, dividend yield, and concentration (HHI / effective
N / top-1 / top-5). It then checks each metric and each weight against the configured risk
limits, producing `RiskLimitCheck`s with a deterministic severity.

Everything is a pure function of the inputs, so the metrics and the breach set are
reproducible; the only non-determinism is which day's market history Yahoo returns, and the
data-coverage figure records how much of the book had usable history.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from . import analytics
from .logconf import get_logger
from .marketdata import Quote
from .reference import risk_limits, vol_ceiling
from .schemas import (
    AllocationBreakdown,
    Holding,
    RiskLimitCheck,
    RiskMetrics,
    Severity,
)

_log = get_logger()


def compute_risk_metrics(
    holdings: List[Holding],
    allocation: AllocationBreakdown,
    histories: Dict[str, List[float]],
    benchmark_history: Optional[List[float]] = None,
    quotes: Optional[Dict[str, Quote]] = None,
) -> RiskMetrics:
    """Compute portfolio-level risk metrics from weights + price history."""
    quotes = quotes or {}
    priced = [h for h in holdings if h.market_value is not None and h.weight]
    total_mv = allocation.total_market_value
    weights = {h.symbol.upper(): float(h.weight or 0.0) for h in priced}

    # returns per symbol from history
    returns_by_symbol = {
        sym: analytics.daily_returns(hist)
        for sym, hist in histories.items() if hist
    }
    covered_mv = sum(
        (h.market_value or 0.0) for h in priced
        if returns_by_symbol.get(h.symbol.upper())
    )
    data_coverage = (covered_mv / total_mv) if total_mv else 0.0

    port_rets = analytics.portfolio_returns(weights, returns_by_symbol)
    bench_rets = analytics.daily_returns(benchmark_history) if benchmark_history else []

    # rebuild an approximate portfolio price path from returns for drawdown/VaR
    port_path: List[float] = []
    if port_rets:
        price = 100.0
        port_path = [price]
        for r in port_rets:
            price *= (1.0 + r)
            port_path.append(price)

    # concentration from position weights
    pos_weights = [b.weight for b in allocation.by_position]
    pos_sorted = sorted(allocation.by_position, key=lambda b: b.weight, reverse=True)
    top1 = pos_sorted[0].weight if pos_sorted else None
    top5 = sum(b.weight for b in pos_sorted[:5]) if pos_sorted else None
    largest = pos_sorted[0].name if pos_sorted else None

    # weighted dividend yield (uses live yield where available)
    div_yield = _weighted_dividend_yield(priced, quotes, total_mv)

    metrics = RiskMetrics(
        lookback_days=len(port_rets),
        n_holdings=len(priced),
        market_value=round(total_mv, 2),
        volatility_annual=_r(analytics.annualised_volatility(port_rets)),
        max_drawdown=_r(analytics.max_drawdown(port_path)) if port_path else None,
        var_95_1d_pct=_r(analytics.historical_var(port_rets, 0.95)),
        expected_return_annual=_r(analytics.annualised_return(port_rets)),
        dividend_yield=_r(div_yield),
        beta=_r(analytics.beta(port_rets, bench_rets)) if bench_rets else None,
        tracking_error=_r(analytics.tracking_error(port_rets, bench_rets)) if bench_rets else None,
        concentration_hhi=_r(analytics.herfindahl(pos_weights)),
        effective_n=_r(analytics.effective_n(pos_weights)),
        top1_weight=_r(top1),
        top5_weight=_r(top5),
        largest_position=largest,
        data_coverage=round(data_coverage, 4),
    )
    if metrics.var_95_1d_pct is not None and total_mv:
        metrics.var_95_1d_value = round(metrics.var_95_1d_pct * total_mv, 2)
    if data_coverage < 0.5:
        metrics.notes.append(
            f"Only {data_coverage:.0%} of market value had usable price history — "
            "volatility / drawdown / VaR are partial. Provide tickers with market data for full coverage."
        )
    _log.info("risk: vol=%s maxDD=%s VaR95=%s beta=%s HHI=%s coverage=%.0f%%",
              metrics.volatility_annual, metrics.max_drawdown, metrics.var_95_1d_pct,
              metrics.beta, metrics.concentration_hhi, data_coverage * 100)
    return metrics


def check_limits(
    metrics: RiskMetrics,
    allocation: AllocationBreakdown,
    mandate: str,
) -> List[RiskLimitCheck]:
    """Check metrics & weights against configured risk limits."""
    limits = risk_limits()
    checks: List[RiskLimitCheck] = []

    # single-name cap
    top1 = metrics.top1_weight
    cap = limits.get("single_name_max_weight")
    checks.append(_check(
        "single_name_max_weight", top1, cap, higher_worse=True,
        warn_frac=0.9,
        detail=(f"Largest position {metrics.largest_position or '—'} at {top1:.1%}"
                if top1 is not None else "No priced positions."),
    ))

    # sector cap (worst sector)
    worst_sector = allocation.by_sector[0] if allocation.by_sector else None
    sec_cap = limits.get("sector_max_weight")
    checks.append(_check(
        "sector_max_weight", worst_sector.weight if worst_sector else None, sec_cap,
        higher_worse=True, warn_frac=0.9,
        detail=(f"Largest sector '{worst_sector.name}' at {worst_sector.weight:.1%}"
                if worst_sector else "No sector data."),
    ))

    # volatility ceiling (mandate-specific)
    checks.append(_check(
        "max_volatility_annual", metrics.volatility_annual, vol_ceiling(mandate),
        higher_worse=True, warn_frac=0.9,
        detail=(f"Annualised volatility {metrics.volatility_annual:.1%}"
                if metrics.volatility_annual is not None else "Volatility not available."),
    ))

    # drawdown alert
    dd = abs(metrics.max_drawdown) if metrics.max_drawdown is not None else None
    checks.append(_check(
        "max_drawdown_alert", dd, limits.get("max_drawdown_alert"),
        higher_worse=True, warn_frac=0.8,
        detail=(f"Trailing max drawdown {metrics.max_drawdown:.1%}"
                if metrics.max_drawdown is not None else "Drawdown not available."),
    ))

    # VaR limit
    checks.append(_check(
        "var_95_1d_limit_pct", metrics.var_95_1d_pct, limits.get("var_95_1d_limit_pct"),
        higher_worse=True, warn_frac=0.9,
        detail=(f"1-day 95% VaR {metrics.var_95_1d_pct:.2%} "
                f"({metrics.var_95_1d_value:,.0f})" if metrics.var_95_1d_pct is not None
                else "VaR not available."),
    ))

    # tracking error
    if metrics.tracking_error is not None:
        checks.append(_check(
            "max_tracking_error", metrics.tracking_error, limits.get("max_tracking_error"),
            higher_worse=True, warn_frac=0.9,
            detail=f"Tracking error {metrics.tracking_error:.1%} vs benchmark.",
        ))

    # minimum cash buffer
    min_cash = limits.get("min_cash_weight", 0.0)
    cash_w = next((b.weight for b in allocation.by_asset_class if b.name == "cash"), 0.0)
    if min_cash and min_cash > 0:
        breached = cash_w < min_cash
        checks.append(RiskLimitCheck(
            name="min_cash_weight", observed=round(cash_w, 4), limit=min_cash,
            higher_is_worse=False, status="breach" if breached else "ok",
            severity=Severity.MEDIUM if breached else Severity.INFO,
            detail=f"Cash buffer {cash_w:.1%} vs minimum {min_cash:.1%}.",
        ))

    return checks


# --------------------------------------------------------------------------- #
def _check(name, observed, limit, higher_worse, warn_frac, detail) -> RiskLimitCheck:
    """Build a limit check: breach if past the limit, warn if within `warn_frac` of it."""
    if observed is None or limit is None:
        return RiskLimitCheck(name=name, observed=observed, limit=limit,
                              higher_is_worse=higher_worse, status="not_available",
                              severity=Severity.INFO, detail=detail)
    if higher_worse:
        if observed > limit:
            status, sev = "breach", Severity.HIGH
        elif observed >= warn_frac * limit:
            status, sev = "warn", Severity.MEDIUM
        else:
            status, sev = "ok", Severity.INFO
    else:
        if observed < limit:
            status, sev = "breach", Severity.HIGH
        elif observed <= (2 - warn_frac) * limit:
            status, sev = "warn", Severity.MEDIUM
        else:
            status, sev = "ok", Severity.INFO
    return RiskLimitCheck(name=name, observed=round(observed, 4), limit=limit,
                          higher_is_worse=higher_worse, status=status, severity=sev,
                          detail=detail)


def _weighted_dividend_yield(holdings, quotes, total_mv) -> Optional[float]:
    if not total_mv:
        return None
    from .reference import assumed_yields
    yields = assumed_yields()
    acc = 0.0
    for h in holdings:
        mv = h.market_value or 0.0
        q = quotes.get(h.symbol.upper())
        if q and q.dividend_yield:
            y = q.dividend_yield if q.dividend_yield < 1 else q.dividend_yield / 100.0
        else:
            y = yields.get(h.asset_class.value, 0.0)
        acc += mv * y
    return acc / total_mv if total_mv else None


def _r(v, ndigits: int = 4) -> Optional[float]:
    return round(v, ndigits) if isinstance(v, (int, float)) else None
