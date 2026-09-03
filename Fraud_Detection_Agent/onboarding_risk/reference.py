"""
Reference data for deterministic screening and scoring.

Everything here is **illustrative sample data** so the system runs end-to-end out of
the box. In production, point each list at a licensed / authoritative feed via the
*_PATH environment variables (see .env.example) — World-Check or Dow Jones for PEP and
adverse media, live OFAC / UN / EU consolidated lists for sanctions, and the current
FATF grey/black lists plus your internal country-risk matrix for jurisdictions. The
matching logic in `screening.py` does not change when you swap the data.

Each loader prefers a JSON file at the configured path and falls back to the bundled
sample. All matching downstream is normalized (case-folded, punctuation-stripped), so
the lists are stored as plain human-readable strings here.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List


# --------------------------------------------------------------------------- #
# Thresholds (config-driven; fixed at run time for reproducibility)
# --------------------------------------------------------------------------- #
def ubo_threshold_pct() -> float:
    """Effective-ownership % at/above which a natural person is treated as a UBO."""
    try:
        return float(os.getenv("UBO_THRESHOLD_PCT", "25.0"))
    except (TypeError, ValueError):
        return 25.0


# Layered-structure opacity thresholds (drive the 'complex structure' EDD trigger).
COMPLEX_LAYERING_DEPTH = 3       # ownership chain length (entities) at/above which it's complex
COMPLEX_ENTITY_COUNT = 4         # number of legal entities at/above which it's complex


# --------------------------------------------------------------------------- #
# Bundled sample lists (replace with licensed feeds in production)
# --------------------------------------------------------------------------- #
_SAMPLE_PEP: List[Dict] = [
    {"name": "Robert Mensah", "position": "Former Minister of Energy (Ghana)", "tier": "high"},
    {"name": "Elena Petrova", "position": "Regional Governor (CIS)", "tier": "high"},
    {"name": "Carlos Mereles", "position": "State-owned enterprise board member", "tier": "medium"},
    {"name": "Aisha Al-Rashid", "position": "Immediate family of a head of state", "tier": "high"},
]

_SAMPLE_SANCTIONS: List[Dict] = [
    {"name": "Viktor Sokolov", "program": "OFAC SDN (illustrative)", "list": "OFAC"},
    {"name": "Orion Trading FZE", "program": "EU Consolidated (illustrative)", "list": "EU"},
    {"name": "Dmitri Volkov", "program": "UN 1267 (illustrative)", "list": "UN"},
]

_SAMPLE_ADVERSE_MEDIA: List[Dict] = [
    {"name": "Marcus Lindqvist", "category": "fraud",
     "summary": "Named in an investigative report on cross-border invoice fraud (illustrative)."},
    {"name": "Sunrise Holdings Ltd", "category": "money_laundering",
     "summary": "Linked in press to a layering scheme via shell entities (illustrative)."},
    {"name": "Priya Nair", "category": "corruption",
     "summary": "Subject of an anti-corruption probe per local media (illustrative)."},
]

# Country / jurisdiction risk tiers: prohibited | high | (absent => standard).
# 'offshore' marks low-/no-tax secrecy jurisdictions that warrant extra scrutiny.
_SAMPLE_JURISDICTIONS: Dict[str, Dict] = {
    "iran": {"tier": "prohibited", "offshore": False},
    "north korea": {"tier": "prohibited", "offshore": False},
    "syria": {"tier": "prohibited", "offshore": False},
    "myanmar": {"tier": "high", "offshore": False},
    "panama": {"tier": "high", "offshore": True},
    "cayman islands": {"tier": "high", "offshore": True},
    "british virgin islands": {"tier": "high", "offshore": True},
    "bvi": {"tier": "high", "offshore": True},
    "seychelles": {"tier": "high", "offshore": True},
    "belize": {"tier": "high", "offshore": True},
    "marshall islands": {"tier": "high", "offshore": True},
    "jersey": {"tier": "high", "offshore": True},
    "guernsey": {"tier": "high", "offshore": True},
    "liechtenstein": {"tier": "high", "offshore": True},
    "lebanon": {"tier": "high", "offshore": False},
    "nigeria": {"tier": "high", "offshore": False},
}


def _load(path_env: str, fallback):
    """Load a JSON list/dict from the path in `path_env`, else return the fallback."""
    path = os.getenv(path_env)
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return fallback
    return fallback


def pep_list() -> List[Dict]:
    return _load("PEP_LIST_PATH", _SAMPLE_PEP)


def sanctions_list() -> List[Dict]:
    return _load("SANCTIONS_LIST_PATH", _SAMPLE_SANCTIONS)


def adverse_media_list() -> List[Dict]:
    return _load("ADVERSE_MEDIA_PATH", _SAMPLE_ADVERSE_MEDIA)


def high_risk_jurisdictions() -> Dict[str, Dict]:
    return _load("HIGH_RISK_JURISDICTIONS_PATH", _SAMPLE_JURISDICTIONS)
