"""
Grounded structured extraction for a credit-submission pack.

The LLM does *language* work only: read the balance sheet / P&L / cash-flow statement,
name each line item, map it to a standardised label, read its value for each period,
and summarize the notes and management commentary. **No ratios, no scoring, no
arithmetic happens here** — those are deterministic and live in `ratios.py`,
`benchmarks.py`, `scoring.py`.

Every extractor is grounded via real LangChain tool-calling: the model is given
`search_pages`/`get_page` tools (see `tools.py`) and must call them itself to find the
pages it needs, rather than Python pre-fetching one fixed query's top-k pages before the
call. Letting the model issue several targeted searches (e.g. balance sheet pages, then
separately cash-flow pages) and cite a specific page number improves grounding over
committing to one query's top-k up front — see `tool_loop.py` for the bounded
tool-calling exchange this runs on. Numbers are transcribed exactly as printed (the LLM
is explicitly told not to compute, infer, or scale them — the deterministic engine
handles all math).
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from .config import get_llm
from .llm_json import safe_json_loads
from .schemas import (
    AuditOpinion,
    CompanyMetadata,
    FacilityRequest,
    LineItem,
    NoteItem,
    PeriodType,
    StandardLabel,
    StatementType,
)
from .tool_loop import run_tool_calling_loop


def _enum(value: Any, enum_cls, default):
    try:
        return enum_cls(value)
    except (ValueError, TypeError):
        return default


def _num(value: Any) -> Optional[float]:
    """Parse a number the model transcribed, tolerating commas, parens (negatives), %."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-", "—", "n/a", "na", "N/A"):
        return None
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


_VALID_PERIOD_LENGTHS = (3, 6, 9, 12)


def _parse_date(value: Any) -> Optional[date]:
    """Parse a model-supplied ISO date string, tolerating anything malformed as 'not stated'."""
    if not value or not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y-%m-%d").date()
    except ValueError:
        return None


def _period_length(value: Any) -> Optional[int]:
    """Only 3/6/9/12 months are meaningful reporting-period lengths; anything else is
    treated as the model not actually knowing — better a gap than a wrong number."""
    try:
        months = int(value)
    except (TypeError, ValueError):
        return None
    return months if months in _VALID_PERIOD_LENGTHS else None


def _extract_json(system: SystemMessage, task: str, tools: list[BaseTool]) -> Any:
    """Run the bounded tool-calling loop and parse its final answer as JSON."""
    user = HumanMessage(content=task)
    raw = run_tool_calling_loop(get_llm(), tools, [system, user])
    return safe_json_loads(raw)


# --------------------------------------------------------------------------- #
# Company metadata + audit opinion + management commentary
# --------------------------------------------------------------------------- #
def extract_company_metadata(tools: list[BaseTool]) -> CompanyMetadata:
    """Detect company identity, industry, auditor, opinion and outlook commentary."""
    opinions = [o.value for o in AuditOpinion]

    system = SystemMessage(content=(
        "You read the front matter, directors' report and auditor's report of a set of "
        "company financial statements. Use the search_pages/get_page tools to find the "
        "relevant pages before answering — try queries like 'company name principal "
        "activities industry sector country of incorporation' and \"auditor's report "
        "opinion number of employees registration number\". From the page text you "
        "retrieve ONLY, extract the company's identity and the audit opinion. Classify "
        f"the audit opinion as one of {opinions} (clean = unqualified). Summarize any "
        "forward-looking / outlook / going-concern commentary into "
        "`management_commentary` (<= 600 chars). Use ONLY the text you retrieved; never "
        "guess. When you're done searching, output ONLY a JSON object — no prose, no "
        "markdown fence — matching:\n"
        "{\n"
        '  "company_name": str|null, "legal_entity_type": str|null,\n'
        '  "industry": str|null, "sub_industry": str|null, "country": str|null,\n'
        '  "registration_number": str|null, "years_in_operation": int|null,\n'
        '  "employee_count": int|null, "auditor": str|null,\n'
        '  "audit_opinion": one of ' + str(opinions) + ',\n'
        '  "reporting_currency": str|null, "management_commentary": str|null, "page": int|null\n'
        "}"
    ))

    data = _extract_json(system, "Extract the company metadata now.", tools)
    if not isinstance(data, dict):
        return CompanyMetadata()

    return CompanyMetadata(
        company_name=data.get("company_name"),
        legal_entity_type=data.get("legal_entity_type"),
        industry=data.get("industry"),
        sub_industry=data.get("sub_industry"),
        country=data.get("country"),
        registration_number=data.get("registration_number"),
        years_in_operation=data.get("years_in_operation") if isinstance(data.get("years_in_operation"), int) else None,
        employee_count=data.get("employee_count") if isinstance(data.get("employee_count"), int) else None,
        auditor=data.get("auditor"),
        audit_opinion=_enum(data.get("audit_opinion"), AuditOpinion, AuditOpinion.UNKNOWN),
        reporting_currency=data.get("reporting_currency"),
        management_commentary=(data.get("management_commentary") or "")[:600] or None,
        page=data.get("page"),
    )


# --------------------------------------------------------------------------- #
# Financial line items (multi-period, all statement types) — the core extraction
# --------------------------------------------------------------------------- #
def extract_line_items(tools: list[BaseTool]) -> list[LineItem]:
    """Extract every financial line item across every presented period.

    The model maps each printed label onto a `standardised_label` from the fixed
    vocabulary so the ratio engine can find inputs regardless of the filer's wording.
    One record is emitted per (line item × period).
    """
    labels = [s.value for s in StandardLabel]
    stmts = [s.value for s in StatementType]
    ptypes = [p.value for p in PeriodType]

    system = SystemMessage(content=(
        "You are a financial-statement spreading analyst. Use the search_pages/get_page "
        "tools to find every relevant page before answering — the balance sheet, income "
        "statement and cash-flow statement are often on different pages, so search "
        "separately for each if one query doesn't surface all of them, e.g. "
        "'consolidated balance sheet statement of financial position', 'income "
        "statement profit and loss revenue EBITDA', 'statement of cash flows operating "
        "cash flow capital expenditure'. Request enough pages (k=10 or more) to cover "
        "every period presented. From the page text you retrieve ONLY, transcribe every "
        "financial line item, for EVERY period (column) shown.\n"
        "Rules:\n"
        "1. Emit ONE record per (line item x period). If a row shows FY2023 and FY2022, "
        "emit two records with the same labels and different `period`/`value`.\n"
        "2. Map each printed label to the single best `standardised_label` from the list. "
        "If none fits, use 'other'. Common synonyms: Turnover/Net sales -> revenue; "
        "Operating profit -> ebit; Profit for the year -> net_income; Trade receivables -> "
        "accounts_receivable; Borrowings/Loans -> use short_term_debt or long_term_debt by "
        "maturity, and also emit total_debt if a total is printed; Shareholders' funds/Net "
        "assets -> total_equity; Cash generated from operations / Net cash from operating "
        "activities -> operating_cash_flow; Purchase of PP&E -> capital_expenditure.\n"
        "3. Transcribe `value` EXACTLY as printed. Do NOT compute, sum, infer, or rescale. "
        "Negatives in parentheses are negative. If a cell is blank/dash, omit that record.\n"
        "4. Record `unit` as 'thousands' or 'millions' if the statement says figures are in "
        "those units (e.g. \"$'000\"), else 'absolute'. Record the `currency` and `page`.\n"
        "5. Set `statement_type` to where the row appears. Use the SAME `period` string "
        "(e.g. 'FY2023') consistently across all statements.\n"
        "6. If the statement states the period's end date (e.g. 'As at 31 December "
        "2023', 'for the year ended 31 March 2024'), record it as `period_end_date` in "
        "ISO format YYYY-MM-DD. If the statement states or implies the reporting period "
        "length (a full fiscal year, a half-year, a quarter, a nine-month interim), "
        "record `period_length_months` as 12, 6, 3, or 9 respectively. Leave either null "
        "if not determinable from the text — do not guess.\n"
        "When you're done searching, output ONLY a JSON array — no prose, no markdown "
        "fence — matching:\n"
        '[{"label": str, "standardised_label": one of ' + str(labels) + ', '
        '"value": number|string|null, "currency": str|null, '
        '"unit": "absolute"|"thousands"|"millions", "period": str, '
        '"period_type": one of ' + str(ptypes) + ', '
        '"period_end_date": "YYYY-MM-DD"|null, "period_length_months": int|null, '
        '"statement_type": one of ' + str(stmts) + ', "page": int}]'
    ))

    data = _extract_json(system, "Extract every financial line item now.", tools)
    if not isinstance(data, list):
        return []

    out: list[LineItem] = []
    for row in data:
        if not isinstance(row, dict) or not row.get("label") or not row.get("period"):
            continue
        out.append(LineItem(
            label=str(row["label"]).strip(),
            standardised_label=_enum(row.get("standardised_label"), StandardLabel, StandardLabel.OTHER),
            value=_num(row.get("value")),
            currency=row.get("currency"),
            unit=str(row.get("unit") or "absolute"),
            period=str(row["period"]).strip(),
            period_type=_enum(row.get("period_type"), PeriodType, PeriodType.ANNUAL),
            period_end_date=_parse_date(row.get("period_end_date")),
            period_length_months=_period_length(row.get("period_length_months")),
            statement_type=_enum(row.get("statement_type"), StatementType, StatementType.OTHER),
            page=row.get("page"),
            source_snippet=(row.get("label") or "")[:200] or None,
        ))
    return out


# --------------------------------------------------------------------------- #
# Notes: off-balance-sheet, contingencies, going concern
# --------------------------------------------------------------------------- #
def extract_notes(tools: list[BaseTool]) -> list[NoteItem]:
    """Extract off-balance-sheet items and contingent liabilities from the notes."""
    system = SystemMessage(content=(
        "You extract OFF-BALANCE-SHEET items and CONTINGENT LIABILITIES from financial "
        "statement notes. Use the search_pages/get_page tools first — try 'contingent "
        "liabilities guarantees commitments operating lease litigation legal "
        "proceedings related party transactions going concern off balance sheet'. For "
        "each item you find, classify the category as one of: 'operating_lease', "
        "'guarantee', 'litigation', 'related_party', 'going_concern', 'other'. Record "
        "the amount if a figure is given. Use ONLY the text you retrieved. When you're "
        "done searching, output ONLY a JSON array — no prose, no markdown fence — "
        'matching: [{"category": str, "description": str, "amount": number|null, '
        '"currency": str|null, "page": int}]'
    ))

    data = _extract_json(system, "Extract the notes and contingencies now.", tools)
    if not isinstance(data, list):
        return []

    out: list[NoteItem] = []
    for row in data:
        if not isinstance(row, dict) or not row.get("description"):
            continue
        out.append(NoteItem(
            category=str(row.get("category") or "other").strip(),
            description=str(row["description"])[:400],
            amount=_num(row.get("amount")),
            currency=row.get("currency"),
            page=row.get("page"),
        ))
    return out


# --------------------------------------------------------------------------- #
# Facility request
# --------------------------------------------------------------------------- #
def extract_facility_request(tools: list[BaseTool]) -> FacilityRequest:
    """Detect the requested credit facility, if stated in the pack."""
    system = SystemMessage(content=(
        "Identify the CREDIT FACILITY being requested, if any is stated in the "
        "documents. Use the search_pages/get_page tools first — try 'credit facility "
        "loan request term loan revolving credit facility amount requested purpose of "
        "facility tenor proposed financing'. Use ONLY the text you retrieved; if no "
        "facility request is present, return nulls. When you're done searching, output "
        "ONLY a JSON object — no prose, no markdown fence — matching: "
        '{"facility_type": str|null, "amount": number|null, "currency": str|null, '
        '"purpose": str|null, "tenor": str|null, "page": int|null}'
    ))

    data = _extract_json(system, "Identify the facility request now.", tools)
    if not isinstance(data, dict):
        return FacilityRequest()
    return FacilityRequest(
        facility_type=data.get("facility_type"),
        amount=_num(data.get("amount")),
        currency=data.get("currency"),
        purpose=data.get("purpose"),
        tenor=data.get("tenor"),
        page=data.get("page"),
    )
