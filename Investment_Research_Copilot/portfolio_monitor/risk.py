"""
Deterministic portfolio risk metrics and risk-limit checks.

No LLM. Given priced holdings (weights) and daily price history (from
``marketdata``), this module assembles annualised volatility, max drawdown,
1-day 95% historical VaR, beta and tracking error vs the mandate benchmark,
dividend yield, and concentration (HHI / effective N / top-1 / top-5). It then
checks each metric against the configured risk limits.

Everything is a pure function of the inputs, so metrics and breaches are
reproducible; the only non-determinism is which day's market history the
provider returns, and the data-coverage figure records how much of the book
had usable history.
"""

from __future__ import annotations

from . import analytics
from .logconf import get_logger
from .marketdata import Quote
from .reference import assumed_yields, risk_limits, vol_ceiling
from .schemas import (
    AllocationBreakdown,
    Holding,
    RiskLimitCheck,
    RiskMetrics,
    Severity,
)

_log = get_logger()

BASE_INDEX_PRICE = 100.0
LOW_COVERAGE_THRESHOLD = 0.5
NEAR_LIMIT_WARN_FRACTION = 0.9
DRAWDOWN_WARN_FRACTION = 0.8
DIVIDEND_YIELD_PERCENT_THRESHOLD = 1.0


def compute_risk_metrics(
    holdings: list[Holding],
    allocation: AllocationBreakdown,
    histories: dict[str, list[float]],
    benchmark_history: list[float] | None = None,
    quotes: dict[str, Quote] | None = None,
) -> RiskMetrics:
    """Compute portfolio-level risk metrics from weights and price history."""
    active_quotes = quotes or {}
    priced_holdings = [h for h in holdings if h.market_value is not None and h.weight]
    total_market_value = allocation.total_market_value
    weights = {h.symbol.upper(): float(h.weight or 0.0) for h in priced_holdings}

    returns_by_symbol = {
        symbol: analytics.daily_returns(history) for symbol, history in histories.items() if history
    }
    data_coverage = _coverage_ratio(priced_holdings, returns_by_symbol, total_market_value)

    portfolio_returns = analytics.portfolio_returns(weights, returns_by_symbol)
    benchmark_returns = analytics.daily_returns(benchmark_history) if benchmark_history else []
    price_path = _rebuild_price_path(portfolio_returns)

    metrics = RiskMetrics(
        lookback_days=len(portfolio_returns),
        n_holdings=len(priced_holdings),
        market_value=round(total_market_value, 2),
        volatility_annual=_rounded(analytics.annualised_volatility(portfolio_returns)),
        max_drawdown=_rounded(analytics.max_drawdown(price_path)) if price_path else None,
        var_95_1d_pct=_rounded(analytics.historical_var(portfolio_returns, 0.95)),
        expected_return_annual=_rounded(analytics.annualised_return(portfolio_returns)),
        dividend_yield=_rounded(
            _weighted_dividend_yield(priced_holdings, active_quotes, total_market_value)
        ),
        beta=_rounded(analytics.beta(portfolio_returns, benchmark_returns))
        if benchmark_returns
        else None,
        tracking_error=_rounded(analytics.tracking_error(portfolio_returns, benchmark_returns))
        if benchmark_returns
        else None,
        concentration_hhi=_rounded(_concentration_weights(allocation, "hhi")),
        effective_n=_rounded(_concentration_weights(allocation, "effective_n")),
        top1_weight=_rounded(_top_weight(allocation, 1)),
        top5_weight=_rounded(_top_weight(allocation, 5)),
        largest_position=_largest_position_name(allocation),
        data_coverage=round(data_coverage, 4),
    )
    if metrics.var_95_1d_pct is not None and total_market_value:
        metrics.var_95_1d_value = round(metrics.var_95_1d_pct * total_market_value, 2)
    if data_coverage < LOW_COVERAGE_THRESHOLD:
        metrics.notes.append(
            f"Only {data_coverage:.0%} of market value had usable price history — "
            "volatility / drawdown / VaR are partial. Provide tickers with market data "
            "for full coverage."
        )
    _log.info(
        "risk: vol=%s maxDD=%s VaR95=%s beta=%s HHI=%s coverage=%.0f%%",
        metrics.volatility_annual,
        metrics.max_drawdown,
        metrics.var_95_1d_pct,
        metrics.beta,
        metrics.concentration_hhi,
        data_coverage * 100,
    )
    return metrics


def check_limits(
    metrics: RiskMetrics,
    allocation: AllocationBreakdown,
    mandate: str,
) -> list[RiskLimitCheck]:
    """Check metrics and weights against configured risk limits."""
    limits = risk_limits()
    checks = [
        _check(
            "single_name_max_weight",
            metrics.top1_weight,
            limits.get("single_name_max_weight"),
            higher_worse=True,
            warn_fraction=NEAR_LIMIT_WARN_FRACTION,
            detail=(
                f"Largest position {metrics.largest_position or '—'} at {metrics.top1_weight:.1%}"
                if metrics.top1_weight is not None
                else "No priced positions."
            ),
        ),
        _check(
            "sector_max_weight",
            allocation.by_sector[0].weight if allocation.by_sector else None,
            limits.get("sector_max_weight"),
            higher_worse=True,
            warn_fraction=NEAR_LIMIT_WARN_FRACTION,
            detail=_sector_detail(allocation),
        ),
        _check(
            "max_volatility_annual",
            metrics.volatility_annual,
            vol_ceiling(mandate),
            higher_worse=True,
            warn_fraction=NEAR_LIMIT_WARN_FRACTION,
            detail=(
                f"Annualised volatility {metrics.volatility_annual:.1%}"
                if metrics.volatility_annual is not None
                else "Volatility not available."
            ),
        ),
        _check(
            "max_drawdown_alert",
            abs(metrics.max_drawdown) if metrics.max_drawdown is not None else None,
            limits.get("max_drawdown_alert"),
            higher_worse=True,
            warn_fraction=DRAWDOWN_WARN_FRACTION,
            detail=(
                f"Trailing max drawdown {metrics.max_drawdown:.1%}"
                if metrics.max_drawdown is not None
                else "Drawdown not available."
            ),
        ),
        _check(
            "var_95_1d_limit_pct",
            metrics.var_95_1d_pct,
            limits.get("var_95_1d_limit_pct"),
            higher_worse=True,
            warn_fraction=NEAR_LIMIT_WARN_FRACTION,
            detail=_var_detail(metrics),
        ),
    ]
    if metrics.tracking_error is not None:
        checks.append(
            _check(
                "max_tracking_error",
                metrics.tracking_error,
                limits.get("max_tracking_error"),
                higher_worse=True,
                warn_fraction=NEAR_LIMIT_WARN_FRACTION,
                detail=f"Tracking error {metrics.tracking_error:.1%} vs benchmark.",
            )
        )
    cash_check = _cash_buffer_check(allocation, limits.get("min_cash_weight", 0.0))
    if cash_check is not None:
        checks.append(cash_check)
    return checks


def _coverage_ratio(
    priced_holdings: list[Holding],
    returns_by_symbol: dict[str, list[float]],
    total_market_value: float,
) -> float:
    if not total_market_value:
        return 0.0
    covered_value = sum(
        (holding.market_value or 0.0)
        for holding in priced_holdings
        if returns_by_symbol.get(holding.symbol.upper())
    )
    return covered_value / total_market_value


def _rebuild_price_path(portfolio_returns: list[float]) -> list[float]:
    if not portfolio_returns:
        return []
    price_path = [BASE_INDEX_PRICE]
    price = BASE_INDEX_PRICE
    for period_return in portfolio_returns:
        price *= 1.0 + period_return
        price_path.append(price)
    return price_path


def _concentration_weights(allocation: AllocationBreakdown, kind: str) -> float | None:
    position_weights = [bucket.weight for bucket in allocation.by_position]
    if kind == "hhi":
        return analytics.herfindahl(position_weights)
    return analytics.effective_n(position_weights)


def _top_weight(allocation: AllocationBreakdown, count: int) -> float | None:
    ranked = sorted(allocation.by_position, key=lambda bucket: bucket.weight, reverse=True)
    if not ranked:
        return None
    if count == 1:
        return ranked[0].weight
    return sum(bucket.weight for bucket in ranked[:count])


def _largest_position_name(allocation: AllocationBreakdown) -> str | None:
    ranked = sorted(allocation.by_position, key=lambda bucket: bucket.weight, reverse=True)
    return ranked[0].name if ranked else None


def _sector_detail(allocation: AllocationBreakdown) -> str:
    if not allocation.by_sector:
        return "No sector data."
    largest_sector = allocation.by_sector[0]
    return f"Largest sector '{largest_sector.name}' at {largest_sector.weight:.1%}."


def _var_detail(metrics: RiskMetrics) -> str:
    if metrics.var_95_1d_pct is None:
        return "VaR not available."
    loss_value = metrics.var_95_1d_value or 0.0
    return f"1-day 95% VaR {metrics.var_95_1d_pct:.2%} ({loss_value:,.0f})"


def _cash_buffer_check(
    allocation: AllocationBreakdown, minimum_cash: float | None
) -> RiskLimitCheck | None:
    if not minimum_cash or minimum_cash <= 0:
        return None
    cash_weight = next(
        (bucket.weight for bucket in allocation.by_asset_class if bucket.name == "cash"), 0.0
    )
    breached = cash_weight < minimum_cash
    return RiskLimitCheck(
        name="min_cash_weight",
        observed=round(cash_weight, 4),
        limit=minimum_cash,
        higher_is_worse=False,
        status="breach" if breached else "ok",
        severity=Severity.MEDIUM if breached else Severity.INFO,
        detail=f"Cash buffer {cash_weight:.1%} vs minimum {minimum_cash:.1%}.",
    )


def _check(
    name: str,
    observed: float | None,
    limit: float | None,
    higher_worse: bool,
    warn_fraction: float,
    detail: str,
) -> RiskLimitCheck:
    """Build a limit check: breach past the limit, warn when close to it."""
    if observed is None or limit is None:
        return RiskLimitCheck(
            name=name,
            observed=observed,
            limit=limit,
            higher_is_worse=higher_worse,
            status="not_available",
            severity=Severity.INFO,
            detail=detail,
        )
    if higher_worse:
        status, severity = _higher_worse_status(observed, limit, warn_fraction)
    else:
        status, severity = _lower_worse_status(observed, limit, warn_fraction)
    return RiskLimitCheck(
        name=name,
        observed=round(observed, 4),
        limit=limit,
        higher_is_worse=higher_worse,
        status=status,
        severity=severity,
        detail=detail,
    )


def _status_for_breach(is_breach: bool, is_warn: bool) -> tuple[str, Severity]:
    """Shared breach/warn/ok ladder for limit checks."""
    if is_breach:
        return "breach", Severity.HIGH
    if is_warn:
        return "warn", Severity.MEDIUM
    return "ok", Severity.INFO


def _higher_worse_status(
    observed: float, limit: float, warn_fraction: float
) -> tuple[str, Severity]:
    return _status_for_breach(observed > limit, observed >= warn_fraction * limit)


def _lower_worse_status(
    observed: float, limit: float, warn_fraction: float
) -> tuple[str, Severity]:
    # Symmetric warn band below the limit: warn when observed falls within
    # warn_fraction of the limit from above (e.g. 0.9 -> warn below 1.1x limit
    # for a lower-is-worse metric is not meaningful, so mirror the distance).
    warn_ceiling = (2 - warn_fraction) * limit
    return _status_for_breach(observed < limit, observed <= warn_ceiling)


def _weighted_dividend_yield(
    holdings: list[Holding],
    quotes: dict[str, Quote],
    total_market_value: float,
) -> float | None:
    if not total_market_value:
        return None
    yields = assumed_yields()
    income = 0.0
    for holding in holdings:
        market_value = holding.market_value or 0.0
        quote = quotes.get(holding.symbol.upper())
        if quote and quote.dividend_yield:
            raw_yield = quote.dividend_yield
            annual_yield = (
                raw_yield / 100.0 if raw_yield >= DIVIDEND_YIELD_PERCENT_THRESHOLD else raw_yield
            )
        else:
            annual_yield = yields.get(holding.asset_class.value, 0.0)
        income += market_value * annual_yield
    return income / total_market_value if total_market_value else None


def _rounded(value: float | int | None, ndigits: int = 4) -> float | None:
    return round(value, ndigits) if isinstance(value, (int, float)) else None
