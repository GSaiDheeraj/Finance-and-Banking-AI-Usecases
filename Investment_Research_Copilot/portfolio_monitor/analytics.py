"""
Deterministic quantitative primitives.

NO LLM, NO network. Pure functions over numeric series — the building blocks the risk
engine and the constructor share. Because they are plain Python/NumPy over the inputs, the
same series always yields the same metric (NFR-1, reproducibility).

All functions tolerate short / missing data by returning `None` rather than raising, so the
pipeline degrades gracefully when market history is sparse.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Sequence

import numpy as np

TRADING_DAYS = 252


def daily_returns(prices: Sequence[float]) -> List[float]:
    """Simple daily returns from a price series (oldest -> newest)."""
    p = [float(x) for x in prices if x is not None and x > 0]
    if len(p) < 2:
        return []
    arr = np.asarray(p, dtype="float64")
    rets = arr[1:] / arr[:-1] - 1.0
    return [float(r) for r in rets]


def annualised_volatility(returns: Sequence[float]) -> Optional[float]:
    """Annualised standard deviation of daily returns."""
    r = np.asarray([x for x in returns if x is not None], dtype="float64")
    if r.size < 2:
        return None
    return float(np.std(r, ddof=1) * np.sqrt(TRADING_DAYS))


def annualised_return(returns: Sequence[float]) -> Optional[float]:
    """Naive annualised mean of daily returns (historical, not a forecast)."""
    r = np.asarray([x for x in returns if x is not None], dtype="float64")
    if r.size < 2:
        return None
    return float(np.mean(r) * TRADING_DAYS)


def max_drawdown(prices: Sequence[float]) -> Optional[float]:
    """Most negative peak-to-trough decline over the series (<= 0)."""
    p = np.asarray([x for x in prices if x is not None and x > 0], dtype="float64")
    if p.size < 2:
        return None
    running_max = np.maximum.accumulate(p)
    drawdowns = p / running_max - 1.0
    return float(np.min(drawdowns))


def historical_var(returns: Sequence[float], confidence: float = 0.95) -> Optional[float]:
    """1-period historical Value-at-Risk as a POSITIVE fraction (e.g. 0.03 = 3% loss).

    The (1-confidence) quantile of the return distribution; reported as a positive loss.
    """
    r = np.asarray([x for x in returns if x is not None], dtype="float64")
    if r.size < 20:                                  # too few points to be meaningful
        return None
    q = float(np.quantile(r, 1.0 - confidence))
    return float(max(0.0, -q))


def beta(asset_returns: Sequence[float], market_returns: Sequence[float]) -> Optional[float]:
    """Beta of an asset/portfolio vs a market series (aligned, equal length)."""
    a = np.asarray(asset_returns, dtype="float64")
    m = np.asarray(market_returns, dtype="float64")
    n = min(a.size, m.size)
    if n < 20:
        return None
    a, m = a[-n:], m[-n:]
    var_m = float(np.var(m, ddof=1))
    if var_m <= 0:
        return None
    cov = float(np.cov(a, m, ddof=1)[0, 1])
    return cov / var_m


def tracking_error(
    asset_returns: Sequence[float], benchmark_returns: Sequence[float]
) -> Optional[float]:
    """Annualised standard deviation of the active return (asset - benchmark)."""
    a = np.asarray(asset_returns, dtype="float64")
    b = np.asarray(benchmark_returns, dtype="float64")
    n = min(a.size, b.size)
    if n < 20:
        return None
    active = a[-n:] - b[-n:]
    return float(np.std(active, ddof=1) * np.sqrt(TRADING_DAYS))


def herfindahl(weights: Sequence[float]) -> Optional[float]:
    """Herfindahl-Hirschman concentration index = sum of squared weights (0..1)."""
    w = np.asarray([x for x in weights if x is not None and x > 0], dtype="float64")
    if w.size == 0:
        return None
    return float(np.sum(w ** 2))


def effective_n(weights: Sequence[float]) -> Optional[float]:
    """Effective number of positions = 1 / HHI (diversification breadth)."""
    h = herfindahl(weights)
    if not h or h <= 0:
        return None
    return float(1.0 / h)


def portfolio_returns(
    weights: Dict[str, float], returns_by_symbol: Dict[str, List[float]]
) -> List[float]:
    """Weighted daily portfolio returns over symbols that have aligned history.

    Weights are renormalised over the covered symbols only, and series are aligned to the
    shortest common length (tail), so a single short history does not break the estimate.
    """
    covered = {s: r for s, r in returns_by_symbol.items()
               if s in weights and weights[s] > 0 and len(r) >= 2}
    if not covered:
        return []
    min_len = min(len(r) for r in covered.values())
    if min_len < 2:
        return []
    total_w = sum(weights[s] for s in covered)
    if total_w <= 0:
        return []
    mat = np.zeros(min_len, dtype="float64")
    for s, r in covered.items():
        w = weights[s] / total_w
        mat += w * np.asarray(r[-min_len:], dtype="float64")
    return [float(x) for x in mat]
