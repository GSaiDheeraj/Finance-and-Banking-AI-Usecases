"""
Orchestration for SME-through-large-corporate credit risk assessment.

The submission pack is indexed once, then grounded extraction reads the company
metadata, every financial line item (multi-period), the notes and the facility request.
The deterministic engine computes ratios, benchmarks them against the sector, classifies
trends, scores the case and decides the lending path. The LLM is used only for language
tasks (extraction, credit memo, Q&A); every decision-bearing number is computed in
deterministic Python.

    submission PDFs
      -> doc_index (pages + embeddings)
      -> extract_company_metadata / line_items / notes / facility          (LLM, grounded)
      -> compute_ratios                                                     (deterministic)
      -> benchmark_ratios + analyse_trends                                  (deterministic)
    ticker (optional) — yfinance FALLBACK fills any line item / period the docs lack
      -> marketdata.fetch_fundamentals (free Yahoo Finance)                 (deterministic)
    company name (optional, alongside the docs)
      -> osint.collect (free DuckDuckGo web + news)                         (no LLM)
      -> osint.sentiment (grounded read: adverse findings + overall tone)   (LLM, grounded)
      -> score_case  (ratios + benchmark + trend + audit + notes + web)     (deterministic)
      -> synthesize_credit_memo + Q&A                                       (LLM, grounded)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from .benchmarks import analyse_trends, benchmark_ratios
from .config import EMBED_MODEL_NAME, LLM_MODEL_NAME, get_llm
from .db.models import AssessmentDocument, AssessmentVersion
from .db.page_index import PgPageIndex
from .db.persist import persist_line_items, persist_memo, persist_rating_factors, persist_ratios
from .db.session import get_session
from .doc_index import load_pack_as_pages
from .extraction import (
    extract_company_metadata,
    extract_facility_request,
    extract_line_items,
    extract_notes,
)
from .logconf import get_logger
from .marketdata import fetch_fundamentals, yfinance_available
from .narrative import synthesize_credit_memo
from .osint.sentiment import run_web_sentiment
from .ratios import UNIT_MULTIPLIERS, compute_ratios, group_by_period
from .schemas import (
    CompanyMetadata,
    CompanySeed,
    CreditAssessment,
    DataReconciliation,
    FacilityRequest,
    FinancialRatios,
    LineItem,
    StandardLabel,
    WebSentimentResult,
)
from .scoring import score_case
from .status_tracker import set_phase
from .tools import make_tools

_log = get_logger()
_MAX_PARALLEL_EXTRACTORS = 4


def _year_of(period: str) -> Optional[str]:
    """Extract a 4-digit year from a period label ('FY2023', '2023', 'Year ended 2023')."""
    import re
    m = re.search(r"(\d{4})", period or "")
    return m.group(1) if m else None


def _merge_yfinance_items(
    doc_items: List[LineItem], yf_items: List[LineItem]
) -> List[LineItem]:
    """Add yfinance line items only for (label, period) cells the documents don't cover.

    Documents are the audited primary source and always win; yfinance only fills empty
    cells, so a fully-documented pack is unchanged.

    Periods are aligned **by fiscal year**, not by exact label: yfinance labels periods
    'FY<year>', but a filing may say '2023', 'FY23' or 'Year ended 31 March 2023'. When a
    yfinance item's year matches a document period, the yfinance item is relabelled to the
    document's own period string so the two land in the SAME period bucket (and the
    document value wins) — rather than splitting one fiscal year across two periods.
    """
    # year -> the document's own label for that year (first seen wins)
    doc_period_by_year: Dict[str, str] = {}
    for li in doc_items:
        y = _year_of(li.period)
        if y and y not in doc_period_by_year:
            doc_period_by_year[y] = li.period

    present = {
        (li.standardised_label, li.period)
        for li in doc_items if li.value is not None
    }

    added: List[LineItem] = []
    for it in yf_items:
        if it.standardised_label == StandardLabel.OTHER:
            continue
        y = _year_of(it.period)
        canon = doc_period_by_year.get(y, it.period) if y else it.period
        if (it.standardised_label, canon) in present:
            continue                                  # document already covers this cell
        if canon != it.period:
            it = it.model_copy(update={"period": canon})
        added.append(it)
    return doc_items + added


# CompanyMetadata fields safe to backfill from yfinance when the filing didn't state them.
_META_BACKFILL_FIELDS = (
    "company_name", "industry", "sub_industry", "country",
    "employee_count", "reporting_currency",
)


def _merge_metadata(doc_meta: CompanyMetadata, yf_meta: CompanyMetadata) -> CompanyMetadata:
    """Fill only the metadata fields the documents left blank, from yfinance."""
    merged = doc_meta.model_copy()
    for field in _META_BACKFILL_FIELDS:
        if getattr(merged, field, None) in (None, "") and getattr(yf_meta, field, None) not in (None, ""):
            setattr(merged, field, getattr(yf_meta, field))
            _log.info("metadata: backfilled %s from yfinance -> %r", field, getattr(merged, field))
    return merged


def _norm_value(li: LineItem) -> Optional[float]:
    """Unit-normalise a line item's value to absolute units for comparison."""
    if li.value is None:
        return None
    return li.value * UNIT_MULTIPLIERS.get(li.unit, 1.0)


def _cross_check(doc_items: List[LineItem], yf_items: List[LineItem]) -> List[str]:
    """Compare document figures against yfinance for the same (label, year) and log mismatches.

    Tolerance is relative; set `YF_DISCREPANCY_TOL` (default 0.02 = 2%). Mismatches are
    logged as warnings — they often flag an extraction error, a restatement, or a units
    mix-up worth an analyst's eye. The document value is never changed.
    """
    try:
        tol = float(os.getenv("YF_DISCREPANCY_TOL", "0.02"))
    except (TypeError, ValueError):
        tol = 0.02

    doc_cells: Dict[tuple, tuple] = {}
    for li in doc_items:
        v = _norm_value(li)
        y = _year_of(li.period)
        if v is not None and y:
            doc_cells.setdefault((li.standardised_label, y), (v, li.period))

    out: List[str] = []
    for li in yf_items:
        if li.standardised_label == StandardLabel.OTHER or li.value is None:
            continue
        y = _year_of(li.period)
        key = (li.standardised_label, y)
        if not y or key not in doc_cells:
            continue
        d, dper = doc_cells[key]
        yv = li.value
        denom = max(abs(d), abs(yv), 1.0)
        diff = (yv - d) / denom
        if abs(diff) > tol:
            msg = (f"{li.standardised_label.value} [{dper}]: document={d:,.0f} vs "
                   f"yfinance={yv:,.0f} ({diff:+.1%})")
            out.append(msg)
            _log.warning("DISCREPANCY %s", msg)
        else:
            _log.debug("cross-check OK %s [%s]: doc=%.0f ~ yfinance=%.0f",
                       li.standardised_label.value, dper, d, yv)
    return out


def _ratio_gaps(
    line_items: List[LineItem],
    ratios_by_period: List[FinancialRatios],
    yf_items: List[LineItem],
) -> List[str]:
    """List ratios that could not be computed and the inputs they still lack.

    For each missing input it notes whether yfinance carried that label for the year —
    so a gap reads either 'not available anywhere' (a genuine data hole) or 'yfinance HAS
    it' (which would point at a label-mapping / period-alignment problem to investigate).
    """
    present_by_year: Dict[Optional[str], set] = {}
    for li in line_items:
        if li.value is not None:
            present_by_year.setdefault(_year_of(li.period), set()).add(li.standardised_label.value)
    yf_label_years = {
        (li.standardised_label.value, _year_of(li.period))
        for li in yf_items if li.value is not None
    }

    out: List[str] = []
    for fr in ratios_by_period:
        y = _year_of(fr.period)
        present = present_by_year.get(y, set())
        for name, rv in fr.ratios.items():
            if rv.value is not None:
                continue
            missing = [inp for inp in rv.inputs_used if inp not in present]
            if not missing:
                continue
            in_yf = [m for m in missing if (m, y) in yf_label_years]
            if in_yf:
                tail = f" (yfinance HAS {in_yf} — check label mapping / period alignment)"
            else:
                tail = " (not in documents or yfinance)"
            msg = f"{name} [{fr.period}]: missing {missing}{tail}"
            out.append(msg)
            _log.info("RATIO GAP %s", msg)
    return out


def _latest_revenue(line_items, periods) -> Optional[float]:
    """Unit-normalised revenue for the newest period, for the size/scale factor."""
    if not periods:
        return None
    grouped = group_by_period(line_items)
    latest = grouped.get(periods[-1], {})
    got = latest.get(StandardLabel.REVENUE)
    return got[0] if got else None


def _persist_documents(
    session: Session, version_id: str, pdf_paths: List[str]
) -> Dict[str, AssessmentDocument]:
    """One `AssessmentDocument` row per path, keyed by filename — `doc_index`'s own
    `doc_name` — so pages loaded afterward can be matched back to the document that
    produced them."""
    documents: Dict[str, AssessmentDocument] = {}
    for path in pdf_paths:
        filename = os.path.basename(path)
        with open(path, "rb") as f:
            content = f.read()
        document = AssessmentDocument(
            assessment_version_id=version_id,
            filename=filename,
            storage_uri=str(Path(path).resolve()),
            sha256_hash=hashlib.sha256(content).hexdigest(),
        )
        session.add(document)
        documents[filename] = document
    session.flush()
    return documents


def _run_extractor(version_id: str, extractor):
    """Run one grounded extractor in its own DB session.

    SQLAlchemy sessions aren't safe to share across threads, so the four extraction
    calls below — run concurrently since they're independent, I/O-bound reads of the
    *same already-ingested* page index — can't reuse the session that did the ingesting.
    Each opens a fresh session over the same, already-committed `page_chunks` rows
    instead; none of them writes anything, so there's no cross-session consistency
    concern beyond the pages already being visible (see the `session.commit()` right
    before this runs).
    """
    session = next(get_session())
    try:
        tools = make_tools(PgPageIndex(session, version_id))
        return extractor(tools)
    finally:
        session.close()


def assess_pack(
    session: Session,
    version: AssessmentVersion,
    pdf_paths: List[str],
    company_name: str = "",
    ticker: str = "",
    search_region: Optional[str] = None,
    enable_web_sentiment: bool = True,
) -> CreditAssessment:
    """Run the full credit assessment and return the result.

    Persists documents, pages, line items, ratios, rating factors and the memo onto
    `version` (already `session.add()`-ed by the caller) as it goes — this is the one
    function that turns a `CreditAssessment` into database rows, not a separate step the
    caller has to remember to do afterward.

    Documents are the primary source. If a `ticker` is supplied, any financial line item
    or period the documents do not cover is filled from Yahoo Finance (`yfinance`) — the
    documents always win on cells they do contain. The pack may even be empty (no PDFs):
    then the assessment runs on yfinance fundamentals alone.

    If `company_name` is given (or read from the filing / yfinance) and
    `enable_web_sentiment` is True, the agent also searches free public web/news and folds
    the sentiment into the deterministic scorecard. Both the yfinance and web steps fail
    soft — on any error they are skipped and scoring proceeds on whatever is available.
    """
    _log.info("=" * 70)
    _log.info("ASSESS start: documents=%d, ticker=%s, company_name=%r",
              len(pdf_paths), ticker.strip() or "—", company_name or "—")

    # 1. grounded extraction from the documents (LLM, language only) — skipped if no pack
    company = CompanyMetadata()
    line_items: List[LineItem] = []
    notes: List = []
    facility = None
    if pdf_paths:
        set_phase(version.id, "indexing_documents", f"{len(pdf_paths)} document(s)")
        documents = _persist_documents(session, version.id, pdf_paths)
        pages = load_pack_as_pages(pdf_paths)
        page_index = PgPageIndex(session, version.id)
        for filename, document in documents.items():
            doc_pages = [p for p in pages if p.doc_name == filename]
            document.page_count = len(doc_pages)
            page_index.ingest(doc_pages, document_id=document.id)
        # Committed here (not just flushed) so the parallel extractors below — each in
        # its own session/connection — can actually see these rows; a flush is only
        # visible within this same session/transaction.
        session.commit()

        set_phase(version.id, "extracting_financials", "company/line-items/notes/facility")
        with ThreadPoolExecutor(max_workers=_MAX_PARALLEL_EXTRACTORS) as executor:
            futures = {
                executor.submit(_run_extractor, version.id, extract_company_metadata): "company",
                executor.submit(_run_extractor, version.id, extract_line_items): "line_items",
                executor.submit(_run_extractor, version.id, extract_notes): "notes",
                executor.submit(_run_extractor, version.id, extract_facility_request): "facility",
            }
            results: Dict[str, Any] = {}
            for future in as_completed(futures):
                name = futures[future]
                results[name] = future.result()  # re-raises here, naming which extractor failed
        company, line_items, notes, facility = (
            results["company"], results["line_items"], results["notes"], results["facility"],
        )
        _log.info("documents: extracted %d line item(s) over periods %s; company=%r",
                  len(line_items), sorted({li.period for li in line_items}),
                  company.company_name)
    else:
        _log.info("documents: none supplied — relying on yfinance fundamentals")
    if facility is None:
        facility = FacilityRequest()

    set_phase(version.id, "enriching_and_scoring", "yfinance fallback, web sentiment, ratios, scorecard")

    # 1b. yfinance FALLBACK + CROSS-CHECK — fill any (line item, period) the documents
    #     lack, and flag where document and yfinance figures disagree. Docs always win.
    doc_items = list(line_items)
    yf_items: List[LineItem] = []
    recon = DataReconciliation(
        ticker=ticker.strip() or None,
        yfinance_attempted=bool(ticker.strip()),
        yfinance_available=yfinance_available(),
        doc_line_items=len(doc_items),
    )
    if ticker.strip() and yfinance_available():
        try:
            yf_items, yf_meta = fetch_fundamentals(ticker)
        except Exception as e:
            _log.warning("yfinance: fetch failed for %s: %s", ticker.strip(), e)
            yf_items, yf_meta = [], CompanyMetadata()
        recon.yfinance_line_items = len(yf_items)
        if yf_items:
            recon.discrepancies = _cross_check(doc_items, yf_items)
            line_items = _merge_yfinance_items(doc_items, yf_items)
            filled = [li for li in line_items
                      if (li.source_snippet or "").startswith("yfinance:")]
            recon.cells_filled_from_yfinance = len(filled)
            recon.filled = [
                f"{li.standardised_label.value} [{li.period}] = {li.value:,.0f}"
                for li in filled if li.value is not None
            ]
            for li in filled:
                _log.info("FILLED from yfinance: %s [%s] = %s",
                          li.standardised_label.value, li.period, li.value)
            if not filled:
                _log.info("yfinance: no gaps to fill — documents already cover every cell")
        company = _merge_metadata(company, yf_meta)
    elif ticker.strip() and not yfinance_available():
        recon.notes.append("yfinance library not installed — fallback skipped "
                           "(pip install yfinance).")
        _log.warning("yfinance: ticker=%s given but library not installed — skipped.",
                     ticker.strip())

    # 1c. web-sentiment enrichment (optional second input — free DuckDuckGo OSINT)
    resolved_name = (company_name or company.company_name or "").strip()
    web_sentiment: Optional[WebSentimentResult] = None
    if enable_web_sentiment and resolved_name:
        seed = CompanySeed(
            company_name=resolved_name, country=company.country,
            industry=company.industry, ticker=ticker.strip() or None,
            search_region=search_region,
        )
        try:
            web_sentiment = run_web_sentiment(seed)
        except Exception:
            web_sentiment = None

    # 2. deterministic engine (no LLM)
    periods, ratios_by_period = compute_ratios(line_items)
    latest = ratios_by_period[-1] if ratios_by_period else None
    benchmark = benchmark_ratios(latest, company.industry) if latest else benchmark_ratios(
        _empty_ratios(), company.industry)
    trend = analyse_trends(periods, ratios_by_period, company.management_commentary)
    scorecard = score_case(
        ratios_by_period, benchmark, trend, company, notes,
        latest_revenue=_latest_revenue(line_items, periods),
        web_sentiment=web_sentiment,
    )

    # 2b. reconcile ratio coverage — which ratios are still uncomputable, and whether the
    #     missing inputs were absent from yfinance too (logged for the analyst).
    recon.ratio_gaps = _ratio_gaps(line_items, ratios_by_period, yf_items)
    _log.info("RECONCILIATION: doc_items=%d, yfinance_items=%d, filled=%d, "
              "discrepancies=%d, ratio_gaps=%d",
              recon.doc_line_items, recon.yfinance_line_items,
              recon.cells_filled_from_yfinance, len(recon.discrepancies),
              len(recon.ratio_gaps))

    assessment = CreditAssessment(
        company=company, facility=facility, line_items=line_items, notes=notes,
        periods=periods, ratios_by_period=ratios_by_period, benchmark=benchmark,
        trend=trend, scorecard=scorecard, web_sentiment=web_sentiment,
        reconciliation=recon,
    )

    # 3. grounded credit memo (LLM, grounded in the deterministic scorecard)
    set_phase(version.id, "writing_memo")
    assessment.memo = synthesize_credit_memo(assessment)

    # 4. persist the assessment's child rows (the caller commits the overall version)
    persist_line_items(session, version.id, assessment.line_items)
    persist_ratios(session, version.id, assessment.ratios_by_period)
    persist_rating_factors(session, version.id, assessment.scorecard.factors)
    persist_memo(session, version.id, assessment.memo)

    return assessment


def _yfinance_line_items(assessment: CreditAssessment) -> List[LineItem]:
    """Line items that came from the yfinance fallback (tagged in source_snippet)."""
    return [li for li in assessment.line_items
            if (li.source_snippet or "").startswith("yfinance:")]


def _empty_ratios():
    from .schemas import FinancialRatios
    return FinancialRatios(period="—")


def answer_question(assessment: CreditAssessment, question: str) -> str:
    sc = assessment.scorecard
    latest = assessment.ratios_by_period[-1] if assessment.ratios_by_period else None
    compact = {
        "company": assessment.company.company_name,
        "grade": sc.grade.value,
        "band": sc.band.value,
        "normalized_score": sc.normalized_score,
        "decision": sc.decision.value,
        "pd": sc.probability_of_default,
        "lgd": sc.loss_given_default,
        "latest_ratios": (
            {n: rv.value for n, rv in latest.ratios.items() if rv.value is not None}
            if latest else {}
        ),
        "active_factors": [
            {"factor": f.factor.value, "severity": f.severity, "evidence": f.evidence}
            for f in sc.factors if f.present
        ],
        "sector_position": assessment.benchmark.overall_position,
        "material_gaps": assessment.benchmark.material_gaps,
        "trajectory": assessment.trend.overall_trajectory,
        "web_sentiment": (
            {
                "overall": assessment.web_sentiment.overall_sentiment,
                "disambiguation_confidence": assessment.web_sentiment.disambiguation_confidence,
                "adverse_findings": [
                    {"category": f.category, "summary": f.summary, "severity": f.severity}
                    for f in assessment.web_sentiment.adverse_findings
                ],
            }
            if assessment.web_sentiment else None
        ),
    }
    system = SystemMessage(content=(
        "You answer questions about a completed credit assessment. Use ONLY the supplied "
        "figures (already correct and final). If the assessment does not contain the "
        "answer, say so. Never invent ratios, a different grade, or a different decision."
    ))
    user = HumanMessage(content=(
        f"Question:\n{question}\n\nAssessment:\n{json.dumps(compact, indent=2, default=str)}\n\n"
        "Answer concisely."
    ))
    return get_llm().invoke([system, user]).content


def run_credit_assessment(
    pdf_paths: List[str],
    question: str = "",
    company_name: str = "",
    ticker: str = "",
    search_region: Optional[str] = None,
    enable_web_sentiment: bool = True,
    session: Optional[Session] = None,
    parent_version_id: Optional[str] = None,
    requested_by: str = "local-analyst",
    version: Optional[AssessmentVersion] = None,
) -> Dict[str, Any]:
    """Assess a credit submission end-to-end and answer an optional question.

    `ticker` (optional) turns on the yfinance fallback that fills any financial data the
    documents lack (and lets the case run with no PDFs at all). `company_name` (optional)
    drives the free web-sentiment search alongside the docs. Returns the assessment, a
    human-readable reasoning trace, an optional answer, run metrics (latency, approximate
    LLM-call count), and the persisted `version_id`.

    Owns exactly one `AssessmentVersion` row per call — `parent_version_id` set makes this
    a rerun (CASE_STUDY.md §7.1: version, don't overwrite). If `session` is omitted, one is
    opened and committed/closed here (the CLI path); if the caller passes one (the API
    path), this function only flushes — the caller controls the transaction's fate, since
    it may be doing more in the same request than just this assessment.

    `version`, if given, is an already-created, already-committed row (the API's
    background-job path: the route creates it synchronously — status="created" — so it
    can return a `version_id` immediately, then hands the actual pipeline work to a
    background task that calls this function with that row and its own fresh session, a
    different session than the one that created it). When omitted (the CLI path, and the
    API's synchronous helpers that don't run the pipeline at all), a new version is
    created here instead.
    """
    owns_session = session is None
    session = session or next(get_session())
    start = time.time()
    reasoning: List[str] = []
    metrics: Dict[str, Any] = {"documents": len(pdf_paths)}

    if version is None:
        version = AssessmentVersion(
            parent_version_id=parent_version_id,
            status="processing",
            company_name=company_name or None,
            ticker=ticker.strip() or None,
            question=question or None,
            enable_web_sentiment=enable_web_sentiment,
            search_region=search_region,
            model_name=LLM_MODEL_NAME,
            embed_model_name=EMBED_MODEL_NAME,
            requested_by=requested_by,
        )
        session.add(version)
        session.flush()
    else:
        version.status = "processing"
        version.model_name = LLM_MODEL_NAME
        version.embed_model_name = EMBED_MODEL_NAME

    try:
        assessment = _run_credit_assessment_body(
            session, version, pdf_paths, question, company_name, ticker,
            search_region, enable_web_sentiment, reasoning, metrics, start,
        )
    except Exception as exc:
        version.status = "validation_review"
        set_phase(version.id, "failed", f"{type(exc).__name__}: {exc}")
        if owns_session:
            session.commit()  # keep the failed version + whatever pages/docs landed
            session.close()
        raise

    if owns_session:
        session.commit()
        session.close()
    else:
        session.flush()

    return {
        "assessment": assessment,
        "answer": version.answer or "",
        "reasoning": reasoning,
        "metrics": metrics,
        "version_id": version.id,
    }


def _run_credit_assessment_body(
    session: Session,
    version: AssessmentVersion,
    pdf_paths: List[str],
    question: str,
    company_name: str,
    ticker: str,
    search_region: Optional[str],
    enable_web_sentiment: bool,
    reasoning: List[str],
    metrics: Dict[str, Any],
    start: float,
) -> CreditAssessment:
    """The actual pipeline + reasoning-trace + version-update work for one assessment.

    Split out from `run_credit_assessment` so that function's session/transaction
    bookkeeping (open, commit-on-success, commit-on-failure) reads as a short, flat
    try/except instead of being buried in the middle of a 100-line pipeline body.
    """
    assessment = assess_pack(
        session, version, pdf_paths, company_name=company_name, ticker=ticker,
        search_region=search_region, enable_web_sentiment=enable_web_sentiment,
    )
    sc = assessment.scorecard

    # LLM calls: 4 doc extractors (only if a pack was supplied) + memo + optional web read
    metrics["llm_calls"] = (4 if pdf_paths else 0) + 1 + (1 if assessment.web_sentiment else 0)
    yf_items = _yfinance_line_items(assessment)
    metrics["yfinance_line_items"] = len(yf_items)
    rec = assessment.reconciliation
    if rec:
        metrics["yfinance_discrepancies"] = len(rec.discrepancies)
        metrics["ratio_gaps"] = len(rec.ratio_gaps)

    reasoning.append(
        f"**Extraction** → company=`{assessment.company.company_name or '—'}`, "
        f"industry=`{assessment.company.industry or '—'}`, "
        f"line_items={len(assessment.line_items)}, periods={assessment.periods}, "
        f"notes={len(assessment.notes)}, audit_opinion={assessment.company.audit_opinion.value}"
    )
    if ticker.strip():
        if yf_items:
            labels = sorted({li.standardised_label.value for li in yf_items})
            reasoning.append(
                f"**yfinance fallback** → ticker=`{ticker.strip()}`, "
                f"filled {len(yf_items)} cell(s) the documents lacked "
                f"({', '.join(labels)})"
            )
        elif not yfinance_available():
            reasoning.append(
                f"**yfinance fallback** → ticker=`{ticker.strip()}` SKIPPED: the `yfinance` "
                f"library is not installed (`pip install yfinance`). Assessed on documents only."
            )
        else:
            reasoning.append(
                f"**yfinance fallback** → ticker=`{ticker.strip()}`: no gaps filled "
                f"(documents already cover every period/line item, or Yahoo returned nothing "
                f"for this ticker)"
            )
        if rec and rec.discrepancies:
            reasoning.append(
                "**Doc vs yfinance discrepancies** (document value kept; review):\n"
                + "\n".join(f"- {d}" for d in rec.discrepancies[:12])
            )
        if rec and rec.ratio_gaps:
            reasoning.append(
                "**Ratios still missing inputs after fallback:**\n"
                + "\n".join(f"- {g}" for g in rec.ratio_gaps[:12])
            )
    latest = assessment.ratios_by_period[-1] if assessment.ratios_by_period else None
    if latest:
        key = ["net_debt_to_ebitda", "interest_coverage", "dscr", "current_ratio",
               "ebitda_margin", "ocf_to_total_debt"]
        shown = ", ".join(
            f"{n}={latest.ratios[n].value}" for n in key
            if latest.ratios.get(n) and latest.ratios[n].value is not None
        )
        reasoning.append(f"**Ratios (deterministic, latest {latest.period})** → {shown or '—'}")
    reasoning.append(
        f"**Benchmark (deterministic)** → industry=`{assessment.benchmark.industry_used}`, "
        f"position=`{assessment.benchmark.overall_position}`, "
        f"material_gaps={assessment.benchmark.material_gaps or '—'}"
    )
    reasoning.append(
        f"**Trend (deterministic)** → trajectory=`{assessment.trend.overall_trajectory}`, "
        f"commentary_signal=`{assessment.trend.commentary_signal}`"
    )
    if assessment.web_sentiment:
        ws = assessment.web_sentiment
        reasoning.append(
            f"**Web sentiment (DuckDuckGo, grounded)** → "
            f"company=`{ws.seed.company_name}`, overall=`{ws.overall_sentiment}`, "
            f"disambiguation=`{ws.disambiguation_confidence}`, "
            f"adverse_findings={len(ws.adverse_findings)}, evidence={len(ws.evidence)}"
        )
    reasoning.append(
        f"**Scorecard (deterministic)** → score={sc.normalized_score}/100, "
        f"grade=`{sc.grade.value}`, band=`{sc.band.value}`, "
        f"PD={sc.probability_of_default:.4f}, LGD={sc.loss_given_default:.2f}, "
        f"EL={sc.expected_loss_pct:.4%}, decision=`{sc.decision.value}`"
        + (f", HARD STOP: {sc.hard_stop_reason}" if sc.hard_stop_reason else "")
    )

    answer = ""
    if question:
        answer = answer_question(assessment, question)
        metrics["llm_calls"] += 1

    metrics["periods"] = len(assessment.periods)
    metrics["grade"] = sc.grade.value
    metrics["band"] = sc.band.value
    metrics["decision"] = sc.decision.value
    metrics["latency_ms"] = round((time.time() - start) * 1000.0, 2)

    version.status = "awaiting_review"
    version.answer = answer or None
    version.grade = sc.grade.value
    version.band = sc.band.value
    version.decision = sc.decision.value
    version.probability_of_default = sc.probability_of_default
    version.loss_given_default = sc.loss_given_default
    version.expected_loss_pct = sc.expected_loss_pct
    version.hard_stop_reason = sc.hard_stop_reason
    version.latency_ms = metrics["latency_ms"]
    version.llm_call_count = metrics["llm_calls"]
    version.raw_result = assessment.model_dump(mode="json")
    version.reasoning = reasoning
    version.completed_at = datetime.now(timezone.utc)
    # The version was created with whatever name the caller supplied (often blank, or a
    # loosely-typed one like "JPMorgan Chase") — once extraction/yfinance has resolved
    # the actual filed name (e.g. "JPMorgan Chase & Co."), that's what the record should
    # show; fall back to the original input only if resolution found nothing.
    version.company_name = assessment.company.company_name or version.company_name
    set_phase(version.id, "done")

    return assessment


# if __name__ == "__main__":
#     import sys

#     if len(sys.argv) < 2:
#         print("Usage:")
#         print("  python -m credit_risk.agent_graph [<pdf> ...] "
#               "[--company \"Name\"] [--ticker AAPL] [--region wt-wt] [-- question]")
#         print("  (at least one of <pdf> or --ticker is required)")
#         sys.exit(1)

#     rest = sys.argv[1:]
#     q = ""
#     if "--" in rest:
#         i = rest.index("--")
#         q = " ".join(rest[i + 1:])
#         rest = rest[:i]

#     def _take_opt(name: str):
#         if name in rest:
#             i = rest.index(name)
#             val = rest[i + 1] if i + 1 < len(rest) else ""
#             del rest[i:i + 2]
#             return val
#         return ""

#     company = _take_opt("--company")
#     ticker = _take_opt("--ticker")
#     region = _take_opt("--region") or None

#     out = run_credit_assessment(
#         rest, q, company_name=company, ticker=ticker, search_region=region)
#     a = out["assessment"]
#     sc = a.scorecard
#     print("\n=== RATING ===")
#     print(f"{a.company.company_name or '—'}: grade={sc.grade.value}, band={sc.band.value}, "
#           f"score={sc.normalized_score}/100, decision={sc.decision.value}")
#     print(f"PD={sc.probability_of_default:.4f}  LGD={sc.loss_given_default:.2f}  "
#           f"Expected loss={sc.expected_loss_pct:.4%}")
#     if sc.suggested_pricing:
#         print("Indicative pricing:", sc.suggested_pricing)
#     if sc.hard_stop_reason:
#         print("HARD STOP:", sc.hard_stop_reason)
#     print("\n=== ACTIVE RISK FACTORS ===")
#     for f in sc.factors:
#         if f.present:
#             print(f"  {f.factor.value}: {f.severity} (contribution {f.contribution})")
#     print("\n=== CONDITIONS ===")
#     for c in sc.conditions:
#         print(f"  - {c}")
#     if a.web_sentiment:
#         ws = a.web_sentiment
#         print("\n=== WEB SENTIMENT (DuckDuckGo) ===")
#         print(f"  overall={ws.overall_sentiment} | disambiguation={ws.disambiguation_confidence} "
#               f"| evidence={len(ws.evidence)}")
#         for f in ws.adverse_findings:
#             print(f"   - [{f.severity}] {f.category}: {f.summary[:90]}")
#     print("\n=== EXECUTIVE SUMMARY ===\n", a.memo.executive_summary)
#     if out["answer"]:
#         print("\n=== ANSWER ===\n", out["answer"])
#     print("\n=== METRICS ===\n", out["metrics"])
