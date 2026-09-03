"""
Translate the domain Pydantic models (`credit_risk/schemas.py`) into ORM rows.

Kept separate from `agent_graph.py`'s orchestration: these functions have exactly one
job each (map one domain list to one table) and no pipeline/business logic of their own.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from ..schemas import CreditMemo, FinancialRatios, LineItem, RatingFactorResult
from .models import CreditMemoRow, LineItemRow, RatingFactorRow, RatioRow


def persist_line_items(session: Session, version_id: str, line_items: list[LineItem]) -> None:
    for item in line_items:
        session.add(LineItemRow(
            assessment_version_id=version_id,
            label=item.label,
            standardised_label=item.standardised_label.value,
            value=item.value,
            currency=item.currency,
            unit=item.unit,
            period=item.period,
            period_type=item.period_type.value,
            period_end_date=item.period_end_date,
            period_length_months=item.period_length_months,
            statement_type=item.statement_type.value,
            page=item.page,
            source_snippet=item.source_snippet,
        ))


def persist_ratios(session: Session, version_id: str, ratios_by_period: list[FinancialRatios]) -> None:
    for period_ratios in ratios_by_period:
        for name, ratio in period_ratios.ratios.items():
            session.add(RatioRow(
                assessment_version_id=version_id,
                period=period_ratios.period,
                ratio_name=name,
                value=ratio.value,
                category=ratio.category,
                inputs_used=ratio.inputs_used,
                missing_inputs=ratio.missing_inputs,
                pages=ratio.pages,
            ))


def persist_rating_factors(session: Session, version_id: str, factors: list[RatingFactorResult]) -> None:
    for factor in factors:
        session.add(RatingFactorRow(
            assessment_version_id=version_id,
            factor_type=factor.factor.value,
            present=factor.present,
            severity=factor.severity,
            weight=factor.weight,
            contribution=factor.contribution,
            evidence=factor.evidence,
            pages=factor.pages,
        ))


def persist_memo(session: Session, version_id: str, memo: CreditMemo) -> None:
    session.add(CreditMemoRow(
        assessment_version_id=version_id,
        executive_summary=memo.executive_summary,
        financial_analysis=memo.financial_analysis,
        key_strengths=memo.key_strengths,
        key_risks=memo.key_risks,
        mitigants=memo.mitigants,
        recommended_covenants=memo.recommended_covenants,
        monitoring_triggers=memo.monitoring_triggers,
    ))
