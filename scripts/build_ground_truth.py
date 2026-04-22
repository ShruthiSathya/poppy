# scripts/build_ground_truth.py
"""
Ground truth from two sources:
  Positives → ChEMBL drug_indication API (max_phase=4, approved)
  Negatives → ClinicalTrials.gov (Phase 3 terminated, lack of efficacy)
              + ChEMBL Phase 3 never approved (fallback)
"""

import requests
import pandas as pd
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

Path("data/ground_truth").mkdir(parents=True, exist_ok=True)


# ─────────────────────────────────────────────────────────────
# SOURCE 1: CHEMBL — Positives
# ─────────────────────────────────────────────────────────────

def fetch_chembl_positives() -> list[dict]:
    base = "https://www.ebi.ac.uk/chembl/api/data/drug_indication"
    all_pairs = []
    offset = 0
    limit = 1000
    total = None

    log.info("Fetching approved drug-disease pairs from ChEMBL...")

    while True:
        try:
            resp = requests.get(base, params={
                "max_phase_for_ind": 4,
                "format": "json",
                "limit": limit,
                "offset": offset,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"ChEMBL failed at offset {offset}: {e}")
            break

        if total is None:
            total = data.get("page_meta", {}).get("total_count", 0)
            log.info(f"ChEMBL total approved indications: {total}")

        records = data.get("drug_indications", [])
        if not records:
            break

        for rec in records:
            chembl_id = rec.get("molecule_chembl_id", "")
            if not chembl_id.startswith("CHEMBL"):
                continue

            efo_id       = rec.get("efo_id") or ""
            mesh_id      = rec.get("mesh_id") or ""
            mesh_heading = rec.get("mesh_heading") or ""

            if efo_id:
                disease_id = efo_id
            elif mesh_id:
                disease_id = f"MESH:{mesh_id}"
            else:
                continue

            all_pairs.append({
                "drug_id":      chembl_id,
                "drug_name":    rec.get("molecule_pref_name") or "",
                "disease_id":   disease_id,
                "disease_name": mesh_heading,
                "efo_id":       efo_id,
                "mesh_heading": mesh_heading,
                "max_phase":    rec.get("max_phase_for_ind") or 4,
                "source":       "ChEMBL_approved",
                "label":        1,
            })

        offset += limit
        log.info(f"  {min(offset, total)}/{total} fetched — {len(all_pairs)} pairs collected")
        time.sleep(0.25)

        if offset >= total:
            break

    return all_pairs


# ─────────────────────────────────────────────────────────────
# RARE DISEASE FILTER
# ─────────────────────────────────────────────────────────────

RARE_DISEASE_KEYWORDS = [
    "gaucher", "fabry", "pompe", "niemann-pick", "krabbe",
    "mucopolysaccharid", "hunter syndrome", "hurler", "morquio",
    "maroteaux", "sanfilippo", "batten", "wolman",
    "cystinosis", "glycogen storage",
    "phenylketonuri", "pku", "maple syrup", "homocystin",
    "tyrosinemia", "methylmalonic", "propionic acid",
    "urea cycle", "hyperammonemia", "organic acid",
    "fatty acid oxidation", "carnitine deficiency",
    "pulmonary arterial hypertension",
    "duchenne", "spinal muscular atrophy", "huntington",
    "friedreich", "spinocerebellar", "charcot-marie",
    "myasthenia gravis", "lambert-eaton",
    "dravet", "lennox-gastaut", "tuberous sclerosis",
    "neurofibromatosis", "sturge-weber",
    "wilson disease", "menkes",
    "hemoglobinuria, paroxysmal", "paroxysmal nocturnal",
    "sickle cell", "thalassemia", "hemophilia",
    "hereditary angioedema", "thrombotic thrombocytopenic",
    "marfan", "ehlers-danlos", "osteogenesis imperfect",
    "cystic fibrosis", "alpha-1 antitrypsin",
    "acromegaly", "cushing syndrome", "addison",
    "congenital adrenal hyperplasia",
    "amyloidosis", "porphyria", "epidermolysis bullosa",
    "ichthyosis", "xeroderma pigmentosum",
    "short bowel", "primary hyperoxaluria",
    "autoimmune hepatitis", "primary biliary",
    "primary sclerosing cholangitis",
    "systemic mastocytosis", "myelofibrosis",
    "aplastic anemia", "fanconi",
    "transthyretin", "hereditary transthyretin",
]

def is_rare_disease(disease_name: str) -> bool:
    name_lower = disease_name.lower()
    return any(kw in name_lower for kw in RARE_DISEASE_KEYWORDS)


# ─────────────────────────────────────────────────────────────
# SOURCE 2: CLINICALTRIALS.GOV — Negatives
# ─────────────────────────────────────────────────────────────

def fetch_clinicaltrials_negatives() -> list[dict]:
    base = "https://clinicaltrials.gov/api/v2/studies"

    EFFICACY_FAIL = [
        "lack of efficacy", "insufficient efficacy",
        "failed to demonstrate", "did not meet primary endpoint",
        "did not meet the primary", "futility", "interim futility",
        "no significant efficacy", "no efficacy",
        "negative efficacy", "poor efficacy", "ineffective",
    ]
    NOT_EFFICACY = [
        "business", "funding", "sponsor decision", "administrative",
        "financial", "slow enrollment", "poor enrollment", "accrual",
        "safety concern", "adverse event", "toxicity", "covid", "pandemic",
    ]

    search_terms = [
        "gaucher disease", "fabry disease", "pompe disease",
        "pulmonary arterial hypertension", "duchenne muscular dystrophy",
        "spinal muscular atrophy", "cystic fibrosis", "huntington disease",
        "mucopolysaccharidosis", "phenylketonuria", "wilson disease",
        "hereditary angioedema", "amyloidosis", "tuberous sclerosis",
        "neurofibromatosis", "lysosomal storage disease",
        "sickle cell disease", "thalassemia", "hemophilia",
        "myasthenia gravis", "dravet syndrome",
    ]

    all_failures = []
    seen_nct = set()

    for term in search_terms:
        log.info(f"  ClinicalTrials searching: '{term}'")
        try:
            # KEY FIX: no 'fields' parameter — that's what caused 400 errors
            resp = requests.get(base, params={
                "query.cond":          term,
                "filter.overallStatus": "TERMINATED",
                "filter.phase":        "PHASE3",
                "pageSize":            100,
                "format":              "json",
            }, timeout=20)
            resp.raise_for_status()
        except Exception as e:
            log.warning(f"  Failed for '{term}': {e}")
            time.sleep(1)
            continue

        studies = resp.json().get("studies", [])
        log.info(f"    → {len(studies)} terminated Phase 3 studies found")

        for study in studies:
            proto  = study.get("protocolSection") or {}
            nct_id = ((proto.get("identificationModule") or {})
                      .get("nctId", ""))
            if not nct_id or nct_id in seen_nct:
                continue
            seen_nct.add(nct_id)

            why_raw = ((proto.get("statusModule") or {})
                       .get("whyStopped") or "")
            why = why_raw.lower().strip()
            if not why:
                continue

            is_efficacy = any(kw in why for kw in EFFICACY_FAIL)
            is_other    = any(kw in why for kw in NOT_EFFICACY)
            if not is_efficacy or is_other:
                continue

            conditions    = ((proto.get("conditionsModule") or {})
                             .get("conditions") or [])
            interventions = ((proto.get("armsInterventionsModule") or {})
                             .get("interventions") or [])

            for iv in interventions:
                if iv.get("type") == "DRUG":
                    all_failures.append({
                        "nct_id":           nct_id,
                        "drug_name_raw":    iv.get("name", ""),
                        "disease_name_raw": "; ".join(conditions),
                        "why_stopped":      why_raw,
                    })

        time.sleep(0.4)

    log.info(f"Found {len(all_failures)} efficacy failure records from ClinicalTrials")
    return all_failures


def resolve_name_to_chembl(drug_name: str) -> str:
    """Resolves a free-text drug name to ChEMBL ID. Returns '' if not found."""
    base = "https://www.ebi.ac.uk/chembl/api/data/molecule"
    for param in [
        {"pref_name__iexact": drug_name},
        {"molecule_synonyms__synonym__iexact": drug_name},
    ]:
        try:
            resp = requests.get(base,
                                params={**param, "format": "json", "limit": 1},
                                timeout=10)
            if resp.status_code == 200:
                mols = resp.json().get("molecules", [])
                if mols:
                    return mols[0]["molecule_chembl_id"]
        except Exception:
            pass
        time.sleep(0.2)
    return ""


def build_negatives_from_ct(failures: list[dict]) -> list[dict]:
    negatives = []
    cache = {}
    seen  = set()

    BIOLOGIC_HINTS = ["mab", "umab", "zumab", "ximab",
                      "alfa", "beta", "factor viii", "insulin", "enzyme"]

    for f in failures:
        name = f["drug_name_raw"].strip()
        if not name:
            continue
        if any(h in name.lower() for h in BIOLOGIC_HINTS):
            continue

        if name not in cache:
            log.info(f"  Resolving CT drug: '{name}'")
            cache[name] = resolve_name_to_chembl(name)

        chembl_id = cache[name]
        if not chembl_id:
            continue

        key = f"{chembl_id}|{f['disease_name_raw']}"
        if key in seen:
            continue
        seen.add(key)

        negatives.append({
            "drug_id":      chembl_id,
            "drug_name":    name,
            "disease_id":   "",           # bio person fills ORPHA: ID
            "disease_name": f["disease_name_raw"],
            "nct_id":       f["nct_id"],
            "why_stopped":  f["why_stopped"],
            "source":       "ClinicalTrials_Phase3_terminated",
            "label":        0,
        })

    return negatives


# ─────────────────────────────────────────────────────────────
# SOURCE 3: CHEMBL Phase 3 never approved — fallback negatives
# ─────────────────────────────────────────────────────────────

def fetch_chembl_phase3_negatives() -> list[dict]:
    """
    Pairs that reached Phase 3 but never Phase 4 for rare diseases.
    Solid computational negatives — tested but never approved.
    """
    base = "https://www.ebi.ac.uk/chembl/api/data/drug_indication"
    all_pairs = []
    offset = 0
    limit  = 1000
    total  = None

    log.info("Fetching Phase 3 (non-approved) rare disease pairs from ChEMBL...")

    while True:
        try:
            resp = requests.get(base, params={
                "max_phase_for_ind": 3,
                "format": "json",
                "limit":  limit,
                "offset": offset,
            }, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.error(f"ChEMBL Phase3 fetch failed at offset {offset}: {e}")
            break

        if total is None:
            total = data.get("page_meta", {}).get("total_count", 0)
            log.info(f"ChEMBL Phase 3 total: {total}")

        records = data.get("drug_indications", [])
        if not records:
            break

        for rec in records:
            chembl_id    = rec.get("molecule_chembl_id", "")
            mesh_heading = rec.get("mesh_heading") or ""
            efo_id       = rec.get("efo_id") or ""
            mesh_id      = rec.get("mesh_id") or ""

            if not chembl_id.startswith("CHEMBL"):
                continue
            if not is_rare_disease(mesh_heading):
                continue

            disease_id = efo_id if efo_id else (f"MESH:{mesh_id}" if mesh_id else "")
            if not disease_id:
                continue

            all_pairs.append({
                "drug_id":      chembl_id,
                "drug_name":    rec.get("molecule_pref_name") or "",
                "disease_id":   disease_id,
                "disease_name": mesh_heading,
                "nct_id":       "",
                "why_stopped":  "Max phase 3 — never approved",
                "source":       "ChEMBL_phase3_not_approved",
                "label":        0,
            })

        offset += limit
        log.info(f"  {min(offset, total)}/{total} — {len(all_pairs)} rare phase-3 pairs")
        time.sleep(0.25)

        if offset >= total:
            break

    return all_pairs


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":

    # ── POSITIVES ─────────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE A: ChEMBL approved drug-disease pairs")
    log.info("=" * 60)

    all_approved = fetch_chembl_positives()
    log.info(f"Total approved pairs fetched: {len(all_approved)}")

    rare_pairs = [p for p in all_approved if is_rare_disease(p["disease_name"])]
    log.info(f"After rare disease filter: {len(rare_pairs)} pairs")

    pos_df = pd.DataFrame(rare_pairs)
    if not pos_df.empty:
        pos_df.drop_duplicates(subset=["drug_id", "disease_id"], inplace=True)

    pos_df.to_csv("data/ground_truth/positives_raw.csv", index=False)
    log.info(f"Saved {len(pos_df)} positives → data/ground_truth/positives_raw.csv")

    # ── NEGATIVES ─────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("PHASE B: Negatives from ClinicalTrials + ChEMBL Phase 3")
    log.info("=" * 60)

    # Source 1: ClinicalTrials efficacy failures
    failures     = fetch_clinicaltrials_negatives()
    ct_negatives = build_negatives_from_ct(failures)
    log.info(f"ClinicalTrials negatives resolved: {len(ct_negatives)}")

    # Source 2: ChEMBL Phase 3 never approved
    chembl_negatives = fetch_chembl_phase3_negatives()
    log.info(f"ChEMBL Phase 3 negatives: {len(chembl_negatives)}")

    # Combine
    all_negatives = ct_negatives + chembl_negatives
    neg_df = pd.DataFrame(all_negatives)

    if not neg_df.empty:
        neg_df.drop_duplicates(subset=["drug_id", "disease_id"], inplace=True)
        # Remove any pair that also appears in positives
        pos_keys = set(zip(pos_df["drug_id"], pos_df["disease_id"]))
        neg_df = neg_df[
            ~neg_df.apply(
                lambda r: (r["drug_id"], r["disease_id"]) in pos_keys, axis=1
            )
        ]

    neg_df.to_csv("data/ground_truth/negatives_raw.csv", index=False)
    log.info(f"Saved {len(neg_df)} negatives → data/ground_truth/negatives_raw.csv")

    # ── SUMMARY ───────────────────────────────────────────────
    log.info(f"""
DONE.
  Positives : {len(pos_df)}  →  data/ground_truth/positives_raw.csv
  Negatives : {len(neg_df)}  →  data/ground_truth/negatives_raw.csv
    """)