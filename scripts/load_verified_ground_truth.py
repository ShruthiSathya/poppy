# scripts/load_verified_ground_truth.py
import pandas as pd
import psycopg2
import os
from dotenv import load_dotenv

load_dotenv()

conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST"),
    port=os.getenv("POSTGRES_PORT"),
    dbname=os.getenv("POSTGRES_DB"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
)
cur = conn.cursor()

for fname, label in [
    ("data/ground_truth/positives_verified.csv", 1),
    ("data/ground_truth/negatives_verified.csv", 0),
]:
    df = pd.read_csv(fname)
    # Normalize disease_id format
    df["disease_id"] = df["disease_id"].str.strip()
    
    loaded, skipped = 0, 0
    for _, row in df.iterrows():
        if not row["drug_id"] or not row["disease_id"]:
            skipped += 1
            continue
        try:
            cur.execute("""
                INSERT INTO ground_truth (drug_id, disease_id, label, evidence_source, notes)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (drug_id, disease_id) DO NOTHING
            """, (
                row["drug_id"],
                row["disease_id"],
                label,
                row.get("source", ""),
                row.get("why_stopped", "") or row.get("mechanism", ""),
            ))
            loaded += 1
        except Exception as e:
            print(f"  Skipping {row['drug_id']} × {row['disease_id']}: {e}")
            skipped += 1

    conn.commit()
    print(f"{fname}: {loaded} loaded, {skipped} skipped")

cur.close()
conn.close()

# Verify
conn = psycopg2.connect(
    host=os.getenv("POSTGRES_HOST"), port=os.getenv("POSTGRES_PORT"),
    dbname=os.getenv("POSTGRES_DB"), user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
)
cur = conn.cursor()
cur.execute("SELECT label, COUNT(*) FROM ground_truth GROUP BY label")
for row in cur.fetchall():
    print(f"  Label {row[0]}: {row[1]} pairs")