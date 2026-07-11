"""
Management command to transliterate English candidate/party names to Tamil using Sarvam API.

Uses Sarvam Transliterate API to convert all unique candidate and party names
from the 2026 (and optionally 2021) CSV data, and merges results into data/names_ta.json.

Usage:
    python manage.py transliterate_names_sarvam
    python manage.py transliterate_names_sarvam --dry-run
    python manage.py transliterate_names_sarvam --year 2021
"""

import csv
import json
import os
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

SARVAM_TRANSLITERATE_URL = "https://api.sarvam.ai/transliterate"
MIN_REQUEST_GAP = 0.15  # seconds between API calls


class Command(BaseCommand):
    help = "Transliterate candidate/party names to Tamil via Sarvam API and save to data/names_ta.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without calling the API",
        )
        parser.add_argument(
            "--year",
            default="2026",
            choices=["2021", "2026", "all"],
            help="Which year's CSV to process (default: 2026)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        year = options["year"]

        api_key = os.environ.get("SARVAM_API_KEY", "")
        if not api_key and not dry_run:
            # Try loading from .env file
            env_path = Path(settings.BASE_DIR).parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("SARVAM_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        if not api_key and not dry_run:
            self.stderr.write(self.style.ERROR("SARVAM_API_KEY not set in environment or .env"))
            return

        data_dir = Path(settings.BASE_DIR).parent / "data"
        output_path = data_dir / "names_ta.json"

        # Load existing translations
        existing: dict[str, str] = {}
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            self.stdout.write(f"Loaded {len(existing)} existing translations")

        # Collect names from CSV files
        csv_files = []
        if year in ("2026", "all"):
            csv_files.append(data_dir / "fct_candidates_26.csv")
        if year in ("2021", "all"):
            csv_files.append(data_dir / "fct_candidates_21.csv")

        candidate_names: set[str] = set()
        party_names: set[str] = set()
        for csv_path in csv_files:
            if not csv_path.exists():
                self.stderr.write(f"CSV not found: {csv_path}")
                continue
            with open(csv_path, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    name = (row.get("candidate") or "").strip()
                    if name:
                        candidate_names.add(name)
                    party = (row.get("party") or "").strip()
                    if party:
                        party_names.add(party)

        all_names = candidate_names | party_names
        self.stdout.write(f"Found {len(candidate_names)} candidates, {len(party_names)} parties")

        # Filter out already-translated names (check both exact and title-case variants)
        to_translate = []
        for name in sorted(all_names):
            if name not in existing and name.title() not in existing:
                to_translate.append(name)

        self.stdout.write(f"Need to transliterate {len(to_translate)} new names")

        if dry_run:
            for name in to_translate[:20]:
                self.stdout.write(f"  Would transliterate: {name}")
            if len(to_translate) > 20:
                self.stdout.write(f"  ... and {len(to_translate) - 20} more")
            return

        if not to_translate:
            self.stdout.write(self.style.SUCCESS("All names already transliterated!"))
            return

        # Transliterate via Sarvam API
        headers = {
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        }
        translated_count = 0
        errors = []
        last_call = 0.0

        def _clean_for_api(raw: str) -> str:
            """Normalize a name so Sarvam returns a good transliteration.
            Strips trailing dots and collapses whitespace/dots runs.
            Converts ALL-CAPS to Title Case (Sarvam is case-sensitive)."""
            import re as _re
            s = _re.sub(r"\.{2,}", ".", raw).strip()
            s = _re.sub(r"\s+", " ", s)
            s = s.rstrip(".").strip()
            if s and s.isupper():
                s = " ".join(w.capitalize() for w in s.split())
            return s

        for i, name in enumerate(to_translate):
            # Rate limiting
            elapsed = time.time() - last_call
            if elapsed < MIN_REQUEST_GAP:
                time.sleep(MIN_REQUEST_GAP - elapsed)

            cleaned = _clean_for_api(name)
            if not cleaned:
                continue

            try:
                resp = requests.post(
                    SARVAM_TRANSLITERATE_URL,
                    headers=headers,
                    json={
                        "input": cleaned,
                        "source_language_code": "en-IN",
                        "target_language_code": "ta-IN",
                    },
                    timeout=10,
                )
                last_call = time.time()
                resp.raise_for_status()
                result = resp.json()
                tamil = result.get("transliterated_text", "").strip()
                if tamil:
                    existing[name] = tamil
                    translated_count += 1

                if (i + 1) % 50 == 0 or i == len(to_translate) - 1:
                    self.stdout.write(f"  Progress: {i + 1}/{len(to_translate)}")
                    # Save intermediate results every 50
                    with open(output_path, "w", encoding="utf-8") as f:
                        json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)

            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 429:
                    self.stderr.write("  Rate limited, waiting 5s...")
                    time.sleep(5)
                    # Retry once
                    try:
                        resp = requests.post(
                            SARVAM_TRANSLITERATE_URL,
                            headers=headers,
                            json={
                                "input": cleaned,
                                "source_language_code": "en-IN",
                                "target_language_code": "ta-IN",
                            },
                            timeout=10,
                        )
                        last_call = time.time()
                        resp.raise_for_status()
                        tamil = resp.json().get("transliterated_text", "").strip()
                        if tamil:
                            existing[name] = tamil
                            translated_count += 1
                    except Exception as e2:
                        self.stderr.write(f"  Retry failed for '{name}': {e2}")
                        errors.append(name)
                else:
                    self.stderr.write(f"  Error for '{name}': {e}")
                    errors.append(name)
            except Exception as e:
                self.stderr.write(f"  Error for '{name}': {e}")
                errors.append(name)

        # Final save
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Transliterated {translated_count} names. "
                f"Total: {len(existing)} in {output_path}"
            )
        )
        if errors:
            self.stdout.write(self.style.WARNING(f"{len(errors)} failures: {errors[:10]}"))
