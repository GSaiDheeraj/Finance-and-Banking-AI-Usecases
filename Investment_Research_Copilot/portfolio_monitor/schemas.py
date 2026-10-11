"""
Schemas for portfolio & risk monitoring and portfolio construction.

Design notes
------------
* The INPUTS are structured records (`Holding`, `CashFlow`, `PortfolioInput`,
  `ConstructionRequest`) — this agent monitors a live portfolio, it does not parse a filing.
  (An optional PDF brokerage-statement path produces the same `Holding`s via grounded LLM
  extraction — see `statement_extract.py`.)
* Everything below the "deterministic outputs" divider is produced by plain Python
  (allocation, drift, rebalancing, cash-flow variance, risk metrics, alerts, scorecard,
  guardrails) so the same inputs always yield the same alerts and the same guardrailed
  portfolio (NFR-1, reproducibility).
* Explainability is first-class: `PolicyLine`, `RebalanceTrade`, `Alert`, `SelectedPosition`
  and `RiskFactorResult` each carry a `rationale` / `recommended_action` string so the agent
  always states the reason for its choices.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class AssetClass(str, Enum):
    EQUITY = "equity"
    FIXED_INCOME = "fixed_income"
    CASH = "cash"
    ALTERNATIVES = "alternatives"
    REAL_ESTATE = "real_estate"
    COMMODITY = "commodity"
    MULTI_ASSET = "multi_asset"
    OTHER = "other"


class Region(str, Enum):
    US = "us"
    DEVELOPED_EX_US = "developed_ex_us"
    EMERGING_MARKETS = "emerging_markets"
    GLOBAL = "global"
    OTHER = "other"


class CashFlowType(str, Enum):
    DIVIDEND = "dividend"
    COUPON = "coupon"
    INTEREST = "interest"
    CONTRIBUTION = "contribution"
    WITHDRAWAL = "withdrawal"
    FEE = "fee"
    TAX = "tax"
    TRADE_BUY = "trade_buy"
    TRADE_SELL = "trade_sell"
    OTHER = "other"


class AlertType(str, Enum):
    REBALANCE = "rebalance"
    CONCENTRATION = "concentration"
    CASHFLOW_VARIANCE = "cashflow_variance"
    RISK_LIMIT = "risk_limit"
    DRAWDOWN = "drawdown"
    EMERGING_RISK = "emerging_risk"
    DATA_QUALITY = "data_quality"


class Severity(str, Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class HealthBand(str, Enum):
    HEALTHY = "healthy"
    WATCH = "watch"
    ELEVATED = "elevated"
    CRITICAL = "critical"


class DriftStatus(str, Enum):
    WITHIN_BAND = "within_band"
    OVERWEIGHT = "overweight"  # above target but inside the band
    UNDERWEIGHT = "underweight"  # below target but inside the band
    BREACH_OVER = "breach_over"  # above the upper band — sell to rebalance
    BREACH_UNDER = "breach_under"  # below the lower band — buy to rebalance


class WeightingMethod(str, Enum):
    EQUAL_WEIGHT = "equal_weight"
    INVERSE_VOL = "inverse_vol"
    RISK_PARITY = "risk_parity"
    RETURN_TILTED = "return_tilted"  # weight toward higher expected-return names


class GeographyMode(str, Enum):
    GLOBAL = "global"  # any country in the universe
    COUNTRY_SPECIFIC = "country_specific"  # exactly one country
    COUNTRY_LIST = "country_list"  # a user-supplied list of countries


class MarketCap(str, Enum):
    LARGE_CAP = "large_cap"
    MID_CAP = "mid_cap"
    SMALL_CAP = "small_cap"
    UNKNOWN = "unknown"


class PortfolioStyle(str, Enum):
    """The equity style the investor wants the book built in."""

    LARGE_CAP = "large_cap"
    MID_CAP = "mid_cap"
    SMALL_CAP = "small_cap"
    MULTI_CAP = "multi_cap"  # spread across caps (uses cap_mix or a default)
    FUND_OF_FUNDS = "fund_of_funds"  # built from funds/ETFs rather than single names
    THEMATIC = "thematic"  # built around a theme (see ConstructionRequest.theme)
    ELSS = "elss"  # tax-advantaged equity (India ELSS / generic tax sleeve)


class InstrumentType(str, Enum):
    STOCK = "stock"
    ETF = "etf"
    MUTUAL_FUND = "mutual_fund"
    OTHER = "other"


# --------------------------------------------------------------------------- #
# Inputs (structured — the "new kind of input")
# --------------------------------------------------------------------------- #
class Holding(BaseModel):
    """One position in the portfolio. Price / market_value / weight are filled by the
    pipeline (manual price > market data > flagged) so the raw input can be minimal."""

    symbol: str
    name: str | None = None
    asset_class: AssetClass = AssetClass.OTHER
    sector: str | None = None
    region: Region = Region.OTHER
    country: str | None = None  # ISO-ish country name (for country exposure)
    currency: str = "USD"
    quantity: float = 0.0
    cost_basis: float | None = None  # total cost (not per-share), if known
    price: float | None = None  # manual price override; else market data
    market_value: float | None = None  # computed = price * quantity
    weight: float | None = None  # computed = market_value / total
    market_cap: MarketCap = MarketCap.UNKNOWN  # filled from market data when available
    instrument_type: InstrumentType = InstrumentType.OTHER
    price_source: str = "input"  # 'input' | 'marketdata' | 'cost_basis' | 'missing'


class CashFlow(BaseModel):
    """One cash-flow / transaction ledger entry. `amount` is positive for inflows
    (dividends, contributions) and may be negative for outflows (fees, withdrawals)."""

    date: str | None = None  # ISO 'YYYY-MM-DD'
    type: CashFlowType = CashFlowType.OTHER
    symbol: str | None = None
    amount: float = 0.0
    currency: str = "USD"
    note: str | None = None


class PortfolioInput(BaseModel):
    name: str = "Portfolio"
    base_currency: str = "USD"
    as_of: str | None = None
    mandate: str = "balanced"  # picks the IPS target allocation
    holdings: list[Holding] = Field(default_factory=list)
    cash_flows: list[CashFlow] = Field(default_factory=list)


class ConstructionConstraints(BaseModel):
    single_name_cap: float = 0.10  # max weight in any one security
    sector_cap: float = 0.30  # max weight in any one sector
    min_position: float = 0.01  # drop positions below this weight
    exclusions: list[str] = Field(default_factory=list)  # excluded symbols / sectors
    region_preference: Region | None = None  # legacy optional tilt / filter
    max_holdings: int | None = None  # optional cap on total number of names

    # Ratio-style filters, checked in universe.build_universe() before selection (same place
    # and same "never occupies a selection slot it'll lose anyway" reasoning as `exclusions`).
    min_credit_rating: str | None = None  # fixed-income only; country-level proxy
    # from the bond-risk research agent —
    # yfinance has no per-security rating.
    min_dividend_yield: float | None = None  # uses Candidate.dividend_yield (0..1)
    max_pe_ratio: float | None = None  # stocks only; skipped when P/E unknown
    max_expense_ratio: float | None = None  # ETF/mutual-fund only


class ConstructionRequest(BaseModel):
    mandate: str = "balanced"  # conservative|balanced|growth|aggressive
    risk_tolerance: str = "medium"  # low|medium|high (qualitative colour)
    horizon_years: int = 5
    base_currency: str = "USD"
    invest_amount: float = 1_000_000.0
    constraints: ConstructionConstraints = Field(default_factory=ConstructionConstraints)
    universe_symbols: list[str] = Field(default_factory=list)  # empty => use country/style universe
    weighting_method: WeightingMethod = WeightingMethod.EQUAL_WEIGHT

    # --- Investor profile (drives WHICH securities and HOW MUCH of each) ---------------
    geography_mode: GeographyMode = GeographyMode.GLOBAL
    countries: list[str] = Field(default_factory=list)  # allowed/favoured countries
    style: PortfolioStyle = PortfolioStyle.MULTI_CAP
    cap_mix: dict[str, float] = Field(
        default_factory=dict
    )  # {large_cap,mid_cap,small_cap} for MULTI_CAP
    theme: str | None = None  # for THEMATIC (e.g. 'clean energy', 'AI')
    instrument_types: list[InstrumentType] = Field(default_factory=list)  # empty => any

    # Equity vs debt split (overrides the mandate's equity/fixed-income proportion when set).
    equity_pct: float | None = None  # 0..1
    debt_pct: float | None = None  # 0..1

    # Return objective: a target annualised % OR a target end value over the horizon.
    target_return_pct: float | None = None  # e.g. 0.12 = 12% p.a.
    target_value: float | None = None  # e.g. 1_500_000 from 1_000_000 in N years

    # Geopolitical/sectoral/bond/FD-rate/market research per country in `countries` — off
    # switch since it's materially more expensive than the news scan (5 agents x N countries).
    enable_country_research: bool = True

    # Committee framework for the LLM constructor: 'vlrt' (Quant-style),
    # 'vcst' (ICICI-style: Valuation/Cycle/Sentiment/Trigger), 'sql'
    # (Motilal Oswal-style: Size/Quality/Longevity), 'opcofinco'
    # (WhiteOak-style: OpCo/FinCo/Business/Value), 'bmv'
    # (Kotak-style: Business/Management/Valuation, sequential gate),
    # 'both' (VLRT+VCST, legacy default) or 'all' (all five committees).
    framework: str = "both"  # vlrt | vcst | sql | opcofinco | bmv | both | all
    cycle_stance: str = "neutral"  # weak | neutral | strong (VCST Cycle)
    enable_vcst_search: bool = True  # live stock/sector search for Sentiment
    enable_of_search: bool = True  # live governance/disruption search for Business
    enable_bmv_search: bool = True  # live governance search for Management


# --------------------------------------------------------------------------- #
# Deterministic outputs — allocation
# --------------------------------------------------------------------------- #
class AllocationBucket(BaseModel):
    name: str
    market_value: float = 0.0
    weight: float = 0.0


class AllocationBreakdown(BaseModel):
    total_market_value: float = 0.0
    base_currency: str = "USD"
    by_asset_class: list[AllocationBucket] = Field(default_factory=list)
    by_sector: list[AllocationBucket] = Field(default_factory=list)
    by_region: list[AllocationBucket] = Field(default_factory=list)
    by_country: list[AllocationBucket] = Field(default_factory=list)
    by_market_cap: list[AllocationBucket] = Field(default_factory=list)
    by_currency: list[AllocationBucket] = Field(default_factory=list)
    by_position: list[AllocationBucket] = Field(default_factory=list)
    equity_debt: dict[str, float] = Field(
        default_factory=dict
    )  # {'equity':w,'debt':w,'cash':w,'other':w}
    priced_coverage: float = 1.0  # fraction of MV that could be priced
    unpriced_symbols: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic outputs — drift vs policy + rebalancing
# --------------------------------------------------------------------------- #
class PolicyLine(BaseModel):
    dimension: str = "asset_class"  # which IPS dimension this line is on
    bucket: str = ""  # e.g. 'equity'
    target_weight: float = 0.0
    actual_weight: float = 0.0
    lower_band: float = 0.0
    upper_band: float = 0.0
    drift: float = 0.0  # actual - target
    status: DriftStatus = DriftStatus.WITHIN_BAND
    rationale: str = ""  # why this status / what to do


class DriftReport(BaseModel):
    mandate: str = ""
    lines: list[PolicyLine] = Field(default_factory=list)
    n_breaches: int = 0
    active_share: float = 0.0  # 0.5 * sum|drift|, total distance from target
    within_tolerance: bool = True


class RebalanceTrade(BaseModel):
    bucket: str = ""  # asset-class sleeve being adjusted
    action: str = "hold"  # buy | sell
    current_weight: float = 0.0
    target_weight: float = 0.0
    current_value: float = 0.0
    trade_value: float = 0.0  # signed: + buy, - sell
    trade_pct: float = 0.0  # |trade_value| / total
    rationale: str = ""


class RebalancePlan(BaseModel):
    trades: list[RebalanceTrade] = Field(default_factory=list)
    total_buy_value: float = 0.0
    total_sell_value: float = 0.0
    turnover_pct: float = 0.0  # one-way turnover as a fraction of MV
    est_transaction_cost: float = 0.0
    summary: str = ""


# --------------------------------------------------------------------------- #
# Deterministic outputs — cash-flow variance
# --------------------------------------------------------------------------- #
class CashFlowVariance(BaseModel):
    type: CashFlowType = CashFlowType.OTHER
    symbol: str | None = None
    expected: float = 0.0
    actual: float = 0.0
    variance: float = 0.0  # actual - expected
    variance_pct: float | None = None
    status: str = "on_track"  # on_track|shortfall|excess|missing|unexpected
    severity: Severity = Severity.INFO
    basis: str = ""  # how 'expected' was derived
    note: str = ""


class CashFlowReport(BaseModel):
    period_start: str | None = None
    period_end: str | None = None
    variances: list[CashFlowVariance] = Field(default_factory=list)
    total_expected_income: float = 0.0
    total_actual_income: float = 0.0
    net_variance: float = 0.0
    n_flags: int = 0
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Deterministic outputs — risk metrics & limits
# --------------------------------------------------------------------------- #
class RiskMetrics(BaseModel):
    lookback_days: int = 0
    n_holdings: int = 0
    market_value: float = 0.0
    volatility_annual: float | None = None  # annualised stdev of returns
    max_drawdown: float | None = None  # most negative peak-to-trough (<=0)
    var_95_1d_pct: float | None = None  # 1-day 95% historical VaR, as a fraction
    var_95_1d_value: float | None = None  # ... in base currency
    beta: float | None = None  # vs the mandate benchmark
    tracking_error: float | None = None  # annualised stdev of (port - benchmark)
    expected_return_annual: float | None = None  # naive historical mean, annualised
    dividend_yield: float | None = None
    concentration_hhi: float | None = None  # sum of squared weights
    effective_n: float | None = None  # 1 / HHI
    top1_weight: float | None = None
    top5_weight: float | None = None
    largest_position: str | None = None
    data_coverage: float = 0.0  # fraction of MV with usable price history
    notes: list[str] = Field(default_factory=list)


class RiskLimitCheck(BaseModel):
    name: str
    observed: float | None = None
    limit: float | None = None
    higher_is_worse: bool = True
    status: str = "ok"  # ok | warn | breach | not_available
    severity: Severity = Severity.INFO
    detail: str = ""


# --------------------------------------------------------------------------- #
# Deterministic outputs — alerts & risk score
# --------------------------------------------------------------------------- #
class Alert(BaseModel):
    type: AlertType
    severity: Severity = Severity.INFO
    title: str = ""
    detail: str = ""
    evidence: list[str] = Field(default_factory=list)
    recommended_action: str = ""
    symbols: list[str] = Field(default_factory=list)


class RiskFactorType(str, Enum):
    ALLOCATION_DRIFT = "allocation_drift"
    CONCENTRATION = "concentration"
    VOLATILITY = "volatility"
    DRAWDOWN = "drawdown"
    CASHFLOW_VARIANCE = "cashflow_variance"
    RISK_LIMIT_BREACH = "risk_limit_breach"
    EMERGING_RISK = "emerging_risk"
    COUNTRY_RISK = "country_risk"


class RiskFactorResult(BaseModel):
    factor: RiskFactorType
    present: bool = False
    severity: str = "low"  # low|medium|high|critical
    weight: float = 0.0
    contribution: float = 0.0
    evidence: list[str] = Field(default_factory=list)


class PortfolioRiskScore(BaseModel):
    factors: list[RiskFactorResult] = Field(default_factory=list)
    raw_score: float = 0.0
    normalized_score: float = 0.0  # 0..100 (higher = riskier / less healthy)
    health_band: HealthBand = HealthBand.HEALTHY
    summary: str = ""


# --------------------------------------------------------------------------- #
# Emerging-risk scan (free web/news — LLM classifies, Python decides tone fallback)
# --------------------------------------------------------------------------- #
class RiskDimension(str, Enum):
    MARKET = "market"  # broad market / volatility
    MACRO = "macro"  # rates, inflation, growth
    GEOPOLITICAL = "geopolitical"
    REGULATORY = "regulatory"
    SECTOR = "sector"
    COMPANY_SPECIFIC = "company_specific"
    LIQUIDITY = "liquidity"
    FIXED_INCOME = "fixed_income"  # bond/credit/duration risk


class NewsEvidenceItem(BaseModel):
    dimension: RiskDimension
    query: str
    title: str | None = None
    url: str | None = None
    snippet: str | None = None
    source: str = "news"  # 'web' | 'news'
    published: str | None = None
    related_symbol: str | None = None


class EmergingRiskFinding(BaseModel):
    scope: str = "portfolio"  # 'portfolio' | 'position' | 'sector'
    subject: str = "market"  # symbol / sector / 'market'
    category: RiskDimension = RiskDimension.MARKET
    severity: str = "medium"  # low|medium|high (LLM read; discounted)
    summary: str = ""
    url: str | None = None


class MarketRiskScan(BaseModel):
    overall_risk_tone: str = "normal"  # calm|normal|elevated|stressed
    summary: str = ""
    findings: list[EmergingRiskFinding] = Field(default_factory=list)
    evidence: list[NewsEvidenceItem] = Field(default_factory=list)
    watchlist_symbols: list[str] = Field(default_factory=list)
    data_gaps: list[str] = Field(default_factory=list)


class CountryResearchProfile(BaseModel):
    """One country's research read: geopolitical/sectoral/bond/market findings (risk-shaped,
    same as MarketRiskScan's findings) plus bank FD/deposit rates (NOT a risk — an opportunity
    /yield number, so it gets its own field rather than being forced into a severity)."""

    country: str
    findings: list[EmergingRiskFinding] = Field(default_factory=list)
    overall_risk_tone: str = "normal"  # calm|normal|elevated|stressed
    fd_rate_pct: float | None = None  # approximate 1y retail FD/deposit rate
    fd_rate_basis: str = ""  # e.g. "1-year retail FD, approximate"
    # A grade (AAA..D) isn't a risk severity any more than an FD rate is — same reasoning,
    # own field rather than forced into `findings`. Country-level proxy (see universe.py /
    # research/deterministic.py) — yfinance has no per-security bond rating at all.
    bond_credit_rating: str | None = None
    bond_credit_rating_basis: str = ""
    # Growth read is an opportunity signal, same reasoning as fd_rate_pct above — not a risk
    # severity, so it gets its own fields rather than being forced into `findings`.
    growth_outlook: str = "unknown"  # low | moderate | high | unknown
    growth_sectors: list[str] = Field(
        default_factory=list
    )  # sectors the research flagged as growing
    data_gaps: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Construction outputs
# --------------------------------------------------------------------------- #
class SelectedPosition(BaseModel):
    symbol: str
    name: str | None = None
    asset_class: AssetClass = AssetClass.OTHER
    sector: str | None = None
    region: Region = Region.OTHER
    country: str | None = None
    market_cap: MarketCap = MarketCap.UNKNOWN
    instrument_type: InstrumentType = InstrumentType.OTHER
    target_weight: float = 0.0
    amount: float = 0.0  # base currency allocated
    shares: float | None = None
    price: float | None = None
    expected_return: float | None = None  # annualised historical proxy
    rationale: str = ""  # why this name & weight (the agent's reason)
    pros: list[str] = Field(
        default_factory=list
    )  # pros of THIS holding (incl. geopolitical/sector)
    cons: list[str] = Field(default_factory=list)  # cons / risks of THIS holding
    vlrt_scores: dict[str, float] = Field(default_factory=dict)  # V/L/R/T + combined 0..1
    vlrt_reason: str = ""  # VLRT committee one-liner for this name
    vcst_scores: dict[str, float] = Field(default_factory=dict)  # V/C/S/T + combined 0..1
    vcst_reason: str = ""  # VCST committee one-liner for this name
    sql_scores: dict[str, float] = Field(default_factory=dict)  # S/Q/L + combined 0..1
    sql_reason: str = ""  # SQL committee one-liner for this name
    of_scores: dict[str, float] = Field(default_factory=dict)  # O/F/B/V + combined 0..1
    of_reason: str = ""  # OPCO-FINCO committee one-liner for this name
    bmv_scores: dict[str, float] = Field(default_factory=dict)  # B/M/V + combined 0..1
    bmv_reason: str = ""  # BMV committee one-liner for this name


class ExcludedCandidate(BaseModel):
    symbol: str
    reason: str = ""


class FeasibilityVerdict(BaseModel):
    """Whether the investor's return objective is achievable from the constructed book."""

    target_return_annual: float | None = None  # the investor's annualised target
    target_basis: str = ""  # 'percent' | 'target_value' | 'none'
    expected_return_annual: float | None = None  # the book's expected (historical proxy)
    universe_ceiling_annual: float | None = None  # best single-asset expected return
    projected_value: float | None = None  # invest_amount compounded at expected return
    realistic: bool = True
    message: str = ""  # plain-English feasibility statement


class ConstructedPortfolio(BaseModel):
    method: str = "deterministic"  # 'deterministic' | 'llm'
    weighting_method: WeightingMethod = WeightingMethod.EQUAL_WEIGHT
    style: PortfolioStyle = PortfolioStyle.MULTI_CAP
    countries: list[str] = Field(default_factory=list)
    positions: list[SelectedPosition] = Field(default_factory=list)
    sleeve_allocation: list[AllocationBucket] = Field(default_factory=list)  # by asset class
    cap_allocation: list[AllocationBucket] = Field(default_factory=list)  # by market cap
    country_allocation: list[AllocationBucket] = Field(default_factory=list)  # by country
    expected_metrics: RiskMetrics = Field(default_factory=RiskMetrics)
    feasibility: FeasibilityVerdict | None = None
    excluded: list[ExcludedCandidate] = Field(default_factory=list)
    guardrail_corrections: list[str] = Field(default_factory=list)
    narrative: str = ""  # overall reasoning for the construction
    total_invested: float = 0.0
    cash_residual: float = 0.0
    vlrt_regime: str = "neutral"  # VLRT risk_on | risk_off | neutral
    vlrt_summary: str = ""  # one-line VLRT committee read
    vcst_stance: str = "neutral"  # VCST buy_dip | neutral | take_profit
    vcst_summary: str = ""  # one-line VCST committee read
    sql_stance: str = "selective"  # SQL high_conviction | selective | cautious
    sql_summary: str = ""  # one-line SQL committee read
    of_stance: str = "selective"  # OPCO-FINCO high_conviction | selective | cautious
    of_summary: str = ""  # one-line OPCO-FINCO committee read
    bmv_stance: str = "selective"  # BMV high_conviction | selective | cautious
    bmv_summary: str = ""  # one-line BMV committee read


class ConstructionComparison(BaseModel):
    # each row: {"sleeve": str, "deterministic": float, "llm": float, "diff": float}
    allocation_diff: list[dict[str, Any]] = Field(default_factory=list)  # per-sleeve det vs llm
    name_overlap: list[str] = Field(default_factory=list)
    det_only: list[str] = Field(default_factory=list)
    llm_only: list[str] = Field(default_factory=list)
    jaccard: float = 0.0
    metric_diff: dict[str, float | None] = Field(default_factory=dict)  # vol/yield/hhi deltas
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Monitoring briefing (templated OR grounded LLM)
# --------------------------------------------------------------------------- #
class MonitoringBriefing(BaseModel):
    method: str = "deterministic"  # 'deterministic' | 'llm'
    executive_summary: str = ""
    allocation_commentary: str = ""
    risk_commentary: str = ""
    cashflow_commentary: str = ""
    recommended_actions: list[str] = Field(default_factory=list)
    watch_items: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Top-level results
# --------------------------------------------------------------------------- #
class MonitoringResult(BaseModel):
    portfolio: PortfolioInput = Field(default_factory=PortfolioInput)
    allocation: AllocationBreakdown = Field(default_factory=AllocationBreakdown)
    drift: DriftReport = Field(default_factory=DriftReport)
    rebalance: RebalancePlan = Field(default_factory=RebalancePlan)
    cashflow: CashFlowReport = Field(default_factory=CashFlowReport)
    risk_metrics: RiskMetrics = Field(default_factory=RiskMetrics)
    limit_checks: list[RiskLimitCheck] = Field(default_factory=list)
    risk_scan: MarketRiskScan | None = None
    country_research: dict[str, CountryResearchProfile] = Field(default_factory=dict)
    alerts: list[Alert] = Field(default_factory=list)
    score: PortfolioRiskScore = Field(default_factory=PortfolioRiskScore)
    # Both briefings can be attached (mode 'both' for side-by-side comparison).
    briefing_deterministic: MonitoringBriefing | None = None
    briefing_llm: MonitoringBriefing | None = None


class ConstructionResult(BaseModel):
    request: ConstructionRequest = Field(default_factory=ConstructionRequest)
    deterministic: ConstructedPortfolio | None = None
    llm: ConstructedPortfolio | None = None
    comparison: ConstructionComparison | None = None
    risk_scan: MarketRiskScan | None = None
    country_research: dict[str, CountryResearchProfile] = Field(default_factory=dict)
