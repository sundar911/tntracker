"""
Management command to transliterate English candidate/party names to Tamil.

Uses Google Translate to transliterate all unique candidate and party names
from the CSV data files, and saves the results to data/names_ta.json.

Also populates the name_ta field for Candidate and Party DB records.

Usage:
    python manage.py transliterate_names
    python manage.py transliterate_names --dry-run
"""

import csv
import json
import time
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Transliterate English candidate/party names to Tamil and save to data/names_ta.json"

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be done without saving",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=10,
            help="Number of names to translate per batch (default: 10)",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        batch_size = options["batch_size"]

        data_dir = Path(settings.BASE_DIR).parent / "data"
        output_path = data_dir / "names_ta.json"

        # Load existing translations if any
        existing = {}
        if output_path.exists():
            with open(output_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            self.stdout.write(f"Loaded {len(existing)} existing translations from {output_path}")

        # Collect all unique candidate and party names from CSV files
        candidate_names = set()
        party_names = set()

        csv_files = [
            data_dir / "fct_candidates_21.csv",
            data_dir / "tn_2021_candidates.csv",
            data_dir / "tn_2026_candidates.csv",
        ]

        for csv_path in csv_files:
            if not csv_path.exists():
                continue
            with open(csv_path, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    name = (row.get("candidate") or "").strip()
                    if name:
                        candidate_names.add(name)
                    party = (row.get("party") or "").strip()
                    if party:
                        party_names.add(party)

        self.stdout.write(f"Found {len(candidate_names)} unique candidate names")
        self.stdout.write(f"Found {len(party_names)} unique party names")

        # Filter out already-translated names
        all_names = candidate_names | party_names
        to_translate = [n for n in sorted(all_names) if n not in existing]
        self.stdout.write(f"Need to translate {len(to_translate)} new names")

        if dry_run:
            for name in to_translate[:20]:
                self.stdout.write(f"  Would translate: {name}")
            if len(to_translate) > 20:
                self.stdout.write(f"  ... and {len(to_translate) - 20} more")
            return

        if not to_translate:
            self.stdout.write(self.style.SUCCESS("All names already translated!"))
            self._update_db(existing)
            return

        # Translate in batches
        try:
            from googletrans import Translator

            translator = Translator()
        except ImportError:
            self.stderr.write(
                self.style.ERROR(
                    "googletrans not installed. Run: pip install googletrans==4.0.0-rc1"
                )
            )
            return

        translated_count = 0
        errors = []

        for i in range(0, len(to_translate), batch_size):
            batch = to_translate[i : i + batch_size]
            # Join batch with newlines for batch translation
            batch_text = "\n".join(batch)

            try:
                result = translator.translate(batch_text, src="en", dest="ta")
                tamil_names = result.text.split("\n")

                for english, tamil in zip(batch, tamil_names):
                    tamil = tamil.strip()
                    if tamil:
                        existing[english] = tamil
                        translated_count += 1

                self.stdout.write(
                    f"  Translated batch {i // batch_size + 1} "
                    f"({min(i + batch_size, len(to_translate))}/{len(to_translate)})"
                )

                # Small delay to avoid rate limiting
                if i + batch_size < len(to_translate):
                    time.sleep(0.5)

            except Exception as e:
                self.stderr.write(f"  Error translating batch at index {i}: {e}")
                errors.append((i, str(e)))
                # Try individual translations for the failed batch
                for name in batch:
                    try:
                        result = translator.translate(name, src="en", dest="ta")
                        existing[name] = result.text.strip()
                        translated_count += 1
                        time.sleep(0.3)
                    except Exception as e2:
                        self.stderr.write(f"  Failed to translate '{name}': {e2}")

        # Save results
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2, sort_keys=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Translated {translated_count} names. "
                f"Total: {len(existing)} translations saved to {output_path}"
            )
        )

        if errors:
            self.stdout.write(self.style.WARNING(f"{len(errors)} batch errors encountered"))

        # Update DB records
        self._update_db(existing)

    def _update_db(self, translations: dict):
        """Populate name_ta for Candidate and Party DB records."""
        from core.models import Candidate, Party

        # Update candidates
        updated = 0
        for candidate in Candidate.objects.filter(name_ta=""):
            tamil_name = translations.get(candidate.name)
            if tamil_name:
                candidate.name_ta = tamil_name
                candidate.save(update_fields=["name_ta"])
                updated += 1

        self.stdout.write(f"Updated {updated} Candidate DB records with name_ta")

        # Update parties
        updated = 0
        for party in Party.objects.filter(name_ta=""):
            tamil_name = translations.get(party.name)
            if tamil_name:
                party.name_ta = tamil_name
                party.save(update_fields=["name_ta"])
                updated += 1

        self.stdout.write(f"Updated {updated} Party DB records with name_ta")
