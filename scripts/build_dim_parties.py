"""Build data/dim_parties.csv from fact tables.

Extracts all unique parties from fct_candidates_21.csv and
fct_candidates_16.csv, computes seats_contested for each election year,
and creates a skeleton CSV with all columns pre-filled where possible.

Usage:
    python scripts/build_dim_parties.py
"""
from __future__ import annotations

import csv
from collections import Counter
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FCT_21_PATH = DATA_DIR / "fct_candidates_21.csv"
FCT_16_PATH = DATA_DIR / "fct_candidates_16.csv"
OUTPUT_PATH = DATA_DIR / "dim_parties.csv"

COLUMNS = [
    # Identifiers
    "party_name",
    "party_name_ta",
    "abbreviation",
    "abbreviation_ta",
    # Party profile
    "founded_year",
    "founder",
    "current_leader",
    "headquarters",
    "website",
    # Political positioning
    "political_ideology",
    "political_position",
    "eci_recognition",
    # Alliance and electoral context
    "coalition_2021",
    "coalition_2026",
    "seats_contested_2021",
    "seats_won_2021",
    "vote_share_pct_2021",
    "seats_contested_2016",
    "seats_won_2016",
    # Manifesto / governance highlights
    "key_manifesto_themes",
    "governance_record_note",
]


def _count_parties(csv_path: Path, constituency_col: str = "2021_constituency") -> Counter[str]:
    """Count how many unique constituencies each party contested in."""
    party_constituencies: dict[str, set[str]] = {}
    if not csv_path.exists():
        return Counter()
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            party = row.get("party", "").strip()
            constituency = row.get(constituency_col, "").strip()
            if party and constituency:
                party_constituencies.setdefault(party, set()).add(constituency)
    return Counter({p: len(cs) for p, cs in party_constituencies.items()})


def main() -> None:
    parties_21 = _count_parties(FCT_21_PATH)
    parties_16 = _count_parties(FCT_16_PATH)

    all_party_names = sorted(set(parties_21.keys()) | set(parties_16.keys()))

    if not all_party_names:
        raise SystemExit("No parties found in fact tables.")

    rows: list[dict[str, str]] = []
    for party_name in all_party_names:
        row: dict[str, str] = {col: "" for col in COLUMNS}
        row["party_name"] = party_name
        contested_21 = parties_21.get(party_name, 0)
        contested_16 = parties_16.get(party_name, 0)
        row["seats_contested_2021"] = str(contested_21) if contested_21 else ""
        row["seats_contested_2016"] = str(contested_16) if contested_16 else ""

        if party_name == "IND":
            row["abbreviation"] = "IND"
            row["eci_recognition"] = "Independent"
        rows.append(row)

    rows.sort(key=lambda r: (-int(r["seats_contested_2021"] or "0"), r["party_name"]))

    with OUTPUT_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} parties to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
