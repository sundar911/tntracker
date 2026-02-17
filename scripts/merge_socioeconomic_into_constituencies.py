"""Merge district-level socioeconomic data into dim_constituencies.csv.

Reads data/dim_districts_socioeconomic.csv and merges its columns into
data/dim_constituencies.csv via the district column.

Usage:
    python scripts/merge_socioeconomic_into_constituencies.py
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONSTITUENCIES_PATH = DATA_DIR / "dim_constituencies.csv"
DISTRICTS_PATH = DATA_DIR / "dim_districts_socioeconomic.csv"

SOCIOECONOMIC_FIELDS = [
    "infant_mortality_rate",
    "under5_mortality_rate",
    "institutional_delivery_pct",
    "child_stunting_pct",
    "child_wasting_pct",
    "full_immunization_pct",
    "anaemia_women_pct",
    "literacy_rate_pct",
    "male_literacy_rate_pct",
    "female_literacy_rate_pct",
    "literacy_gender_gap_pct",
    "secondary_education_pct",
    "graduate_and_above_pct",
    "per_capita_income_inr",
    "bpl_households_pct",
    "unemployment_rate_pct",
    "agricultural_workers_pct",
    "banking_access_pct",
    "crime_rate_per_lakh",
    "crimes_against_women_per_lakh",
    "crimes_against_sc_st_per_lakh",
    "pucca_housing_pct",
    "tap_water_pct",
    "electricity_pct",
    "sanitation_pct",
]


def main() -> None:
    if not DISTRICTS_PATH.exists():
        raise SystemExit(f"Missing: {DISTRICTS_PATH}")
    if not CONSTITUENCIES_PATH.exists():
        raise SystemExit(f"Missing: {CONSTITUENCIES_PATH}")

    district_data: dict[str, dict[str, str]] = {}
    with DISTRICTS_PATH.open("r", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            district_name = row.get("district", "").strip().upper()
            if district_name:
                district_data[district_name] = row

    with CONSTITUENCIES_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        constituencies = list(reader)

    merged = 0
    unmatched_districts: set[str] = set()
    for row in constituencies:
        district = row.get("district", "").strip().upper()
        district_row = district_data.get(district)
        if district_row:
            for field in SOCIOECONOMIC_FIELDS:
                if field in district_row and district_row[field]:
                    row[field] = district_row[field]
            row["data_granularity"] = "district"
            merged += 1
        else:
            if district:
                unmatched_districts.add(district)

    with CONSTITUENCIES_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(constituencies)

    print(f"Merged socioeconomic data for {merged}/{len(constituencies)} constituencies")
    if unmatched_districts:
        print(f"Unmatched districts: {sorted(unmatched_districts)}")


if __name__ == "__main__":
    main()
