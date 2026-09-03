"""
Schema for SME-through-large-corporate credit risk assessment.

Design notes
------------
* `LineItem` is **flat** and carries a `standardised_label` drawn from a fixed
  vocabulary (`StandardLabel`). Different filings call the same thing different names
  ("Turnover" vs "Revenue" vs "Net sales"); the LLM maps each to one standard label so
  the deterministic ratio engine can find inputs by meaning, not by the filer's wording.
* Each `LineItem` belongs to a `period` (e.g. "FY2023") so the same standard label across
  periods forms a time series that the trend engine consumes.
* The LLM only fills the *extraction* records (CompanyMetadata / LineItem / NoteItem /
  FacilityRequest). Every *decision-bearing* record below the divider (FinancialRatios,
  SectorBenchmarkResult, TrendAnalysis, RatingFactorResult, CreditScorecard) is produced
  by deterministic Python so the rating is reproducible and auditable.
* Every fact keeps its source `page` for traceability.
"""
from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class StatementType(str, Enum):
    BALANCE_SHEET = "balance_sheet"
    INCOME_STATEMENT = "income_statement"
    CASH_FLOW_STATEMENT = "cash_flow_statement"
    NOTES = "notes"
    OTHER = "other"


class AuditOpinion(str, Enum):
    CLEAN = "clean"                  # unqualified
    QUALIFIED = "qualified"
    ADVERSE = "adverse"
    DISCLAIMER = "disclaimer"
    UNKNOWN = "unknown"


class StandardLabel(str, Enum):
    """Fixed vocabulary the LLM maps raw line-item labels onto.

    The ratio engine references *only* these labels, so a filing that says "Turnover"
    and one that says "Net sales" both resolve to REVENUE and compute identically.
    """
    # --- Income statement ---
    REVENUE = "revenue"
    COST_OF_GOODS_SOLD = "cost_of_goods_sold"
    GROSS_PROFIT = "gross_profit"
    OPERATING_EXPENSES = "operating_expenses"
    EBIT = "ebit"                                  # operating profit
    EBITDA = "ebitda"
    DEPRECIATION_AMORTIZATION = "depreciation_amortization"
    INTEREST_EXPENSE = "interest_expense"
    PRETAX_INCOME = "pretax_income"
    TAX_EXPENSE = "tax_expense"
    NET_INCOME = "net_income"

    # --- Balance sheet: assets ---
    CASH_AND_EQUIVALENTS = "cash_and_equivalents"
    ACCOUNTS_RECEIVABLE = "accounts_receivable"
    INVENTORY = "inventory"
    CURRENT_ASSETS = "current_assets"
    PROPERTY_PLANT_EQUIPMENT = "property_plant_equipment"
    TOTAL_ASSETS = "total_assets"

    # --- Balance sheet: liabilities & equity ---
    ACCOUNTS_PAYABLE = "accounts_payable"
    SHORT_TERM_DEBT = "short_term_debt"
    CURRENT_LIABILITIES = "current_liabilities"
    LONG_TERM_DEBT = "long_term_debt"
    TOTAL_DEBT = "total_debt"
    TOTAL_LIABILITIES = "total_liabilities"
    TOTAL_EQUITY = "total_equity"

    # --- Cash flow ---
    OPERATING_CASH_FLOW = "operating_cash_flow"
    CAPITAL_EXPENDITURE = "capital_expenditure"
    FREE_CASH_FLOW = "free_cash_flow"
    DEBT_PRINCIPAL_REPAYMENT = "debt_principal_repayment"
    DIVIDENDS_PAID = "dividends_paid"

    OTHER = "other"


class PeriodType(str, Enum):
    ANNUAL = "annual"
    INTERIM = "interim"
    QUARTERLY = "quarterly"


# --------------------------------------------------------------------------- #
# Rating taxonomy
# --------------------------------------------------------------------------- #
class RatingGrade(str, Enum):
    AAA = "AAA"
    AA = "AA"
    A = "A"
    BBB = "BBB"
    BB = "BB"
    B = "B"
    CCC = "CCC"
    CC = "CC"
    C = "C"
    D = "D"


class RiskBand(str, Enum):
    INVESTMENT_GRADE_STRONG = "investment_grade_strong"   # AAA/AA
    INVESTMENT_GRADE = "investment_grade"                 # A/BBB
    SUB_INVESTMENT_GRADE = "sub_investment_grade"         # BB
    SPECULATIVE = "speculative"                           # B/CCC
    DISTRESSED = "distressed"                             # CC/C
    DEFAULT = "default"                                   # D


class LendingDecision(str, Enum):
    APPROVE = "approve"
    APPROVE_WITH_CONDITIONS = "approve_with_conditions"
    REFER_CREDIT_COMMITTEE = "refer_credit_committee"
    DECLINE = "decline"


class RatingFactorType(str, Enum):
    LEVERAGE = "leverage"
    LIQUIDITY = "liquidity"
    PROFITABILITY = "profitability"
    DEBT_SERVICE_COVERAGE = "debt_service_coverage"
    CASH_FLOW_QUALITY = "cash_flow_quality"
    DETERIORATING_TREND = "deteriorating_trend"
    SECTOR_UNDERPERFORMANCE = "sector_underperformance"
    SIZE_SCALE = "size_scale"
    JURISDICTION = "jurisdiction"
    AUDIT_QUALITY = "audit_quality"          # qualified/adverse opinion, going-concern
    OFF_BALANCE_SHEET = "off_balance_sheet"  # contingencies, guarantees, litigation
    ADVERSE_MEDIA = "adverse_media"          # web-found fraud / litigation / distress news
    MARKET_SENTIMENT = "market_sentiment"    # overall public/news sentiment on the borrower


# --------------------------------------------------------------------------- #
# Extraction records (filled by the grounded LLM extractors)
# --------------------------------------------------------------------------- #
class CompanyMetadata(BaseModel):
    company_name: Optional[str] = None
    legal_entity_type: Optional[str] = None          # e.g. 'Private Limited', 'PLC'
    industry: Optional[str] = None                   # used to pick the sector benchmark
    sub_industry: Optional[str] = None
    country: Optional[str] = None
    registration_number: Optional[str] = None
    years_in_operation: Optional[int] = None
    employee_count: Optional[int] = None
    auditor: Optional[str] = None
    audit_opinion: AuditOpinion = AuditOpinion.UNKNOWN
    reporting_currency: Optional[str] = None
    management_commentary: Optional[str] = Field(
        None, description="Forward-looking / outlook text from MD&A, verbatim or summarized."
    )
    page: Optional[int] = None


class LineItem(BaseModel):
    """One financial figure for one period. `standardised_label` is the join key the
    ratio engine reads; `label` keeps the filer's original wording for the audit trail."""
    label: str = Field(..., description="The line-item label exactly as printed.")
    standardised_label: StandardLabel = StandardLabel.OTHER
    value: Optional[float] = Field(
        None, description="Numeric value in `unit`s of `currency`. None if illegible."
    )
    currency: Optional[str] = None
    unit: str = Field("absolute", description="'absolute' | 'thousands' | 'millions'")
    period: str = Field(..., description="Period label, e.g. 'FY2023', 'Q2-2024'.")
    period_type: PeriodType = PeriodType.ANNUAL
    period_end_date: Optional[date] = Field(
        None, description="The period's end date, if stated (e.g. 'as at 31 December 2023')."
    )
    period_length_months: Optional[int] = Field(
        None, description="Length of the reporting period in months (3, 6, 9, or 12), if determinable."
    )
    statement_type: StatementType = StatementType.OTHER
    page: Optional[int] = None
    source_snippet: Optional[str] = None


class NoteItem(BaseModel):
    """Off-balance-sheet item or contingency declared in the notes."""
    category: str                                   # 'operating_lease' | 'guarantee' | 'litigation' | 'related_party' | 'going_concern' | 'other'
    description: str
    amount: Optional[float] = None
    currency: Optional[str] = None
    page: Optional[int] = None


class FacilityRequest(BaseModel):
    facility_type: Optional[str] = None             # 'term_loan' | 'revolving_credit' | 'capex' | 'bond' ...
    amount: Optional[float] = None
    currency: Optional[str] = None
    purpose: Optional[str] = None
    tenor: Optional[str] = None                     # e.g. '5 years'
    page: Optional[int] = None


# --------------------------------------------------------------------------- #
# Deterministic outputs (no LLM — reproducible & auditable)
# --------------------------------------------------------------------------- #
class RatioValue(BaseModel):
    """One computed ratio for one period, with the inputs it used for traceability."""
    name: str                                       # e.g. 'net_debt_to_ebitda'
    value: Optional[float] = None                   # None when a required input was missing
    category: str = ""                              # 'leverage' | 'liquidity' | ...
    inputs_used: List[str] = Field(default_factory=list)   # standardised labels consumed
    missing_inputs: List[str] = Field(default_factory=list)
    pages: List[int] = Field(default_factory=list)


class FinancialRatios(BaseModel):
    """The full ratio set for a single period."""
    period: str
    ratios: Dict[str, RatioValue] = Field(default_factory=dict)


class BenchmarkComparison(BaseModel):
    ratio_name: str
    company_value: Optional[float] = None
    sector_median: Optional[float] = None
    sector_p25: Optional[float] = None
    sector_p75: Optional[float] = None
    classification: str = "not_available"           # above_75th|above_median|median|below_median|below_25th|not_available
    higher_is_better: bool = True
    is_material_gap: bool = False                    # bottom-quartile on a ratio that matters


class SectorBenchmarkResult(BaseModel):
    industry_used: Optional[str] = None              # which benchmark profile was applied
    comparisons: List[BenchmarkComparison] = Field(default_factory=list)
    overall_position: str = "average"                # 'strong' | 'average' | 'weak'
    material_gaps: List[str] = Field(default_factory=list)


class RatioTrend(BaseModel):
    ratio_name: str
    series: List[Optional[float]] = Field(default_factory=list)   # oldest -> newest
    periods: List[str] = Field(default_factory=list)
    direction: str = "stable"                        # improving|stable|deteriorating|volatile
    magnitude: str = "minor"                         # minor|moderate|significant
    pct_change: Optional[float] = None               # newest vs oldest, as a fraction


class TrendAnalysis(BaseModel):
    trends: List[RatioTrend] = Field(default_factory=list)
    overall_trajectory: str = "stable"               # improving|stable|deteriorating|mixed
    commentary_signal: str = "neutral"               # risk|neutral|positive (from MD&A text)
    notes: List[str] = Field(default_factory=list)


class RatingFactorResult(BaseModel):
    factor: RatingFactorType
    present: bool                                    # whether this factor adds risk
    severity: str = "low"                            # low|medium|high|critical
    weight: float = 0.0
    contribution: float = 0.0                        # weight * severity multiplier (0 when absent)
    evidence: List[str] = Field(default_factory=list)
    pages: List[int] = Field(default_factory=list)


class CreditScorecard(BaseModel):
    factors: List[RatingFactorResult] = Field(default_factory=list)
    raw_score: float = 0.0                           # sum of contributions
    normalized_score: float = 0.0                    # 0..100 (higher = riskier)
    grade: RatingGrade = RatingGrade.BBB
    band: RiskBand = RiskBand.INVESTMENT_GRADE
    probability_of_default: float = 0.0              # 0..1, from the grade->PD table
    loss_given_default: float = 0.45                 # 0..1, sector-adjusted
    expected_loss_pct: float = 0.0                   # PD * LGD, as a fraction of exposure
    decision: LendingDecision = LendingDecision.APPROVE
    suggested_pricing: Optional[str] = None          # e.g. 'SOFR + 200-250 bps'
    conditions: List[str] = Field(default_factory=list)
    hard_stop_reason: Optional[str] = None           # set on default / disclaimer opinion


class CreditMemo(BaseModel):
    """LLM-written memo grounded in the deterministic scorecard (never re-rates)."""
    executive_summary: str = ""
    financial_analysis: str = ""
    key_strengths: List[str] = Field(default_factory=list)
    key_risks: List[str] = Field(default_factory=list)
    mitigants: List[str] = Field(default_factory=list)
    recommended_covenants: List[str] = Field(default_factory=list)
    monitoring_triggers: List[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Web-sentiment enrichment (free DuckDuckGo OSINT — second, optional input)
# --------------------------------------------------------------------------- #
# The user may supply a company name (alongside the financial docs). The agent then
# searches FREE, PUBLIC web/news sources for the company's recent press, and the LLM
# turns the collected results into a grounded WebSentimentResult. The deterministic
# scorecard consumes that result via the ADVERSE_MEDIA and MARKET_SENTIMENT factors —
# the LLM classifies findings (language work); Python decides their score contribution.
class SentimentDimension(str, Enum):
    """The angles we search the web from, for a rounded read on the borrower."""
    GENERAL_NEWS = "general_news"               # recent press, what's happening
    FINANCIAL_PERFORMANCE = "financial_performance"  # results, guidance, downgrades
    ADVERSE = "adverse"                         # fraud, litigation, regulatory, default
    CREDIT_DISTRESS = "credit_distress"         # restructuring, covenant breach, layoffs
    GOVERNANCE = "governance"                   # management, board, audit, accounting
    MARKET_VIEW = "market_view"                 # analyst / investor / rating commentary


class CompanySeed(BaseModel):
    """The few starting details that drive the web search."""
    company_name: str
    aliases: List[str] = Field(default_factory=list)
    country: Optional[str] = None
    industry: Optional[str] = None
    ticker: Optional[str] = None
    search_region: Optional[str] = Field(
        None, description="DuckDuckGo region, e.g. 'wt-wt', 'uk-en', 'in-en'."
    )


class WebEvidenceItem(BaseModel):
    """One public web/news result the agent found."""
    dimension: SentimentDimension
    query: str
    title: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None
    source: str = "web"                         # 'web' | 'news'
    published: Optional[str] = None


class WebAdverseFinding(BaseModel):
    """A single negative item found on the web (classified by the LLM, scored by Python)."""
    category: str                               # fraud|litigation|regulatory|default|distress|governance|other
    summary: str
    severity: str = "medium"                    # low|medium|high (LLM's read; discounted in scoring)
    url: Optional[str] = None


class WebSentimentResult(BaseModel):
    """Grounded synthesis of the public web/news footprint for the borrower."""
    seed: CompanySeed
    disambiguation_confidence: str = "low"      # are the results actually about THIS company?
    overall_sentiment: str = "neutral"          # positive|neutral|mixed|negative
    sentiment_summary: str = ""
    adverse_findings: List[WebAdverseFinding] = Field(default_factory=list)
    positive_highlights: List[str] = Field(default_factory=list)
    evidence: List[WebEvidenceItem] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    data_gaps: List[str] = Field(default_factory=list)


class DataReconciliation(BaseModel):
    """Audit record of where each figure came from and what is still missing.

    Filled deterministically while merging the documents with the yfinance fallback, and
    written to the logs so an analyst can see exactly what was sourced, cross-checked and
    left as a gap.
    """
    ticker: Optional[str] = None
    yfinance_attempted: bool = False
    yfinance_available: bool = False                  # was the library importable
    doc_line_items: int = 0
    yfinance_line_items: int = 0                      # mapped items yfinance returned
    cells_filled_from_yfinance: int = 0
    filled: List[str] = Field(default_factory=list)        # "revenue [FY2022] = 450 (yfinance:X)"
    discrepancies: List[str] = Field(default_factory=list) # doc vs yfinance value mismatches
    ratio_gaps: List[str] = Field(default_factory=list)    # ratios still uncomputable + why
    notes: List[str] = Field(default_factory=list)


class CreditAssessment(BaseModel):
    """Top-level result for one credit case."""
    company: CompanyMetadata = Field(default_factory=CompanyMetadata)
    facility: FacilityRequest = Field(default_factory=FacilityRequest)
    line_items: List[LineItem] = Field(default_factory=list)
    notes: List[NoteItem] = Field(default_factory=list)
    periods: List[str] = Field(default_factory=list)          # ordered oldest -> newest
    ratios_by_period: List[FinancialRatios] = Field(default_factory=list)
    benchmark: SectorBenchmarkResult = Field(default_factory=SectorBenchmarkResult)
    trend: TrendAnalysis = Field(default_factory=TrendAnalysis)
    scorecard: CreditScorecard = Field(default_factory=CreditScorecard)
    memo: CreditMemo = Field(default_factory=CreditMemo)
    # Attached when a company name was supplied and web search ran (see osint/).
    web_sentiment: Optional[WebSentimentResult] = None
    # Attached when a ticker was supplied (yfinance fallback / cross-check audit trail).
    reconciliation: Optional[DataReconciliation] = None
