"""
Translate candidate text fields (profession, education details) between English and Tamil
using the Sarvam Translate API, and store both versions in the CSV.

Adds columns: self_profession_ta, spouse_profession_ta, education_details_ta

Usage:
    python manage.py translate_candidate_fields
    python manage.py translate_candidate_fields --dry-run
"""

import csv
import os
import re
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"


def _is_tamil(text: str) -> bool:
    """Check if text contains Tamil Unicode characters."""
    return bool(re.search(r'[\u0B80-\u0BFF]', text))


def _translate(text: str, src: str, tgt: str, api_key: str) -> str:
    """Translate text using Sarvam API. Returns empty string on failure."""
    if not text or not text.strip():
        return ""
    try:
        resp = requests.post(
            SARVAM_TRANSLATE_URL,
            headers={"api-subscription-key": api_key, "Content-Type": "application/json"},
            json={
                "input": text[:1000],  # mayura:v1 limit
                "source_language_code": src,
                "target_language_code": tgt,
                "model": "mayura:v1",
            },
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json().get("translated_text", "").strip()
    except Exception:
        return ""


class Command(BaseCommand):
    help = "Translate candidate profession/education fields between English and Tamil"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        data_dir = Path(settings.BASE_DIR).parent / "data"
        csv_path = data_dir / "fct_candidates_26.csv"

        api_key = os.environ.get("SARVAM_API_KEY", "")
        if not api_key:
            env_path = data_dir.parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("SARVAM_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        if not api_key and not dry_run:
            self.stderr.write(self.style.ERROR("SARVAM_API_KEY not set"))
            return

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            rows = list(reader)

        # Add _ta columns if not present
        for col in ["self_profession_ta", "spouse_profession_ta", "education_details_ta"]:
            if col not in fieldnames:
                fieldnames.append(col)

        # Fields to translate: (source_col, target_col)
        translate_pairs = [
            ("self_profession", "self_profession_ta"),
            ("spouse_profession", "spouse_profession_ta"),
            ("education_details", "education_details_ta"),
        ]

        translated = 0
        total_calls = 0

        for row in rows:
            for src_col, ta_col in translate_pairs:
                src_text = (row.get(src_col) or "").strip()
                existing_ta = (row.get(ta_col) or "").strip()

                if not src_text or existing_ta:
                    row.setdefault(ta_col, existing_ta)
                    continue

                if _is_tamil(src_text):
                    # Source is already Tamil — use as _ta, no API call needed
                    row[ta_col] = src_text
                    translated += 1
                else:
                    # Source is English — translate to Tamil
                    if dry_run:
                        self.stdout.write(f"  Would translate ({src_col}): {src_text[:60]}")
                        row[ta_col] = ""
                    else:
                        tamil = _translate(src_text, "en-IN", "ta-IN", api_key)
                        row[ta_col] = tamil
                        if tamil:
                            translated += 1
                        total_calls += 1
                        time.sleep(0.15)

                        if total_calls % 50 == 0:
                            self.stdout.write(f"  API calls: {total_calls}")
                            # Save intermediate
                            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                                writer = csv.DictWriter(f, fieldnames=fieldnames)
                                writer.writeheader()
                                writer.writerows(rows)

        if not dry_run:
            with open(csv_path, "w", encoding="utf-8", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(
            f"Translated {translated} fields ({total_calls} API calls). Updated {csv_path}"
        ))
