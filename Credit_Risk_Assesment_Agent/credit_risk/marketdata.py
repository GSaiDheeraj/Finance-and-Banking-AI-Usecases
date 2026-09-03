"""
yfinance fallback — fill gaps the uploaded documents don't cover.

The audited documents are always the PRIMARY source. When a ticker is supplied and a
required financial line item (or whole period) is missing from the extraction, this
module pulls the public financial statements from Yahoo Finance via the free `yfinance`
library and maps them onto the SAME `StandardLabel` vocabulary the ratio engine reads.

This step is **deterministic** — the mapping from a Yahoo row label to a `StandardLabel`
is a fixed dictionary, no LLM and no arithmetic. yfinance values are absolute amounts in
the company's reporting currency; each fallback line item is tagged with provenance
`source_snippet = "yfinance:<TICKER>"` so the audit trail shows exactly which figures
came from the market-data fallback rather than from a filing page.

Degrades gracefully: if `yfinance` is not installed or the lookup fails, it returns
empty results and the financial assessment proceeds on the documents alone.
"""
from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from .logconf import get_logger
from .schemas import (
    CompanyMetadata,
    LineItem,
    PeriodType,
    StandardLabel,
    StatementType,
)

_log = get_logger()

try:                                            # free, no API key
    import yfinance as yf
except Exception:                               # pragma: no cover
    yf = None

try:
    import pandas as pd
except Exception:                               # pragma: no cover
    pd = None


def yfinance_available() -> bool:
    """True if the yfinance + pandas backend is importable."""
    return yf is not None and pd is not None


def _norm(label: str) -> str:
    """Normalise a statement row label for robust matching (case/punctuation-insensitive)."""
    return re.sub(r"[^a-z0-9]+", " ", str(label).lower()).strip()


# Fixed map: normalised Yahoo-Finance row label -> our StandardLabel. Yahoo's wording has
# varied across versions, so several synonyms map to the same standard label.
_LABEL_MAP: Dict[str, StandardLabel] = {_norm(k): v for k, v in {
    # --- income statement ---
    "Total Revenue": StandardLabel.REVENUE,
    "Operating Revenue": StandardLabel.REVENUE,
    "Cost Of Revenue": StandardLabel.COST_OF_GOODS_SOLD,
    "Gross Profit": StandardLabel.GROSS_PROFIT,
    "Operating Expense": StandardLabel.OPERATING_EXPENSES,
    "Operating Income": StandardLabel.EBIT,
    "EBIT": StandardLabel.EBIT,
    "EBITDA": StandardLabel.EBITDA,
    "Normalized EBITDA": StandardLabel.EBITDA,
    "Reconciled Depreciation": StandardLabel.DEPRECIATION_AMORTIZATION,
    "Depreciation And Amortization In Income Statement": StandardLabel.DEPRECIATION_AMORTIZATION,
    "Depreciation Amortization Depletion Income Statement": StandardLabel.DEPRECIATION_AMORTIZATION,
    "Interest Expense": StandardLabel.INTEREST_EXPENSE,
    "Interest Expense Non Operating": StandardLabel.INTEREST_EXPENSE,
    "Pretax Income": StandardLabel.PRETAX_INCOME,
    "Tax Provision": StandardLabel.TAX_EXPENSE,
    "Net Income": StandardLabel.NET_INCOME,
    "Net Income Common Stockholders": StandardLabel.NET_INCOME,
    "Net Income From Continuing Operations": StandardLabel.NET_INCOME,
    # --- balance sheet: assets ---
    "Cash And Cash Equivalents": StandardLabel.CASH_AND_EQUIVALENTS,
    "Cash Cash Equivalents And Short Term Investments": StandardLabel.CASH_AND_EQUIVALENTS,
    "Accounts Receivable": StandardLabel.ACCOUNTS_RECEIVABLE,
    "Receivables": StandardLabel.ACCOUNTS_RECEIVABLE,
    "Inventory": StandardLabel.INVENTORY,
    "Current Assets": StandardLabel.CURRENT_ASSETS,
    "Total Current Assets": StandardLabel.CURRENT_ASSETS,
    "Net PPE": StandardLabel.PROPERTY_PLANT_EQUIPMENT,
    "Gross PPE": StandardLabel.PROPERTY_PLANT_EQUIPMENT,
    "Total Assets": StandardLabel.TOTAL_ASSETS,
    # --- balance sheet: liabilities & equity ---
    "Accounts Payable": StandardLabel.ACCOUNTS_PAYABLE,
    "Payables": StandardLabel.ACCOUNTS_PAYABLE,
    "Current Debt": StandardLabel.SHORT_TERM_DEBT,
    "Current Debt And Capital Lease Obligation": StandardLabel.SHORT_TERM_DEBT,
    "Current Liabilities": StandardLabel.CURRENT_LIABILITIES,
    "Total Current Liabilities": StandardLabel.CURRENT_LIABILITIES,
    "Long Term Debt": StandardLabel.LONG_TERM_DEBT,
    "Long Term Debt And Capital Lease Obligation": StandardLabel.LONG_TERM_DEBT,
    "Total Debt": StandardLabel.TOTAL_DEBT,
    "Total Liabilities Net Minority Interest": StandardLabel.TOTAL_LIABILITIES,
    "Total Liabilities": StandardLabel.TOTAL_LIABILITIES,
    "Stockholders Equity": StandardLabel.TOTAL_EQUITY,
    "Total Equity Gross Minority Interest": StandardLabel.TOTAL_EQUITY,
    "Common Stock Equity": StandardLabel.TOTAL_EQUITY,
    # --- cash flow ---
    "Operating Cash Flow": StandardLabel.OPERATING_CASH_FLOW,
    "Cash Flow From Continuing Operating Activities": StandardLabel.OPERATING_CASH_FLOW,
    "Capital Expenditure": StandardLabel.CAPITAL_EXPENDITURE,
    "Free Cash Flow": StandardLabel.FREE_CASH_FLOW,
    "Repayment Of Debt": StandardLabel.DEBT_PRINCIPAL_REPAYMENT,
    "Cash Dividends Paid": StandardLabel.DIVIDENDS_PAID,
    "Common Stock Dividend Paid": StandardLabel.DIVIDENDS_PAID,
}.items()}


def _period_label(col) -> Optional[str]:
    """Derive a 'FY<year>' period label from a yfinance statement column (a date)."""
    try:
        year = getattr(col, "year", None)
        if year is None:
            year = pd.Timestamp(col).year
        return f"FY{int(year)}"
    except Exception:
        return None


def _value(cell) -> Optional[float]:
    if cell is None or pd.isna(cell):
        return None
    try:
        return float(cell)
    except (TypeError, ValueError):
        return None


def _extract_statement(
    df, statement_type: StatementType, currency: Optional[str],
    ticker: str, max_years: int,
) -> List[LineItem]:
    """Turn one yfinance statement DataFrame (rows=labels, cols=period dates) into LineItems."""
    if df is None or pd is None or getattr(df, "empty", True):
        return []
    out: List[LineItem] = []
    columns = list(df.columns)[:max_years]
    for raw_label in df.index:
        std = _LABEL_MAP.get(_norm(raw_label))
        if std is None:
            continue
        for col in columns:
            period = _period_label(col)
            if not period:
                continue
            val = _value(df.loc[raw_label, col])
            if val is None:
                continue
            out.append(LineItem(
                label=str(raw_label),
                standardised_label=std,
                value=val,
                currency=currency,
                unit="absolute",
                period=period,
                period_type=PeriodType.ANNUAL,
                statement_type=statement_type,
                page=None,
                source_snippet=f"yfinance:{ticker}",
            ))
    return out


def fetch_fundamentals(
    ticker: str, max_years: int = 5
) -> Tuple[List[LineItem], CompanyMetadata]:
    """Fetch public financial statements + basic metadata for `ticker` from Yahoo Finance.

    Returns (line_items, metadata_patch). Both are empty / default when yfinance is
    unavailable or the lookup fails — callers gap-fill, so an empty return is safe.
    """
    ticker = ticker.strip()
    if not yfinance_available():
        _log.warning("yfinance fallback requested (ticker=%s) but the `yfinance` library "
                     "is not installed — skipping. Run `pip install yfinance`.", ticker)
        return [], CompanyMetadata()
    if not ticker:
        return [], CompanyMetadata()

    _log.info("yfinance: fetching fundamentals for ticker=%s (max_years=%d)", ticker, max_years)
    try:
        t = yf.Ticker(ticker)
    except Exception as e:
        _log.warning("yfinance: could not open Ticker(%s): %s", ticker, e)
        return [], CompanyMetadata()

    # Company info (best-effort; the .info call can be slow or partial).
    info: Dict = {}
    try:
        info = t.info or {}
    except Exception as e:
        _log.debug("yfinance: .info unavailable for %s: %s", ticker, e)
        info = {}
    currency = info.get("financialCurrency") or info.get("currency")

    items: List[LineItem] = []
    for attr, stmt in (
        ("financials", StatementType.INCOME_STATEMENT),
        ("balance_sheet", StatementType.BALANCE_SHEET),
        ("cashflow", StatementType.CASH_FLOW_STATEMENT),
    ):
        try:
            df = getattr(t, attr)
        except Exception as e:
            _log.debug("yfinance: %s statement unavailable for %s: %s", attr, ticker, e)
            df = None
        got = _extract_statement(df, stmt, currency, ticker, max_years)
        _log.info("yfinance: %-20s -> %d mapped line item(s)", stmt.value, len(got))
        items.extend(got)

    if not items:
        _log.warning("yfinance: no usable financial-statement data returned for %s "
                     "(unknown/illiquid ticker, or Yahoo returned nothing).", ticker)
    else:
        labels = sorted({it.standardised_label.value for it in items})
        periods = sorted({it.period for it in items})
        _log.info("yfinance: %d line item(s) across periods %s; labels: %s",
                  len(items), periods, ", ".join(labels))

    meta = CompanyMetadata(
        company_name=info.get("longName") or info.get("shortName"),
        industry=info.get("industry"),
        sub_industry=info.get("sector"),
        country=info.get("country"),
        employee_count=info.get("fullTimeEmployees")
        if isinstance(info.get("fullTimeEmployees"), int) else None,
        reporting_currency=currency,
    )
    return items, meta
