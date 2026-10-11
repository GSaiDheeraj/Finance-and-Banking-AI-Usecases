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

import re

from .schemas import (
    FinancialRatios,
    LineItem,
    RatioValue,
    StandardLabel,
)

# A period's figures, keyed by standardised label, already unit-normalised to absolute.
PeriodValues = dict[StandardLabel, tuple[float, int | None]]  # label -> (value, page)


# Shared with agent_graph.py's yfinance cross-check, which normalises the same way.
UNIT_MULTIPLIERS = {"absolute": 1.0, "thousands": 1_000.0, "millions": 1_000_000.0}

DAYS_PER_YEAR = 365.0
RATIO_DECIMAL_PLACES = 4
DAYS_DECIMAL_PLACES = 1
TINY_DENOMINATOR_GUARD = 1e-9


def _normalise_value(item: LineItem) -> float | None:
    """Convert a line item to absolute currency units."""
    if item.value is None:
        return None
    return item.value * UNIT_MULTIPLIERS.get(item.unit, 1.0)


def group_by_period(line_items: list[LineItem]) -> dict[str, PeriodValues]:
    """Collapse line items into {period: {standard_label: (value, page)}}.

    When the same standardised label appears more than once in a period (e.g. two rows
    both mapped to long_term_debt), the values are summed — the common case is a
    statement splitting a total across sub-lines. OTHER is ignored for ratio purposes.
    """
    grouped: dict[str, PeriodValues] = {}
    period_totals: dict[str, dict[StandardLabel, float]] = {}
    period_pages: dict[str, dict[StandardLabel, int | None]] = {}

    for line_item in line_items:
        if line_item.standardised_label == StandardLabel.OTHER:
            continue
        normalised = _normalise_value(line_item)
        if normalised is None:
            continue
        period = line_item.period
        period_totals.setdefault(period, {})
        period_pages.setdefault(period, {})
        label = line_item.standardised_label
        period_totals[period][label] = (
            period_totals[period].get(label, 0.0) + normalised
        )
        # keep the first page we saw a label on for citation
        period_pages[period].setdefault(label, line_item.page)

    for period, label_values in period_totals.items():
        grouped[period] = {
            label: (value, period_pages[period].get(label))
            for label, value in label_values.items()
        }
    return grouped


def _period_sort_key(period: str):
    """Sort key: 4-digit year first, then raw label; year-less labels sort first."""
    match = re.search(r"(\d{4})", period)
    year = int(match.group(1)) if match else -1
    return (year, period)


def order_periods(periods: list[str]) -> list[str]:
    """Order period strings oldest -> newest.

    Sorts by the first 4-digit year found in the label, then by the raw string, so
    'FY2021','FY2022','FY2023' and 'Q1-2024','Q2-2024' both order naturally. Periods
    without a detectable year keep insertion order at the front.
    """
    return sorted(set(periods), key=_period_sort_key)


# --------------------------------------------------------------------------- #
# Single-ratio helpers — each returns (value|None, inputs_used, missing_inputs, pages)
# --------------------------------------------------------------------------- #
def _get(
    period_values: PeriodValues, label: StandardLabel
) -> tuple[float, int | None] | None:
    """Return the (value, page) tuple for a label, or None when absent."""
    return period_values.get(label)


def _pages_for_labels(
    period_values: PeriodValues, *labels: StandardLabel
) -> list[int | None]:
    """Collect page numbers for the given labels that are present in the period."""
    return [period_values[label][1] for label in labels if label in period_values]


def _single_page(page: int | None) -> list[int]:
    """Wrap an optional page into a list, dropping None."""
    return [page] if page else []


def _derive_ebitda(
    pv: PeriodValues,
) -> tuple[float, list[StandardLabel], list[int]] | None:
    """EBITDA, preferring a reported figure, else EBIT + D&A."""
    direct = _get(pv, StandardLabel.EBITDA)
    if direct is not None:
        return direct[0], [StandardLabel.EBITDA], _single_page(direct[1])
    ebit = _get(pv, StandardLabel.EBIT)
    depreciation = _get(pv, StandardLabel.DEPRECIATION_AMORTIZATION)
    if ebit is not None and depreciation is not None:
        pages = [page for page in [ebit[1], depreciation[1]] if page]
        return (
            ebit[0] + depreciation[0],
            [StandardLabel.EBIT, StandardLabel.DEPRECIATION_AMORTIZATION],
            pages,
        )
    return None


def _derive_total_debt(
    pv: PeriodValues,
) -> tuple[float, list[StandardLabel], list[int]] | None:
    """Total debt, preferring a reported total, else short-term + long-term debt."""
    direct = _get(pv, StandardLabel.TOTAL_DEBT)
    if direct is not None:
        return direct[0], [StandardLabel.TOTAL_DEBT], _single_page(direct[1])
    short_term = _get(pv, StandardLabel.SHORT_TERM_DEBT)
    long_term = _get(pv, StandardLabel.LONG_TERM_DEBT)
    debt_parts = [
        (entry, label)
        for entry, label in [
            (short_term, StandardLabel.SHORT_TERM_DEBT),
            (long_term, StandardLabel.LONG_TERM_DEBT),
        ]
        if entry is not None
    ]
    if debt_parts:
        total = sum(entry[0] for entry, _ in debt_parts)
        pages = [entry[1] for entry, _ in debt_parts if entry[1]]
        return total, [label for _, label in debt_parts], pages
    return None


def _ratio(
    numerator: float | None,
    denominator: float | None,
    used: list[StandardLabel],
    missing: list[StandardLabel],
    pages: list[int | None],
    name: str,
    category: str,
) -> RatioValue:
    """Build a RatioValue, returning None value (not raising) on missing/zero denom."""
    value = None
    if numerator is not None and denominator not in (None, 0):
        value = round(numerator / denominator, RATIO_DECIMAL_PLACES)
    return RatioValue(
        name=name,
        value=value,
        category=category,
        inputs_used=[label.value for label in used],
        missing_inputs=[label.value for label in missing],
        pages=sorted({page for page in pages if page}),
    )


class _PeriodSnapshot:
    """Resolved absolute values for one period, hiding dict lookups from ratio builders."""

    def __init__(self, period_values: PeriodValues) -> None:
        self.values = period_values
        ebitda_derived = _derive_ebitda(period_values)
        self.ebitda = ebitda_derived[0] if ebitda_derived else None
        self.ebitda_used = ebitda_derived[1] if ebitda_derived else []
        self.ebitda_pages: list[int] = ebitda_derived[2] if ebitda_derived else []
        debt_derived = _derive_total_debt(period_values)
        self.total_debt = debt_derived[0] if debt_derived else None
        self.debt_used = debt_derived[1] if debt_derived else []
        self.debt_pages: list[int] = debt_derived[2] if debt_derived else []

    def value(self, label: StandardLabel) -> float | None:
        """Return the absolute value for a label, or None when absent."""
        entry = self.values.get(label)
        return entry[0] if entry else None

    def pages(self, *labels: StandardLabel) -> list[int | None]:
        """Return page numbers for labels present in this period."""
        return _pages_for_labels(self.values, *labels)


def _leverage_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build leverage ratios from resolved period values."""
    cash = snapshot.value(StandardLabel.CASH_AND_EQUIVALENTS)
    net_debt = (
        (snapshot.total_debt - cash)
        if (snapshot.total_debt is not None and cash is not None)
        else snapshot.total_debt
    )
    equity = snapshot.value(StandardLabel.TOTAL_EQUITY)
    assets = snapshot.value(StandardLabel.TOTAL_ASSETS)
    total_liabilities = snapshot.value(StandardLabel.TOTAL_LIABILITIES)
    return {
        "debt_to_equity": _ratio(
            snapshot.total_debt,
            equity,
            snapshot.debt_used + [StandardLabel.TOTAL_EQUITY],
            [] if snapshot.total_debt is not None else [StandardLabel.TOTAL_DEBT],
            snapshot.debt_pages + snapshot.pages(StandardLabel.TOTAL_EQUITY),
            "debt_to_equity",
            "leverage",
        ),
        "debt_to_assets": _ratio(
            snapshot.total_debt,
            assets,
            snapshot.debt_used + [StandardLabel.TOTAL_ASSETS],
            [],
            snapshot.debt_pages + snapshot.pages(StandardLabel.TOTAL_ASSETS),
            "debt_to_assets",
            "leverage",
        ),
        "net_debt_to_ebitda": _ratio(
            net_debt,
            snapshot.ebitda,
            snapshot.debt_used
            + snapshot.ebitda_used
            + [StandardLabel.CASH_AND_EQUIVALENTS],
            [] if snapshot.ebitda is not None else [StandardLabel.EBITDA],
            snapshot.debt_pages
            + snapshot.ebitda_pages
            + snapshot.pages(StandardLabel.CASH_AND_EQUIVALENTS),
            "net_debt_to_ebitda",
            "leverage",
        ),
        "total_liabilities_to_equity": _ratio(
            total_liabilities,
            equity,
            [StandardLabel.TOTAL_LIABILITIES, StandardLabel.TOTAL_EQUITY],
            [],
            snapshot.pages(StandardLabel.TOTAL_LIABILITIES, StandardLabel.TOTAL_EQUITY),
            "total_liabilities_to_equity",
            "leverage",
        ),
    }


def _liquidity_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build liquidity ratios from resolved period values."""
    current_assets = snapshot.value(StandardLabel.CURRENT_ASSETS)
    current_liabilities = snapshot.value(StandardLabel.CURRENT_LIABILITIES)
    inventory = snapshot.value(StandardLabel.INVENTORY)
    cash = snapshot.value(StandardLabel.CASH_AND_EQUIVALENTS)
    quick_numerator = (
        (current_assets - inventory)
        if (current_assets is not None and inventory is not None)
        else current_assets
    )
    return {
        "current_ratio": _ratio(
            current_assets,
            current_liabilities,
            [StandardLabel.CURRENT_ASSETS, StandardLabel.CURRENT_LIABILITIES],
            [],
            snapshot.pages(
                StandardLabel.CURRENT_ASSETS, StandardLabel.CURRENT_LIABILITIES
            ),
            "current_ratio",
            "liquidity",
        ),
        "quick_ratio": _ratio(
            quick_numerator,
            current_liabilities,
            [
                StandardLabel.CURRENT_ASSETS,
                StandardLabel.INVENTORY,
                StandardLabel.CURRENT_LIABILITIES,
            ],
            [],
            snapshot.pages(
                StandardLabel.CURRENT_ASSETS,
                StandardLabel.INVENTORY,
                StandardLabel.CURRENT_LIABILITIES,
            ),
            "quick_ratio",
            "liquidity",
        ),
        "cash_ratio": _ratio(
            cash,
            current_liabilities,
            [StandardLabel.CASH_AND_EQUIVALENTS, StandardLabel.CURRENT_LIABILITIES],
            [],
            snapshot.pages(
                StandardLabel.CASH_AND_EQUIVALENTS, StandardLabel.CURRENT_LIABILITIES
            ),
            "cash_ratio",
            "liquidity",
        ),
    }


def _profitability_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build profitability ratios from resolved period values."""
    revenue = snapshot.value(StandardLabel.REVENUE)
    ebit = snapshot.value(StandardLabel.EBIT)
    net_income = snapshot.value(StandardLabel.NET_INCOME)
    equity = snapshot.value(StandardLabel.TOTAL_EQUITY)
    assets = snapshot.value(StandardLabel.TOTAL_ASSETS)
    current_liabilities = snapshot.value(StandardLabel.CURRENT_LIABILITIES)
    capital_employed = (
        (assets - current_liabilities)
        if (assets is not None and current_liabilities is not None)
        else None
    )
    return {
        "ebitda_margin": _ratio(
            snapshot.ebitda,
            revenue,
            snapshot.ebitda_used + [StandardLabel.REVENUE],
            [],
            snapshot.ebitda_pages + snapshot.pages(StandardLabel.REVENUE),
            "ebitda_margin",
            "profitability",
        ),
        "ebit_margin": _ratio(
            ebit,
            revenue,
            [StandardLabel.EBIT, StandardLabel.REVENUE],
            [],
            snapshot.pages(StandardLabel.EBIT, StandardLabel.REVENUE),
            "ebit_margin",
            "profitability",
        ),
        "net_margin": _ratio(
            net_income,
            revenue,
            [StandardLabel.NET_INCOME, StandardLabel.REVENUE],
            [],
            snapshot.pages(StandardLabel.NET_INCOME, StandardLabel.REVENUE),
            "net_margin",
            "profitability",
        ),
        "return_on_equity": _ratio(
            net_income,
            equity,
            [StandardLabel.NET_INCOME, StandardLabel.TOTAL_EQUITY],
            [],
            snapshot.pages(StandardLabel.NET_INCOME, StandardLabel.TOTAL_EQUITY),
            "return_on_equity",
            "profitability",
        ),
        "return_on_assets": _ratio(
            net_income,
            assets,
            [StandardLabel.NET_INCOME, StandardLabel.TOTAL_ASSETS],
            [],
            snapshot.pages(StandardLabel.NET_INCOME, StandardLabel.TOTAL_ASSETS),
            "return_on_assets",
            "profitability",
        ),
        "return_on_capital_employed": _ratio(
            ebit,
            capital_employed,
            [
                StandardLabel.EBIT,
                StandardLabel.TOTAL_ASSETS,
                StandardLabel.CURRENT_LIABILITIES,
            ],
            [],
            snapshot.pages(
                StandardLabel.EBIT,
                StandardLabel.TOTAL_ASSETS,
                StandardLabel.CURRENT_LIABILITIES,
            ),
            "return_on_capital_employed",
            "profitability",
        ),
    }


def _coverage_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build debt-service coverage ratios from resolved period values."""
    interest = snapshot.value(StandardLabel.INTEREST_EXPENSE)
    capex = snapshot.value(StandardLabel.CAPITAL_EXPENDITURE)
    principal = snapshot.value(StandardLabel.DEBT_PRINCIPAL_REPAYMENT)
    operating_cash_flow = snapshot.value(StandardLabel.OPERATING_CASH_FLOW)
    # interest expense is often reported as a negative; use magnitude for coverage
    interest_magnitude = abs(interest) if interest is not None else None
    capex_magnitude = abs(capex) if capex is not None else None
    principal_magnitude = abs(principal) if principal is not None else None
    dscr_numerator = (
        (snapshot.ebitda - capex_magnitude)
        if (snapshot.ebitda is not None and capex_magnitude is not None)
        else None
    )
    dscr_denominator = None
    if interest_magnitude is not None or principal_magnitude is not None:
        dscr_denominator = (interest_magnitude or 0.0) + (principal_magnitude or 0.0)
    return {
        "interest_coverage": _ratio(
            snapshot.ebitda,
            interest_magnitude,
            snapshot.ebitda_used + [StandardLabel.INTEREST_EXPENSE],
            [],
            snapshot.ebitda_pages + snapshot.pages(StandardLabel.INTEREST_EXPENSE),
            "interest_coverage",
            "coverage",
        ),
        "dscr": _ratio(
            dscr_numerator,
            dscr_denominator,
            snapshot.ebitda_used
            + [
                StandardLabel.CAPITAL_EXPENDITURE,
                StandardLabel.INTEREST_EXPENSE,
                StandardLabel.DEBT_PRINCIPAL_REPAYMENT,
            ],
            [],
            snapshot.ebitda_pages
            + snapshot.pages(
                StandardLabel.CAPITAL_EXPENDITURE,
                StandardLabel.INTEREST_EXPENSE,
                StandardLabel.DEBT_PRINCIPAL_REPAYMENT,
            ),
            "dscr",
            "coverage",
        ),
        "ocf_to_total_debt": _ratio(
            operating_cash_flow,
            snapshot.total_debt,
            [StandardLabel.OPERATING_CASH_FLOW] + snapshot.debt_used,
            [],
            snapshot.pages(StandardLabel.OPERATING_CASH_FLOW) + snapshot.debt_pages,
            "ocf_to_total_debt",
            "coverage",
        ),
    }


def _cash_flow_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build cash-flow quality ratios from resolved period values."""
    operating_cash_flow = snapshot.value(StandardLabel.OPERATING_CASH_FLOW)
    net_income = snapshot.value(StandardLabel.NET_INCOME)
    revenue = snapshot.value(StandardLabel.REVENUE)
    capex = snapshot.value(StandardLabel.CAPITAL_EXPENDITURE)
    capex_magnitude = abs(capex) if capex is not None else None
    free_cash_flow = (
        (operating_cash_flow - capex_magnitude)
        if (operating_cash_flow is not None and capex_magnitude is not None)
        else None
    )
    return {
        "cash_conversion": _ratio(
            operating_cash_flow,
            net_income,
            [StandardLabel.OPERATING_CASH_FLOW, StandardLabel.NET_INCOME],
            [],
            snapshot.pages(StandardLabel.OPERATING_CASH_FLOW, StandardLabel.NET_INCOME),
            "cash_conversion",
            "cash_flow",
        ),
        "fcf_margin": _ratio(
            free_cash_flow,
            revenue,
            [
                StandardLabel.OPERATING_CASH_FLOW,
                StandardLabel.CAPITAL_EXPENDITURE,
                StandardLabel.REVENUE,
            ],
            [],
            snapshot.pages(
                StandardLabel.OPERATING_CASH_FLOW,
                StandardLabel.CAPITAL_EXPENDITURE,
                StandardLabel.REVENUE,
            ),
            "fcf_margin",
            "cash_flow",
        ),
    }


def _days_ratio(
    name: str,
    numerator: float | None,
    denominator: float | None,
    missing_label: StandardLabel,
    snapshot: _PeriodSnapshot,
    used_labels: list[StandardLabel],
) -> RatioValue:
    """Build an annualised days ratio, returning None value on missing inputs."""
    days_value = None
    if numerator is not None and denominator not in (None, 0):
        days_value = round(
            numerator / abs(denominator) * DAYS_PER_YEAR, DAYS_DECIMAL_PLACES
        )
    if days_value is not None:
        missing_inputs: list[str] = []
    elif denominator in (None, 0):
        missing_inputs = [missing_label.value]
    else:
        missing_inputs = []
    return RatioValue(
        name=name,
        value=days_value,
        category="efficiency",
        inputs_used=[label.value for label in used_labels],
        missing_inputs=missing_inputs,
        pages=sorted({page for page in snapshot.pages(*used_labels) if page}),
    )


def _efficiency_ratios(snapshot: _PeriodSnapshot) -> dict[str, RatioValue]:
    """Build efficiency ratios from resolved period values."""
    revenue = snapshot.value(StandardLabel.REVENUE)
    assets = snapshot.value(StandardLabel.TOTAL_ASSETS)
    inventory = snapshot.value(StandardLabel.INVENTORY)
    receivables = snapshot.value(StandardLabel.ACCOUNTS_RECEIVABLE)
    cost_of_goods = snapshot.value(StandardLabel.COST_OF_GOODS_SOLD)
    return {
        "asset_turnover": _ratio(
            revenue,
            assets,
            [StandardLabel.REVENUE, StandardLabel.TOTAL_ASSETS],
            [],
            snapshot.pages(StandardLabel.REVENUE, StandardLabel.TOTAL_ASSETS),
            "asset_turnover",
            "efficiency",
        ),
        "inventory_days": _days_ratio(
            "inventory_days",
            inventory,
            cost_of_goods,
            StandardLabel.COST_OF_GOODS_SOLD,
            snapshot,
            [StandardLabel.INVENTORY, StandardLabel.COST_OF_GOODS_SOLD],
        ),
        "days_sales_outstanding": _days_ratio(
            "days_sales_outstanding",
            receivables,
            revenue,
            StandardLabel.REVENUE,
            snapshot,
            [StandardLabel.ACCOUNTS_RECEIVABLE, StandardLabel.REVENUE],
        ),
    }


def compute_ratios_for_period(
    period_values: PeriodValues, period: str
) -> FinancialRatios:
    """Compute the full ratio set for one period's normalised figures."""
    snapshot = _PeriodSnapshot(period_values)
    ratios: dict[str, RatioValue] = {}
    ratios.update(_leverage_ratios(snapshot))
    ratios.update(_liquidity_ratios(snapshot))
    ratios.update(_profitability_ratios(snapshot))
    ratios.update(_coverage_ratios(snapshot))
    ratios.update(_cash_flow_ratios(snapshot))
    ratios.update(_efficiency_ratios(snapshot))
    return FinancialRatios(period=period, ratios=ratios)


def compute_ratios(
    line_items: list[LineItem],
) -> tuple[list[str], list[FinancialRatios]]:
    """Compute ratios for every period present. Returns (ordered_periods, ratios_by_period)."""
    grouped = group_by_period(line_items)
    periods = order_periods(list(grouped.keys()))
    return periods, [compute_ratios_for_period(grouped[p], p) for p in periods]
