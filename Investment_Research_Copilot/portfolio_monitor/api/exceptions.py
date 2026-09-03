"""API-specific exceptions."""
from __future__ import annotations


class PortfolioNotFoundError(Exception):
    def __init__(self, portfolio_id: str):
        self.portfolio_id = portfolio_id
        super().__init__(f"Portfolio run {portfolio_id} not found")


class PortfolioNotReadyError(Exception):
    def __init__(self, portfolio_id: str, status: str):
        self.portfolio_id = portfolio_id
        self.status = status
        super().__init__(f"Portfolio run {portfolio_id} is not ready (status={status})")
