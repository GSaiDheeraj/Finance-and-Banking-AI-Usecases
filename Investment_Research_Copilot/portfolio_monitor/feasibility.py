"""
Deterministic return-objective feasibility check.

NO LLM (the LLM only rephrases the verdict in the briefing). Turns the investor's return
objective — given either as an annualised **percent** (e.g. 12% p.a.) or as a **target end
value** over the horizon (e.g. 100k → 150k in 5 years) — into a single annualised target, and
compares it against what the constructed book and the candidate universe can plausibly deliver
(using historical expected-return proxies). It flags **unrealistic** targets so the LLM can
say so plainly.

Heuristic (illustrative; tune in production):
  * target above the BEST single-asset historical return in the universe  → unrealistic
    (no holding even achieved it historically);
  * target above an absolute sanity ceiling (default 50% p.a.)            → unrealistic;
  * target meaningfully above the book's expected return                  → ambitious (possible
    only by concentrating / taking more risk) — realistic flag stays True but the message says so;
  * otherwise                                                             → on track.
"""

from __future__ import annotations

import os

from .schemas import ConstructionRequest, FeasibilityVerdict, SelectedPosition

# Absolute sanity ceiling on an annualised return target.
_ABSURD_ANNUAL = float(os.getenv("MAX_REALISTIC_ANNUAL_RETURN", "0.50"))

# How far above the book's expected return a target can be before it reads as
# ambitious rather than achievable (5pp of headroom).
AMBITIOUS_HEADROOM = 0.05

# A whole-number target above 1.0 was entered as a percent (e.g. 12 means 12%).
_WHOLE_PERCENT_THRESHOLD = 1.0
_PERCENT_DIVISOR = 100.0


def implied_target_annual(request: ConstructionRequest) -> tuple:
    """Resolve the investor's objective to (annual_target, basis).

    Basis is one of "percent", "target_value", or "none".
    """
    if request.target_return_pct is not None:
        target = float(request.target_return_pct)
        if target > _WHOLE_PERCENT_THRESHOLD:  # entered as '12' meaning 12%
            target = target / _PERCENT_DIVISOR
        return target, "percent"
    if not _has_target_value_request(request):
        return None, "none"
    growth_ratio = request.target_value / request.invest_amount
    if growth_ratio <= 0:
        return None, "none"
    return growth_ratio ** (1.0 / request.horizon_years) - 1.0, "target_value"


def _has_target_value_request(request: ConstructionRequest) -> bool:
    """Whether the request carries a usable target-value objective."""
    return bool(
        request.target_value
        and request.invest_amount
        and request.invest_amount > 0
        and request.horizon_years
        and request.horizon_years > 0
    )


def assess_feasibility(
    request: ConstructionRequest,
    positions: list[SelectedPosition],
    universe_expected_returns: list[float] | None = None,
) -> FeasibilityVerdict:
    """Build the feasibility verdict for a constructed book."""
    target, basis = implied_target_annual(request)
    expected = _book_expected_return(positions)
    ceiling = _universe_ceiling(positions, universe_expected_returns)
    projected = _projected_value(request, expected)

    if target is None:
        message = _no_target_message(request, expected, projected)
    elif target > _ABSURD_ANNUAL or (ceiling is not None and target > ceiling):
        message = _unrealistic_message(target, ceiling)
    elif expected is not None and target > expected + AMBITIOUS_HEADROOM:
        message = _ambitious_message(target, expected)
    elif expected is not None:
        message = _achievable_message(request, target, expected, projected)
    else:
        message = (
            f"Target {target:.1%} p.a. recorded, but there is insufficient price "
            "history to estimate the book's expected return."
        )
    realistic = not (
        target is not None
        and (target > _ABSURD_ANNUAL or (ceiling is not None and target > ceiling))
    )

    return FeasibilityVerdict(
        target_return_annual=(round(target, 4) if target is not None else None),
        target_basis=basis,
        expected_return_annual=(round(expected, 4) if expected is not None else None),
        universe_ceiling_annual=(round(ceiling, 4) if ceiling is not None else None),
        projected_value=(round(projected, 2) if projected is not None else None),
        realistic=realistic,
        message=message,
    )


def _book_expected_return(positions: list[SelectedPosition]) -> float | None:
    """Weight-weighted expected return of the priced positions."""
    weighted_return = 0.0
    priced_weight = 0.0
    for position in positions:
        if position.expected_return is not None:
            weighted_return += position.target_weight * position.expected_return
            priced_weight += position.target_weight
    if priced_weight <= 0:
        return None
    return weighted_return / priced_weight


def _universe_ceiling(
    positions: list[SelectedPosition],
    universe_expected_returns: list[float] | None,
) -> float | None:
    """Best single-asset historical return available as a plausibility ceiling."""
    pool = [expected for expected in (universe_expected_returns or []) if expected is not None]
    if pool:
        return max(pool)
    position_returns = [
        position.expected_return for position in positions if position.expected_return is not None
    ]
    if position_returns:
        return max(position_returns)
    return None


def _projected_value(request: ConstructionRequest, expected: float | None) -> float | None:
    """Projected end value of the investment at the book's expected return."""
    if expected is None or not request.invest_amount or not request.horizon_years:
        return None
    return request.invest_amount * ((1.0 + expected) ** request.horizon_years)


def _projection_suffix(request: ConstructionRequest, projected: float | None) -> str:
    """Trailing clause turning a message into a value projection when possible."""
    if projected is None:
        return "."
    return (
        f", projecting {request.invest_amount:,.0f} → ~{projected:,.0f} over "
        f"{request.horizon_years}y."
    )


def _no_target_message(
    request: ConstructionRequest, expected: float | None, projected: float | None
) -> str:
    """Message for a book built without an explicit return target."""
    if expected is None:
        return (
            "No explicit return target set, and insufficient history "
            "to estimate an expected return."
        )
    return (
        f"No explicit return target set. The book's expected return is ~{expected:.1%} p.a."
        + _projection_suffix(request, projected)
    )


def _unrealistic_message(target: float, ceiling: float | None) -> str:
    """Message for a target no holding in the universe ever achieved."""
    ceiling_txt = (
        f" the best single holding only delivered ~{ceiling:.1%} p.a. historically;"
        if ceiling is not None
        else ""
    )
    return (
        f"A {target:.1%} p.a. target is unrealistic for this universe —{ceiling_txt} "
        "no combination of the available holdings is expected to reach it without "
        "leverage or far higher risk. Consider lowering the target or widening the "
        "universe / risk tolerance."
    )


def _ambitious_message(target: float, expected: float) -> str:
    """Message for a target reachable only by concentrating risk."""
    return (
        f"A {target:.1%} p.a. target is ambitious: the constructed book's expected "
        f"return is ~{expected:.1%} p.a. It may be reachable only by concentrating in "
        "higher-return, higher-risk names — within your single-name and risk limits."
    )


def _achievable_message(
    request: ConstructionRequest, target: float, expected: float, projected: float | None
) -> str:
    """Message for a target the constructed book already covers."""
    return (
        f"A {target:.1%} p.a. target looks achievable: the book's expected return is "
        f"~{expected:.1%} p.a." + _projection_suffix(request, projected)
    )
