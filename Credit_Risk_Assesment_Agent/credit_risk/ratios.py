"""
Deterministic financial-ratio computation.

NO LLM calls. This module turns the extracted `LineItem`s into the standard set of
credit ratios, one set per fiscal period. Because the arithmetic is plain Python over
the standardised labels, **the same line items always produce the same ratios** —
the reproducibility guarantee in NFR-1.

Every ratio records the standardised labels it consumed and the pages they came from,
and lists any missing inputs as `None` so the analyst always knows what data was absent
rather than seeing a silently-wrong number.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from .schemas import (
    FinancialRatios,
    LineItem,
    RatioValue,
    StandardLabel,
)


# A period's figures, keyed by standardised label, already unit-normalised to absolute.
PeriodValues = Dict[StandardLabel, Tuple[float, Optional[int]]]   # label -> (value, page)


# Shared with agent_graph.py's yfinance cross-check, which normalises the same way.
UNIT_MULTIPLIERS = {"absolute": 1.0, "thousands": 1_000.0, "millions": 1_000_000.0}


def _normalise_value(item: LineItem) -> Optional[float]:
    if item.value is None:
        return None
    return item.value * UNIT_MULTIPLIERS.get(item.unit, 1.0)


def group_by_period(line_items: List[LineItem]) -> Dict[str, PeriodValues]:
    """Collapse line items into {period: {standard_label: (value, page)}}.

    When the same standardised label appears more than once in a period (e.g. two rows
    both mapped to long_term_debt), the values are summed — the common case is a
    statement splitting a total across sub-lines. OTHER is ignored for ratio purposes.
    """
    out: Dict[str, PeriodValues] = {}
    sums: Dict[str, Dict[StandardLabel, float]] = {}
    pages: Dict[str, Dict[StandardLabel, Optional[int]]] = {}

    for it in line_items:
        if it.standardised_label == StandardLabel.OTHER:
            continue
        val = _normalise_value(it)
        if val is None:
            continue
        p = it.period
        sums.setdefault(p, {})
        pages.setdefault(p, {})
        sums[p][it.standardised_label] = sums[p].get(it.standardised_label, 0.0) + val
        # keep the first page we saw a label on for citation
        pages[p].setdefault(it.standardised_label, it.page)

    for p, label_vals in sums.items():
        out[p] = {lbl: (v, pages[p].get(lbl)) for lbl, v in label_vals.items()}
    return out


def order_periods(periods: List[str]) -> List[str]:
    """Order period strings oldest -> newest.

    Sorts by the first 4-digit year found in the label, then by the raw string, so
    'FY2021','FY2022','FY2023' and 'Q1-2024','Q2-2024' both order naturally. Periods
    without a detectable year keep insertion order at the front.
    """
    import re

    def key(p: str):
        m = re.search(r"(\d{4})", p)
        year = int(m.group(1)) if m else -1
        return (year, p)

    return sorted(set(periods), key=key)


# --------------------------------------------------------------------------- #
# Single-ratio helpers — each returns (value|None, inputs_used, missing_inputs, pages)
# --------------------------------------------------------------------------- #
def _get(pv: PeriodValues, label: StandardLabel) -> Optional[Tuple[float, Optional[int]]]:
    return pv.get(label)


def _derive_ebitda(pv: PeriodValues) -> Optional[Tuple[float, List[StandardLabel], List[int]]]:
    """EBITDA, preferring a reported figure, else EBIT + D&A."""
    direct = _get(pv, StandardLabel.EBITDA)
    if direct is not None:
        return direct[0], [StandardLabel.EBITDA], [p for p in [direct[1]] if p]
    ebit = _get(pv, StandardLabel.EBIT)
    da = _get(pv, StandardLabel.DEPRECIATION_AMORTIZATION)
    if ebit is not None and da is not None:
        pages = [p for p in [ebit[1], da[1]] if p]
        return ebit[0] + da[0], [StandardLabel.EBIT, StandardLabel.DEPRECIATION_AMORTIZATION], pages
    return None


def _derive_total_debt(pv: PeriodValues) -> Optional[Tuple[float, List[StandardLabel], List[int]]]:
    """Total debt, preferring a reported total, else short-term + long-term debt."""
    direct = _get(pv, StandardLabel.TOTAL_DEBT)
    if direct is not None:
        return direct[0], [StandardLabel.TOTAL_DEBT], [p for p in [direct[1]] if p]
    st = _get(pv, StandardLabel.SHORT_TERM_DEBT)
    lt = _get(pv, StandardLabel.LONG_TERM_DEBT)
    parts = [(x, lbl) for x, lbl in [(st, StandardLabel.SHORT_TERM_DEBT), (lt, StandardLabel.LONG_TERM_DEBT)] if x is not None]
    if parts:
        total = sum(x[0] for x, _ in parts)
        pages = [x[1] for x, _ in parts if x[1]]
        return total, [lbl for _, lbl in parts], pages
    return None


def _ratio(
    numer: Optional[float], denom: Optional[float],
    used: List[StandardLabel], missing: List[StandardLabel],
    pages: List[Optional[int]], name: str, category: str,
) -> RatioValue:
    """Build a RatioValue, returning None value (not raising) on missing/zero denom."""
    value = None
    if numer is not None and denom not in (None, 0):
        value = round(numer / denom, 4)
    return RatioValue(
        name=name, value=value, category=category,
        inputs_used=[l.value for l in used],
        missing_inputs=[l.value for l in missing],
        pages=sorted({p for p in pages if p}),
    )


def compute_ratios_for_period(pv: PeriodValues, period: str) -> FinancialRatios:
    """Compute the full ratio set for one period's normalised figures."""
    ratios: Dict[str, RatioValue] = {}

    def v(label: StandardLabel) -> Optional[float]:
        got = pv.get(label)
        return got[0] if got else None

    def pg(*labels: StandardLabel) -> List[Optional[int]]:
        return [pv[l][1] for l in labels if l in pv]

    # Derived aggregates
    ebitda_d = _derive_ebitda(pv)
    ebitda = ebitda_d[0] if ebitda_d else None
    ebitda_used = ebitda_d[1] if ebitda_d else []
    ebitda_pages = ebitda_d[2] if ebitda_d else []

    debt_d = _derive_total_debt(pv)
    total_debt = debt_d[0] if debt_d else None
    debt_used = debt_d[1] if debt_d else []
    debt_pages = debt_d[2] if debt_d else []

    cash = v(StandardLabel.CASH_AND_EQUIVALENTS)
    net_debt = (total_debt - cash) if (total_debt is not None and cash is not None) else total_debt

    equity = v(StandardLabel.TOTAL_EQUITY)
    assets = v(StandardLabel.TOTAL_ASSETS)
    revenue = v(StandardLabel.REVENUE)
    ebit = v(StandardLabel.EBIT)
    net_income = v(StandardLabel.NET_INCOME)
    interest = v(StandardLabel.INTEREST_EXPENSE)
    cur_assets = v(StandardLabel.CURRENT_ASSETS)
    cur_liab = v(StandardLabel.CURRENT_LIABILITIES)
    inventory = v(StandardLabel.INVENTORY)
    ocf = v(StandardLabel.OPERATING_CASH_FLOW)
    capex = v(StandardLabel.CAPITAL_EXPENDITURE)
    principal = v(StandardLabel.DEBT_PRINCIPAL_REPAYMENT)
    total_liab = v(StandardLabel.TOTAL_LIABILITIES)
    receivables = v(StandardLabel.ACCOUNTS_RECEIVABLE)
    cogs = v(StandardLabel.COST_OF_GOODS_SOLD)

    # interest expense is often reported as a negative; use magnitude for coverage
    interest_mag = abs(interest) if interest is not None else None
    capex_mag = abs(capex) if capex is not None else None
    principal_mag = abs(principal) if principal is not None else None

    # --- Leverage ---
    ratios["debt_to_equity"] = _ratio(
        total_debt, equity, debt_used + [StandardLabel.TOTAL_EQUITY],
        [] if total_debt is not None else [StandardLabel.TOTAL_DEBT],
        debt_pages + pg(StandardLabel.TOTAL_EQUITY), "debt_to_equity", "leverage")
    ratios["debt_to_assets"] = _ratio(
        total_debt, assets, debt_used + [StandardLabel.TOTAL_ASSETS], [],
        debt_pages + pg(StandardLabel.TOTAL_ASSETS), "debt_to_assets", "leverage")
    ratios["net_debt_to_ebitda"] = _ratio(
        net_debt, ebitda, debt_used + ebitda_used + [StandardLabel.CASH_AND_EQUIVALENTS],
        [] if ebitda is not None else [StandardLabel.EBITDA],
        debt_pages + ebitda_pages + pg(StandardLabel.CASH_AND_EQUIVALENTS),
        "net_debt_to_ebitda", "leverage")
    ratios["total_liabilities_to_equity"] = _ratio(
        total_liab, equity, [StandardLabel.TOTAL_LIABILITIES, StandardLabel.TOTAL_EQUITY], [],
        pg(StandardLabel.TOTAL_LIABILITIES, StandardLabel.TOTAL_EQUITY),
        "total_liabilities_to_equity", "leverage")

    # --- Liquidity ---
    ratios["current_ratio"] = _ratio(
        cur_assets, cur_liab, [StandardLabel.CURRENT_ASSETS, StandardLabel.CURRENT_LIABILITIES], [],
        pg(StandardLabel.CURRENT_ASSETS, StandardLabel.CURRENT_LIABILITIES), "current_ratio", "liquidity")
    quick_numer = (cur_assets - inventory) if (cur_assets is not None and inventory is not None) else cur_assets
    ratios["quick_ratio"] = _ratio(
        quick_numer, cur_liab,
        [StandardLabel.CURRENT_ASSETS, StandardLabel.INVENTORY, StandardLabel.CURRENT_LIABILITIES], [],
        pg(StandardLabel.CURRENT_ASSETS, StandardLabel.INVENTORY, StandardLabel.CURRENT_LIABILITIES),
        "quick_ratio", "liquidity")
    ratios["cash_ratio"] = _ratio(
        cash, cur_liab, [StandardLabel.CASH_AND_EQUIVALENTS, StandardLabel.CURRENT_LIABILITIES], [],
        pg(StandardLabel.CASH_AND_EQUIVALENTS, StandardLabel.CURRENT_LIABILITIES), "cash_ratio", "liquidity")

    # --- Profitability ---
    ratios["ebitda_margin"] = _ratio(
        ebitda, revenue, ebitda_used + [StandardLabel.REVENUE], [],
        ebitda_pages + pg(StandardLabel.REVENUE), "ebitda_margin", "profitability")
    ratios["ebit_margin"] = _ratio(
        ebit, revenue, [StandardLabel.EBIT, StandardLabel.REVENUE], [],
        pg(StandardLabel.EBIT, StandardLabel.REVENUE), "ebit_margin", "profitability")
    ratios["net_margin"] = _ratio(
        net_income, revenue, [StandardLabel.NET_INCOME, StandardLabel.REVENUE], [],
        pg(StandardLabel.NET_INCOME, StandardLabel.REVENUE), "net_margin", "profitability")
    ratios["return_on_equity"] = _ratio(
        net_income, equity, [StandardLabel.NET_INCOME, StandardLabel.TOTAL_EQUITY], [],
        pg(StandardLabel.NET_INCOME, StandardLabel.TOTAL_EQUITY), "return_on_equity", "profitability")
    ratios["return_on_assets"] = _ratio(
        net_income, assets, [StandardLabel.NET_INCOME, StandardLabel.TOTAL_ASSETS], [],
        pg(StandardLabel.NET_INCOME, StandardLabel.TOTAL_ASSETS), "return_on_assets", "profitability")
    # ROCE = EBIT / (Total assets - Current liabilities)
    capital_employed = (assets - cur_liab) if (assets is not None and cur_liab is not None) else None
    ratios["return_on_capital_employed"] = _ratio(
        ebit, capital_employed,
        [StandardLabel.EBIT, StandardLabel.TOTAL_ASSETS, StandardLabel.CURRENT_LIABILITIES], [],
        pg(StandardLabel.EBIT, StandardLabel.TOTAL_ASSETS, StandardLabel.CURRENT_LIABILITIES),
        "return_on_capital_employed", "profitability")

    # --- Debt-service coverage ---
    ratios["interest_coverage"] = _ratio(
        ebitda, interest_mag, ebitda_used + [StandardLabel.INTEREST_EXPENSE], [],
        ebitda_pages + pg(StandardLabel.INTEREST_EXPENSE), "interest_coverage", "coverage")
    # DSCR = (EBITDA - Capex) / (Principal + Interest)
    dscr_numer = (ebitda - capex_mag) if (ebitda is not None and capex_mag is not None) else None
    dscr_denom = None
    if interest_mag is not None or principal_mag is not None:
        dscr_denom = (interest_mag or 0.0) + (principal_mag or 0.0)
    ratios["dscr"] = _ratio(
        dscr_numer, dscr_denom,
        ebitda_used + [StandardLabel.CAPITAL_EXPENDITURE, StandardLabel.INTEREST_EXPENSE,
                       StandardLabel.DEBT_PRINCIPAL_REPAYMENT], [],
        ebitda_pages + pg(StandardLabel.CAPITAL_EXPENDITURE, StandardLabel.INTEREST_EXPENSE,
                          StandardLabel.DEBT_PRINCIPAL_REPAYMENT), "dscr", "coverage")
    ratios["ocf_to_total_debt"] = _ratio(
        ocf, total_debt, [StandardLabel.OPERATING_CASH_FLOW] + debt_used, [],
        pg(StandardLabel.OPERATING_CASH_FLOW) + debt_pages, "ocf_to_total_debt", "coverage")

    # --- Cash-flow quality ---
    ratios["cash_conversion"] = _ratio(
        ocf, net_income, [StandardLabel.OPERATING_CASH_FLOW, StandardLabel.NET_INCOME], [],
        pg(StandardLabel.OPERATING_CASH_FLOW, StandardLabel.NET_INCOME), "cash_conversion", "cash_flow")
    fcf = (ocf - capex_mag) if (ocf is not None and capex_mag is not None) else None
    ratios["fcf_margin"] = _ratio(
        fcf, revenue,
        [StandardLabel.OPERATING_CASH_FLOW, StandardLabel.CAPITAL_EXPENDITURE, StandardLabel.REVENUE], [],
        pg(StandardLabel.OPERATING_CASH_FLOW, StandardLabel.CAPITAL_EXPENDITURE, StandardLabel.REVENUE),
        "fcf_margin", "cash_flow")

    # --- Efficiency ---
    ratios["asset_turnover"] = _ratio(
        revenue, assets, [StandardLabel.REVENUE, StandardLabel.TOTAL_ASSETS], [],
        pg(StandardLabel.REVENUE, StandardLabel.TOTAL_ASSETS), "asset_turnover", "efficiency")
    # Inventory days = Inventory / COGS * 365
    inv_days = None
    if inventory is not None and cogs not in (None, 0):
        inv_days = round(inventory / abs(cogs) * 365.0, 1)
    ratios["inventory_days"] = RatioValue(
        name="inventory_days", value=inv_days, category="efficiency",
        inputs_used=[StandardLabel.INVENTORY.value, StandardLabel.COST_OF_GOODS_SOLD.value],
        missing_inputs=[] if inv_days is not None else
                       ([StandardLabel.COST_OF_GOODS_SOLD.value] if cogs in (None, 0) else []),
        pages=sorted({p for p in pg(StandardLabel.INVENTORY, StandardLabel.COST_OF_GOODS_SOLD) if p}))
    # DSO = Receivables / Revenue * 365
    dso = None
    if receivables is not None and revenue not in (None, 0):
        dso = round(receivables / revenue * 365.0, 1)
    ratios["days_sales_outstanding"] = RatioValue(
        name="days_sales_outstanding", value=dso, category="efficiency",
        inputs_used=[StandardLabel.ACCOUNTS_RECEIVABLE.value, StandardLabel.REVENUE.value],
        missing_inputs=[] if dso is not None else [StandardLabel.REVENUE.value] if revenue in (None, 0) else [],
        pages=sorted({p for p in pg(StandardLabel.ACCOUNTS_RECEIVABLE, StandardLabel.REVENUE) if p}))

    return FinancialRatios(period=period, ratios=ratios)


def compute_ratios(line_items: List[LineItem]) -> Tuple[List[str], List[FinancialRatios]]:
    """Compute ratios for every period present. Returns (ordered_periods, ratios_by_period)."""
    grouped = group_by_period(line_items)
    periods = order_periods(list(grouped.keys()))
    return periods, [compute_ratios_for_period(grouped[p], p) for p in periods]
