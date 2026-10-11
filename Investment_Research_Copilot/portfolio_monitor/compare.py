"""
Deterministic comparison of two constructed portfolios (deterministic vs LLM).

NO LLM. Diffs the two `ConstructedPortfolio`s so the UI can show them side-by-side: per-sleeve
allocation deltas, name overlap (Jaccard), names unique to each, and deltas on the expected
risk metrics. Pure function of the two inputs.
"""

from __future__ import annotations

from .schemas import ConstructedPortfolio, ConstructionComparison

# Sleeve-weight delta worth calling out in notes (2pp).
NOTABLE_SLEEVE_DIFF = 0.02


def compare_constructions(
    det: ConstructedPortfolio, llm: ConstructedPortfolio
) -> ConstructionComparison:
    det_names = {p.symbol for p in det.positions}
    llm_names = {p.symbol for p in llm.positions}
    shared_names = det_names & llm_names
    all_names = det_names | llm_names
    jaccard = (len(shared_names) / len(all_names)) if all_names else 0.0

    det_sleeve = {b.name: b.weight for b in det.sleeve_allocation}
    llm_sleeve = {b.name: b.weight for b in llm.sleeve_allocation}
    alloc_diff: list[dict[str, float]] = []
    for sleeve in sorted(set(det_sleeve) | set(llm_sleeve)):
        det_weight = det_sleeve.get(sleeve, 0.0)
        llm_weight = llm_sleeve.get(sleeve, 0.0)
        alloc_diff.append(
            {
                "sleeve": sleeve,
                "deterministic": round(det_weight, 4),
                "llm": round(llm_weight, 4),
                "diff": round(llm_weight - det_weight, 4),
            }
        )

    metric_diff: dict[str, float | None] = {
        "volatility": _delta(
            llm.expected_metrics.volatility_annual, det.expected_metrics.volatility_annual
        ),
        "dividend_yield": _delta(
            llm.expected_metrics.dividend_yield, det.expected_metrics.dividend_yield
        ),
        "concentration_hhi": _delta(
            llm.expected_metrics.concentration_hhi, det.expected_metrics.concentration_hhi
        ),
        "effective_n": _delta(llm.expected_metrics.effective_n, det.expected_metrics.effective_n),
    }

    notes: list[str] = []
    notes.append(f"Name overlap: {len(shared_names)}/{len(all_names)} (Jaccard {jaccard:.0%}).")
    if det.guardrail_corrections:
        notes.append(f"Deterministic guardrail corrections: {len(det.guardrail_corrections)}.")
    if llm.guardrail_corrections:
        notes.append(
            f"LLM guardrail corrections: {len(llm.guardrail_corrections)} "
            "(where the rules overrode the model)."
        )
    biggest = max(alloc_diff, key=lambda x: abs(x["diff"]), default=None)
    if biggest and abs(biggest["diff"]) >= NOTABLE_SLEEVE_DIFF:
        notes.append(
            f"Largest sleeve difference: {biggest['sleeve']} "
            f"({biggest['diff']:+.0%} LLM vs deterministic)."
        )

    return ConstructionComparison(
        allocation_diff=alloc_diff,
        name_overlap=sorted(shared_names),
        det_only=sorted(det_names - llm_names),
        llm_only=sorted(llm_names - det_names),
        jaccard=round(jaccard, 4),
        metric_diff=metric_diff,
        notes=notes,
    )


def _delta(current: float | None, baseline: float | None) -> float | None:
    if current is None or baseline is None:
        return None
    return round(current - baseline, 4)
