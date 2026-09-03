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
from typing import List, Optional

from .schemas import ConstructionRequest, FeasibilityVerdict, SelectedPosition

# Absolute sanity ceiling on an annualised return target.
_ABSURD_ANNUAL = float(os.getenv("MAX_REALISTIC_ANNUAL_RETURN", "0.50"))


def implied_target_annual(request: ConstructionRequest) -> tuple:
    """Resolve the investor's objective to (annual_target, basis). basis ∈ percent|target_value|none."""
    if request.target_return_pct is not None:
        t = float(request.target_return_pct)
        if t > 1.0:                                   # entered as '12' meaning 12%
            t = t / 100.0
        return t, "percent"
    if (request.target_value and request.invest_amount and request.invest_amount > 0
            and request.horizon_years and request.horizon_years > 0):
        ratio = request.target_value / request.invest_amount
        if ratio > 0:
            return ratio ** (1.0 / request.horizon_years) - 1.0, "target_value"
    return None, "none"


def assess_feasibility(
    request: ConstructionRequest,
    positions: List[SelectedPosition],
    universe_expected_returns: Optional[List[float]] = None,
) -> FeasibilityVerdict:
    """Build the feasibility verdict for a constructed book."""
    target, basis = implied_target_annual(request)

    # book expected return = weight-weighted expected return of priced positions
    num = den = 0.0
    for p in positions:
        if p.expected_return is not None:
            num += p.target_weight * p.expected_return
            den += p.target_weight
    expected = (num / den) if den > 0 else None

    ceiling = None
    pool = [r for r in (universe_expected_returns or []) if r is not None]
    if pool:
        ceiling = max(pool)
    elif positions:
        ers = [p.expected_return for p in positions if p.expected_return is not None]
        ceiling = max(ers) if ers else None

    projected = None
    if expected is not None and request.invest_amount and request.horizon_years:
        projected = request.invest_amount * ((1.0 + expected) ** request.horizon_years)

    realistic = True
    if target is None:
        message = (
            (f"No explicit return target set. The book's expected return is ~{expected:.1%} p.a."
             + (f", projecting {request.invest_amount:,.0f} → ~{projected:,.0f} over "
                f"{request.horizon_years}y." if projected is not None else "."))
            if expected is not None else
            "No explicit return target set, and insufficient history to estimate an expected return.")
    else:
        if target > _ABSURD_ANNUAL or (ceiling is not None and target > ceiling):
            realistic = False
            ceil_txt = f" the best single holding only delivered ~{ceiling:.1%} p.a. historically;" if ceiling is not None else ""
            message = (
                f"A {target:.1%} p.a. target is unrealistic for this universe —{ceil_txt} "
                f"no combination of the available holdings is expected to reach it without "
                f"leverage or far higher risk. Consider lowering the target or widening the "
                f"universe / risk tolerance.")
        elif expected is not None and target > expected + 0.05:
            message = (
                f"A {target:.1%} p.a. target is ambitious: the constructed book's expected "
                f"return is ~{expected:.1%} p.a. It may be reachable only by concentrating in "
                f"higher-return, higher-risk names — within your single-name and risk limits.")
        elif expected is not None:
            message = (
                f"A {target:.1%} p.a. target looks achievable: the book's expected return is "
                f"~{expected:.1%} p.a."
                + (f", projecting {request.invest_amount:,.0f} → ~{projected:,.0f} over "
                   f"{request.horizon_years}y." if projected is not None else "."))
        else:
            message = (f"Target {target:.1%} p.a. recorded, but there is insufficient price "
                       f"history to estimate the book's expected return.")

    return FeasibilityVerdict(
        target_return_annual=(round(target, 4) if target is not None else None),
        target_basis=basis,
        expected_return_annual=(round(expected, 4) if expected is not None else None),
        universe_ceiling_annual=(round(ceiling, 4) if ceiling is not None else None),
        projected_value=(round(projected, 2) if projected is not None else None),
        realistic=realistic,
        message=message,
    )
