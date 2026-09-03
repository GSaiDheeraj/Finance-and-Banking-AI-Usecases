"""Persist monitoring results to normalized Postgres tables."""
from __future__ import annotations

import hashlib
from pathlib import Path

from sqlalchemy.orm import Session

from ..schemas import MonitoringResult
from .models import AlertRow, BriefingRow, HoldingRow, PortfolioDocument, PortfolioRun, RebalancingTradeRow


def persist_documents(session: Session, run_id: str, paths: list[str], doc_type: str = "holdings") -> None:
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
    for h in result.portfolio.holdings:
        session.add(
            HoldingRow(
                portfolio_run_id=run.id,
                symbol=h.symbol,
                name=h.name,
                asset_class=h.asset_class.value if hasattr(h.asset_class, "value") else str(h.asset_class),
                sector=h.sector,
                region=h.region.value if hasattr(h.region, "value") else str(h.region) if h.region else None,
                currency=h.currency,
                quantity=h.quantity,
                market_value=h.market_value,
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
    run.completed_at = run.completed_at or __import__("datetime").datetime.now(__import__("datetime").timezone.utc)
