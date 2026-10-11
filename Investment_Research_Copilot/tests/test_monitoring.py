"""Tests for holdings ingest and allocation computation."""

from __future__ import annotations

from portfolio_monitor.allocation import compute_allocation
from portfolio_monitor.ingest import load_holdings_from_text
from portfolio_monitor.schemas import Holding

_TWO_HOLDINGS_CSV = """symbol,quantity,price,asset_class
AAPL,10,200,equity
GOOG,5,100,equity
"""


def test_load_holdings_csv_parses_rows():
    holdings = load_holdings_from_text(_TWO_HOLDINGS_CSV)
    assert [holding.symbol for holding in holdings] == ["AAPL", "GOOG"]
    assert holdings[0].quantity == 10.0
    assert holdings[0].price == 200.0


def test_load_holdings_csv_skips_rows_without_symbol():
    holdings = load_holdings_from_text("symbol,quantity,price\n,10,200\nMSFT,2,50\n")
    assert [holding.symbol for holding in holdings] == ["MSFT"]


def test_load_holdings_json_parses_list():
    holdings = load_holdings_from_text(
        '[{"symbol": "aapl", "quantity": 3, "price": 10}]', fmt="json"
    )
    assert len(holdings) == 1
    assert holdings[0].symbol == "AAPL"


def test_allocation_weights_sum_to_one():
    holdings = [
        Holding(symbol="AAPL", quantity=10, price=200.0, market_value=2000.0),
        Holding(symbol="GOOG", quantity=5, price=100.0, market_value=500.0),
    ]
    allocation = compute_allocation(holdings)
    assert allocation.total_market_value == 2500.0
    assert abs(sum(bucket.weight for bucket in allocation.by_position) - 1.0) < 1e-6
    assert allocation.priced_coverage == 1.0
    assert allocation.unpriced_symbols == []


def test_allocation_flags_unpriced_holdings():
    holdings = [
        Holding(symbol="AAPL", quantity=10, price=200.0, market_value=2000.0),
        Holding(symbol="UNKNOWN", quantity=1),
    ]
    allocation = compute_allocation(holdings)
    assert allocation.unpriced_symbols == ["UNKNOWN"]
    assert [bucket.name for bucket in allocation.by_position] == ["AAPL"]
