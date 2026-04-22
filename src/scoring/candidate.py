# src/scoring/candidate.py
from dataclasses import dataclass, field
from typing import Optional
import datetime

@dataclass
class LayerScores:
    target_overlap_jaccard: Optional[float] = None
    network_proximity: Optional[float] = None       # Lower = better (hops)
    transcriptomic_reversal: Optional[float] = None # KS statistic, negative = good
    kg_embedding_cosine: Optional[float] = None
    admet_composite: Optional[float] = None
    literature_cooccurrence: Optional[float] = None
    clinical_trial_evidence: Optional[int] = None   # 0-5 scale
    business_ip: Optional[int] = None               # 1-5
    business_regulatory: Optional[int] = None
    business_market: Optional[int] = None
    business_manufacturing: Optional[int] = None
    business_clinical_adoption: Optional[int] = None
    business_speed_to_revenue: Optional[int] = None
    chirality_divergence: Optional[float] = None
    pgx_metabolizer_risk: Optional[float] = None

@dataclass
class Flags:
    # Hard disqualifiers — any True = candidate dropped
    existing_patent_on_indication: bool = False
    herg_risk_high: bool = False
    faers_ror_critical: bool = False         # ROR > 3 for serious events
    bioavailability_insufficient: bool = False  # < 20%
    lipinski_violations: int = 0
    # Warnings — flagged but not disqualifying
    ddi_risk_narrow_index: bool = False
    polymorph_risk: bool = False
    pediatric_formulation_needed: bool = False
    pgx_poor_metabolizer_risk_high: bool = False
    south_asian_founder_variant_specific: bool = False

@dataclass
class CandidatePair:
    drug_id: str                    # ChEMBL ID e.g. CHEMBL192
    drug_name: str
    disease_id: str                 # OMIM or Orphanet ID
    disease_name: str
    scores: LayerScores = field(default_factory=LayerScores)
    flags: Flags = field(default_factory=Flags)
    composite_score: Optional[float] = None
    business_total: Optional[int] = None    # /30
    data_sources: dict = field(default_factory=dict)  # source → version → timestamp
    created_at: datetime.datetime = field(default_factory=datetime.datetime.utcnow)
    notes: str = ""