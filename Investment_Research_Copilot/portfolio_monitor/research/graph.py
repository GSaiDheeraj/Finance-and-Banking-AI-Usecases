"""
The country-research LangGraph: one subgraph per country, fanned out via `Send`.

Two graphs, nested:

* The COUNTRY subgraph (`_build_country_subgraph`) has 6 parallel analysis nodes
  (geopolitical, sectoral, bond risk, FD rates, market risk, growth outlook) — all read the same `country`
  from state and fan into a single `merge` node once all 5 finish. `merge` computes one
  deterministic `overall_risk_tone` for the country from the merged findings (NOT five
  separately-guessed tones from five LLM calls that would then need reconciling) and returns
  the finished `CountryResearchProfile`.
* The OUTER graph has one node, `analyze_country`, which *is* the compiled country subgraph.
  A conditional edge from `START` returns one `Send("analyze_country", {"country": c})` per
  requested country — LangGraph's built-in map-reduce primitive: each `Send` runs the target
  node as an independent, concurrent invocation, so no manual thread pool or `asyncio.gather`
  is needed to fan out over N countries. Each invocation's `{"country": {...profile}}` output
  merges into the outer `profiles` dict via the `operator.or_` reducer on that state key.

Fails soft end-to-end: `run_country_research` is the one public entry point everything else
in this codebase calls, and it never raises — a wiring problem, a network outage, or an LLM
gateway being down all just mean fewer (or zero) profiles come back, exactly like the existing
`osint/scan.py::run_risk_scan`.
"""
from __future__ import annotations

import operator
from typing import Annotated, Dict, List, Optional, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from ..logconf import get_logger
from ..schemas import CountryResearchProfile
from . import nodes

_log = get_logger()


class _CountryState(TypedDict, total=False):
    country: str
    findings: Annotated[list, operator.add]
    data_gaps: Annotated[list, operator.add]
    fd_rate_pct: Optional[float]
    fd_rate_basis: str
    bond_credit_rating: Optional[str]
    bond_credit_rating_basis: str
    growth_outlook: str
    growth_sectors: List[str]
    # Shared with _ResearchState below — this is how a compiled subgraph hands a value back
    # up to its parent graph: only keys declared on BOTH schemas cross that boundary.
    profiles: Annotated[Dict[str, CountryResearchProfile], operator.or_]


def _merge_country(state: _CountryState) -> Dict[str, Dict[str, CountryResearchProfile]]:
    country = state["country"]
    findings = state.get("findings", [])
    n_high = sum(1 for f in findings if (f.severity or "").lower() == "high")
    n_medium = sum(1 for f in findings if (f.severity or "").lower() == "medium")
    tone = ("stressed" if n_high >= 3 else
            "elevated" if (n_high >= 1 or n_medium >= 2) else "normal")
    profile = CountryResearchProfile(
        country=country,
        findings=findings,
        overall_risk_tone=tone,
        fd_rate_pct=state.get("fd_rate_pct"),
        fd_rate_basis=state.get("fd_rate_basis", ""),
        bond_credit_rating=state.get("bond_credit_rating"),
        bond_credit_rating_basis=state.get("bond_credit_rating_basis", ""),
        growth_outlook=state.get("growth_outlook", "unknown"),
        growth_sectors=state.get("growth_sectors", []),
        data_gaps=state.get("data_gaps", []),
    )
    return {"profiles": {country: profile}}


def _build_country_subgraph():
    g = StateGraph(_CountryState)
    g.add_node("geopolitical", nodes.analyze_geopolitical)
    g.add_node("sectoral", nodes.analyze_sectoral)
    g.add_node("bond_risk", nodes.analyze_bond_risk)
    g.add_node("fd_rates", nodes.analyze_fd_rates)
    g.add_node("market_risk", nodes.analyze_market_risk)
    g.add_node("growth", nodes.analyze_growth)
    g.add_node("merge", _merge_country)
    for node_name in ("geopolitical", "sectoral", "bond_risk", "fd_rates", "market_risk",
                     "growth"):
        g.add_edge(START, node_name)
        g.add_edge(node_name, "merge")
    g.add_edge("merge", END)
    return g.compile()


class _ResearchState(TypedDict, total=False):
    countries: List[str]
    profiles: Annotated[Dict[str, CountryResearchProfile], operator.or_]


def _dispatch(state: _ResearchState) -> List[Send]:
    return [Send("analyze_country", {"country": c}) for c in state["countries"]]


_GRAPH = None


def _graph():
    global _GRAPH
    if _GRAPH is None:
        g = StateGraph(_ResearchState)
        g.add_node("analyze_country", _build_country_subgraph())
        g.add_conditional_edges(START, _dispatch, ["analyze_country"])
        g.add_edge("analyze_country", END)
        _GRAPH = g.compile()
    return _GRAPH


# Per-process cache (country -> profile), keyed by a normalized lowercase country string. No
# TTL — the exact convention `marketdata.py` already uses for quotes/history.
_PROFILE_CACHE: Dict[str, CountryResearchProfile] = {}


def run_country_research(countries: List[str]) -> Dict[str, CountryResearchProfile]:
    """Research every country in `countries` (deduped, cached per process). Never raises."""
    keys = {c.strip().lower() for c in countries if c and c.strip()}
    wanted = sorted(k for k in keys if k not in _PROFILE_CACHE)
    if wanted:
        try:
            result = _graph().invoke({"countries": wanted, "profiles": {}})
            _PROFILE_CACHE.update(result.get("profiles", {}))
        except Exception as e:
            _log.warning("research: country-research graph failed (%s) — skipped.", e)
    return {k: _PROFILE_CACHE[k] for k in keys if k in _PROFILE_CACHE}
