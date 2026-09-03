"""LangGraph node functions for the table-based extraction pipeline.

Ported from FinDoc Pypi's `fundamentals_module/nodes.py`. Each node reads the
fields of `PipelineState` populated by upstream nodes, does its own narrow job,
appends a diagnostic entry, and returns a partial dict LangGraph merges back into
the state. `classify_consolidation` (the real classifier) is used, not the
reference's `bypass_consolidation` test stub.
"""
from __future__ import annotations

from typing import Any, Dict, List

from bs4 import BeautifulSoup
from langchain_core.messages import HumanMessage, SystemMessage

from .models import (
    ConsolidationMeta,
    ExtractedLineItems,
    LineItem,
    RelevanceVerdict,
    StatementClassification,
)
from .prompts import (
    CONSOLIDATION_PROMPT,
    LINE_ITEM_EXTRACTION_PROMPT,
    RELEVANCE_PROMPT,
    STATEMENT_CLASSIFIER_PROMPT,
)
from .state import ClassifiedTable, ConsolidationFiltered, ExtractedTable, PipelineState, RelevantTable
from .structured_llm import StructuredExtractor
from .table_finder import StatementCandidate, find_tables

_EXTRACTION_MAX_TOKENS = 8192


def _diag(state: PipelineState, **entry: Any) -> List[dict]:
    """Return the diagnostics list with `entry` appended."""
    return state["diagnostics"] + [entry]


def _candidate_summary(candidate: StatementCandidate) -> str:
    """One-paragraph summary used by the classifier nodes."""
    return (
        f"Nearest H1: {candidate.nearest_h1 or '(none)'}\n"
        f"Nearest H2: {candidate.nearest_h2 or '(none)'}\n"
        f"Caption: {candidate.caption or '(none)'}\n"
        f"Outer scale annotation: {candidate.outer_annotation or '(none)'}\n"
        f"Page: {candidate.page_number if candidate.page_number is not None else '(unknown)'}\n"
    )


def _column_headers(candidate: StatementCandidate) -> List[str]:
    """Flatten the candidate's first table into a list of header texts.

    For multi-row <thead>, picks the row with the most non-empty cells rather
    than always using the last row — some balance sheets place period dates in
    the first header row and delta sub-labels (Amount, %) in the last row.
    """
    if not candidate.tables:
        return []
    soup = BeautifulSoup(candidate.tables[0].html, "html.parser")
    thead = soup.find("thead")
    headers: List[str] = []
    if thead is not None:
        rows = thead.find_all("tr")
        if rows:
            best_row = max(
                rows, key=lambda tr: sum(1 for c in tr.find_all(["th", "td"]) if c.get_text(strip=True))
            )
            for cell in best_row.find_all(["th", "td"]):
                headers.append(cell.get_text(strip=True))
    if not headers:
        first_tr = soup.find("tr")
        if first_tr is not None:
            for cell in first_tr.find_all(["th", "td"]):
                headers.append(cell.get_text(strip=True))
    return headers


# --------------------------------------------------------------------------- #
# Node 0 — find tables (pure Python, no LLM call)
# --------------------------------------------------------------------------- #
def find_tables_node(state: PipelineState) -> Dict[str, Any]:
    """Discover tables in the markdown — each table is its own candidate,
    matching the reference's own wired node (not the grouped alternative)."""
    md_text = state["md_path"].read_text(encoding="utf-8")
    blocks = find_tables(md_text)
    tables = [
        StatementCandidate(
            tables=[block], nearest_h1=block.nearest_h1, nearest_h2=block.nearest_h2,
            caption=block.caption, outer_annotation=block.outer_annotation,
            page_number=block.page_number,
        )
        for block in blocks
    ]
    return {
        "raw_tables": tables,
        "diagnostics": _diag(state, node="find_tables", tables_detected=len(tables)),
    }


# --------------------------------------------------------------------------- #
# Node 1 — relevance filter (small model)
# --------------------------------------------------------------------------- #
def filter_relevant(state: PipelineState) -> Dict[str, Any]:
    """Drop candidates that don't contribute to fundamentals analysis."""
    extractor = StructuredExtractor(RelevanceVerdict, model=state["small_model"])

    relevant: List[RelevantTable] = []
    dropped: List[dict] = []
    for index, candidate in enumerate(state["raw_tables"]):
        user = f"{_candidate_summary(candidate)}\nTable HTML:\n{candidate.html}"
        try:
            verdict = extractor.extract(
                [SystemMessage(content=RELEVANCE_PROMPT), HumanMessage(content=user)]
            )
        except Exception as exc:
            dropped.append({"index": index, "page": candidate.page_number, "error": repr(exc)})
            continue

        if verdict.relevant:
            relevant.append({"candidate": candidate, "verdict": verdict})
        else:
            dropped.append({"index": index, "page": candidate.page_number, "reason": verdict.reason})

    return {
        "relevant_tables": relevant,
        "diagnostics": _diag(
            state, node="filter_relevant", kept=len(relevant), dropped=len(dropped),
            dropped_detail=dropped[:10],
        ),
    }


# --------------------------------------------------------------------------- #
# Node 2 — statement classifier (small model)
# --------------------------------------------------------------------------- #
def classify_statement(state: PipelineState) -> Dict[str, Any]:
    """Tag each relevant candidate as balance_sheet/income_statement/cash_flow/
    changes_in_equity/other. Every bucket, including "other" (KPIs, ratios,
    operational metrics), flows through to extraction — see prompts.py's
    STATEMENT_CLASSIFIER_PROMPT for what belongs in "other"."""
    extractor = StructuredExtractor(StatementClassification, model=state["small_model"])

    classified: List[ClassifiedTable] = []
    counts: Dict[str, int] = {}
    errors: List[dict] = []
    for index, entry in enumerate(state["relevant_tables"]):
        candidate = entry["candidate"]
        user = f"{_candidate_summary(candidate)}\nTable HTML:\n{candidate.html}"
        try:
            classification = extractor.extract(
                [SystemMessage(content=STATEMENT_CLASSIFIER_PROMPT), HumanMessage(content=user)]
            )
        except Exception as exc:
            errors.append({"index": index, "page": candidate.page_number, "error": repr(exc)})
            continue

        classified.append({"candidate": candidate, "classification": classification})
        counts[classification.statement_type] = counts.get(classification.statement_type, 0) + 1

    return {
        "classified_tables": classified,
        "diagnostics": _diag(
            state, node="classify_statement", counts=counts, errors=len(errors),
            error_detail=errors[:10],
        ),
    }


# --------------------------------------------------------------------------- #
# Node 3 — consolidation classifier (small model)
# --------------------------------------------------------------------------- #
def classify_consolidation(state: PipelineState) -> Dict[str, Any]:
    """Decide which columns to keep and whether the table is a segment."""
    extractor = StructuredExtractor(ConsolidationMeta, model=state["small_model"])

    filtered: List[ConsolidationFiltered] = []
    skipped: List[dict] = []
    for entry in state["classified_tables"]:
        candidate = entry["candidate"]
        classification = entry["classification"]
        numbered = "\n".join(
            f"  [{i}] {h or '(empty)'}" for i, h in enumerate(_column_headers(candidate))
        )
        user = (
            f"{_candidate_summary(candidate)}\n"
            f"Statement type: {classification.statement_type}\n"
            f"is_breakdown: {classification.is_breakdown}\n\n"
            f"Column headers (0-based):\n{numbered}"
        )
        try:
            meta = extractor.extract(
                [SystemMessage(content=CONSOLIDATION_PROMPT), HumanMessage(content=user)]
            )
        except Exception:
            continue

        if meta.skip and meta.segment_id is None:
            skipped.append({"page": candidate.page_number, "reason": meta.reason or "no consolidated columns"})
            continue

        filtered.append({
            "candidate": candidate,
            "statement_type": classification.statement_type,
            "is_breakdown": classification.is_breakdown,
            "breakdown_of": classification.breakdown_of,
            "meta": meta,
        })

    return {
        "consolidation_filtered": filtered,
        "diagnostics": _diag(
            state, node="classify_consolidation", kept=len(filtered), skipped=len(skipped),
            skipped_detail=skipped[:10],
        ),
    }


# --------------------------------------------------------------------------- #
# Node 4 — line-item extraction (large model)
# --------------------------------------------------------------------------- #
def extract_line_items(state: PipelineState) -> Dict[str, Any]:
    """Extract flat consolidated-only line items from each surviving table."""
    extractor = StructuredExtractor(
        ExtractedLineItems, model=state["large_model"], max_tokens=_EXTRACTION_MAX_TOKENS,
    )

    extracted: List[ExtractedTable] = []
    errors: List[dict] = []
    for entry in state["consolidation_filtered"]:
        candidate = entry["candidate"]
        meta = entry["meta"]
        kept_columns = meta.consolidated_columns if meta.consolidated_columns else None

        user_parts = [
            _candidate_summary(candidate),
            f"Statement type: {entry['statement_type']}",
            f"Segment id (apply to coordinates if set): {meta.segment_id or '(none)'}",
            f"Is detail breakdown: {entry['is_breakdown']}",
            f"Breakdown of (if applicable): {entry['breakdown_of'] or '(n/a)'}",
            f"Column indices to KEEP: {kept_columns if kept_columns is not None else 'all numeric columns'}",
            "",
            "Table HTML:",
            candidate.html,
        ]
        try:
            result = extractor.extract(
                [SystemMessage(content=LINE_ITEM_EXTRACTION_PROMPT), HumanMessage(content="\n".join(user_parts))]
            )
        except Exception as exc:
            errors.append({"page": candidate.page_number, "error": repr(exc)})
            continue

        extracted.append({
            "candidate": candidate,
            "statement_type": entry["statement_type"],
            "is_breakdown": entry["is_breakdown"],
            "breakdown_of": entry["breakdown_of"],
            "segment_id": meta.segment_id,
            "line_items": result.line_items,
        })

    return {
        "extracted_per_table": extracted,
        "diagnostics": _diag(
            state, node="extract_line_items", extracted=len(extracted), errors=len(errors),
            error_detail=errors[:10],
        ),
    }


# --------------------------------------------------------------------------- #
# Node 5 — aggregate (pure Python, no LLM call)
# --------------------------------------------------------------------------- #
def aggregate_all(state: PipelineState) -> Dict[str, Any]:
    """Collect every extracted line item, grouped by statement type.

    Each cell gets a `source_table` coordinate added so the same label
    extracted from two different tables (e.g. a 3-month and 6-month IS)
    remains distinguishable downstream.
    """
    by_type: Dict[str, List[LineItem]] = {}

    for table in state["extracted_per_table"]:
        page_number = table["candidate"].page_number
        bucket = by_type.setdefault(table["statement_type"], [])
        for item in table["line_items"]:
            tagged_values = []
            for cell in item.values:
                coords = dict(cell.coordinates)
                coords.setdefault("source_table", f"page {page_number}")
                coords.setdefault("page", str(page_number))
                tagged_values.append(cell.model_copy(update={"coordinates": coords}))
            bucket.append(item.model_copy(update={"values": tagged_values}))

    return {
        "aggregated": by_type,
        "diagnostics": _diag(state, node="aggregate_all", counts={k: len(v) for k, v in by_type.items()}),
    }
