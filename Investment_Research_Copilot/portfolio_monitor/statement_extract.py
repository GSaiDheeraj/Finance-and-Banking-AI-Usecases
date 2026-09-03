"""
OPTIONAL grounded ingest path: a PDF brokerage / custodian statement -> Holdings.

The primary input is structured CSV/JSON (see `ingest.py`). This module is the *fallback*
ingest: when the user only has a PDF statement, the LLM does language work only — it reads the
holdings table from the statement text and transcribes each position into the SAME `Holding`
schema, which then flows through the identical deterministic engine. Numbers are transcribed
exactly as printed (no arithmetic, no inference) so the deterministic layer stays the single
source of truth.

Text PDFs only (no OCR). Degrades clearly: if PyMuPDF or the LLM is unavailable, it raises a
helpful error rather than guessing.
"""
from __future__ import annotations

import json
from typing import List

from .config import get_llm, llm_available
from .ingest import _ASSET_CLASS_MAP, _REGION_MAP, _num
from .logconf import get_logger
from .schemas import AssetClass, Holding, Region

_log = get_logger()


def _read_pdf_text(pdf_path: str, max_pages: int = 12) -> str:
    try:
        import fitz  # PyMuPDF
    except Exception as e:                          # pragma: no cover
        raise RuntimeError("PyMuPDF (fitz) not installed — cannot read PDF statements. "
                           "pip install PyMuPDF, or use a CSV/JSON holdings file.") from e
    doc = fitz.open(pdf_path)
    blocks: List[str] = []
    for i, page in enumerate(doc):
        if i >= max_pages:
            break
        text = page.get_text("text")
        if text and text.strip():
            blocks.append(f"--- PAGE {i + 1} ---\n{text}")
    doc.close()
    if not blocks:
        raise ValueError("No extractable text in the PDF (scanned/image-only statements are "
                         "out of scope — OCR not supported).")
    return "\n\n".join(blocks)


def _strip_json(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = "\n".join(t.split("\n")[1:])
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


def extract_holdings_from_statement(pdf_path: str) -> List[Holding]:
    """Grounded extraction of holdings from a text PDF brokerage statement."""
    if not llm_available():
        raise RuntimeError("LLM not configured (LLM_ENDPOINT / LLM_API_KEY) — the PDF ingest "
                           "path needs the LLM. Use a CSV/JSON holdings file instead.")
    text = _read_pdf_text(pdf_path)
    from langchain_core.messages import HumanMessage, SystemMessage

    labels = [a.value for a in AssetClass]
    regions = [r.value for r in Region]
    system = SystemMessage(content=(
        "You read a brokerage / custodian account statement and transcribe the HOLDINGS table. "
        "From the provided page text ONLY, emit one record per position. Transcribe `quantity` "
        "and `price` and `market_value` EXACTLY as printed — do NOT compute, sum, infer or "
        "rescale them. Map each holding's asset class to one of " + str(labels) + " and region "
        "to one of " + str(regions) + " (best effort; use 'other' if unclear). Output ONLY a "
        "JSON array:\n"
        '[{"symbol": str, "name": str|null, "asset_class": str, "sector": str|null, '
        '"region": str, "currency": str|null, "quantity": number|null, "price": number|null, '
        '"market_value": number|null, "cost_basis": number|null}]'
    ))
    user = HumanMessage(content=f"Statement text:\n{text}\n\nReturn the holdings JSON array.")

    try:
        data = json.loads(_strip_json(get_llm().invoke([system, user]).content))
    except Exception as e:
        _log.warning("statement_extract: parse failed: %s", e)
        return []
    if not isinstance(data, list):
        return []

    out: List[Holding] = []
    for row in data:
        if not isinstance(row, dict) or not row.get("symbol"):
            continue
        ac = str(row.get("asset_class") or "").strip().lower()
        rg = str(row.get("region") or "").strip().lower()
        price = _num(row.get("price"))
        out.append(Holding(
            symbol=str(row["symbol"]).strip().upper(),
            name=(str(row["name"]).strip() if row.get("name") else None),
            asset_class=_ASSET_CLASS_MAP.get(ac, _enum_ac(ac)),
            sector=(str(row["sector"]).strip() if row.get("sector") else None),
            region=_REGION_MAP.get(rg, _enum_region(rg)),
            currency=(str(row.get("currency") or "USD").strip().upper() or "USD"),
            quantity=_num(row.get("quantity")) or 0.0,
            price=price,
            market_value=_num(row.get("market_value")),
            cost_basis=_num(row.get("cost_basis")),
            price_source="input" if price is not None else "marketdata",
        ))
    _log.info("statement_extract: extracted %d holding(s) from %s", len(out), pdf_path)
    return out


def _enum_ac(value: str) -> AssetClass:
    try:
        return AssetClass(value)
    except (ValueError, TypeError):
        return AssetClass.OTHER


def _enum_region(value: str) -> Region:
    try:
        return Region(value)
    except (ValueError, TypeError):
        return Region.OTHER
