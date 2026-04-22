-- db/schema.sql

CREATE TABLE drugs (
    chembl_id VARCHAR(20) PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    synonyms JSONB,
    molecule_type VARCHAR(50),
    chirality VARCHAR(50),          -- 'Racemic mixture', 'Single stereoisomer', etc.
    oral_bioavailability FLOAT,
    half_life_hours FLOAT,
    mw FLOAT,
    logp FLOAT,
    hbd INTEGER,
    hba INTEGER,
    patent_expiry_year INTEGER,
    bcs_class VARCHAR(10),
    raw_drugbank JSONB,
    raw_chembl JSONB,
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE diseases (
    disease_id VARCHAR(30) PRIMARY KEY,   -- 'OMIM:123456' or 'ORPHA:12345'
    name VARCHAR(255) NOT NULL,
    id_source VARCHAR(20),                -- 'OMIM' or 'ORPHANET'
    orphanet_id VARCHAR(20),
    omim_id VARCHAR(20),
    causal_genes JSONB,                   -- [{gene_symbol, uniprot_id, evidence_level}]
    age_of_onset VARCHAR(50),
    prevalence_global FLOAT,
    prevalence_india_estimate FLOAT,
    primary_affected_tissue VARCHAR(100),
    disease_subtype_ids JSONB,
    natural_history_level INTEGER,        -- 1-5 scale
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE candidate_pairs (
    id SERIAL PRIMARY KEY,
    drug_id VARCHAR(20) REFERENCES drugs(chembl_id),
    disease_id VARCHAR(30) REFERENCES diseases(disease_id),
    -- Layer scores
    score_target_overlap FLOAT,
    score_network_proximity FLOAT,
    score_transcriptomic FLOAT,
    score_kg_embedding FLOAT,
    score_admet FLOAT,
    score_literature FLOAT,
    score_clinical_trial_evidence INTEGER,
    score_business_total INTEGER,
    -- Business subscores
    score_ip INTEGER,
    score_regulatory INTEGER,
    score_market INTEGER,
    score_manufacturing INTEGER,
    score_clinical_adoption INTEGER,
    score_speed INTEGER,
    -- Flags (stored as booleans + JSONB for detail)
    flag_disqualified BOOLEAN DEFAULT FALSE,
    flag_disqualify_reason VARCHAR(255),
    flag_herg BOOLEAN,
    flag_patent_conflict BOOLEAN,
    flag_ddi_risk BOOLEAN,
    flag_pgx_risk BOOLEAN,
    flag_polymorph BOOLEAN,
    flag_pediatric_formulation BOOLEAN,
    -- Composite
    composite_score FLOAT,
    -- Traceability
    data_sources JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(drug_id, disease_id)
);

CREATE TABLE ground_truth (
    drug_id VARCHAR(20),
    disease_id VARCHAR(30),
    label INTEGER NOT NULL,         -- 1 = known positive, 0 = known negative
    evidence_source VARCHAR(100),   -- 'DrugBank approved', 'Phase III failure', etc.
    notes TEXT,
    PRIMARY KEY (drug_id, disease_id)
);

CREATE INDEX idx_composite ON candidate_pairs(composite_score DESC);
CREATE INDEX idx_business ON candidate_pairs(score_business_total DESC);
CREATE INDEX idx_disqualified ON candidate_pairs(flag_disqualified);