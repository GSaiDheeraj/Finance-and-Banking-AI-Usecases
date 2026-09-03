"""
Deterministic guardrails for portfolio construction.

NO LLM. This is the hard-constraint layer that BOTH constructors (the deterministic optimizer
and the LLM advisor) pass their proposed weights through, so neither can violate a constraint:

  * drop excluded symbols / sectors;
  * drop sub-minimum positions;
  * enforce the single-name cap (clip + redistribute the excess);
  * enforce the sector cap (scale the offending sector down + redistribute);
  * renormalise to sum to 1 (any weight that cannot be placed becomes cash residual).

Every change is recorded as a human-readable correction string so the agent can show exactly
where it overrode a proposal — important when comparing the LLM's free choices to the rules.
Then `finalise_positions` converts the guardrailed weights into `SelectedPosition`s with
amounts, share counts and the per-position rationale.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..logconf import get_logger
from ..schemas import (
    AssetClass,
    ConstructionConstraints,
    InstrumentType,
    MarketCap,
    Region,
    SelectedPosition,
)
from ..universe import Candidate

_log = get_logger()
_EPS = 1e-6


def enforce(
    weights: Dict[str, float],
    meta: Dict[str, Candidate],
    constraints: ConstructionConstraints,
) -> Tuple[Dict[str, float], List[str]]:
    """Return (guardrailed_weights, corrections) honouring all hard constraints."""
    corrections: List[str] = []
    excl = {x.strip().upper() for x in constraints.exclusions}

    # 0. drop exclusions (by symbol or by sector name)
    w = {}
    for s, wt in weights.items():
        su = s.upper()
        sec = (meta[su].sector or "").upper() if su in meta else ""
        if su in excl or sec in excl:
            corrections.append(f"Excluded {su} (matches exclusion list).")
            continue
        if wt > 0:
            w[su] = float(wt)
    if not w:
        return {}, corrections

    w = _normalise(w)

    # 1. drop sub-minimum positions, then renormalise
    minp = constraints.min_position
    small = [s for s, wt in w.items() if wt < minp]
    if small:
        for s in small:
            corrections.append(f"Dropped {s} ({w[s]:.2%} < {minp:.2%} minimum position).")
            del w[s]
        w = _normalise(w)
    if not w:
        return {}, corrections

    cap = constraints.single_name_cap

    # 2. enforce single-name and sector caps by water-filling. Capping/scaling are tracked
    #    once per symbol/sector (with the ORIGINAL pre-guardrail weight) so the correction log
    #    is deduped — not one line per solver iteration — and the result never exceeds a cap.
    capped_from: Dict[str, float] = {}
    scaled_from: Dict[str, float] = {}
    for _ in range(50):
        changed = False
        over = {s: wt for s, wt in w.items() if wt > cap + _EPS}
        if over:
            changed = True
            for s, wt in over.items():
                capped_from.setdefault(s, wt)
            w = _cap_normalise(w, cap)               # water-fill to ≤ cap, sum preserved
        sec_over = _sector_overflow(w, meta, constraints.sector_cap)
        if sec_over:
            changed = True
            for sector, total in sec_over.items():
                scaled_from.setdefault(sector, total)
            w = _scale_sectors(w, meta, constraints.sector_cap)
        if not changed:
            break

    for s, orig in capped_from.items():
        corrections.append(f"Capped {s} from {orig:.1%} to the single-name limit {cap:.1%}.")
    for sector, orig in scaled_from.items():
        corrections.append(
            f"Scaled sector '{sector}' from {orig:.1%} to the sector cap "
            f"{constraints.sector_cap:.1%}.")
    return w, corrections


def finalise_positions(
    weights: Dict[str, float],
    meta: Dict[str, Candidate],
    invest_amount: float,
    rationale_by_symbol: Optional[Dict[str, str]] = None,
) -> Tuple[List[SelectedPosition], float, float]:
    """Convert guardrailed weights into SelectedPositions. Returns (positions, invested, cash)."""
    rationale_by_symbol = rationale_by_symbol or {}
    positions: List[SelectedPosition] = []
    invested = 0.0
    for sym, wt in sorted(weights.items(), key=lambda kv: kv[1], reverse=True):
        c = meta.get(sym)
        amount = wt * invest_amount
        price = c.price if c else None
        shares = (amount / price) if (price and price > 0) else None
        if shares is not None:
            shares = round(shares, 4)
            invested += (shares * price)
        positions.append(SelectedPosition(
            symbol=sym,
            name=(c.name if c else None),
            asset_class=(c.asset_class if c else AssetClass.OTHER),
            sector=(c.sector if c else None),
            region=(c.region if c else Region.OTHER),
            country=(c.country if c else None),
            market_cap=(c.market_cap if c else MarketCap.UNKNOWN),
            instrument_type=(c.instrument_type if c else InstrumentType.OTHER),
            expected_return=(c.expected_return if c else None),
            target_weight=round(wt, 6),
            amount=round(amount, 2),
            shares=shares,
            price=price,
            rationale=rationale_by_symbol.get(sym, ""),
        ))
    cash_residual = round(invest_amount - invested, 2)
    return positions, round(invested, 2), cash_residual


# --------------------------------------------------------------------------- #
def _normalise(w: Dict[str, float]) -> Dict[str, float]:
    total = sum(w.values())
    if total <= 0:
        return {}
    return {s: wt / total for s, wt in w.items()}


def _cap_normalise(w: Dict[str, float], cap: float) -> Dict[str, float]:
    """Water-fill weights so none exceeds `cap`, distributing the excess ONLY to names that
    are strictly below the cap (so they are never pushed over). Converges to all ≤ cap with
    the total preserved when feasible (n·cap ≥ total); if infeasible, every name sits at the
    cap and the shortfall is left as cash residual (the total cannot exceed n·cap)."""
    out = dict(w)
    for _ in range(200):
        over = [s for s, wt in out.items() if wt > cap + _EPS]
        if not over:
            break
        excess = sum(out[s] - cap for s in over)
        for s in over:
            out[s] = cap
        under = {s: wt for s, wt in out.items() if wt < cap - _EPS}
        u_total = sum(under.values())
        if u_total <= _EPS:                          # nobody can absorb more — all at cap
            break
        # Distribute proportionally WITHOUT clipping here: any name pushed over the cap is
        # caught and re-distributed on the next iteration, so no weight is lost (when
        # feasible the total is preserved; if infeasible the shortfall becomes cash).
        for s, wt in under.items():
            out[s] = wt + excess * (wt / u_total)
    return out


def _sector_totals(w: Dict[str, float], meta: Dict[str, Candidate]) -> Dict[str, float]:
    totals: Dict[str, float] = {}
    for s, wt in w.items():
        sec = (meta[s].sector if s in meta and meta[s].sector else "Unclassified")
        totals[sec] = totals.get(sec, 0.0) + wt
    return totals


def _sector_overflow(w, meta, cap) -> Dict[str, float]:
    return {sec: t for sec, t in _sector_totals(w, meta).items()
            if t > cap + _EPS and sec != "Unclassified"}


def _scale_sectors(w: Dict[str, float], meta: Dict[str, Candidate], cap: float) -> Dict[str, float]:
    """Scale each over-cap sector's members down to the cap; redistribute to other sectors."""
    totals = _sector_totals(w, meta)
    out = dict(w)
    freed = 0.0
    over_secs = {sec for sec, t in totals.items() if t > cap + _EPS and sec != "Unclassified"}
    for s, wt in out.items():
        sec = (meta[s].sector if s in meta and meta[s].sector else "Unclassified")
        if sec in over_secs and totals[sec] > 0:
            scaled = wt * (cap / totals[sec])
            freed += wt - scaled
            out[s] = scaled
    # receivers: members of non-over sectors
    receivers = {s: wt for s, wt in out.items()
                 if (meta[s].sector if s in meta and meta[s].sector else "Unclassified") not in over_secs
                 and wt > 0}
    rtotal = sum(receivers.values())
    if rtotal > 0 and freed > 0:
        for s, wt in receivers.items():
            out[s] = wt + freed * (wt / rtotal)
    return out
