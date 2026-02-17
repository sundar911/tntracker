"""Import constituency dimension data from data/dim_constituencies.csv.

Updates existing Constituency records with socioeconomic indicators,
geography data (parliamentary constituency, region, urbanization), and
demographic information.

Usage:
    python manage.py import_dim_constituencies
    python manage.py import_dim_constituencies --csv-path data/dim_constituencies.csv
"""
from __future__ import annotations

import csv
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Constituency


def _normalize(name: str) -> str:
    upper = name.upper()
    upper = re.sub(r"\(\s*(?:SC|ST)\s*\)", "", upper)
    upper = re.sub(r"\s+(?:SC|ST)\s*$", "", upper.strip())
    return re.sub(r"[^A-Z0-9]", "", upper)


def _parse_decimal(value: str) -> Decimal | None:
    if not value or not value.strip():
        return None
    try:
        return Decimal(value.strip())
    except InvalidOperation:
        return None


def _parse_int(value: str) -> int | None:
    if not value or not value.strip():
        return None
    cleaned = value.strip().replace(",", "")
    return int(cleaned) if cleaned.isdigit() else None


FIELD_MAP = {
    "parliamentary_constituency": ("char", "parliamentary_constituency"),
    "region": ("char", "region"),
    "urbanization_type": ("char", "urbanization_type"),
    "population": ("int", "population"),
    "area_sq_km": ("decimal", "area_sq_km"),
    "infant_mortality_rate": ("decimal", "infant_mortality_rate"),
    "under5_mortality_rate": ("decimal", "under5_mortality_rate"),
    "institutional_delivery_pct": ("decimal", "institutional_delivery_pct"),
    "child_stunting_pct": ("decimal", "child_stunting_pct"),
    "child_wasting_pct": ("decimal", "child_wasting_pct"),
    "full_immunization_pct": ("decimal", "full_immunization_pct"),
    "anaemia_women_pct": ("decimal", "anaemia_women_pct"),
    "literacy_rate_pct": ("decimal", "literacy_rate_pct"),
    "male_literacy_rate_pct": ("decimal", "male_literacy_rate_pct"),
    "female_literacy_rate_pct": ("decimal", "female_literacy_rate_pct"),
    "literacy_gender_gap_pct": ("decimal", "literacy_gender_gap_pct"),
    "secondary_education_pct": ("decimal", "secondary_education_pct"),
    "graduate_and_above_pct": ("decimal", "graduate_and_above_pct"),
    "per_capita_income_inr": ("int", "per_capita_income_inr"),
    "bpl_households_pct": ("decimal", "bpl_households_pct"),
    "unemployment_rate_pct": ("decimal", "unemployment_rate_pct"),
    "agricultural_workers_pct": ("decimal", "agricultural_workers_pct"),
    "banking_access_pct": ("decimal", "banking_access_pct"),
    "crime_rate_per_lakh": ("decimal", "crime_rate_per_lakh"),
    "crimes_against_women_per_lakh": ("decimal", "crimes_against_women_per_lakh"),
    "crimes_against_sc_st_per_lakh": ("decimal", "crimes_against_sc_st_per_lakh"),
    "pucca_housing_pct": ("decimal", "pucca_housing_pct"),
    "tap_water_pct": ("decimal", "tap_water_pct"),
    "electricity_pct": ("decimal", "electricity_pct"),
    "sanitation_pct": ("decimal", "sanitation_pct"),
}


class Command(BaseCommand):
    help = "Import constituency dimension data from dim_constituencies.csv"

    def add_arguments(self, parser):
        parser.add_argument(
            "--csv-path",
            type=str,
            default="",
            help="Path to dim_constituencies.csv (default: data/dim_constituencies.csv)",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        if not csv_path:
            csv_path = str(settings.BASE_DIR.parent / "data" / "dim_constituencies.csv")

        path = Path(csv_path)
        if not path.exists():
            raise ValueError(f"CSV not found: {path}")

        with path.open("r", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        if not rows:
            self.stdout.write(self.style.WARNING("No rows found in CSV."))
            return

        constituencies_by_norm: dict[str, Constituency] = {}
        for c in Constituency.objects.all():
            norm = _normalize(c.name)
            constituencies_by_norm[norm] = c

        updated = 0
        created = 0
        skipped = 0
        for row in rows:
            csv_name = row.get("constituency_name", "").strip()
            if not csv_name:
                skipped += 1
                continue

            norm = _normalize(csv_name)
            constituency = constituencies_by_norm.get(norm)

            if not constituency:
                constituency = Constituency(name=csv_name)
                created += 1

            number = _parse_int(row.get("constituency_number", ""))
            if number and not constituency.number:
                constituency.number = number

            district = row.get("district", "").strip()
            if district and not constituency.district:
                constituency.district = district

            reservation = row.get("reservation_type", "").strip()
            if reservation and not constituency.reservation_category:
                constituency.reservation_category = reservation

            changed = False
            for csv_col, (field_type, model_field) in FIELD_MAP.items():
                raw_value = row.get(csv_col, "").strip()
                if not raw_value:
                    continue

                if field_type == "char":
                    new_value = raw_value
                    if getattr(constituency, model_field, "") != new_value:
                        setattr(constituency, model_field, new_value)
                        changed = True
                elif field_type == "decimal":
                    new_value = _parse_decimal(raw_value)
                    if new_value is not None and getattr(constituency, model_field) != new_value:
                        setattr(constituency, model_field, new_value)
                        changed = True
                elif field_type == "int":
                    new_value = _parse_int(raw_value)
                    if new_value is not None and getattr(constituency, model_field) != new_value:
                        setattr(constituency, model_field, new_value)
                        changed = True

            if changed or constituency.pk is None:
                constituency.save()
                if constituency.pk and not changed:
                    pass
                else:
                    updated += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Done: {updated} updated, {created} created, {skipped} skipped"
            )
        )
