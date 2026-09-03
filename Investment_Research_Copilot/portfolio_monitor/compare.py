"""
Deterministic comparison of two constructed portfolios (deterministic vs LLM).

NO LLM. Diffs the two `ConstructedPortfolio`s so the UI can show them side-by-side: per-sleeve
allocation deltas, name overlap (Jaccard), names unique to each, and deltas on the expected
risk metrics. Pure function of the two inputs.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from .schemas import ConstructedPortfolio, ConstructionComparison


def compare_constructions(
    det: ConstructedPortfolio, llm: ConstructedPortfolio
) -> ConstructionComparison:
    det_names = {p.symbol for p in det.positions}
    llm_names = {p.symbol for p in llm.positions}
    inter = det_names & llm_names
    union = det_names | llm_names
    jaccard = (len(inter) / len(union)) if union else 0.0

    det_sleeve = {b.name: b.weight for b in det.sleeve_allocation}
    llm_sleeve = {b.name: b.weight for b in llm.sleeve_allocation}
    alloc_diff: List[Dict[str, float]] = []
    for sleeve in sorted(set(det_sleeve) | set(llm_sleeve)):
        d = det_sleeve.get(sleeve, 0.0)
        l = llm_sleeve.get(sleeve, 0.0)
        alloc_diff.append({"sleeve": sleeve, "deterministic": round(d, 4),
                           "llm": round(l, 4), "diff": round(l - d, 4)})

    metric_diff: Dict[str, Optional[float]] = {
        "volatility": _delta(llm.expected_metrics.volatility_annual,
                             det.expected_metrics.volatility_annual),
        "dividend_yield": _delta(llm.expected_metrics.dividend_yield,
                                 det.expected_metrics.dividend_yield),
        "concentration_hhi": _delta(llm.expected_metrics.concentration_hhi,
                                    det.expected_metrics.concentration_hhi),
        "effective_n": _delta(llm.expected_metrics.effective_n,
                              det.expected_metrics.effective_n),
    }

    notes: List[str] = []
    notes.append(f"Name overlap: {len(inter)}/{len(union)} (Jaccard {jaccard:.0%}).")
    if det.guardrail_corrections:
        notes.append(f"Deterministic guardrail corrections: {len(det.guardrail_corrections)}.")
    if llm.guardrail_corrections:
        notes.append(f"LLM guardrail corrections: {len(llm.guardrail_corrections)} "
                     "(where the rules overrode the model).")
    biggest = max(alloc_diff, key=lambda x: abs(x["diff"]), default=None)
    if biggest and abs(biggest["diff"]) >= 0.02:
        notes.append(f"Largest sleeve difference: {biggest['sleeve']} "
                     f"({biggest['diff']:+.0%} LLM vs deterministic).")

    return ConstructionComparison(
        allocation_diff=alloc_diff,
        name_overlap=sorted(inter),
        det_only=sorted(det_names - llm_names),
        llm_only=sorted(llm_names - det_names),
        jaccard=round(jaccard, 4),
        metric_diff=metric_diff,
        notes=notes,
    )


def _delta(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b is None:
        return None
    return round(a - b, 4)
