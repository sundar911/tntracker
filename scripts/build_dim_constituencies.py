"""Build data/dim_constituencies.csv from GeoJSON + fact tables.

Parses tn_ac_2021.geojson for constituency identifiers, reservation status,
parliamentary constituency, and district. Cross-references fct_candidates_21.csv
to compute total_candidates_2021. Leaves socioeconomic and election-result
columns as empty placeholders for later enrichment.

Usage:
    python scripts/build_dim_constituencies.py
"""
from __future__ import annotations

import csv
import json
import re
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
GEOJSON_PATH = DATA_DIR / "tn_ac_2021.geojson"
FCT_21_PATH = DATA_DIR / "fct_candidates_21.csv"
OUTPUT_PATH = DATA_DIR / "dim_constituencies.csv"

REGION_MAP: dict[str, str] = {
    "CHENNAI": "Chennai",
    "THIRUVALLUR": "Northern TN",
    "CHENGALPATTU": "Northern TN",
    "KANCHEEPURAM": "Northern TN",
    "RANIPET": "Northern TN",
    "VELLORE": "Northern TN",
    "TIRUPATHUR": "Northern TN",
    "TIRUVANNAMALAI": "Northern TN",
    "COIMBATORE": "Western TN",
    "TIRUPPUR": "Western TN",
    "ERODE": "Western TN",
    "THE NILGIRIS": "Western TN",
    "NAMAKKAL": "Western TN",
    "SALEM": "Western TN",
    "DHARMAPURI": "Western TN",
    "KRISHNAGIRI": "Western TN",
    "MADURAI": "Southern TN",
    "THENI": "Southern TN",
    "DINDIGUL": "Southern TN",
    "VIRUDHUNAGAR": "Southern TN",
    "RAMANATHAPURAM": "Southern TN",
    "SIVAGANGA": "Southern TN",
    "THOOTHUKUDI": "Southern TN",
    "TIRUNELVELI": "Southern TN",
    "TENKASI": "Southern TN",
    "KANNIYAKUMARI": "Southern TN",
    "THANJAVUR": "Delta",
    "THIRUVARUR": "Delta",
    "NAGAPATTINAM": "Delta",
    "PUDUKKOTTAI": "Delta",
    "TIRUCHIRAPPALLI": "Central TN",
    "KARUR": "Central TN",
    "PERAMBALUR": "Central TN",
    "ARIYALUR": "Central TN",
    "CUDDALORE": "Central TN",
    "KALLAKURICHI": "Central TN",
    "VILLUPPURAM": "Central TN",
}

COLUMNS = [
    # Identifiers and geography
    "constituency_number",
    "constituency_name",
    "constituency_name_ta",
    "district",
    "parliamentary_constituency",
    "is_reserved",
    "reservation_type",
    "region",
    # Demographics
    "population",
    "area_sq_km",
    "urbanization_type",
    # Health (district-level, NFHS-5)
    "infant_mortality_rate",
    "under5_mortality_rate",
    "institutional_delivery_pct",
    "child_stunting_pct",
    "child_wasting_pct",
    "full_immunization_pct",
    "anaemia_women_pct",
    # Education (district-level, Census 2011 / UDISE+)
    "literacy_rate_pct",
    "male_literacy_rate_pct",
    "female_literacy_rate_pct",
    "literacy_gender_gap_pct",
    "secondary_education_pct",
    "graduate_and_above_pct",
    # Economic (district-level)
    "per_capita_income_inr",
    "bpl_households_pct",
    "unemployment_rate_pct",
    "agricultural_workers_pct",
    "banking_access_pct",
    # Safety and infrastructure (district-level)
    "crime_rate_per_lakh",
    "crimes_against_women_per_lakh",
    "crimes_against_sc_st_per_lakh",
    "pucca_housing_pct",
    "tap_water_pct",
    "electricity_pct",
    "sanitation_pct",
    # Election stats (2021)
    "total_electors_2021",
    "votes_polled_2021",
    "voter_turnout_pct_2021",
    "total_candidates_2021",
    "winning_party_2021",
    "winning_candidate_2021",
    "victory_margin_2021",
    "victory_margin_pct_2021",
    "runner_up_party_2021",
    "nota_votes_2021",
    "nota_pct_2021",
    # Metadata
    "data_granularity",
]


def _parse_reservation(ac_name: str) -> tuple[bool, str]:
    """Extract reservation type from AC name like 'Ponneri (SC)'.

    Also handles malformed entries like 'Kilvaithinankuppam(SC' (missing
    closing paren).
    """
    if re.search(r"\(\s*SC\s*\)?\s*$", ac_name, re.IGNORECASE):
        return True, "SC"
    if re.search(r"\(\s*ST\s*\)?\s*$", ac_name, re.IGNORECASE):
        return True, "ST"
    return False, "General"


def _clean_ac_name(ac_name: str) -> str:
    """Remove reservation suffix to get clean constituency name.

    Handles both well-formed '(SC)' and malformed '(SC' suffixes.
    Does NOT strip directional suffixes like (West), (East), (North), (South).
    """
    cleaned = re.sub(r"\s*\(\s*(?:SC|ST)\s*\)?\s*$", "", ac_name, flags=re.IGNORECASE)
    return cleaned.strip()


def _normalize(name: str) -> str:
    """Normalize constituency name for matching.

    Strips reservation suffixes (SC/ST) and non-alphanumeric chars so that
    'PONNERI (SC)' and 'Ponneri' both become 'PONNERI'.
    """
    upper = name.upper()
    upper = re.sub(r"\(\s*(?:SC|ST)\s*\)", "", upper)
    upper = re.sub(r"\s+(?:SC|ST)\s*$", "", upper.strip())
    return re.sub(r"[^A-Z0-9]", "", upper)


GEOJSON_TO_CSV_ALIASES: dict[str, str] = {
    "ARUPPUKKOTTAI": "ARUPPUKOTTAI",
    "BODINAYAKANUR": "BODINAYAKKANUR",
    "CHEPAUKTHIRUVALLIKEN": "CHEPAUKTHIRUVALLIKENI",
    "COLACHEL": "COLACHAL",
    "DRRADHAKRISHNANNAGA": "DRRADHAKRISHNANNAGAR",
    "GANDHARVAKOTTAI": "GANDARVAKOTTAI",
    "KILVAITHINANKUPPAM": "KILVAITHINANKUPPAM",
    "MADAVARAM": "MADHAVARAM",
    "MADURAVOYAL": "MADHURAVOYAL",
    "METTUPPALAYAM": "METTUPALAYAM",
    "MUDHUKULATHUR": "MUDUKULATHUR",
    "PALACODU": "PALACODE",
    "PAPPIREDDIPPATTI": "PAPPIREDDIPATTI",
    "SHOLINGUR": "SHOLINGHUR",
    "SHOZHINGANALLUR": "SHOLINGANALLUR",
    "THALLI": "THALLY",
    "TIRUVOTTIYUR": "THIRUVOTTIYUR",
    "VRIDDHACHALAM": "VRIDHACHALAM",
    "VEDARANYAM": "VEDHARANYAM",
    "THIRUVARUR": "THIRUVAUR",
    "THOOTHUKKUDI": "THOOTHUKUDI",
}


def _count_candidates(csv_path: Path) -> dict[str, int]:
    """Count candidates per constituency from fact table."""
    counts: Counter[str] = Counter()
    if not csv_path.exists():
        return dict(counts)
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            constituency = row.get("2021_constituency", "").strip()
            if constituency:
                counts[_normalize(constituency)] += 1
    return dict(counts)


def main() -> None:
    if not GEOJSON_PATH.exists():
        raise SystemExit(f"Missing GeoJSON: {GEOJSON_PATH}")

    with GEOJSON_PATH.open("r", encoding="utf-8") as f:
        geojson = json.load(f)

    features = geojson.get("features", [])
    if not features:
        raise SystemExit("No features found in GeoJSON.")

    candidate_counts = _count_candidates(FCT_21_PATH)

    rows: list[dict[str, str]] = []
    for feature in features:
        props = feature.get("properties", {})
        ac_name = props.get("AC_NAME", "")
        ac_no = props.get("AC_NO", "")
        dist_name = props.get("DIST_NAME", "")
        pc_name = props.get("PC_NAME", "")

        is_reserved, reservation_type = _parse_reservation(ac_name)
        clean_name = _clean_ac_name(ac_name)
        region = REGION_MAP.get(dist_name.upper(), "")

        norm_key = _normalize(clean_name)
        total_candidates = candidate_counts.get(norm_key, "")
        if not total_candidates:
            alias = GEOJSON_TO_CSV_ALIASES.get(norm_key, "")
            if alias:
                total_candidates = candidate_counts.get(alias, "")

        row: dict[str, str] = {col: "" for col in COLUMNS}
        row["constituency_number"] = str(ac_no)
        row["constituency_name"] = clean_name.upper()
        row["district"] = dist_name.upper()
        row["parliamentary_constituency"] = re.sub(
            r"\s*\(\s*(?:SC|ST)\s*\)\s*$", "", pc_name, flags=re.IGNORECASE
        ).strip().upper()
        row["is_reserved"] = "TRUE" if is_reserved else "FALSE"
        row["reservation_type"] = reservation_type
        row["region"] = region
        row["total_candidates_2021"] = str(total_candidates) if total_candidates else ""
        row["data_granularity"] = "district"

        rows.append(row)

    rows.sort(key=lambda r: int(r["constituency_number"]) if r["constituency_number"].isdigit() else 0)

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} constituencies to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
