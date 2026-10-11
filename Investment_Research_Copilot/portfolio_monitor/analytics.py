"""
Deterministic quantitative primitives.

Pure functions over numeric series shared by the risk engine and the
constructor. No LLM, no network. The same inputs always yield the same
metrics.

Functions tolerate short or missing data by returning ``None`` rather than
raising, so the pipeline degrades gracefully when market history is sparse.
"""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

TRADING_DAYS_PER_YEAR = 252

MIN_POINTS_FOR_MOMENT = 2
MIN_POINTS_FOR_TAIL_STATISTIC = 20


def daily_returns(prices: Sequence[float]) -> list[float]:
    """Simple daily returns from a price series (oldest -> newest)."""
    clean_prices = [float(price) for price in prices if price is not None and price > 0]
    if len(clean_prices) < MIN_POINTS_FOR_MOMENT:
        return []
    price_array = np.asarray(clean_prices, dtype="float64")
    period_returns = price_array[1:] / price_array[:-1] - 1.0
    return [float(ret) for ret in period_returns]


def annualised_volatility(returns: Sequence[float]) -> float | None:
    """Annualised standard deviation of daily returns."""
    return_array = _clean_return_array(returns)
    if return_array.size < MIN_POINTS_FOR_MOMENT:
        return None
    return float(np.std(return_array, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def annualised_return(returns: Sequence[float]) -> float | None:
    """Naive annualised mean of daily returns (historical, not a forecast)."""
    return_array = _clean_return_array(returns)
    if return_array.size < MIN_POINTS_FOR_MOMENT:
        return None
    return float(np.mean(return_array) * TRADING_DAYS_PER_YEAR)


def max_drawdown(prices: Sequence[float]) -> float | None:
    """Most negative peak-to-trough decline over the series (<= 0)."""
    price_array = np.asarray(
        [price for price in prices if price is not None and price > 0], dtype="float64"
    )
    if price_array.size < MIN_POINTS_FOR_MOMENT:
        return None
    running_peak = np.maximum.accumulate(price_array)
    drawdowns = price_array / running_peak - 1.0
    return float(np.min(drawdowns))


def historical_var(returns: Sequence[float], confidence: float = 0.95) -> float | None:
    """One-period historical Value-at-Risk as a positive fraction.

    The (1 - confidence) quantile of the return distribution, reported as a
    positive loss (e.g. 0.03 means a 3% loss).
    """
    return_array = _clean_return_array(returns)
    if return_array.size < MIN_POINTS_FOR_TAIL_STATISTIC:
        return None
    loss_quantile = float(np.quantile(return_array, 1.0 - confidence))
    return float(max(0.0, -loss_quantile))


def beta(asset_returns: Sequence[float], market_returns: Sequence[float]) -> float | None:
    """Beta of an asset/portfolio vs a market series (aligned, equal length)."""
    asset_aligned, market_aligned = _align_paired_series(asset_returns, market_returns)
    if asset_aligned is None or market_aligned is None:
        return None
    market_variance = float(np.var(market_aligned, ddof=1))
    if market_variance <= 0:
        return None
    covariance = float(np.cov(asset_aligned, market_aligned, ddof=1)[0, 1])
    return covariance / market_variance


def tracking_error(
    asset_returns: Sequence[float], benchmark_returns: Sequence[float]
) -> float | None:
    """Annualised standard deviation of the active return (asset - benchmark)."""
    asset_aligned, benchmark_aligned = _align_paired_series(asset_returns, benchmark_returns)
    if asset_aligned is None or benchmark_aligned is None:
        return None
    active_returns = asset_aligned - benchmark_aligned
    return float(np.std(active_returns, ddof=1) * np.sqrt(TRADING_DAYS_PER_YEAR))


def herfindahl(weights: Sequence[float]) -> float | None:
    """Herfindahl-Hirschman concentration index: sum of squared weights (0..1)."""
    weight_array = np.asarray(
        [weight for weight in weights if weight is not None and weight > 0], dtype="float64"
    )
    if weight_array.size == 0:
        return None
    return float(np.sum(weight_array**2))


def effective_n(weights: Sequence[float]) -> float | None:
    """Effective number of positions: 1 / HHI (diversification breadth)."""
    concentration = herfindahl(weights)
    if not concentration or concentration <= 0:
        return None
    return float(1.0 / concentration)


def portfolio_returns(
    weights: dict[str, float], returns_by_symbol: dict[str, list[float]]
) -> list[float]:
    """Weighted daily portfolio returns over symbols that have aligned history.

    Weights are renormalised over the covered symbols only, and series are
    aligned to the shortest common length (tail), so a single short history
    does not break the estimate.
    """
    covered = {
        symbol: series
        for symbol, series in returns_by_symbol.items()
        if symbol in weights and weights[symbol] > 0 and len(series) >= MIN_POINTS_FOR_MOMENT
    }
    if not covered:
        return []
    common_length = min(len(series) for series in covered.values())
    if common_length < MIN_POINTS_FOR_MOMENT:
        return []
    total_weight = sum(weights[symbol] for symbol in covered)
    if total_weight <= 0:
        return []
    blended = np.zeros(common_length, dtype="float64")
    for symbol, series in covered.items():
        symbol_weight = weights[symbol] / total_weight
        blended += symbol_weight * np.asarray(series[-common_length:], dtype="float64")
    return [float(value) for value in blended]


def _clean_return_array(returns: Sequence[float]) -> np.ndarray:
    return np.asarray([ret for ret in returns if ret is not None], dtype="float64")


def _align_paired_series(
    first: Sequence[float], second: Sequence[float]
) -> tuple[np.ndarray, np.ndarray] | tuple[None, None]:
    """Truncate two return series to a shared tail, or (None, None) if too short."""
    first_array = np.asarray(first, dtype="float64")
    second_array = np.asarray(second, dtype="float64")
    shared_length = min(first_array.size, second_array.size)
    if shared_length < MIN_POINTS_FOR_TAIL_STATISTIC:
        return None, None
    return first_array[-shared_length:], second_array[-shared_length:]
