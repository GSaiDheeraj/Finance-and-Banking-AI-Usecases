"""Correctness validation for Credit-Risk LLM outputs — beyond schema.

Schema (Pydantic) guarantees *shape*. These rules estimate *correctness*:

- Classification (label mapping, audit opinion, sentiment severity/category):
  `score_classification()` reports an estimated % correct.
- Extraction (line items, metadata, notes, facility):
  `score_extraction()` checks grounding + plausibility and yields 0-1 confidence.
- Suggestion (decision/pricing/covenants are deterministic, but the LLM memo
  suggests mitigants/covenants/triggers): `score_suggestion()`.

If confidence < CONFIDENCE_THRESHOLD (0.50), callers collect
`failures` and reinvoke the LLM with `build_retry_addendum(failures)`
appended to the prompt (see `validate_with_retry`).
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_THRESHOLD = 0.50
MAX_ATTEMPTS = 3
MAX_RETRY_FAILURES_SHOWN = 12

PENALTY_UNKNOWN_LABEL = 0.4
PENALTY_EMPTY_SOURCE_LABEL = 0.2
PENALTY_UNGROUNDED_LABEL = 0.2
PENALTY_EMPTY_CLASSIFICATION = 0.6
PENALTY_MISSING_FIELD = 0.3
PENALTY_IMPLAUSIBLE_NUMBER = 0.25
PENALTY_UNGROUNDED_VALUE = 0.2
PENALTY_EMPTY_EXTRACTION = 0.6
PENALTY_VAGUE_SUGGESTION = 0.3
PENALTY_UNIVERSE_VIOLATION = 0.3
PENALTY_NO_RISK_ACK = 0.2
PENALTY_EMPTY_SUGGESTION = 0.5
MAX_SANE_MAGNITUDE = 1e15
MIN_ACTIONABLE_WORDS = 3
EVIDENCE_PREFIX_LENGTH = 4
DETAIL_SNIPPET_LENGTH = 60
SUGGESTION_SNIPPET_LENGTH = 80


@dataclass
class ValidationFailure:
    rule: str
    detail: str
    penalty: float  # 0..1 subtracted from 1.0


@dataclass
class ConfidenceReport:
    confidence: float  # 0..1
    failures: list[ValidationFailure] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def passed(self) -> bool:
        """True when confidence meets the retry threshold."""
        return self.confidence >= CONFIDENCE_THRESHOLD

    @property
    def percent_correct(self) -> float:
        """Estimated % correct for classification-style outputs."""
        if self.checks_run == 0:
            return 0.0
        return round(100.0 * self.checks_passed / self.checks_run, 1)

    def failure_summary(self) -> list[str]:
        """Render failures as human-readable [rule] detail lines."""
        return [f"[{failure.rule}] {failure.detail}" for failure in self.failures]


def _clamp(value: float) -> float:
    """Clamp a confidence value into the 0..1 range."""
    return max(0.0, min(1.0, value))


def _penalty(base_penalty: float, item_count: int) -> float:
    """Normalise a per-row penalty across the output size."""
    return base_penalty / max(1, item_count)


def _is_grounded(source_label: str, evidence: str) -> bool:
    """Check token-prefix overlap between a label and the evidence text."""
    if not source_label:
        return False
    first_token = source_label.lower().split()[0][:EVIDENCE_PREFIX_LENGTH]
    return first_token in evidence


def score_classification(
    items: list[dict[str, Any]],
    allowed_labels: list[str],
    evidence_text: str = "",
    label_key: str = "standardised_label",
    text_key: str = "label",
) -> ConfidenceReport:
    """Estimate what % of LLM classifications are correct.

    Checks per item: (a) label in allowed vocabulary (+0.4 penalty if not),
    (b) label text grounded in evidence (+0.2 if evidence given but no token
    overlap), (c) non-empty source label (+0.2). Confidence = 1 - avg penalty.
    """
    failures: list[ValidationFailure] = []
    passed_checks = 0
    total_checks = 0
    allowed = {allowed_label.lower() for allowed_label in allowed_labels}
    evidence = (evidence_text or "").lower()
    for row_index, row in enumerate(items):
        total_checks += 3
        predicted = str(row.get(label_key) or "").strip()
        source = str(row.get(text_key) or "").strip()
        if predicted and predicted.lower() in allowed:
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.unknown_label",
                    f"row {row_index}: '{predicted}' not in allowed vocabulary "
                    f"({len(allowed_labels)} labels)",
                    _penalty(PENALTY_UNKNOWN_LABEL, len(items)),
                )
            )
        if source:
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.empty_source_label",
                    f"row {row_index}: empty source label — nothing to map from",
                    _penalty(PENALTY_EMPTY_SOURCE_LABEL, len(items)),
                )
            )
        if not evidence:
            passed_checks += 1  # no evidence to check against — don't penalise
        elif _is_grounded(source, evidence):
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.ungrounded_label",
                    f"row {row_index}: source label '{source[:DETAIL_SNIPPET_LENGTH]}' "
                    "has no token overlap with evidence",
                    _penalty(PENALTY_UNGROUNDED_LABEL, len(items)),
                )
            )
    confidence = _clamp(1.0 - sum(failure.penalty for failure in failures))
    if not items:
        failures.append(
            ValidationFailure(
                "classification.empty_output",
                "no items classified",
                PENALTY_EMPTY_CLASSIFICATION,
            )
        )
        confidence = 0.0
    return ConfidenceReport(
        confidence=confidence,
        failures=failures,
        checks_run=total_checks,
        checks_passed=passed_checks,
    )


def score_extraction(
    items: list[dict[str, Any]],
    evidence_text: str = "",
    required_fields: tuple[str, ...] = ("label", "period", "value"),
    numeric_field: str = "value",
) -> ConfidenceReport:
    """Score extraction correctness: completeness, grounding, plausibility.

    - required field present (+0.3 each missing, normalised)
    - numeric field parses to finite number within sane magnitude (<1e15)
    - label grounded in evidence text (token overlap) when evidence supplied
    """
    failures: list[ValidationFailure] = []
    passed_checks = 0
    total_checks = 0
    evidence = (evidence_text or "").lower()
    for row_index, row in enumerate(items):
        _score_required_fields(row, row_index, required_fields, len(items), failures)
        passed_checks += _count_present_fields(row, required_fields)
        total_checks += len(required_fields)
        total_checks += 1
        if _is_plausible_number(row.get(numeric_field)):
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "extraction.implausible_number",
                    f"row {row_index}: field '{numeric_field}'={row.get(numeric_field)!r} "
                    "not a plausible finite number",
                    _penalty(PENALTY_IMPLAUSIBLE_NUMBER, len(items)),
                )
            )
        label = str(row.get("label") or "")
        if evidence and label:
            total_checks += 1
            if _is_grounded(label, evidence):
                passed_checks += 1
            else:
                failures.append(
                    ValidationFailure(
                        "extraction.ungrounded_value",
                        f"row {row_index}: label '{label[:DETAIL_SNIPPET_LENGTH]}' "
                        "not found in evidence text",
                        _penalty(PENALTY_UNGROUNDED_VALUE, len(items)),
                    )
                )
    if not items:
        failures.append(
            ValidationFailure(
                "extraction.empty_output", "no rows extracted", PENALTY_EMPTY_EXTRACTION
            )
        )
        return ConfidenceReport(
            confidence=0.0, failures=failures, checks_run=1, checks_passed=0
        )
    confidence = _clamp(1.0 - sum(failure.penalty for failure in failures))
    return ConfidenceReport(
        confidence=confidence,
        failures=failures,
        checks_run=total_checks,
        checks_passed=passed_checks,
    )


def _score_required_fields(
    row: dict[str, Any],
    row_index: int,
    required_fields: tuple[str, ...],
    item_count: int,
    failures: list[ValidationFailure],
) -> None:
    """Append a failure per missing/empty required field."""
    for field_name in required_fields:
        value = row.get(field_name)
        if value is None or (isinstance(value, str) and not value.strip()):
            failures.append(
                ValidationFailure(
                    "extraction.missing_field",
                    f"row {row_index}: required field '{field_name}' missing/empty",
                    _penalty(PENALTY_MISSING_FIELD, item_count),
                )
            )


def _count_present_fields(row: dict[str, Any], required_fields: tuple[str, ...]) -> int:
    """Count required fields that are present and non-empty."""
    present = 0
    for field_name in required_fields:
        value = row.get(field_name)
        if value is not None and (not isinstance(value, str) or value.strip()):
            present += 1
    return present


def _is_plausible_number(raw_number: Any) -> bool:
    """Check a value parses to a finite number within a sane magnitude."""
    if raw_number is None:
        return False
    try:
        parsed = float(str(raw_number).replace(",", ""))
    except (ValueError, TypeError):
        return False
    return math.isfinite(parsed) and abs(parsed) < MAX_SANE_MAGNITUDE


def score_suggestion(
    suggestions: list[str],
    allowed_universe: list[str] | None = None,
    evidence_text: str = "",
    must_acknowledge_risk: bool = False,
) -> ConfidenceReport:
    """Score LLM suggestions (memo mitigants/covenants/triggers, EDD-style lists).

    - non-empty, actionable (verb-like, >=3 words)
    - whitelisted references only (no invented entities) when universe given
    - risk acknowledgement when required
    """
    failures: list[ValidationFailure] = []
    passed_checks = 0
    total_checks = 0
    allowed_terms = {term.lower() for term in (allowed_universe or [])}
    for index, suggestion in enumerate(suggestions):
        total_checks += 2
        text = (suggestion or "").strip()
        if len(text.split()) >= MIN_ACTIONABLE_WORDS:
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.vague",
                    f"suggestion {index} too short/vague: {text[:SUGGESTION_SNIPPET_LENGTH]!r}",
                    _penalty(PENALTY_VAGUE_SUGGESTION, len(suggestions)),
                )
            )
        if _references_allowed_universe(text, allowed_terms):
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.universe_violation",
                    f"suggestion {index} references entity outside supplied universe",
                    _penalty(PENALTY_UNIVERSE_VIOLATION, len(suggestions)),
                )
            )
    if must_acknowledge_risk and suggestions:
        total_checks += 1
        combined = " ".join(suggestions).lower()
        if any(
            word in combined
            for word in ("risk", "covenant", "monitor", "condition", "trigger")
        ):
            passed_checks += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.no_risk_ack",
                    "memo suggestions never mention risk/covenant/monitor",
                    PENALTY_NO_RISK_ACK,
                )
            )
    if not suggestions:
        failures.append(
            ValidationFailure(
                "suggestion.empty_output",
                "no suggestions produced",
                PENALTY_EMPTY_SUGGESTION,
            )
        )
        return ConfidenceReport(
            confidence=0.0, failures=failures, checks_run=1, checks_passed=0
        )
    confidence = _clamp(1.0 - sum(failure.penalty for failure in failures))
    return ConfidenceReport(
        confidence=confidence,
        failures=failures,
        checks_run=total_checks,
        checks_passed=passed_checks,
    )


def _references_allowed_universe(text: str, allowed_terms: set) -> bool:
    """Check a suggestion stays within the allowed entity universe."""
    if not allowed_terms:
        return True
    lowered = text.lower()
    if any(term in lowered for term in allowed_terms):
        return True
    return not any(
        word.isupper() and len(word) > MIN_ACTIONABLE_WORDS for word in text.split()
    )


def build_retry_addendum(failures: list[ValidationFailure]) -> str:
    """Render failed validations as prompt exceptions for the reinvocation."""
    lines = ["PREVIOUS ATTEMPT FAILED THESE VALIDATIONS — fix all of them: "]
    for failure in failures[:MAX_RETRY_FAILURES_SHOWN]:
        lines.append(f"- [{failure.rule}] {failure.detail}")
    lines.append(
        "If a fact is not in the evidence, return null/omit it rather than guessing."
    )
    return "\n".join(lines)


def validate_with_retry(
    llm_fn: Callable[[str], Any],
    score_fn: Callable[[Any], ConfidenceReport],
    max_attempts: int = MAX_ATTEMPTS,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[Any, ConfidenceReport, int]:
    """Call llm_fn(addendum) → score → retry with failures appended.

    Returns (best_output, best_report, attempts_used). Never raises on low
    confidence — returns best attempt so callers can fail-soft deterministically.
    """
    best_output: Any = None
    best_report = ConfidenceReport(
        confidence=0.0,
        checks_run=1,
        checks_passed=0,
        failures=[
            ValidationFailure("retry.no_attempt", "llm_fn produced nothing", 1.0)
        ],
    )
    addendum = ""
    attempts_used = 0
    for _ in range(max_attempts):
        attempts_used += 1
        output = llm_fn(addendum)
        report = score_fn(output)
        if report.confidence > best_report.confidence:
            best_output, best_report = output, report
        if report.confidence >= threshold:
            break
        addendum = build_retry_addendum(report.failures)
    return best_output, best_report, attempts_used
