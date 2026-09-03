"""
Grounded structured extraction.

Line items come from `fundamentals_pipeline` — a table-detection + multi-stage
classification pipeline (ported from FinDoc Pypi's `fundamentals_module`; see
the plan for the port's scope). This module flattens the pipeline's cell-based
output (one item, many periods) into one `schemas.LineItem` row per cell/period.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage

from .config import get_config, get_llm
from .fundamentals_pipeline.graph import GRAPH
from .fundamentals_pipeline.models import LineItem as PipelineLineItem
from .fundamentals_pipeline.state import PipelineState
from .input_pipeline.processor import InputProcessor
from .pdf_index import PageIndex
from .schemas import LineItem, StatementType

_PERIOD_TYPE_MONTHS = {"3M": 3, "6M": 6, "9M": 9, "12M": 12}

# One InputProcessor per process: its OCR model (when actually needed) is
# loaded lazily on first use and reused across every document after that.
_input_processor: Optional[InputProcessor] = None


def _get_input_processor() -> InputProcessor:
    global _input_processor
    if _input_processor is None:
        _input_processor = InputProcessor()
    return _input_processor


def _strip_json(text: str) -> str:
    """Remove ```json fences if the model wrapped its output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines)
    return text.strip()


def _safe_json(text: str) -> Any:
    try:
        return json.loads(_strip_json(text))
    except Exception:
        return None


def _evidence_from_pages(index: PageIndex, query: str, k: int) -> str:
    hits = index.search(query, k=k)
    blocks = [f"--- PAGE {p.page_number} ---\n{p.text}" for p in hits]
    return "\n\n".join(blocks)


def _flatten_pipeline_items(aggregated: Dict[str, List[PipelineLineItem]]) -> List[LineItem]:
    """Flatten the pipeline's cell-based line items into one flat schemas.LineItem
    row per cell (one row per period/axis point)."""
    items: List[LineItem] = []
    for statement_value, pipeline_items in aggregated.items():
        try:
            statement = StatementType(statement_value)
        except ValueError:
            statement = StatementType.OTHER
        for pipeline_item in pipeline_items:
            for cell in pipeline_item.values:
                period_end = cell.coordinates.get("period_end")
                try:
                    period_end_date = date.fromisoformat(period_end) if period_end else None
                except ValueError:
                    period_end_date = None
                page_str = cell.coordinates.get("page")
                items.append(LineItem(
                    statement=statement,
                    label=pipeline_item.label,
                    section_path=pipeline_item.section_path,
                    value=cell.value,
                    unit=None,
                    currency=cell.currency,
                    period=period_end,
                    period_end_date=period_end_date,
                    period_length_months=_PERIOD_TYPE_MONTHS.get(cell.coordinates.get("period_type")),
                    scale=cell.scale,
                    consolidated=cell.coordinates.get("consolidation") == "consolidated",
                    page=int(page_str) if page_str and page_str.isdigit() else None,
                    source_snippet=cell.coordinates.get("source_table"),
                ))
    return items


def extract_document_content(pdf_path: str) -> Tuple[List[LineItem], int, List[dict]]:
    """Convert `pdf_path` to Markdown-with-HTML-tables (via `input_pipeline`),
    run the table-based fundamentals pipeline on it, flatten its output into our
    flat LineItem rows, and report the LLM call count (one call per surviving
    candidate at each of the 4 LLM-backed stages) plus the pipeline's own
    per-stage diagnostics (kept/dropped/error counts)."""
    config = get_config()
    source = Path(pdf_path)
    extraction_result = _get_input_processor().process(source, source.parent)
    if extraction_result.markdown_path is None:
        return [], 0, [{
            "node": "input_pipeline",
            "error": f"no markdown produced (document_class={extraction_result.document_class})",
        }]

    initial_state: PipelineState = {
        "md_path": extraction_result.markdown_path,
        "small_model": config.small_llm_model_name,
        "large_model": config.large_llm_model_name,
        "raw_tables": [],
        "relevant_tables": [],
        "classified_tables": [],
        "consolidation_filtered": [],
        "extracted_per_table": [],
        "aggregated": {},
        "diagnostics": [],
    }
    result = GRAPH.invoke(initial_state)
    llm_calls = (
        len(result["raw_tables"]) + len(result["relevant_tables"])
        + len(result["classified_tables"]) + len(result["consolidation_filtered"])
    )
    return _flatten_pipeline_items(result["aggregated"]), llm_calls, result["diagnostics"]


def detect_issuer_metadata(index: PageIndex) -> Dict[str, Optional[str]]:
    """Best-effort issuer / period / currency detection from the cover & statements."""
    evidence = _evidence_from_pages(
        index, "company name annual report financial year reporting currency", k=3
    )
    system = SystemMessage(content=(
        "Identify the reporting entity metadata from the text. "
        "Output ONLY a JSON object: "
        '{"issuer": str|null, "period": str|null, "currency": str|null}'
    ))
    raw = get_llm().invoke([system, HumanMessage(content=evidence)]).content
    data = _safe_json(raw)
    if not isinstance(data, dict):
        return {"issuer": None, "period": None, "currency": None}
    return {
        "issuer": data.get("issuer"),
        "period": data.get("period"),
        "currency": data.get("currency"),
    }
