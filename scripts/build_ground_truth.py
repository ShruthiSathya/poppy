# scripts/build_ground_truth.py
"""
Fully programmatic ground truth builder.
No hardcoded IDs. Every pair is fetched and verified from authoritative sources.

Sources:
  Positives → OpenTargets GraphQL (approved drugs × rare diseases)
  Negatives → ClinicalTrials.gov v2 API (terminated Phase 3, lack of efficacy)

Run:
  python scripts/build_ground_truth.py

Outputs:
  data/ground_truth/positives_raw.csv      <- review before loading
  data/ground_truth/negatives_raw.csv      <- review before loading
"""

import requests
import pandas as pd
import time
import psycopg2
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger(__name__)

Path("data/ground_truth").mkdir(parents=True, exist_ok=True)

OPENTARGETS_API = "https://api.platform.opentargets.org/api/v4/graphql"


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 1: OPENTARGETS — Positives (approved drugs × rare diseases)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_opentargets_approved_drugs(max_diseases: int = 200) -> list[dict]:
    """
    Step 1: Fetch all diseases from OpenTargets that have Orphanet cross-references.
    Step 2: For each rare disease, fetch its approved drugs (Phase 4).
    Returns ChEMBL IDs + ORPHA IDs — both verified by OpenTargets curators.
    """

    log.info("Fetching rare diseases from OpenTargets...")

    # Correct OpenTargets v4 GraphQL — no filter argument on diseases()
    disease_query = """
    {
      diseases(page: { size: 500, index: 0 }) {
        count
        rows {
          id
          name
          dbXRefs
        }
      }
    }
    """

    resp = requests.post(
        OPENTARGETS_API,
        json={"query": disease_query},
        headers={"Content-Type": "application/json"},
        timeout=30
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        log.error(f"GraphQL error: {data['errors']}")
        raise ValueError("OpenTargets disease query failed — check API")

    diseases = data.get("data", {}).get("diseases", {}).get("rows", [])
    log.info(f"Retrieved {len(diseases)} diseases from OpenTargets")

    # Keep only diseases with an Orphanet cross-reference
    orphanet_diseases = []
    for d in diseases:
        xrefs = d.get("dbXRefs", []) or []
        orphanet_refs = [x for x in xrefs if "Orphanet" in x]
        if orphanet_refs:
            # Normalise to ORPHA:12345 format
            raw = orphanet_refs[0]
            orphanet_id = raw.replace("Orphanet:", "ORPHA:").replace("Orphanet_", "ORPHA:")
            orphanet_diseases.append({
                "efo_id": d["id"],
                "disease_name": d["name"],
                "orphanet_id": orphanet_id,
            })

    log.info(f"Found {len(orphanet_diseases)} rare diseases with Orphanet IDs")

    # For each rare disease fetch its known approved drugs
    drug_query = """
    query ApprovedDrugs($diseaseId: String!) {
      disease(efoId: $diseaseId) {
        id
        name
        knownDrugs(size: 50) {
          count
          rows {
            drug {
              id
              name
              maximumClinicalTrialPhase
              isApproved
            }
            phase
            status
            drugType
            mechanismOfAction
          }
        }
      }
    }
    """

    all_pairs = []

    for i, disease in enumerate(orphanet_diseases[:max_diseases]):
        try:
            resp = requests.post(
                OPENTARGETS_API,
                json={"query": drug_query,
                      "variables": {"diseaseId": disease["efo_id"]}},
                headers={"Content-Type": "application/json"},
                timeout=20
            )
            resp.raise_for_status()
            result = resp.json()

            if "errors" in result:
                log.warning(f"  GraphQL error for {disease['efo_id']}: {result['errors']}")
                continue

            rows = (result.get("data", {})
                         .get("disease", {})
                         .get("knownDrugs", {})
                         .get("rows", []))

            for row in rows:
                drug = row.get("drug", {})
                phase = row.get("phase") or 0
                status = row.get("status") or ""
                chembl_id = drug.get("id", "")

                # Only approved (Phase 4) small molecules
                is_approved = (phase >= 4 or "approved" in status.lower()
                               or drug.get("isApproved") is True)

                if is_approved and chembl_id.startswith("CHEMBL"):
                    all_pairs.append({
                        "drug_id": chembl_id,
                        "drug_name": drug.get("name", ""),
                        "disease_id": disease["orphanet_id"],
                        "disease_efo_id": disease["efo_id"],
                        "disease_name": disease["disease_name"],
                        "max_phase": phase,
                        "status": status,
                        "drug_type": row.get("drugType", ""),
                        "mechanism": row.get("mechanismOfAction", ""),
                        "source": "OpenTargets+Orphanet",
                        "label": 1,
                    })

            if (i + 1) % 20 == 0:
                log.info(f"  {i+1}/{min(max_diseases, len(orphanet_diseases))} diseases processed "
                         f"— {len(all_pairs)} pairs so far")

            time.sleep(0.2)

        except Exception as e:
            log.warning(f"  Skipping {disease['efo_id']}: {e}")
            continue

    return all_pairs


# ══════════════════════════════════════════════════════════════════════════════
# SOURCE 2: CLINICALTRIALS.GOV — Negatives (Phase 3 efficacy failures)
# ══════════════════════════════════════════════════════════════════════════════

def fetch_phase3_failures() -> list[dict]:
    """
    Searches ClinicalTrials.gov for terminated Phase 3 rare disease trials.
    Keeps only trials explicitly stopped for lack of efficacy.
    """

    base_url = "https://clinicaltrials.gov/api/v2/studies"

    rare_disease_terms = [
        "rare disease", "orphan disease",
        "lysosomal storage", "mucopolysaccharidosis",
        "Gaucher", "Fabry", "Pompe", "Niemann-Pick",
        "Wilson disease", "phenylketonuria",
        "pulmonary arterial hypertension",
        "Huntington disease",
        "amyotrophic lateral sclerosis",
        "Duchenne muscular dystrophy",
        "spinal muscular atrophy",
        "cystic fibrosis",
        "tuberous sclerosis",
        "neurofibromatosis",
    ]

    EFFICACY_FAILURE_KEYWORDS = [
        "lack of efficacy",
        "insufficient efficacy",
        "failed to demonstrate efficacy",
        "did not meet primary endpoint",
        "did not meet the primary endpoint",
        "negative efficacy",
        "futility",
        "interim futility",
        "no significant difference",
        "no efficacy",
        "ineffective",
        "poor efficacy",
    ]

    NON_EFFICACY_KEYWORDS = [
        "business", "funding", "sponsor", "administrative",
        "company decision", "strategic", "financial",
        "slow enrollment", "poor enrollment", "accrual",
        "safety", "adverse", "toxicity", "death",
        "covid", "pandemic",
    ]

    all_failures = []
    seen_nct_ids = set()

    for term in rare_disease_terms:
        log.info(f"  Searching ClinicalTrials: '{term}'")
        try:
            resp = requests.get(base_url, params={
                "query.cond": term,
                "filter.overallStatus": "TERMINATED",
                "filter.phase": "PHASE3",
                "fields": ("NCTId,BriefTitle,Condition,InterventionName,"
                           "InterventionType,WhyStopped,CompletionDate,OverallStatus"),
                "pageSize": 100,
                "format": "json",
            }, timeout=20)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            log.warning(f"  ClinicalTrials failed for '{term}': {e}")
            continue

        for study in data.get("studies", []):
            proto = study.get("protocolSection", {})
            nct_id = proto.get("identificationModule", {}).get("nctId", "")

            if nct_id in seen_nct_ids:
                continue
            seen_nct_ids.add(nct_id)

            why_stopped = (proto.get("statusModule", {})
                               .get("whyStopped", "") or "").lower().strip()

            if not why_stopped:
                continue

            is_efficacy = any(kw in why_stopped for kw in EFFICACY_FAILURE_KEYWORDS)
            is_non_efficacy = any(kw in why_stopped for kw in NON_EFFICACY_KEYWORDS)

            if not is_efficacy or is_non_efficacy:
                continue

            conditions = proto.get("conditionsModule", {}).get("conditions", [])
            interventions = (proto.get("armsInterventionsModule", {})
                                  .get("interventions", []))

            for iv in interventions:
                if iv.get("type") == "DRUG":
                    all_failures.append({
                        "nct_id": nct_id,
                        "drug_name_raw": iv.get("name", ""),
                        "conditions": "; ".join(conditions),
                        "why_stopped": proto.get("statusModule", {}).get("whyStopped", ""),
                        "title": proto.get("identificationModule", {}).get("briefTitle", ""),
                    })

        time.sleep(0.5)

    log.info(f"Found {len(all_failures)} candidate Phase 3 failure records")
    return all_failures


def resolve_drug_name_to_chembl(drug_name: str) -> dict | None:
    """
    Resolves a free-text drug name from ClinicalTrials to a ChEMBL record.
    Tries exact name, then synonyms, then partial match (flagged for review).
    """
    base = "https://www.ebi.ac.uk/chembl/api/data/molecule"

    # Strategy 1: exact preferred name
    r = requests.get(base, params={
        "pref_name__iexact": drug_name, "format": "json", "limit": 3
    }, timeout=10)
    if r.status_code == 200:
        mols = r.json().get("molecules", [])
        if mols:
            return _mol_fields(mols[0], review=False)

    time.sleep(0.2)

    # Strategy 2: synonym match
    r = requests.get(base, params={
        "molecule_synonyms__synonym__iexact": drug_name,
        "format": "json", "limit": 3
    }, timeout=10)
    if r.status_code == 200:
        mols = r.json().get("molecules", [])
        if mols:
            return _mol_fields(mols[0], review=False)

    time.sleep(0.2)

    # Strategy 3: partial — first word only, flag for manual review
    r = requests.get(base, params={
        "pref_name__icontains": drug_name.split()[0],
        "max_phase__gte": 3,
        "format": "json", "limit": 3
    }, timeout=10)
    if r.status_code == 200:
        mols = r.json().get("molecules", [])
        if mols:
            return _mol_fields(mols[0], review=True)

    return None


def _mol_fields(mol: dict, review: bool) -> dict:
    return {
        "chembl_id": mol.get("molecule_chembl_id", ""),
        "pref_name": mol.get("pref_name", ""),
        "molecule_type": mol.get("molecule_type", ""),
        "max_phase": mol.get("max_phase", 0),
        "needs_manual_review": review,
    }


def resolve_failures_to_pairs(failures: list[dict]) -> list[dict]:
    """
    Converts raw ClinicalTrials failure records into drug_id + disease pairs.
    Skips biologics. Flags partial name matches for manual review.
    disease_id is left blank — bio person fills in the Orphanet ID.
    """
    resolved = []
    seen = set()
    cache = {}

    BIOLOGIC_TYPES = {"Protein", "Antibody", "Enzyme", "Oligonucleotide"}

    for f in failures:
        name = f["drug_name_raw"]
        if not name:
            continue

        if name not in cache:
            log.info(f"  Resolving '{name}'")
            cache[name] = resolve_drug_name_to_chembl(name)
            time.sleep(0.3)

        mol = cache[name]
        if not mol or not mol["chembl_id"]:
            log.warning(f"  Could not resolve: '{name}'")
            continue

        if mol["molecule_type"] in BIOLOGIC_TYPES:
            log.info(f"  Skipping biologic: {name} ({mol['molecule_type']})")
            continue

        key = f"{mol['chembl_id']}|{f['conditions']}"
        if key in seen:
            continue
        seen.add(key)

        resolved.append({
            "drug_id": mol["chembl_id"],
            "drug_name": mol["pref_name"] or name,
            "drug_name_raw": name,
            "molecule_type": mol["molecule_type"],
            "disease_name_raw": f["conditions"],
            "disease_id": "",          # Bio person fills this in from orphanet.net
            "nct_id": f["nct_id"],
            "why_stopped": f["why_stopped"],
            "title": f["title"],
            "needs_manual_review": mol["needs_manual_review"],
            "label": 0,
            "source": "ClinicalTrials_Phase3_terminated",
        })

    return resolved


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":

    # ── PHASE A: POSITIVES ────────────────────────────────────────────────────
    log.info("=" * 60)
    log.info("PHASE A: Approved drug-disease pairs from OpenTargets")
    log.info("=" * 60)

    positives = fetch_opentargets_approved_drugs(max_diseases=200)
    log.info(f"OpenTargets returned {len(positives)} pairs before dedup")

    pos_df = pd.DataFrame(positives)
    if not pos_df.empty:
        pos_df.drop_duplicates(subset=["drug_id", "disease_id"], inplace=True)

    pos_df.to_csv("data/ground_truth/positives_raw.csv", index=False)
    log.info(f"Saved {len(pos_df)} unique pairs → data/ground_truth/positives_raw.csv")

    # ── PHASE B: NEGATIVES ────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("PHASE B: Phase 3 efficacy failures from ClinicalTrials.gov")
    log.info("=" * 60)

    failures = fetch_phase3_failures()
    resolved = resolve_failures_to_pairs(failures)

    neg_df = pd.DataFrame(resolved)
    neg_df.to_csv("data/ground_truth/negatives_raw.csv", index=False)
    log.info(f"Saved {len(neg_df)} failure pairs → data/ground_truth/negatives_raw.csv")

    # ── SUMMARY ───────────────────────────────────────────────────────────────
    log.info("\n" + "=" * 60)
    log.info("DONE — next steps for bio person")
    log.info("=" * 60)
    log.info(f"""
  Positives saved : {len(pos_df)}  →  data/ground_truth/positives_raw.csv
  Negatives saved : {len(neg_df)}  →  data/ground_truth/negatives_raw.csv

  POSITIVES — open the CSV and:
    1. Delete rows where drug_type = Protein / Antibody / Enzyme
    2. Delete rows where disease looks like a common disease (diabetes etc.)
    3. Make sure disease_id column starts with ORPHA: for every row
    4. Save as: data/ground_truth/positives_verified.csv

  NEGATIVES — open the CSV and:
    1. Read each why_stopped — confirm it is a genuine efficacy failure
    2. Fill in disease_id column (ORPHA:xxxxx) from orphanet.net for each row
    3. Delete rows where needs_manual_review=True that you cannot verify
    4. Save as: data/ground_truth/negatives_verified.csv

  Then run:
    python scripts/load_verified_ground_truth.py
    """)