"""Persist monitoring results to normalized Postgres tables."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from ..schemas import MonitoringResult
from .models import (
    AlertRow,
    BriefingRow,
    HoldingRow,
    PortfolioDocument,
    PortfolioRun,
    RebalancingTradeRow,
)


def persist_documents(
    session: Session, run_id: str, paths: list[str], doc_type: str = "holdings"
) -> None:
    for path in paths:
        p = Path(path)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()
        session.add(
            PortfolioDocument(
                portfolio_run_id=run_id,
                filename=p.name,
                storage_uri=str(p.resolve()),
                sha256_hash=digest,
                doc_type=doc_type,
            )
        )


def persist_monitoring_result(
    session: Session,
    run: PortfolioRun,
    result: MonitoringResult,
    reasoning: list[str],
    metrics: dict,
) -> None:
    for holding in result.portfolio.holdings:
        session.add(
            HoldingRow(
                portfolio_run_id=run.id,
                symbol=holding.symbol,
                name=holding.name,
                asset_class=_enum_value(holding.asset_class),
                sector=holding.sector,
                region=_optional_enum_value(holding.region),
                currency=holding.currency,
                quantity=holding.quantity,
                market_value=holding.market_value,
            )
        )

    for alert in result.alerts:
        session.add(
            AlertRow(
                portfolio_run_id=run.id,
                alert_type=alert.type.value,
                severity=alert.severity.value,
                title=alert.title,
                message=alert.detail,
                recommended_action=alert.recommended_action,
            )
        )

    for trade in result.rebalance.trades:
        session.add(
            RebalancingTradeRow(
                portfolio_run_id=run.id,
                bucket=trade.bucket,
                action=trade.action,
                trade_value=trade.trade_value,
                rationale=trade.rationale,
            )
        )

    briefing = result.briefing_llm or result.briefing_deterministic
    if briefing:
        session.add(
            BriefingRow(
                portfolio_run_id=run.id,
                method=briefing.method,
                executive_summary=briefing.executive_summary,
                key_findings=briefing.recommended_actions + briefing.watch_items,
            )
        )

    run.health_score = result.score.normalized_score
    run.health_band = result.score.health_band.value
    run.latency_ms = metrics.get("latency_ms")
    run.llm_call_count = metrics.get("llm_calls")
    run.raw_result = result.model_dump(mode="json")
    run.reasoning = reasoning
    run.status = "awaiting_review"
    run.completed_at = run.completed_at or datetime.now(timezone.utc)


def _enum_value(value) -> str:
    """String value of a str-enum (or str fallback for plain strings)."""
    return value.value if hasattr(value, "value") else str(value)


def _optional_enum_value(value) -> str | None:
    """String value of an optional str-enum, preserving None."""
    if value is None:
        return None
    return _enum_value(value)
