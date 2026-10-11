"""Correctness validation for Fraud/Onboarding LLM outputs — beyond schema.

Covers structure extraction (parties/edges/roles), source-of-wealth,
identity gaps, case metadata (classification + extraction) and the
Profile-360 / EDD suggestion paths. Confidence < 0.50 → failures are
rendered into the prompt and the LLM is reinvoked (bounded).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_THRESHOLD = 0.50
MAX_ATTEMPTS = 3
MAX_RETRY_FAILURES_SHOWN = 12

_PARTY_ID_PATTERN = re.compile(r"^P\d+$")
_VALID_DISAMBIGUATION = ("low", "medium", "high")
_VALID_SEVERITIES = ("low", "medium", "high")
_EMPTY_GRAPH_CONFIDENCE = 0.55

_UNKNOWN_TYPE_PENALTY = 0.35
_UNGROUNDED_PARTY_PENALTY = 0.25
_BAD_PARTY_ID_PENALTY = 0.2
_EMPTY_PARTIES_PENALTY = 0.6
_DANGLING_EDGE_PENALTY = 0.4
_BAD_PERCENTAGE_PENALTY = 0.3
_BAD_DISAMBIGUATION_PENALTY = 0.25
_BAD_SEVERITY_PENALTY = 0.25
_EMPTY_SUMMARY_PENALTY = 0.25
_UNCITED_ADVERSE_PENALTY = 0.35
_NAME_PREFIX_LENGTH = 4
_NAME_PREVIEW_LENGTH = 50
_MAX_TYPE_PREVIEW = 8


@dataclass
class ValidationFailure:
    rule: str
    detail: str
    penalty: float


@dataclass
class ConfidenceReport:
    confidence: float
    failures: list[ValidationFailure] = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    @property
    def passed(self) -> bool:
        return self.confidence >= CONFIDENCE_THRESHOLD

    @property
    def percent_correct(self) -> float:
        if self.checks_run == 0:
            return 0.0
        return round(100.0 * self.checks_passed / self.checks_run, 1)

    def failure_summary(self) -> list[str]:
        return [f"[{failure.rule}] {failure.detail}" for failure in self.failures]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _penalty(base: float, item_count: int) -> float:
    return base / max(1, item_count)


def _confidence_from_failures(failures: list[ValidationFailure]) -> float:
    return _clamp(1.0 - sum(failure.penalty for failure in failures))


def _check_party_type(
    party: dict[str, Any],
    index: int,
    allowed_types: set[str],
    party_count: int,
    failures: list[ValidationFailure],
) -> bool:
    party_type = str(party.get("party_type") or "")
    if party_type.lower() in allowed_types:
        return True
    preview = sorted(allowed_types)[:_MAX_TYPE_PREVIEW]
    failures.append(
        ValidationFailure(
            "classification.unknown_party_type",
            f"party {index}: type '{party_type}' not in {preview}...",
            _penalty(_UNKNOWN_TYPE_PENALTY, party_count),
        )
    )
    return False


def _check_party_grounding(
    party: dict[str, Any],
    index: int,
    evidence_text: str,
    party_count: int,
    failures: list[ValidationFailure],
) -> bool:
    name = str(party.get("name") or "").strip()
    first_token = name.lower().split()[0][:_NAME_PREFIX_LENGTH] if name else ""
    if name and (not evidence_text or first_token in evidence_text):
        return True
    failures.append(
        ValidationFailure(
            "classification.ungrounded_party",
            f"party {index}: name '{name[:_NAME_PREVIEW_LENGTH]}' not found in evidence",
            _penalty(_UNGROUNDED_PARTY_PENALTY, party_count),
        )
    )
    return False


def _check_party_id(
    party: dict[str, Any],
    index: int,
    party_count: int,
    failures: list[ValidationFailure],
) -> bool:
    party_id = str(party.get("party_id") or "").strip()
    if _PARTY_ID_PATTERN.match(party_id):
        return True
    failures.append(
        ValidationFailure(
            "classification.bad_party_id",
            f"party {index}: party_id '{party.get('party_id')}' must look like P1, P2, ...",
            _penalty(_BAD_PARTY_ID_PENALTY, party_count),
        )
    )
    return False


def score_party_classification(
    parties: list[dict[str, Any]],
    allowed_types: list[str],
    evidence_text: str = "",
) -> ConfidenceReport:
    """What % of party_type classifications look correct.

    Checks: type in vocabulary, name non-empty + grounded in evidence,
    stable id format (P<int>).
    """
    failures: list[ValidationFailure] = []
    passed = 0
    total = 0
    allowed = {allowed_type.lower() for allowed_type in allowed_types}
    normalized_evidence = (evidence_text or "").lower()
    for index, party in enumerate(parties):
        total += 3
        passed += _check_party_type(party, index, allowed, len(parties), failures)
        passed += _check_party_grounding(party, index, normalized_evidence, len(parties), failures)
        passed += _check_party_id(party, index, len(parties), failures)
    if not parties:
        failures.append(
            ValidationFailure(
                "classification.empty_output", "no parties extracted", _EMPTY_PARTIES_PENALTY
            )
        )
        return ConfidenceReport(confidence=0.0, failures=failures, checks_run=1, checks_passed=0)
    return ConfidenceReport(
        confidence=_confidence_from_failures(failures),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def _check_edge_endpoints(
    edge: dict[str, Any],
    index: int,
    valid_ids: set[str],
    edge_count: int,
    failures: list[ValidationFailure],
) -> bool:
    owner_id = edge.get("owner_id")
    owned_id = edge.get("owned_id")
    if owner_id in valid_ids and owned_id in valid_ids and owner_id != owned_id:
        return True
    failures.append(
        ValidationFailure(
            "extraction.dangling_edge",
            f"edge {index}: owner={owner_id} owned={owned_id} — must reference known distinct party_ids",
            _penalty(_DANGLING_EDGE_PENALTY, edge_count),
        )
    )
    return False


def _check_edge_percentage(
    edge: dict[str, Any],
    index: int,
    edge_count: int,
    failures: list[ValidationFailure],
) -> bool:
    percentage = edge.get("percentage")
    try:
        parsed = None if percentage is None else float(percentage)
    except (ValueError, TypeError):
        parsed = "invalid"
    if parsed is None or (isinstance(parsed, float) and 0 <= parsed <= 100):
        return True
    failures.append(
        ValidationFailure(
            "extraction.bad_percentage",
            f"edge {index}: percentage={percentage!r} must be null or 0-100",
            _penalty(_BAD_PERCENTAGE_PENALTY, edge_count),
        )
    )
    return False


def score_ownership_extraction(
    edges: list[dict[str, Any]],
    valid_ids: list[str],
    evidence_text: str = "",
) -> ConfidenceReport:
    """Ownership-edge correctness: referential integrity + % plausibility."""
    failures: list[ValidationFailure] = []
    passed = 0
    total = 0
    known_ids = set(valid_ids)
    for index, edge in enumerate(edges):
        total += 2
        passed += _check_edge_endpoints(edge, index, known_ids, len(edges), failures)
        passed += _check_edge_percentage(edge, index, len(edges), failures)
    total = max(total, 1)
    if not edges:
        return ConfidenceReport(
            confidence=_EMPTY_GRAPH_CONFIDENCE, failures=[], checks_run=1, checks_passed=1
        )
    return ConfidenceReport(
        confidence=_confidence_from_failures(failures),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def _check_disambiguation(profile: dict[str, Any], failures: list[ValidationFailure]) -> bool:
    confidence = str(profile.get("disambiguation_confidence") or "").lower()
    if confidence in _VALID_DISAMBIGUATION:
        return True
    failures.append(
        ValidationFailure(
            "suggestion.bad_disambiguation",
            f"disambiguation_confidence={profile.get('disambiguation_confidence')!r} must be low/medium/high",
            _BAD_DISAMBIGUATION_PENALTY,
        )
    )
    return False


def _check_adverse_item(
    adverse_item: dict[str, Any],
    index: int,
    evidence_urls: set[str],
    failures: list[ValidationFailure],
) -> int:
    passed = 0
    if str(adverse_item.get("severity") or "").lower() in _VALID_SEVERITIES:
        passed += 1
    else:
        failures.append(
            ValidationFailure(
                "suggestion.bad_severity",
                f"adverse {index}: severity={adverse_item.get('severity')!r}",
                _BAD_SEVERITY_PENALTY,
            )
        )
    if str(adverse_item.get("summary") or "").strip():
        passed += 1
    else:
        failures.append(
            ValidationFailure(
                "suggestion.empty_summary",
                f"adverse {index}: empty summary",
                _EMPTY_SUMMARY_PENALTY,
            )
        )
    url = str(adverse_item.get("url") or "").lower()
    if not url or url in evidence_urls or url == "none":
        passed += 1
    else:
        failures.append(
            ValidationFailure(
                "suggestion.uncited_adverse",
                f"adverse {index}: url not in collected evidence — possible hallucination",
                _UNCITED_ADVERSE_PENALTY,
            )
        )
    return passed


def score_profile_suggestion(
    profile: dict[str, Any],
    evidence_urls: list[str],
    seed_name: str = "",
) -> ConfidenceReport:
    """Profile-360 / adverse-media suggestion correctness.

    - severity in vocabulary; high severity must cite a URL also present in evidence
    - disambiguation_confidence in low/medium/high
    - adverse summaries non-empty; no adverse finding without any citation
    """
    failures: list[ValidationFailure] = []
    passed = 0
    total = 0
    normalized_urls = {str(url or "").lower() for url in evidence_urls}
    total += 1
    passed += _check_disambiguation(profile, failures)
    for index, adverse_item in enumerate(profile.get("adverse_media") or []):
        total += 3
        passed += _check_adverse_item(adverse_item, index, normalized_urls, failures)
    if not (profile.get("adverse_media") or []):
        total += 1
        passed += 1
    return ConfidenceReport(
        confidence=_confidence_from_failures(failures),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def build_retry_addendum(failures: list[ValidationFailure]) -> str:
    lines = ["PREVIOUS ATTEMPT FAILED THESE VALIDATIONS — fix all of them:"]
    for failure in failures[:MAX_RETRY_FAILURES_SHOWN]:
        lines.append(f"- [{failure.rule}] {failure.detail}")
    lines.append(
        "Use ONLY the page/evidence text. Never invent parties, percentages, or URLs. "
        "Use null/omit when the text does not state a fact."
    )
    return "\n".join(lines)


def validate_with_retry(
    llm_fn: Callable[[str], Any],
    score_fn: Callable[[Any], ConfidenceReport],
    max_attempts: int = MAX_ATTEMPTS,
    threshold: float = CONFIDENCE_THRESHOLD,
) -> tuple[Any, ConfidenceReport, int]:
    best: Any = None
    best_report = ConfidenceReport(
        confidence=0.0,
        checks_run=1,
        checks_passed=0,
        failures=[ValidationFailure("retry.no_attempt", "llm_fn produced nothing", 1.0)],
    )
    addendum = ""
    attempts = 0
    for _ in range(max_attempts):
        attempts += 1
        output = llm_fn(addendum)
        report = score_fn(output)
        if report.confidence > best_report.confidence:
            best, best_report = output, report
        if report.confidence >= threshold:
            break
        addendum = build_retry_addendum(report.failures)
    return best, best_report, attempts
