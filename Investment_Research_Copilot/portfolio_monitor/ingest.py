"""
Structured-input ingestion — the primary "new kind of input" for this agent.

A portfolio is loaded from structured files, not parsed from a filing:
  * holdings      — CSV or JSON of positions (symbol, quantity, asset_class, ...)
  * transactions  — CSV or JSON of the cash-flow ledger (date, type, symbol, amount, ...)

Both loaders are tolerant of column-name variants (e.g. 'ticker'/'symbol',
'qty'/'quantity'/'shares') and of either a CSV header layout or a JSON list of objects.
Nothing here calls an LLM — ingestion is fully deterministic. (The optional PDF
brokerage-statement path lives in `statement_extract.py` and produces the same `Holding`s.)
"""
from __future__ import annotations

import csv
import io
import json
from typing import Dict, List, Optional

from .logconf import get_logger
from .schemas import (
    AssetClass,
    CashFlow,
    CashFlowType,
    Holding,
    PortfolioInput,
    Region,
)

_log = get_logger()


# --------------------------------------------------------------------------- #
# Column aliases (lowercased, stripped) -> canonical field
# --------------------------------------------------------------------------- #
_HOLDING_ALIASES: Dict[str, str] = {
    "symbol": "symbol", "ticker": "symbol", "security": "symbol", "instrument": "symbol",
    "name": "name", "description": "name", "security_name": "name",
    "asset_class": "asset_class", "assetclass": "asset_class", "class": "asset_class",
    "sector": "sector", "gics_sector": "sector", "industry": "sector",
    "region": "region", "geography": "region", "country": "region",
    "currency": "currency", "ccy": "currency",
    "quantity": "quantity", "qty": "quantity", "shares": "quantity", "units": "quantity",
    "cost_basis": "cost_basis", "cost": "cost_basis", "book_value": "cost_basis",
    "price": "price", "last_price": "price", "market_price": "price",
    "market_value": "market_value", "value": "market_value", "mv": "market_value",
}

_CASHFLOW_ALIASES: Dict[str, str] = {
    "date": "date", "trade_date": "date", "value_date": "date", "posted": "date",
    "type": "type", "transaction_type": "type", "activity": "type", "kind": "type",
    "symbol": "symbol", "ticker": "symbol", "security": "symbol",
    "amount": "amount", "value": "amount", "cash": "amount", "net_amount": "amount",
    "currency": "currency", "ccy": "currency",
    "note": "note", "description": "note", "memo": "note",
}

# Free-text asset-class / region / cash-flow-type normalisation.
_ASSET_CLASS_MAP = {
    "equity": AssetClass.EQUITY, "equities": AssetClass.EQUITY, "stock": AssetClass.EQUITY,
    "stocks": AssetClass.EQUITY, "shares": AssetClass.EQUITY,
    "fixed_income": AssetClass.FIXED_INCOME, "fixed income": AssetClass.FIXED_INCOME,
    "bond": AssetClass.FIXED_INCOME, "bonds": AssetClass.FIXED_INCOME, "fi": AssetClass.FIXED_INCOME,
    "cash": AssetClass.CASH, "money_market": AssetClass.CASH, "mmf": AssetClass.CASH,
    "alternatives": AssetClass.ALTERNATIVES, "alt": AssetClass.ALTERNATIVES,
    "commodity": AssetClass.COMMODITY, "commodities": AssetClass.COMMODITY, "gold": AssetClass.COMMODITY,
    "real_estate": AssetClass.REAL_ESTATE, "reit": AssetClass.REAL_ESTATE, "property": AssetClass.REAL_ESTATE,
    "multi_asset": AssetClass.MULTI_ASSET, "balanced": AssetClass.MULTI_ASSET,
}
_REGION_MAP = {
    "us": Region.US, "usa": Region.US, "united states": Region.US, "north america": Region.US,
    "developed_ex_us": Region.DEVELOPED_EX_US, "developed": Region.DEVELOPED_EX_US,
    "eafe": Region.DEVELOPED_EX_US, "europe": Region.DEVELOPED_EX_US, "japan": Region.DEVELOPED_EX_US,
    "emerging_markets": Region.EMERGING_MARKETS, "em": Region.EMERGING_MARKETS,
    "emerging": Region.EMERGING_MARKETS, "asia": Region.EMERGING_MARKETS,
    "global": Region.GLOBAL, "world": Region.GLOBAL, "international": Region.GLOBAL,
}
_CASHFLOW_TYPE_MAP = {
    "dividend": CashFlowType.DIVIDEND, "div": CashFlowType.DIVIDEND, "dividends": CashFlowType.DIVIDEND,
    "coupon": CashFlowType.COUPON, "interest": CashFlowType.INTEREST,
    "contribution": CashFlowType.CONTRIBUTION, "deposit": CashFlowType.CONTRIBUTION,
    "withdrawal": CashFlowType.WITHDRAWAL, "redemption": CashFlowType.WITHDRAWAL,
    "fee": CashFlowType.FEE, "fees": CashFlowType.FEE, "management_fee": CashFlowType.FEE,
    "tax": CashFlowType.TAX, "withholding": CashFlowType.TAX,
    "buy": CashFlowType.TRADE_BUY, "purchase": CashFlowType.TRADE_BUY,
    "sell": CashFlowType.TRADE_SELL, "sale": CashFlowType.TRADE_SELL,
}


def _num(value) -> Optional[float]:
    """Parse a number tolerating commas, parens (negatives), %, and currency symbols."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().replace(",", "").replace("%", "").replace("$", "").replace("£", "")
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if s in ("", "-", "—", "n/a", "na", "N/A", "None"):
        return None
    try:
        v = float(s)
        return -v if neg else v
    except ValueError:
        return None


def _rows_from_path(path: str) -> List[Dict]:
    """Read a CSV or JSON file into a list of dict rows."""
    if path.lower().endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):                    # allow {"holdings": [...]} wrappers
            for key in ("holdings", "positions", "transactions", "cash_flows", "rows", "data"):
                if isinstance(data.get(key), list):
                    return data[key]
            return [data]
        return data if isinstance(data, list) else []
    # CSV
    with open(path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _canon(row: Dict, aliases: Dict[str, str]) -> Dict:
    """Lowercase/normalise keys and map them onto canonical field names."""
    out: Dict = {}
    for raw_key, val in row.items():
        if raw_key is None:
            continue
        key = str(raw_key).strip().lower().replace(" ", "_")
        canon = aliases.get(key)
        if canon and (canon not in out or out[canon] in (None, "")):
            out[canon] = val
    return out


def _holding_from_row(row: Dict) -> Optional[Holding]:
    r = _canon(row, _HOLDING_ALIASES)
    symbol = (str(r.get("symbol") or "")).strip().upper()
    if not symbol:
        return None
    ac_raw = str(r.get("asset_class") or "").strip().lower()
    region_raw = str(r.get("region") or "").strip().lower()
    return Holding(
        symbol=symbol,
        name=(str(r["name"]).strip() if r.get("name") else None),
        asset_class=_ASSET_CLASS_MAP.get(ac_raw, AssetClass.OTHER),
        sector=(str(r["sector"]).strip() if r.get("sector") else None),
        region=_REGION_MAP.get(region_raw, Region.OTHER),
        currency=(str(r.get("currency") or "USD").strip().upper() or "USD"),
        quantity=_num(r.get("quantity")) or 0.0,
        cost_basis=_num(r.get("cost_basis")),
        price=_num(r.get("price")),
        market_value=_num(r.get("market_value")),
        price_source="input" if _num(r.get("price")) is not None else "marketdata",
    )


def _cashflow_from_row(row: Dict) -> Optional[CashFlow]:
    r = _canon(row, _CASHFLOW_ALIASES)
    amount = _num(r.get("amount"))
    type_raw = str(r.get("type") or "").strip().lower().replace(" ", "_")
    cf_type = _CASHFLOW_TYPE_MAP.get(type_raw, CashFlowType.OTHER)
    if amount is None and cf_type == CashFlowType.OTHER and not r.get("symbol"):
        return None
    return CashFlow(
        date=(str(r["date"]).strip() if r.get("date") else None),
        type=cf_type,
        symbol=(str(r["symbol"]).strip().upper() if r.get("symbol") else None),
        amount=amount or 0.0,
        currency=(str(r.get("currency") or "USD").strip().upper() or "USD"),
        note=(str(r["note"]).strip() if r.get("note") else None),
    )


def load_holdings(path: str) -> List[Holding]:
    """Load holdings from a CSV or JSON file."""
    rows = _rows_from_path(path)
    out = [h for h in (_holding_from_row(r) for r in rows) if h is not None]
    _log.info("ingest: loaded %d holding(s) from %s", len(out), path)
    return out


def load_cash_flows(path: str) -> List[CashFlow]:
    """Load the cash-flow / transactions ledger from a CSV or JSON file."""
    rows = _rows_from_path(path)
    out = [c for c in (_cashflow_from_row(r) for r in rows) if c is not None]
    _log.info("ingest: loaded %d cash-flow record(s) from %s", len(out), path)
    return out


def load_holdings_from_text(text: str, fmt: str = "csv") -> List[Holding]:
    """Parse holdings from an in-memory CSV/JSON string (used by the Streamlit upload)."""
    if fmt == "json":
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("holdings", [])
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    return [h for h in (_holding_from_row(r) for r in rows) if h is not None]


def load_cash_flows_from_text(text: str, fmt: str = "csv") -> List[CashFlow]:
    if fmt == "json":
        data = json.loads(text)
        rows = data if isinstance(data, list) else data.get("transactions", [])
    else:
        rows = list(csv.DictReader(io.StringIO(text)))
    return [c for c in (_cashflow_from_row(r) for r in rows) if c is not None]


def build_portfolio(
    holdings: List[Holding],
    cash_flows: Optional[List[CashFlow]] = None,
    name: str = "Portfolio",
    base_currency: str = "USD",
    mandate: str = "balanced",
    as_of: Optional[str] = None,
) -> PortfolioInput:
    return PortfolioInput(
        name=name, base_currency=base_currency, mandate=mandate, as_of=as_of,
        holdings=holdings, cash_flows=cash_flows or [],
    )
