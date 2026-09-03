"""
Schema for HNI/UHNI customer-onboarding risk scoring.

Design notes
------------
* `Party` is **flat** and carries a stable `party_id`. Natural persons and legal
  entities are both parties; their connections live in `OwnershipEdge` (who owns whom,
  with what %) and `Relationship` (who plays what role: settlor, trustee, signatory…).
  This makes a layered legal structure a *graph*, not a single applicant record.
* The LLM only fills the *extraction* records (Party / OwnershipEdge / Relationship /
  SourceOfWealth / IdentityCheck). Every *decision-bearing* record below the divider
  (UBO, WatchlistHit, RiskFactorResult, RiskScorecard) is produced by deterministic
  Python so the risk rating is reproducible and auditable.
* Every fact keeps its source `page` for traceability.
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Controlled vocabularies
# --------------------------------------------------------------------------- #
class PartyType(str, Enum):
    INDIVIDUAL = "individual"
    TRUST = "trust"
    FOUNDATION = "foundation"
    HOLDING_COMPANY = "holding_company"
    OPERATING_COMPANY = "operating_company"
    SPV = "spv"
    PARTNERSHIP = "partnership"
    FUND = "fund"
    NOMINEE = "nominee"
    OTHER = "other"


class RoleType(str, Enum):
    APPLICANT = "applicant"          # the primary onboarding subject (often the structure)
    SETTLOR = "settlor"              # trust
    TRUSTEE = "trustee"
    PROTECTOR = "protector"
    BENEFICIARY = "beneficiary"
    DIRECTOR = "director"
    SHAREHOLDER = "shareholder"
    SIGNATORY = "signatory"
    SPOUSE = "spouse"
    NOMINEE = "nominee"
    AUTHORIZED_PERSON = "authorized_person"
    PARTNER = "partner"
    UBO = "ubo"


class InterestType(str, Enum):
    EQUITY = "equity"
    VOTING = "voting"
    BENEFICIAL = "beneficial"
    CONTROL = "control"


# Roles that confer control even without crossing the ownership threshold.
CONTROL_ROLES = {
    RoleType.SETTLOR, RoleType.TRUSTEE, RoleType.PROTECTOR,
    RoleType.DIRECTOR, RoleType.SIGNATORY,
}


class RiskFactorType(str, Enum):
    SANCTIONS = "sanctions"                       # hard stop
    PEP = "pep"
    ADVERSE_MEDIA = "adverse_media"
    HIGH_RISK_GEOGRAPHY = "high_risk_geography"
    OPAQUE_SOURCE_OF_WEALTH = "opaque_source_of_wealth"
    COMPLEX_STRUCTURE = "complex_structure"
    LAYERING = "layering"
    NOMINEE_ARRANGEMENT = "nominee_arrangement"
    BEARER_SHARES = "bearer_shares"
    ID_VERIFICATION_GAP = "id_verification_gap"
    OFFSHORE_JURISDICTION = "offshore_jurisdiction"


class RiskBand(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    PROHIBITED = "prohibited"


class OnboardingDecision(str, Enum):
    APPROVE_STANDARD_CDD = "approve_standard_cdd"
    APPROVE_WITH_EDD = "approve_with_edd"
    ESCALATE_MLRO = "escalate_mlro"
    DECLINE = "decline"


# --------------------------------------------------------------------------- #
# Extraction records (filled by the grounded LLM extractors)
# --------------------------------------------------------------------------- #
class Party(BaseModel):
    party_id: str = Field(..., description="Stable id within this case, e.g. 'P1'.")
    name: str
    party_type: PartyType = PartyType.OTHER
    country: Optional[str] = Field(
        None, description="Residence (person) or country of incorporation (entity)."
    )
    nationality: Optional[str] = None
    date_of_birth_or_incorporation: Optional[str] = None
    identifiers: Dict[str, str] = Field(
        default_factory=dict, description="e.g. {'passport': '...', 'reg_no': '...'}"
    )
    declared_pep: Optional[bool] = Field(
        None, description="PEP status as declared on the form (screening confirms independently)."
    )
    page: Optional[int] = None
    source_snippet: Optional[str] = None


class OwnershipEdge(BaseModel):
    """`owner_id` holds `percentage`% of `owned_id`."""
    owner_id: str
    owned_id: str
    percentage: Optional[float] = None       # None when only control (not %) is disclosed
    interest_type: InterestType = InterestType.EQUITY
    page: Optional[int] = None
    source_snippet: Optional[str] = None


class Relationship(BaseModel):
    party_id: str
    role: RoleType
    related_to: Optional[str] = Field(
        None, description="party_id of the structure/entity the role is held in."
    )
    page: Optional[int] = None


class SourceOfWealth(BaseModel):
    party_id: str
    declared_source: str = ""                # e.g. 'sale of business', 'inheritance', 'unknown'
    narrative: Optional[str] = None
    corroborating_evidence_present: Optional[bool] = None
    page: Optional[int] = None


class IdentityCheck(BaseModel):
    party_id: str
    document_type: Optional[str] = None      # passport, national_id, certificate_of_incorporation
    document_number: Optional[str] = None
    verified: Optional[bool] = None
    gaps: List[str] = Field(default_factory=list)
    page: Optional[int] = None


class CaseMetadata(BaseModel):
    applicant_name: Optional[str] = None
    applicant_party_id: Optional[str] = None
    product: Optional[str] = None            # e.g. 'discretionary mandate', 'lombard loan'
    relationship_purpose: Optional[str] = None


# --------------------------------------------------------------------------- #
# Deterministic outputs (no LLM — reproducible & auditable)
# --------------------------------------------------------------------------- #
class UBO(BaseModel):
    party_id: str
    name: str
    effective_ownership_pct: Optional[float] = None
    basis: str = "ownership"                 # 'ownership' | 'control' | 'senior_managing_official'
    control_roles: List[str] = Field(default_factory=list)
    paths: List[str] = Field(
        default_factory=list, description="Human-readable ownership chains to the applicant."
    )
    pages: List[int] = Field(default_factory=list)


class OwnershipGraphResult(BaseModel):
    applicant_party_id: Optional[str] = None
    ubos: List[UBO] = Field(default_factory=list)
    num_entities: int = 0                    # legal entities (non-individuals) in the structure
    num_parties: int = 0
    max_layering_depth: int = 0              # longest ownership chain (entities) to the applicant
    has_nominee: bool = False
    has_bearer_shares: bool = False
    has_circular_ownership: bool = False
    notes: List[str] = Field(default_factory=list)


class WatchlistHit(BaseModel):
    party_id: str
    party_name: str
    list_type: str                           # 'pep' | 'sanctions' | 'adverse_media'
    matched_name: str
    match_score: float                       # 0..1 normalized similarity
    source_list: Optional[str] = None        # e.g. 'OFAC', 'World-Check'
    details: Optional[str] = None
    tier: Optional[str] = None               # for PEP: low|medium|high


class GeographyHit(BaseModel):
    party_id: str
    party_name: str
    country: str
    tier: str                                # 'high' | 'prohibited'
    offshore: bool = False


class RiskFactorResult(BaseModel):
    factor: RiskFactorType
    present: bool
    severity: str = "medium"                 # 'low' | 'medium' | 'high'
    weight: float = 0.0
    contribution: float = 0.0                # weight * severity multiplier (0 when absent)
    evidence: List[str] = Field(default_factory=list)
    pages: List[int] = Field(default_factory=list)


class RiskScorecard(BaseModel):
    factors: List[RiskFactorResult] = Field(default_factory=list)
    raw_score: float = 0.0                   # sum of contributions
    normalized_score: float = 0.0            # 0..100
    band: RiskBand = RiskBand.LOW
    edd_required: bool = False
    triggers: List[RiskFactorType] = Field(default_factory=list)
    decision: OnboardingDecision = OnboardingDecision.APPROVE_STANDARD_CDD
    hard_stop_reason: Optional[str] = None   # set when sanctions / prohibited jurisdiction


class EDDRationale(BaseModel):
    """LLM-written narrative grounded in the deterministic scorecard (never re-rates)."""
    risk_summary: str = ""
    key_drivers: List[str] = Field(default_factory=list)
    edd_measures: List[str] = Field(default_factory=list)   # what to do before/at onboarding
    information_required: List[str] = Field(default_factory=list)
    watch_items: List[str] = Field(default_factory=list)


class OnboardingAssessment(BaseModel):
    """Top-level result for one onboarding case."""
    case: CaseMetadata = Field(default_factory=CaseMetadata)
    parties: List[Party] = Field(default_factory=list)
    edges: List[OwnershipEdge] = Field(default_factory=list)
    relationships: List[Relationship] = Field(default_factory=list)
    source_of_wealth: List[SourceOfWealth] = Field(default_factory=list)
    identity_checks: List[IdentityCheck] = Field(default_factory=list)
    ownership: OwnershipGraphResult = Field(default_factory=OwnershipGraphResult)
    watchlist_hits: List[WatchlistHit] = Field(default_factory=list)
    geography_hits: List[GeographyHit] = Field(default_factory=list)
    scorecard: RiskScorecard = Field(default_factory=RiskScorecard)
    edd_rationale: EDDRationale = Field(default_factory=EDDRationale)
    # Attached when the case was built from the name-driven OSINT flow (see osint/).
    profile_360: Optional["Profile360"] = None


# --------------------------------------------------------------------------- #
# Name-driven OSINT 360° profiling
# --------------------------------------------------------------------------- #
# These records support the second input path: instead of a pack of PDFs, the user
# gives a person's name plus a few details, and the agent gathers their PUBLIC
# footprint from free web sources. The collected evidence is synthesized into a
# Profile360, which then feeds the SAME deterministic scoring/EDD engine.
class OsintDimension(str, Enum):
    """The angles we research a person from, for a full 360° view."""
    BIOGRAPHY = "biography"                 # who they are
    PROFESSIONAL = "professional"           # jobs, roles, directorships
    COMPANY_PERFORMANCE = "company_performance"  # how their companies are doing
    ADVERSE_MEDIA = "adverse_media"         # negative news, investigations, lawsuits
    PEP = "pep"                             # political connections / public office
    SOCIAL_BEHAVIORAL = "social_behavioral"  # public posts, interviews, conduct
    RELATIONSHIPS = "relationships"         # spouse/partner, family, associates
    WEALTH = "wealth"                       # net worth, where the money comes from


class SubjectSeed(BaseModel):
    """The few starting details the user provides about the person."""
    full_name: str
    aliases: List[str] = Field(default_factory=list)
    approx_age: Optional[str] = None
    date_of_birth: Optional[str] = None
    location: Optional[str] = None          # city / country of residence
    nationality: Optional[str] = None
    education: List[str] = Field(default_factory=list)
    work_history: List[str] = Field(default_factory=list)
    known_companies: List[str] = Field(default_factory=list)
    relationship_purpose: Optional[str] = None
    search_region: Optional[str] = Field(
        None, description="2-letter country code to bias web search, e.g. 'in', 'us'."
    )


class EvidenceItem(BaseModel):
    """One piece of public information the agent found on the web."""
    dimension: OsintDimension
    query: str                              # the search that found it
    title: Optional[str] = None
    url: Optional[str] = None
    snippet: Optional[str] = None
    source: str = "web"                     # 'web' | 'news' | 'wikipedia' | 'page'
    published: Optional[str] = None


class CompanyAffiliation(BaseModel):
    """A company the person is connected to, and how that company is doing."""
    company: str
    role: Optional[str] = None              # director / partner / founder / shareholder
    status: Optional[str] = None            # active / dissolved / unknown
    performance_summary: Optional[str] = None
    evidence_urls: List[str] = Field(default_factory=list)


class AdverseFinding(BaseModel):
    """A single piece of negative information found about the person."""
    category: str                           # fraud / litigation / regulatory / reputational / other
    summary: str
    severity: str = "medium"                # low | medium | high
    url: Optional[str] = None


class RelationshipFinding(BaseModel):
    """A person connected to the subject (spouse, family, business associate)."""
    name: str
    relation: str                           # spouse / partner / parent / child / associate
    details: Optional[str] = None
    is_pep: Optional[bool] = None
    evidence_urls: List[str] = Field(default_factory=list)


class BehavioralProfile(BaseModel):
    """A plain-language read of how the person presents in public."""
    summary: str = ""
    traits: List[str] = Field(default_factory=list)
    risk_indicators: List[str] = Field(default_factory=list)   # things that warrant caution
    public_sentiment: Optional[str] = None  # positive / mixed / negative
    confidence: str = "low"                 # how much to trust this (low/medium/high)


class Profile360(BaseModel):
    """The assembled public picture of the person, grounded in the collected evidence."""
    seed: SubjectSeed
    disambiguation_confidence: str = "low"  # are we sure it's the same person?
    biography: str = ""
    professional_summary: str = ""
    company_affiliations: List[CompanyAffiliation] = Field(default_factory=list)
    adverse_media: List[AdverseFinding] = Field(default_factory=list)
    pep_indicators: List[str] = Field(default_factory=list)
    wealth_summary: Optional[str] = None
    source_of_wealth_signal: Optional[str] = None   # what public info suggests funds came from
    source_of_wealth_corroborated: Optional[bool] = None
    relationships: List[RelationshipFinding] = Field(default_factory=list)
    behavioral: BehavioralProfile = Field(default_factory=BehavioralProfile)
    evidence: List[EvidenceItem] = Field(default_factory=list)
    citations: List[str] = Field(default_factory=list)
    data_gaps: List[str] = Field(default_factory=list)     # what we could NOT find


OnboardingAssessment.model_rebuild()
