"""
One-time script to augment fct_candidates_21.csv with derived columns:
  1. education_details_clean  — education_details with "Category: <education> " prefix stripped
  2. criminal_cases_summary   — keyword summary from criminal_cases_details
  3. election_expenditure_rs  — election_expenditure with commas removed (plain integer string)

Usage:
    python data/augment_csv.py
"""

import csv
import re
from pathlib import Path


def clean_education_details(education: str, education_details: str) -> str:
    """Strip 'Category: <education> ' prefix from education_details."""
    if not education_details or not education:
        return ""
    prefix = f"Category: {education} "
    if education_details.startswith(prefix):
        return education_details[len(prefix):].strip()
    # Try case-insensitive match
    if education_details.lower().startswith(prefix.lower()):
        return education_details[len(prefix):].strip()
    return ""


def summarise_criminal_cases(details: str) -> str:
    """Extract keyword summary from criminal_cases_details."""
    if not details:
        return ""
    # Pattern: "charges related to <description> (IPC Section-XXX)"
    charges = re.findall(r"charges related to (.+?) \(IPC", details, re.IGNORECASE)
    seen = set()
    keywords = []
    for charge in charges:
        charge = charge.strip()
        # Strip "Punishment for " prefix
        if charge.lower().startswith("punishment for "):
            charge = charge[len("Punishment for "):]
        charge = charge.strip().title()
        key = charge.lower()
        if key not in seen:
            seen.add(key)
            keywords.append(charge)
    return ", ".join(keywords)


def strip_expenditure(value: str) -> str:
    """Remove commas from election_expenditure and return plain integer string."""
    if not value:
        return ""
    cleaned = value.replace(",", "").replace('"', "").strip()
    if cleaned.isdigit():
        return cleaned
    return ""


def main():
    csv_path = Path(__file__).parent / "fct_candidates_21.csv"
    if not csv_path.exists():
        print(f"CSV not found: {csv_path}")
        return

    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # Add new columns if not already present
    new_cols = ["education_details_clean", "criminal_cases_summary", "election_expenditure_rs"]
    for col in new_cols:
        if col not in fieldnames:
            fieldnames.append(col)

    for row in rows:
        education = row.get("education", "").strip()
        education_details = row.get("education_details", "").strip()
        row["education_details_clean"] = clean_education_details(education, education_details)

        criminal_details = row.get("criminal_cases_details", "").strip()
        row["criminal_cases_summary"] = summarise_criminal_cases(criminal_details)

        expenditure = row.get("election_expenditure", "").strip()
        row["election_expenditure_rs"] = strip_expenditure(expenditure)

    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Augmented {len(rows)} rows with columns: {', '.join(new_cols)}")
    # Sample output
    for row in rows[:3]:
        print(f"  education_details_clean: {row['education_details_clean'][:80]}")
        print(f"  criminal_cases_summary: {row['criminal_cases_summary'][:80]}")
        print(f"  election_expenditure_rs: {row['election_expenditure_rs']}")
        print()


if __name__ == "__main__":
    main()
