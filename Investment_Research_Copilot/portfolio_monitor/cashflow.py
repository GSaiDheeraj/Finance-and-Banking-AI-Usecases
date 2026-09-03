"""
Deterministic cash-flow variance — expected income vs the actual ledger.

NO LLM. "Identifies cash flow variances" from the use case: for each income-producing
holding we derive an EXPECTED income over the ledger's date span and compare it to the
ACTUAL income recorded in the transactions ledger (dividends, coupons, interest). Sizeable
shortfalls / excesses / missing distributions are flagged with a deterministic severity.

Expected income basis (deterministic, offline-friendly):
  expected_per_holding = market_value * assumed_annual_yield(asset_class) * (period_days/365)
refined per-symbol by the live yfinance trailing dividend rate when available
  (dividend_rate * quantity * period_fraction).

Separately, large NON-income outflows in the ledger (fees, withdrawals, tax) above an
absolute threshold are surfaced as 'unexpected' so an operations team sees them.
"""
from __future__ import annotations

from datetime import date
from typing import Dict, List, Optional

from .logconf import get_logger
from .marketdata import Quote
from .reference import (
    CASHFLOW_SHORTFALL_HIGH,
    CASHFLOW_TOLERANCE,
    MATERIAL_INCOME_FLOOR,
    UNEXPECTED_FLOW_ABS,
    assumed_yields,
)
from .schemas import (
    CashFlow,
    CashFlowReport,
    CashFlowType,
    CashFlowVariance,
    Holding,
    Severity,
)

_log = get_logger()

_INCOME_TYPES = {CashFlowType.DIVIDEND, CashFlowType.COUPON, CashFlowType.INTEREST}
_UNEXPECTED_TYPES = {CashFlowType.FEE, CashFlowType.WITHDRAWAL, CashFlowType.TAX}


def _parse_date(s: Optional[str]) -> Optional[date]:
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            from datetime import datetime
            return datetime.strptime(s.strip(), fmt).date()
        except (ValueError, TypeError):
            continue
    return None


def _period_fraction(cash_flows: List[CashFlow]) -> tuple:
    """Return (period_fraction_of_year, start_iso, end_iso) from the ledger date span."""
    dates = [d for d in (_parse_date(c.date) for c in cash_flows) if d is not None]
    if len(dates) < 2:
        return 1.0, (dates[0].isoformat() if dates else None), (dates[0].isoformat() if dates else None)
    start, end = min(dates), max(dates)
    days = max(1, (end - start).days)
    return days / 365.0, start.isoformat(), end.isoformat()


def _severity(status: str, ratio: Optional[float]) -> Severity:
    """Map a variance status + |variance/expected| to a severity.

    Excess income is not a portfolio risk, so it is INFO (reported, not alerted). Shortfalls
    and missing distributions are the signals an operations team acts on.
    """
    if status in ("on_track", "excess"):
        return Severity.INFO
    if status == "missing":
        return Severity.MEDIUM
    if status == "unexpected":
        return Severity.MEDIUM
    if ratio is None:                                # shortfall, unknown magnitude
        return Severity.LOW
    if ratio >= CASHFLOW_SHORTFALL_HIGH:
        return Severity.HIGH
    return Severity.MEDIUM


def analyse_cash_flows(
    holdings: List[Holding],
    cash_flows: List[CashFlow],
    quotes: Optional[Dict[str, Quote]] = None,
) -> CashFlowReport:
    """Build the cash-flow variance report from holdings + the transactions ledger."""
    quotes = quotes or {}
    have_market_data = bool(quotes)
    period_frac, start_iso, end_iso = _period_fraction(cash_flows)
    yields = assumed_yields()

    # 1. actual income aggregated by symbol
    actual_by_symbol: Dict[str, float] = {}
    for c in cash_flows:
        if c.type in _INCOME_TYPES and c.symbol:
            sym = c.symbol.upper()
            actual_by_symbol[sym] = actual_by_symbol.get(sym, 0.0) + c.amount

    variances: List[CashFlowVariance] = []
    total_expected = total_actual = 0.0
    held_symbols = set()

    # 2. per-holding expected vs actual
    for h in holdings:
        sym = h.symbol.upper()
        held_symbols.add(sym)
        mv = h.market_value or 0.0
        q = quotes.get(sym)
        if q and q.dividend_rate and h.quantity:
            expected_annual = q.dividend_rate * h.quantity
            basis = f"yfinance dividend rate {q.dividend_rate:.2f}/sh × {h.quantity:g} sh"
        elif have_market_data and q is not None:
            # We have live market data for this name and it reports no dividend rate — so we
            # expect no distribution (avoids phantom 'missing' flags on e.g. gold/non-payers).
            expected_annual = 0.0
            basis = "market data: security reports no distribution"
        else:
            y = yields.get(h.asset_class.value, 0.0)
            expected_annual = mv * y
            basis = f"assumed {y:.1%} yield on {h.asset_class.value} market value"
        expected = expected_annual * period_frac
        actual = actual_by_symbol.get(sym, 0.0)

        if expected <= 0 and actual <= 0:
            continue                                  # no income expected or received — skip
        # An expected distribution too small to matter is not worth a flag.
        if 0 < expected < MATERIAL_INCOME_FLOOR and actual == 0:
            continue
        total_expected += max(0.0, expected)
        total_actual += actual

        variance = actual - expected
        ratio = (abs(variance) / expected) if expected > 0 else None
        if expected > 0 and actual == 0:
            status = "missing"
        elif expected <= 0 and actual > 0:
            status = "excess"
        elif ratio is not None and ratio <= CASHFLOW_TOLERANCE:
            status = "on_track"
        elif variance < 0:
            status = "shortfall"
        else:
            status = "excess"

        variances.append(CashFlowVariance(
            type=CashFlowType.DIVIDEND if h.asset_class.value == "equity" else CashFlowType.COUPON,
            symbol=sym, expected=round(expected, 2), actual=round(actual, 2),
            variance=round(variance, 2),
            variance_pct=(round(variance / expected, 4) if expected > 0 else None),
            status=status, severity=_severity(status, ratio), basis=basis,
            note=_variance_note(sym, status, expected, actual),
        ))

    # 3. income received for a symbol NOT in the book, and large non-income outflows
    for sym, amt in actual_by_symbol.items():
        if sym not in held_symbols:
            variances.append(CashFlowVariance(
                symbol=sym, expected=0.0, actual=round(amt, 2), variance=round(amt, 2),
                status="unexpected", severity=Severity.MEDIUM, basis="not a current holding",
                note=f"Income of {amt:,.0f} received for {sym}, which is not in the portfolio.",
            ))

    for c in cash_flows:
        if c.type in _UNEXPECTED_TYPES and abs(c.amount) >= UNEXPECTED_FLOW_ABS:
            variances.append(CashFlowVariance(
                type=c.type, symbol=c.symbol, expected=0.0, actual=round(c.amount, 2),
                variance=round(c.amount, 2), status="unexpected",
                severity=Severity.MEDIUM if abs(c.amount) < 5 * UNEXPECTED_FLOW_ABS else Severity.HIGH,
                basis="large non-income flow", note=(c.note or f"{c.type.value} of {c.amount:,.0f}"),
            ))

    flags = [v for v in variances if v.status != "on_track"]
    notes: List[str] = []
    if not cash_flows:
        notes.append("No transactions ledger supplied — cash-flow variance not assessed.")
    if period_frac < 1.0:
        notes.append(f"Ledger spans ~{period_frac*365:.0f} days; expected income prorated accordingly.")

    report = CashFlowReport(
        period_start=start_iso, period_end=end_iso, variances=variances,
        total_expected_income=round(total_expected, 2),
        total_actual_income=round(total_actual, 2),
        net_variance=round(total_actual - total_expected, 2),
        n_flags=len(flags), notes=notes,
    )
    _log.info("cashflow: expected=%.0f actual=%.0f net=%.0f flags=%d",
              report.total_expected_income, report.total_actual_income,
              report.net_variance, report.n_flags)
    return report


def _variance_note(sym: str, status: str, expected: float, actual: float) -> str:
    if status == "missing":
        return f"No distribution recorded for {sym}; expected ~{expected:,.0f} over the period."
    if status == "shortfall":
        return f"{sym} income {actual:,.0f} is below the ~{expected:,.0f} expected (shortfall)."
    if status == "excess":
        return f"{sym} income {actual:,.0f} exceeds the ~{expected:,.0f} expected."
    return f"{sym} income {actual:,.0f} in line with the ~{expected:,.0f} expected."
