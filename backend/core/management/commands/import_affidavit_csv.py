from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.eci_affidavit import load_affidavit_csv
from core.models import Affidavit, Candidate, Constituency, Party, SourceDocument


class Command(BaseCommand):
    help = "Import candidate affidavit data from a CSV file."

    def add_arguments(self, parser):
        parser.add_argument("csv_path", type=str, help="Path to affidavit CSV file")
        parser.add_argument("--source-title", type=str, default="ECI Affidavit CSV")
        parser.add_argument("--source-url", type=str, default="")

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        source = SourceDocument.objects.create(
            title=options["source_title"],
            url=options["source_url"],
            source_type=SourceDocument.SourceType.OFFICIAL,
        )

        records = load_affidavit_csv(csv_path)
        created = 0
        for record in records:
            constituency, _ = Constituency.objects.get_or_create(name=record.constituency)
            party, _ = Party.objects.get_or_create(name=record.party)
            candidate, _ = Candidate.objects.get_or_create(
                name=record.name,
                constituency=constituency,
                defaults={"party": party, "status": record.status},
            )
            candidate.party = party
            candidate.status = record.status
            candidate.education = record.education or candidate.education
            candidate.save()

            Affidavit.objects.create(
                candidate=candidate,
                source_document=source,
                criminal_cases_count=record.criminal_cases,
                serious_criminal_cases_count=record.serious_cases,
                assets_total=record.assets_total,
                liabilities_total=record.liabilities_total,
                education=record.education,
            )
            created += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {created} affidavit records."))
