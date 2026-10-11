"""Correctness validation for Investment Copilot LLM outputs — beyond schema.

- Classification (news risk category/severity, geo/sector, sleeve breaches):
  `score_classification()` → estimated % correct.
- Extraction (holdings-table transcription): `score_extraction()` checks
  symbol grounding, arithmetic identity qty*price≈mv, enum membership.
- Suggestion (rebalance actions, construction picks, briefing prose):
  `score_suggestion()` enforces universe whitelist + guardrail pre-checks.

Confidence < 0.50 → `build_retry_addendum()` appended to prompt, reinvoke ≤3.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

CONFIDENCE_THRESHOLD = 0.50
MAX_ATTEMPTS = 3


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
        return [f"[{f.rule}] {f.detail}" for f in self.failures]


def _clamp(v: float) -> float:
    return max(0.0, min(1.0, v))


def score_classification(
    items: list[dict[str, Any]],
    allowed_categories: list[str],
    evidence_text: str = "",
    category_key: str = "category",
    severity_key: str = "severity",
) -> ConfidenceReport:
    """Estimate % of risk/geo/sector classifications that are correct."""
    failures: list[ValidationFailure] = []
    passed = total = 0
    allowed = {a.lower() for a in allowed_categories}
    ev = (evidence_text or "").lower()
    for i, row in enumerate(items):
        total += 3
        cat = str(row.get(category_key) or "")
        if cat.lower() in allowed:
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.unknown_category",
                    f"finding {i}: category '{cat}' not in allowed set",
                    0.35 / max(1, len(items)),
                )
            )
        if str(row.get(severity_key) or "").lower() in ("low", "medium", "high"):
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.bad_severity",
                    f"finding {i}: severity={row.get(severity_key)!r}",
                    0.25 / max(1, len(items)),
                )
            )
        summ = str(row.get("summary") or "")
        if not ev or (summ and summ.lower().split()[0][:5] in ev):
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "classification.ungrounded_summary",
                    f"finding {i}: summary not grounded in snippets",
                    0.2 / max(1, len(items)),
                )
            )
    if not items:
        return ConfidenceReport(confidence=0.55, failures=[], checks_run=1, checks_passed=1)
    return ConfidenceReport(
        confidence=_clamp(1.0 - sum(f.penalty for f in failures)),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def score_extraction(
    holdings: list[dict[str, Any]],
    evidence_text: str = "",
) -> ConfidenceReport:
    """Holdings-transcription correctness: symbol present, qty>0, mv identity."""
    failures: list[ValidationFailure] = []
    passed = total = 0
    ev = (evidence_text or "").lower()
    for i, h in enumerate(holdings):
        total += 4
        sym = str(h.get("symbol") or "").strip().upper()
        if sym:
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "extraction.missing_symbol",
                    f"holding {i}: empty symbol",
                    0.35 / max(1, len(holdings)),
                )
            )
        try:
            q = float(h.get("quantity"))
            if q > 0:
                passed += 1
            else:
                raise ValueError
        except (TypeError, ValueError):
            failures.append(
                ValidationFailure(
                    "extraction.bad_quantity",
                    f"holding {i} {sym}: quantity={h.get('quantity')!r} must be > 0",
                    0.3 / max(1, len(holdings)),
                )
            )
        # qty * price ≈ market_value (tolerance 5%) when all three present
        try:
            q = float(h.get("quantity"))
            p = float(h.get("price"))
            mv = float(h.get("market_value"))
            if abs(q * p - mv) / max(1e-9, abs(mv)) <= 0.05:
                passed += 1
            else:
                failures.append(
                    ValidationFailure(
                        "extraction.arithmetic_mismatch",
                        f"holding {i} {sym}: qty({q})*price({p}) != mv({mv}) beyond 5%",
                        0.3 / max(1, len(holdings)),
                    )
                )
        except (TypeError, ValueError):
            passed += 1  # fields optional — don't penalise absence
        if not ev or (sym and sym.lower() in ev):
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "extraction.ungrounded_symbol",
                    f"holding {i}: symbol {sym} not found in statement text",
                    0.3 / max(1, len(holdings)),
                )
            )
    if not holdings:
        failures.append(ValidationFailure("extraction.empty_output", "no holdings extracted", 0.6))
        return ConfidenceReport(confidence=0.0, failures=failures, checks_run=1, checks_passed=0)
    return ConfidenceReport(
        confidence=_clamp(1.0 - sum(f.penalty for f in failures)),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def score_suggestion(
    picks: list[dict[str, Any]],
    valid_symbols: list[str],
    max_weight: float = 0.35,
) -> ConfidenceReport:
    """Construction/rebalance suggestion correctness pre-guardrail.

    - symbol in candidate universe (hallucinated tickers fail hard)
    - weight in (0, max_weight], weights sum ≈ 1
    - reason non-empty and references the pick
    """
    failures: list[ValidationFailure] = []
    passed = total = 0
    universe = {s.upper() for s in valid_symbols}
    wsum = 0.0
    for i, p in enumerate(picks):
        total += 3
        sym = str(p.get("symbol") or "").upper()
        if sym in universe:
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.hallucinated_ticker",
                    f"pick {i}: symbol '{sym}' not in candidate universe ({len(universe)} names)",
                    0.5 / max(1, len(picks)),
                )
            )
        try:
            w = float(p.get("weight"))
            wsum += w
            if 0 < w <= max_weight:
                passed += 1
            else:
                raise ValueError
        except (TypeError, ValueError):
            failures.append(
                ValidationFailure(
                    "suggestion.bad_weight",
                    f"pick {i} {sym}: weight={p.get('weight')!r} must be in (0, {max_weight}]",
                    0.3 / max(1, len(picks)),
                )
            )
        if str(p.get("reason") or "").strip():
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.missing_reason",
                    f"pick {i} {sym}: empty reason",
                    0.2 / max(1, len(picks)),
                )
            )
    if picks:
        total += 1
        if abs(wsum - 1.0) <= 0.05:
            passed += 1
        else:
            failures.append(
                ValidationFailure(
                    "suggestion.weights_dont_sum", f"weights sum to {wsum:.3f}, expected ≈ 1.0", 0.3
                )
            )
    else:
        failures.append(ValidationFailure("suggestion.empty_output", "no picks proposed", 0.6))
        return ConfidenceReport(confidence=0.0, failures=failures, checks_run=1, checks_passed=0)
    return ConfidenceReport(
        confidence=_clamp(1.0 - sum(f.penalty for f in failures)),
        failures=failures,
        checks_run=total,
        checks_passed=passed,
    )


def build_retry_addendum(failures: list[ValidationFailure]) -> str:
    lines = ["PREVIOUS ATTEMPT FAILED THESE VALIDATIONS — fix all of them:"]
    for f in failures[:12]:
        lines.append(f"- [{f.rule}] {f.detail}")
    lines.append(
        "Transcribe EXACTLY as printed; use ONLY supplied symbols/snippets; "
        "weights must sum to 1.0 and respect caps. Do not invent tickers or events."
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
        out = llm_fn(addendum)
        report = score_fn(out)
        if report.confidence > best_report.confidence:
            best, best_report = out, report
        if report.confidence >= threshold:
            break
        addendum = build_retry_addendum(report.failures)
    return best, best_report, attempts
