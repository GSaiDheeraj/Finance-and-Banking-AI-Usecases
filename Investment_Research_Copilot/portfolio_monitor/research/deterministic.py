"""
Deterministic interpretation of country-research findings.

NO LLM. Mirrors the exact credibility-discount pattern already used for the emerging-risk news
scan (`alerts.py` / `scoring.py`): a finding's severity is read but never trusted at face
value, and only a tone + high-severity-count combination crosses the bar for a hard exclusion
— a single finding, however severe, never excludes a country on its own.
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

from ..reference import normalize_country
from ..schemas import CountryResearchProfile

# Simplified S&P/Moody's-style bucket scale — sufficient for a country-level proxy comparison,
# not a securities-grade rating system.
_RATING_RANK = {
    "AAA": 10, "AA": 9, "A": 8, "BBB": 7, "BB": 6, "B": 5, "CCC": 4, "CC": 3, "C": 2, "D": 1,
}


def _rating_rank(rating: Optional[str]) -> Optional[int]:
    if not rating:
        return None
    key = rating.strip().upper().split("+")[0].split("-")[0]   # AA+ / BBB- -> AA / BBB
    return _RATING_RANK.get(key)


def _severity_counts(profile: CountryResearchProfile) -> tuple[int, int]:
    n_high = sum(1 for f in profile.findings if (f.severity or "").lower() == "high")
    n_medium = sum(1 for f in profile.findings if (f.severity or "").lower() == "medium")
    return n_high, n_medium


def country_risk_penalty(profile: CountryResearchProfile) -> float:
    """A small, bounded penalty subtracted from a country's candidates' ranking return.

    Mirrors `scoring.py`'s `SEVERITY_MULT` idea: capped well below what a hard exclusion would
    do, so an "elevated" reading nudges ranking without ever dominating expected-return-based
    selection outright.
    """
    n_high, n_medium = _severity_counts(profile)
    tone = (profile.overall_risk_tone or "normal").lower()
    if tone == "stressed" or n_high >= 2:
        return 0.03
    if tone == "elevated" or n_high >= 1 or n_medium >= 2:
        return 0.015
    return 0.0


def should_exclude_country(profile: CountryResearchProfile) -> bool:
    """Hard exclusion — only when the read is acutely bad, the same bar `alerts.py`/
    `scoring.py` already use before letting emerging-risk news override anything harder."""
    n_high, _ = _severity_counts(profile)
    return (profile.overall_risk_tone or "normal").lower() == "stressed" and n_high >= 2


def meets_credit_floor(profile: CountryResearchProfile, min_rating: str) -> bool:
    """True if the country's researched bond-credit-rating proxy is at/above `min_rating`.

    Unknown data never fails the floor (same policy as universe.py's ratio constraints) —
    only a rating that was actually read and falls below the floor rejects.
    """
    observed = _rating_rank(profile.bond_credit_rating)
    floor = _rating_rank(min_rating)
    if observed is None or floor is None:
        return True
    return observed >= floor


def _sector_is_flagged(sector: Optional[str], profile: CountryResearchProfile) -> bool:
    if not sector or not profile.growth_sectors:
        return False
    needle = sector.strip().lower()
    return any(needle in g.lower() or g.lower() in needle for g in profile.growth_sectors)


def growth_potential(
    sector: Optional[str],
    country: Optional[str],
    country_research: Optional[Dict[str, CountryResearchProfile]],
) -> Tuple[str, str]:
    """Blend a country's researched growth outlook with whether this position's sector is one
    of that country's flagged growth sectors. Never trusts a single signal alone for "high" —
    same discount-not-trust posture as `country_risk_penalty`/`should_exclude_country` above.

    Returns (rating, rationale) with rating in low|medium|high|unknown — "unknown" only when
    there's no researched profile for the country (research off, failed soft, or not requested).
    """
    key = normalize_country(country) if country else None
    profile = country_research.get(key) if (country_research and key) else None
    if profile is None:
        return "unknown", "No country growth research available for this position."

    outlook = (profile.growth_outlook or "unknown").lower()
    flagged = _sector_is_flagged(sector, profile)

    if flagged and outlook in ("moderate", "high"):
        return "high", f"{sector} is a flagged growth sector in {profile.country} ({outlook} outlook)."
    if flagged:
        return "medium", (f"{sector} is a flagged growth sector in {profile.country}, "
                          "though the overall outlook there is subdued.")
    if outlook == "high":
        return "medium", f"{profile.country}'s overall growth outlook is high."
    if outlook in ("low", "moderate"):
        return "low", f"{profile.country}'s overall growth outlook is {outlook}."
    return "unknown", f"No growth-outlook read available for {profile.country}."
