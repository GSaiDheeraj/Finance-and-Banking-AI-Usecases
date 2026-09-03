"""
Deterministic portfolio constructor (rules + optimisation), investor-profile aware.

NO LLM. Builds a portfolio from scratch for the investor profile on the request:
  1. set the sleeve targets — the mandate's strategic asset allocation, OVERRIDDEN by the
     investor's equity/debt split when given;
  2. within the EQUITY sleeve, honour the chosen style (large / mid / small / multi-cap with a
     cap mix, fund-of-funds, thematic, ELSS) over the chosen countries, selecting the "most
     trusted" curated names RANKED BY EXPECTED RETURN, and weighting them by the chosen method
     (equal-weight default; inverse-vol; risk-parity-lite; return-tilted);
  3. fund the debt / cash / alternatives sleeves from broad funds;
  4. enforce the hard guardrails (caps, exclusions, min size) and size to share quantities;
  5. assess whether the investor's return objective is realistic.

Every position and exclusion carries a rationale; the same request + universe always yields
the same portfolio (NFR-1).
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from ..feasibility import assess_feasibility
from ..logconf import get_logger
from ..reference import normalize_country, target_allocation
from ..schemas import (
    AssetClass,
    ConstructedPortfolio,
    ConstructionRequest,
    ExcludedCandidate,
    GeographyMode,
    InstrumentType,
    MarketCap,
    PortfolioStyle,
    WeightingMethod,
)
from ..universe import Candidate, candidates_by_sleeve
from . import common, guardrails

_log = get_logger()

_MAX_PER_SLEEVE = 8       # names per (sleeve / cap tier)
_MAX_PER_SECTOR = 2       # max stocks from the same sector in a single cap tier
_DEFAULT_CAP_MIX = {"large_cap": 0.5, "mid_cap": 0.3, "small_cap": 0.2}   # 50/30/20 default
_CAP_ENUM = {"large_cap": MarketCap.LARGE_CAP, "mid_cap": MarketCap.MID_CAP,
             "small_cap": MarketCap.SMALL_CAP}


def _sleeve_targets(request: ConstructionRequest) -> Dict[str, float]:
    """Asset-class sleeve targets: mandate SAA, overridden by the equity/debt split if given."""
    base = dict(target_allocation(request.mandate))
    if request.equity_pct is None and request.debt_pct is None:
        return base
    cash = base.get("cash", 0.0)
    alt = base.get("alternatives", 0.0)
    eq = request.equity_pct if request.equity_pct is not None else max(0.0, 1.0 - (request.debt_pct or 0.0) - cash - alt)
    eq = min(max(eq, 0.0), 1.0)
    if request.debt_pct is not None:
        debt = min(max(request.debt_pct, 0.0), 1.0)
    else:
        debt = max(0.0, 1.0 - eq - cash - alt)
    targets = {"equity": eq, "fixed_income": debt, "cash": cash, "alternatives": alt}
    total = sum(targets.values()) or 1.0
    return {k: v / total for k, v in targets.items()}


def _rank_by_return(members: List[Candidate]) -> List[Candidate]:
    """Most trusted-then-best: priced first, then by expected return desc (None last)."""
    return sorted(
        members,
        key=lambda c: (c.price is None, -(c.expected_return if c.expected_return is not None else -9.0)),
    )


def _sector_diverse_select(
    members: List[Candidate],
    max_per_sector: int = _MAX_PER_SECTOR,
    max_total: int = _MAX_PER_SLEEVE,
) -> List[Candidate]:
    """Select up to `max_total` names with guaranteed sector coverage + return optimisation.

    **Phase 1 — Sector guarantee (mandatory):**
    Take the *best-return* stock from EVERY sector, regardless of absolute return level.
    This ensures Technology, Energy, FMCG, Telecom etc. are all represented even if those
    sectors underperformed in the lookback window — e.g. TCS gets picked even when Infra
    stocks have higher recent returns.

    **Phase 2 — Fill remaining slots:**
    Fill up to `max_total` with the next best names by return, subject to `max_per_sector`.

    Example for India MULTI_CAP: phase 1 guarantees IT (TCS), Finance (HDFC), Energy
    (Reliance), FMCG (HUL), Telecom (Bharti), Pharma (Sun Pharma) etc.; phase 2 then
    adds the best second picks where cap allows.
    """
    ranked = _rank_by_return(members)
    # Phase 1: one guaranteed best-return pick per sector
    best_per_sector: Dict[str, Candidate] = {}
    for c in ranked:
        sector = (c.sector or "Unclassified").strip()
        if sector not in best_per_sector:
            best_per_sector[sector] = c

    # Order Phase-1 picks by their position in the return-ranked list (higher return first)
    rank_pos = {c.symbol: i for i, c in enumerate(ranked)}
    phase1 = sorted(best_per_sector.values(), key=lambda c: rank_pos.get(c.symbol, 9999))
    phase1 = phase1[:max_total]                       # cap Phase 1 if more sectors than slots

    # Phase 2: fill remaining slots (respecting max_per_sector)
    sector_count = {(c.sector or "Unclassified").strip(): 1 for c in phase1}
    selected = list(phase1)
    already = {c.symbol for c in selected}

    if len(selected) < max_total:
        for c in ranked:
            if c.symbol in already:
                continue
            sector = (c.sector or "Unclassified").strip()
            if sector_count.get(sector, 0) < max_per_sector:
                selected.append(c)
                sector_count[sector] = sector_count.get(sector, 0) + 1
                already.add(c.symbol)
            if len(selected) >= max_total:
                break

    return selected


def _etf_diverse_select(
    members: List[Candidate], max_total: int = _MAX_PER_SLEEVE
) -> List[Candidate]:
    """Select ETFs ensuring all sub-types are represented (equity / bond / commodity / REIT).

    Pass 1: pick the best-return ETF from each sub-type category.
    Pass 2: fill remaining slots by expected return.
    """
    from ..schemas import AssetClass as AC
    # Sub-type priority order: equity first, then bond, then commodity, then REIT, then other
    priority = [AC.EQUITY, AC.FIXED_INCOME, AC.ALTERNATIVES, AC.REAL_ESTATE]
    by_type: Dict = {}
    for c in _rank_by_return(members):
        by_type.setdefault(c.asset_class, []).append(c)

    selected: List[Candidate] = []
    for ac in priority:
        pool = by_type.get(ac, [])
        if pool and pool[0].symbol not in {c.symbol for c in selected}:
            selected.append(pool[0])

    if len(selected) < max_total:
        already = {c.symbol for c in selected}
        for c in _rank_by_return(members):
            if c.symbol not in already:
                selected.append(c)
            if len(selected) >= max_total:
                break

    return selected[:max_total]


def _mf_diverse_select(members: List[Candidate], max_total: int = 6) -> List[Candidate]:
    """Select mutual funds ensuring equity / debt / liquid categories are all represented."""
    from ..schemas import AssetClass as AC
    cat_priority = [AC.EQUITY, AC.FIXED_INCOME, AC.CASH]
    by_cat: Dict = {}
    for c in _rank_by_return(members):
        by_cat.setdefault(c.asset_class, []).append(c)

    selected: List[Candidate] = []
    for ac in cat_priority:
        pool = by_cat.get(ac, [])
        if pool and pool[0].symbol not in {c.symbol for c in selected}:
            selected.append(pool[0])

    # Fill remaining with best return
    if len(selected) < max_total:
        already = {c.symbol for c in selected}
        for c in _rank_by_return(members):
            if c.symbol not in already:
                selected.append(c)
            if len(selected) >= max_total:
                break

    return selected[:max_total]


def _weights_within(members: List[Candidate], method: WeightingMethod) -> Dict[str, Tuple[float, str]]:
    """Weights (sum 1) within a group of candidates + a per-name rationale."""
    members = members[:_MAX_PER_SLEEVE]
    n = len(members)
    if n == 0:
        return {}
    if method == WeightingMethod.RETURN_TILTED:
        scores = {c.symbol: max(0.0, (c.expected_return or 0.0)) + 0.01 for c in members}
        tot = sum(scores.values())
        return {c.symbol: (scores[c.symbol] / tot,
                           f"Return-tilted: expected return "
                           f"{(c.expected_return or 0):.1%} → weight {scores[c.symbol]/tot:.1%}.")
                for c in members}
    if method in (WeightingMethod.INVERSE_VOL, WeightingMethod.RISK_PARITY):
        vols = {c.symbol: c.volatility for c in members if c.volatility and c.volatility > 0}
        if len(vols) == n:
            inv = {s: 1.0 / v for s, v in vols.items()}
            tot = sum(inv.values())
            return {c.symbol: (inv[c.symbol] / tot,
                               f"{method.value}: vol {vols[c.symbol]:.1%} → weight {inv[c.symbol]/tot:.1%}.")
                    for c in members}
    w = 1.0 / n
    return {c.symbol: (w, f"Equal-weight across {n} names ({w:.1%} each).") for c in members}


def _select_equity(
    equity_candidates: List[Candidate], request: ConstructionRequest, equity_target: float
) -> Tuple[Dict[str, float], Dict[str, str], List[ExcludedCandidate]]:
    """Pick & weight equity names honouring country split + cap style/mix.

    When the investor named specific countries, the equity sleeve is split EQUALLY across the
    named countries (so each is represented), and names are ranked by expected return WITHIN
    each country. In GLOBAL mode, ranking is across the whole equity pool (best ideas anywhere).
    """
    named = [normalize_country(c) for c in request.countries]
    named = [c for c in named if c]
    if request.geography_mode != GeographyMode.GLOBAL and len(named) >= 1:
        groups: Dict[str, List[Candidate]] = {}
        for cand in equity_candidates:
            key = normalize_country(cand.country)
            if key:
                groups.setdefault(key, []).append(cand)
        active = [c for c in named if groups.get(c)]
        if active:
            weights: Dict[str, float] = {}
            rationale: Dict[str, str] = {}
            excluded: List[ExcludedCandidate] = []
            share = 1.0 / len(active)
            for ckey in active:
                w, r, ex = _select_cap(groups[ckey], request, equity_target * share,
                                       country_label=ckey.replace("_", " ").title())
                weights.update(w); rationale.update(r); excluded.extend(ex)
            return weights, rationale, excluded
    # GLOBAL (or no resolvable country): rank across the whole equity pool
    return _select_cap(equity_candidates, request, equity_target, country_label=None)


def _select_cap(
    equity_candidates: List[Candidate], request: ConstructionRequest, equity_target: float,
    country_label: Optional[str] = None,
) -> Tuple[Dict[str, float], Dict[str, str], List[ExcludedCandidate]]:
    """Pick & weight equity names within one (country) pool, honouring the cap style / mix."""
    weights: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    excluded: List[ExcludedCandidate] = []
    cprefix = f"{country_label} · " if country_label else ""

    # Determine the cap tiers and their share of the equity sleeve.
    if request.style == PortfolioStyle.MULTI_CAP:
        mix = request.cap_mix or _DEFAULT_CAP_MIX
        tot = sum(mix.values()) or 1.0
        tier_share = {k: v / tot for k, v in mix.items()}
    elif request.style == PortfolioStyle.LARGE_CAP:
        tier_share = {"large_cap": 1.0}
    elif request.style == PortfolioStyle.MID_CAP:
        tier_share = {"mid_cap": 1.0}
    elif request.style == PortfolioStyle.SMALL_CAP:
        tier_share = {"small_cap": 1.0}
    else:                                             # FoF / thematic / ELSS: one undivided sleeve
        tier_share = {"_all": 1.0}

    by_cap: Dict[str, List[Candidate]] = {}
    for c in equity_candidates:
        key = c.market_cap.value if c.market_cap != MarketCap.UNKNOWN else "_all"
        by_cap.setdefault(key, []).append(c)

    for tier, share in tier_share.items():
        if share <= 0:
            continue
        if tier == "_all":
            members = list(equity_candidates)
        else:
            members = by_cap.get(tier, [])
            if not members:
                members = list(equity_candidates)     # tier empty → use any equity name

        # Choose the selection strategy based on what instruments are in this tier
        has_etf  = any(c.instrument_type == InstrumentType.ETF for c in members)
        has_mf   = any(c.instrument_type == InstrumentType.MUTUAL_FUND for c in members)
        has_stk  = any(c.instrument_type == InstrumentType.STOCK for c in members)

        if has_mf and not has_stk:
            # Pure mutual-fund tier: ensure equity/debt/liquid categories are all present
            chosen = _mf_diverse_select(members, max_total=_MAX_PER_SLEEVE)
        elif has_etf and not has_stk:
            # Pure ETF tier: ensure equity/bond/commodity/REIT sub-types are all present
            chosen = _etf_diverse_select(members, max_total=_MAX_PER_SLEEVE)
        else:
            # Stock tier (or mixed): enforce sector diversity
            chosen = _sector_diverse_select(members, _MAX_PER_SECTOR, _MAX_PER_SLEEVE)

        ranked_all = _rank_by_return(members)
        chosen_syms = {c.symbol for c in chosen}
        inner = _weights_within(chosen, request.weighting_method)
        tier_label = tier.replace("_", " ")
        for sym, (w_in, why) in inner.items():
            port_w = w_in * equity_target * share
            weights[sym] = weights.get(sym, 0.0) + port_w
            c = next((x for x in chosen if x.symbol == sym), None)
            sec_note = (f" sector: {c.sector};" if c and c.sector else "")
            rationale[sym] = (f"{cprefix}equity sleeve · {tier_label} ({share:.0%} of tier);"
                              f"{sec_note} sector-diverse selection by expected return; " + why)
        for c in ranked_all:
            if c.symbol not in chosen_syms:
                excluded.append(ExcludedCandidate(
                    symbol=c.symbol,
                    reason=f"Sector diversity cap: {c.sector or 'sector unclassified'} "
                           f"already at {_MAX_PER_SECTOR} picks in {tier_label} tier."))
    return weights, rationale, excluded


def _select_sleeve(members: List[Candidate], target: float, method: WeightingMethod,
                   label: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    weights: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    ranked = _rank_by_return(members)
    inner = _weights_within(ranked, method)
    for sym, (w_in, why) in inner.items():
        weights[sym] = w_in * target
        rationale[sym] = f"{label} sleeve target {target:.0%}; " + why
    return weights, rationale


def construct_deterministic(
    request: ConstructionRequest, universe: List[Candidate]
) -> ConstructedPortfolio:
    """Build the deterministic portfolio for the request from the candidate universe."""
    targets = _sleeve_targets(request)
    by_sleeve = candidates_by_sleeve(universe)

    proposed: Dict[str, float] = {}
    rationale: Dict[str, str] = {}
    excluded: List[ExcludedCandidate] = []
    notes: List[str] = []

    # equity sleeve (style / cap-mix / country aware)
    eq_target = targets.get("equity", 0.0)
    if eq_target > 0:
        eq_cands = by_sleeve.get(AssetClass.EQUITY, [])
        if eq_cands:
            w, r, ex = _select_equity(eq_cands, request, eq_target)
            proposed.update(w); rationale.update(r); excluded.extend(ex)
        else:
            notes.append(f"No equity candidates for the chosen countries/style "
                         f"(equity target {eq_target:.0%}) — left unfunded.")

    # debt / cash / alternatives sleeves
    for sleeve_key, ac in (("fixed_income", AssetClass.FIXED_INCOME),
                           ("cash", AssetClass.CASH),
                           ("alternatives", AssetClass.ALTERNATIVES)):
        t = targets.get(sleeve_key, 0.0)
        if t <= 0:
            continue
        members = by_sleeve.get(ac, [])
        if not members:
            country_note = (
                f" No {sleeve_key} instruments are defined for "
                f"'{', '.join(request.countries)}' in the country universe. "
                f"Add them to COUNTRY_UNIVERSE_PATH to fund this sleeve."
                if request.countries and request.geography_mode != GeographyMode.GLOBAL
                else ""
            )
            notes.append(
                f"⚠️ {sleeve_key} sleeve (target {t:.0%}) is unfunded — no candidates in the "
                f"universe for this sleeve.{country_note}")
            continue
        w, r = _select_sleeve(members, t, request.weighting_method, sleeve_key)
        proposed.update(w); rationale.update(r)

    meta: Dict[str, Candidate] = {c.symbol.upper(): c for c in universe}
    guardrailed, corrections = guardrails.enforce(proposed, meta, request.constraints)
    rationale_u = {k.upper(): v for k, v in rationale.items()}
    positions, invested, cash = guardrails.finalise_positions(
        guardrailed, meta, request.invest_amount, rationale_u)

    metrics = common.compute_expected_metrics(positions, request.mandate, request.base_currency)
    feasibility = assess_feasibility(
        request, positions, [c.expected_return for c in universe])

    cp = ConstructedPortfolio(
        method="deterministic", weighting_method=request.weighting_method,
        style=request.style, countries=request.countries,
        positions=positions,
        sleeve_allocation=common.sleeve_allocation(positions),
        cap_allocation=common.cap_allocation(positions),
        country_allocation=common.country_allocation(positions),
        expected_metrics=metrics, feasibility=feasibility,
        excluded=excluded, guardrail_corrections=corrections,
        narrative=_narrative(request, positions, targets, corrections, notes, feasibility),
        total_invested=invested, cash_residual=cash,
    )
    _log.info("construct[deterministic]: %d position(s), invested=%.0f, cash=%.0f, "
              "corrections=%d, realistic=%s", len(positions), invested, cash,
              len(corrections), feasibility.realistic)
    return cp


def _narrative(request, positions, targets, corrections, notes, feasibility) -> str:
    tstr = ", ".join(f"{k} {v:.0%}" for k, v in targets.items() if v > 0)
    geo = (request.geography_mode.value if not request.countries
           else ", ".join(request.countries))
    bits = [
        f"Deterministic construction for a {request.mandate} mandate, {request.style.value} "
        f"style ({request.weighting_method.value}), geography: {geo}.",
        f"Sleeve targets: {tstr}.",
        f"Selected {len(positions)} holdings, investing {request.invest_amount:,.0f} "
        f"{request.base_currency}; equity names ranked by expected return.",
    ]
    if request.equity_pct is not None or request.debt_pct is not None:
        bits.append("Equity/debt split set from the investor's stated ratio (overrides the mandate).")
    if corrections:
        bits.append(f"{len(corrections)} guardrail correction(s) applied to honour the caps.")
    if feasibility and feasibility.message:
        bits.append("Return objective: " + feasibility.message)
    bits.extend(notes)
    return " ".join(bits)
