"""Import party dimension data from data/dim_parties.csv.

Updates existing Party records with ideology, leadership, coalition info,
and governance records.

Usage:
    python manage.py import_dim_parties
    python manage.py import_dim_parties --csv-path data/dim_parties.csv
"""
from __future__ import annotations

import csv
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Party


def _parse_int(value: str) -> int | None:
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace(",", "")
    return int(cleaned) if cleaned.isdigit() else None


FIELD_MAP = {
    "party_name_ta": "name_ta",
    "abbreviation": "abbreviation",
    "abbreviation_ta": "abbreviation_ta",
    "website": "website",
    "founded_year": None,
    "founder": "founder",
    "current_leader": "current_leader",
    "headquarters": "headquarters",
    "political_ideology": "political_ideology",
    "political_position": "political_position",
    "eci_recognition": "eci_recognition",
    "governance_record_note": "governance_record_note",
}


class Command(BaseCommand):
    help = "Import party dimension data from dim_parties.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            type=str,
            default="",
            help="Path to dim_parties.csv (default: data/dim_parties.csv)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        if not csv_path:
            csv_path = str(settings.BASE_DIR.parent / "data" / "dim_parties.csv")

        path = Path(csv_path)
        if not path.exists():
            raise ValueError(f"CSV not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            self.stdout.write(self.style.WARNING("No rows found in CSV."))
            return

        updated = 0
        created = 0
        skipped = 0

        for row in rows:
            party_name = row.get("party_name", "").strip()
            if not party_name or party_name == "IND":
                skipped += 1
                continue

            party, was_created = Party.objects.get_or_create(name=party_name)

            changed = False
            for csv_col, model_field in FIELD_MAP.items():
                raw_value = row.get(csv_col, "").strip()
                if not raw_value:
                    continue

                if csv_col == "founded_year":
                    int_val = _parse_int(raw_value)
                    if int_val and party.founded_year != int_val:
                        party.founded_year = int_val
                        changed = True
                    continue

                if model_field and getattr(party, model_field, "") != raw_value:
                    setattr(party, model_field, raw_value)
                    changed = True

            if changed or was_created:
                party.save()
                if was_created:
                    created += 1
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {updated} updated, {created} created, {skipped} skipped"
            )
        )
