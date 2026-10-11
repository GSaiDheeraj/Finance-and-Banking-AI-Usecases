"""
Free market data via yfinance (Yahoo Finance — no API key).

Provides three things the deterministic engine needs and the documents/inputs may not carry:
  * a live PRICE per symbol (to value positions and size construction trades),
  * daily price HISTORY (to compute volatility / drawdown / VaR / beta), and
  * light CLASSIFICATION (quote type, sector, dividend rate) to enrich holdings and the
    auto-fetched construction universe.

This module is **deterministic relative to what Yahoo returns at fetch time** (it performs
no arithmetic on the figures beyond reading them) and **fails soft**: if `yfinance` is not
installed or the network is unavailable, every call returns empty/None and the pipeline
proceeds on whatever it already has (manual prices, cost basis, or — for risk metrics — a
reduced data-coverage figure). Results are cached per process to avoid refetching.
"""

from __future__ import annotations

import os

from pydantic import BaseModel

from .logconf import get_logger

_log = get_logger()

try:  # free, no API key
    import yfinance as yf
except Exception:  # pragma: no cover
    yf = None

try:
    import pandas as pd
except Exception:  # pragma: no cover
    pd = None


def market_data_available() -> bool:
    """True if the yfinance + pandas backend is importable."""
    return yf is not None and pd is not None


def _history_period() -> str:
    return os.getenv("PM_HISTORY_PERIOD", "1y")


class Quote(BaseModel):
    symbol: str
    price: float | None = None
    currency: str | None = None
    name: str | None = None
    quote_type: str | None = None  # EQUITY | ETF | MUTUALFUND | ...
    sector: str | None = None
    country: str | None = None  # issuer country (for country exposure)
    market_cap: float | None = None  # market capitalisation (issuer currency)
    dividend_rate: float | None = None  # trailing annual dividend per share
    dividend_yield: float | None = None
    pe_ratio: float | None = None  # trailing, else forward P/E (stocks)
    expense_ratio: float | None = None  # annual report expense ratio (funds/ETFs)


# Per-process caches (symbol -> value). Avoids refetching across modules in one run.
_QUOTE_CACHE: dict[str, Quote] = {}
_HISTORY_CACHE: dict[str, list[float]] = {}


def get_quote(symbol: str) -> Quote:
    """Fetch a single quote + light classification. Cached; fails soft to an empty Quote."""
    sym = symbol.strip().upper()
    if sym in _QUOTE_CACHE:
        return _QUOTE_CACHE[sym]
    q = Quote(symbol=sym)
    if not market_data_available() or not sym:
        _QUOTE_CACHE[sym] = q
        return q
    try:
        tk = yf.Ticker(sym)
        info: dict = {}
        # fast_info is cheap and robust; .info is richer but can be flaky.
        try:
            fi = tk.fast_info
            q.price = _f(getattr(fi, "last_price", None)) or _f(
                fi.get("last_price") if hasattr(fi, "get") else None
            )
            q.currency = (
                getattr(fi, "currency", None) if not hasattr(fi, "get") else fi.get("currency")
            )
        except Exception:
            pass
        try:
            info = tk.info or {}
        except Exception:
            info = {}
        if q.price is None:
            q.price = _f(
                info.get("currentPrice")
                or info.get("regularMarketPrice")
                or info.get("previousClose")
            )
        q.currency = q.currency or info.get("currency")
        q.name = info.get("shortName") or info.get("longName")
        q.quote_type = info.get("quoteType")
        q.sector = info.get("sector")
        q.country = info.get("country")
        q.market_cap = _f(info.get("marketCap") or info.get("totalAssets"))
        q.dividend_rate = _f(info.get("dividendRate") or info.get("trailingAnnualDividendRate"))
        q.dividend_yield = _f(info.get("dividendYield") or info.get("trailingAnnualDividendYield"))
        q.pe_ratio = _f(info.get("trailingPE") or info.get("forwardPE"))
        q.expense_ratio = _f(info.get("annualReportExpenseRatio"))
    except Exception as e:  # pragma: no cover - network
        _log.warning("marketdata: quote failed for %s: %s", sym, e)
    _QUOTE_CACHE[sym] = q
    return q


def get_quotes(symbols: list[str]) -> dict[str, Quote]:
    return {s.strip().upper(): get_quote(s) for s in symbols if s and s.strip()}


def get_price_history(symbol: str, period: str | None = None) -> list[float]:
    """Daily closing prices (oldest -> newest) for the symbol. Cached; [] on failure."""
    sym = symbol.strip().upper()
    if sym in _HISTORY_CACHE:
        return _HISTORY_CACHE[sym]
    closes: list[float] = []
    if not market_data_available() or not sym:
        _HISTORY_CACHE[sym] = closes
        return closes
    try:
        df = yf.Ticker(sym).history(period=period or _history_period(), auto_adjust=True)
        if df is not None and not df.empty and "Close" in df.columns:
            closes = [float(x) for x in df["Close"].tolist() if x == x and x > 0]
    except Exception as e:  # pragma: no cover - network
        _log.warning("marketdata: history failed for %s: %s", sym, e)
    _HISTORY_CACHE[sym] = closes
    return closes


def get_histories(symbols: list[str], period: str | None = None) -> dict[str, list[float]]:
    return {s.strip().upper(): get_price_history(s, period) for s in symbols if s and s.strip()}


def _f(value) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        return v if v == v else None  # drop NaN
    except (TypeError, ValueError):
        return None
