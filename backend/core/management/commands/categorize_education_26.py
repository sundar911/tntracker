"""
Categorize raw education text from 2026 affidavits into standard categories.

Reads fct_candidates_26.csv, derives education_category from the raw education
field, and writes the updated CSV with two columns:
  - education_category: standardized (Graduate, Post Graduate, 10th Pass, etc.)
  - education_details: the original raw text (preserved for modal display)

Usage:
    python manage.py categorize_education_26
    python manage.py categorize_education_26 --dry-run
"""

import csv
import re
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


# Ordered from most specific to least — first match wins
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("Doctorate", [
        r"\bph\.?d\b", r"\bdoctorate\b", r"\bmunaiver\b", r"\bமுனைவர்\b",
    ]),
    ("Post Graduate", [
        r"\bm\.?a\.?\b", r"\bm\.?sc\b", r"\bm\.?com\b", r"\bm\.?b\.?a\b",
        r"\bm\.?tech\b", r"\bm\.?e\.?\b", r"\bm\.?phil\b", r"\bm\.?s\.?w\b",
        r"\bm\.?c\.?a\b", r"\bm\.?l\.?\b", r"\bl\.?l\.?m\b",
        r"\bpost\s*graduate\b", r"\bpost[-\s]?graduation\b",
        r"\bமுதுகலை\b", r"\bஎம்\.?\s*ஏ\b",
    ]),
    ("Graduate Professional", [
        r"\bb\.?e\.?\b", r"\bb\.?tech\b", r"\bb\.?l\.?\b", r"\bl\.?l\.?b\b",
        r"\bm\.?b\.?b\.?s\b", r"\bb\.?d\.?s\b", r"\bb\.?arch\b",
        r"\bengineering\b", r"\blaw\b.*\bcollege\b", r"\bmedical\b",
    ]),
    ("Graduate", [
        r"\bb\.?a\.?\b", r"\bb\.?sc\b", r"\bb\.?com\b", r"\bb\.?b\.?a\b",
        r"\bb\.?c\.?a\b", r"\bb\.?s\.?w\b",
        r"\bbachelor\b", r"\bgraduate\b", r"\bdegree\b",
        r"\bபட்டதாரி\b", r"\bபட்டம்\b", r"\bபல்கலைக்கழகம்\b",
    ]),
    ("Diploma", [
        r"\bdiploma\b", r"\bdeee\b", r"\bdme\b", r"\bdece\b",
        r"\bpolytechnic\b", r"\bடிப்ளமா\b", r"\bபாலிடெக்னிக்\b",
    ]),
    ("10th Pass", [
        r"\b10th\b", r"\bsslc\b", r"\bmatriculation\b",
        r"\bபத்தாம்\s*வகுப்பு\b", r"\b10-ஆம்\b",
    ]),
    ("12th Pass", [
        r"\b12th\b", r"\bhsc\b", r"\bhigher\s*secondary\b",
        r"\b\+2\b", r"\bplus\s*two\b", r"\bமேல்நிலை\b",
    ]),
    ("8th Pass", [
        r"\b8th\b", r"\beighth\b", r"\b8-ஆம்\b",
    ]),
    ("5th Pass", [
        r"\b5th\b", r"\bfifth\b", r"\b5-ஆம்\b",
    ]),
    ("Literate", [
        r"\bliterate\b", r"\bread\b.*\bwrite\b",
        r"\bஎழுத்தறிவு\b",
    ]),
    ("Illiterate", [
        r"\billiterate\b", r"\bஎழுத்தறிவின்மை\b",
    ]),
]


def categorize_education(raw: str) -> str:
    """Return a standard education category for the given raw text."""
    if not raw or not raw.strip():
        return ""
    text = raw.strip().lower()
    for category, patterns in CATEGORY_RULES:
        for pattern in patterns:
            if re.search(pattern, text, re.IGNORECASE):
                return category
    return "Others"


class Command(BaseCommand):
    help = "Categorize raw education text into standard categories for 2026 candidates"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        data_dir = Path(settings.BASE_DIR).parent / "data"
        csv_path = data_dir / "fct_candidates_26.csv"

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            rows = list(reader)

        # Add new columns if not present
        if "education_category" not in fieldnames:
            idx = fieldnames.index("education") + 1
            fieldnames.insert(idx, "education_category")
        if "education_details" not in fieldnames:
            idx = fieldnames.index("education_category") + 1
            fieldnames.insert(idx, "education_details")

        categorized = 0
        for row in rows:
            raw = (row.get("education") or "").strip()
            category = categorize_education(raw)
            row["education_details"] = raw
            row["education_category"] = category
            if category:
                categorized += 1
                if dry_run:
                    self.stdout.write(f"  {raw[:80]} -> {category}")

        self.stdout.write(f"Categorized {categorized}/{len(rows)} candidates")

        if dry_run:
            return

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"Updated {csv_path}"))
