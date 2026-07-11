"""
Import prominent candidates from JSON to CSV format.

Reads data/prominent_candidates_26_detailed.json and writes
data/fct_candidates_26.csv with the standard column schema.

Usage:
    python manage.py import_prominent_candidates
    python manage.py import_prominent_candidates --dry-run
    python manage.py import_prominent_candidates --backup
"""

import csv
import json
import shutil
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

CSV_FIELDNAMES = [
    "candidate", "party", "constituency", "age", "gender",
    "address", "fathers_name", "criminal_cases",
    "education", "education_category", "education_details",
    "self_profession", "spouse_profession",
    "total_assets_rs", "liabilities_rs",
    "phone", "email", "facebook", "twitter", "instagram", "youtube",
    "photo_url", "affidavit_url", "affidavit_pdf_url",
    "current_status", "pdf_path", "legal_summary_short",
]


class Command(BaseCommand):
    help = "Import prominent candidates from JSON to CSV"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--backup", action="store_true",
                            help="Back up existing CSV before overwriting")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        backup = options["backup"]
        data_dir = Path(settings.BASE_DIR).parent / "data"
        json_path = data_dir / "eci_2026" / "candidates_final.json"
        csv_path = data_dir / "fct_candidates_26.csv"

        if not json_path.exists():
            self.stderr.write(self.style.ERROR(f"JSON not found: {json_path}"))
            return

        with open(json_path, "r", encoding="utf-8") as f:
            candidates = json.load(f)

        self.stdout.write(f"Loaded {len(candidates)} candidates from JSON")

        import re as _re
        rows = []
        for c in candidates:
            row = {field: "" for field in CSV_FIELDNAMES}
            row["candidate"] = (c.get("name") or "").strip()
            row["party"] = (c.get("party") or "").strip()
            constituency = (c.get("constituency") or "").strip()
            address = (c.get("address") or "").strip()
            # ECI lumps two different constituencies under "TIRUPPATTUR":
            # Sivaganga district (pincode 630xxx) = Tiruppathur AC
            # Tirupathur district (pincode 635xxx) = Tirupattur AC
            if constituency.upper() == "TIRUPPATTUR":
                pincode_match = _re.search(r"\b(\d{6})\b", address)
                if pincode_match:
                    pin = pincode_match.group(1)
                    if pin.startswith("630"):
                        constituency = "TIRUPPATHUR"  # Sivaganga
                    elif pin.startswith("635"):
                        constituency = "TIRUPATTUR"  # Tirupathur district
            row["constituency"] = constituency
            row["age"] = (str(c.get("age") or "")).strip()
            row["gender"] = (c.get("gender") or "").strip()
            row["address"] = address
            row["fathers_name"] = (c.get("fathers_name") or "").strip()
            row["photo_url"] = (c.get("photo_url") or "").strip()
            row["affidavit_url"] = (c.get("affidavit_url") or "").strip()
            row["affidavit_pdf_url"] = (c.get("latest_affidavit_pdf_url") or "").strip()
            row["current_status"] = (c.get("current_status") or "").strip()
            row["pdf_path"] = (c.get("pdf_path") or "").strip()
            rows.append(row)

        # Stats
        parties = len({r["party"] for r in rows if r["party"]})
        constituencies = len({r["constituency"] for r in rows if r["constituency"]})
        self.stdout.write(f"  {len(rows)} candidates, {parties} parties, {constituencies} constituencies")

        if dry_run:
            self.stdout.write("Dry run — no files written")
            for r in rows[:3]:
                self.stdout.write(f"  {r['candidate']} | {r['party']} | {r['constituency']}")
            return

        if backup and csv_path.exists():
            bak = csv_path.with_suffix(".csv.bak")
            shutil.copy2(csv_path, bak)
            self.stdout.write(f"Backed up to {bak}")

        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(f"Written {len(rows)} candidates to {csv_path}"))
