"""Django management command: import a parsed OCR affidavit JSON into the database.

Usage:
    python manage.py import_pdf_affidavit_2026 <ocr_json_path> \\
        --candidate-name "C. Joseph Vijay" \\
        --constituency "Vilavancode" \\
        --party "Tamilaga Vettri Kazhagam" \\
        --source-url "https://affidavit.eci.gov.in/..."

The OCR JSON is produced by scripts/extract_affidavit_ocr.py.
Parser overrides (--candidate-name etc.) take precedence over auto-parsed values.
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from core.ingestion.affidavit_parser_2026 import ParsedAffidavit2026, parse_affidavit_text
from core.models import Affidavit, Candidate, Constituency, Election, LegalCase, Party, SourceDocument


class Command(BaseCommand):
    help = "Import a 2026 candidate affidavit from an OCR JSON file into the database."

    def add_arguments(self, parser):
        parser.add_argument("ocr_json_path", help="Path to the OCR JSON produced by extract_affidavit_ocr.py")
        parser.add_argument("--candidate-name", default="", help="Override parsed candidate name")
        parser.add_argument("--constituency", default="", help="Override parsed constituency name")
        parser.add_argument("--party", default="", help="Override parsed party name")
        parser.add_argument("--source-url", default="", help="URL of the original affidavit document")
        parser.add_argument("--status", default="contesting", help="Candidate status (default: contesting)")
        parser.add_argument("--dry-run", action="store_true", help="Parse and print without saving to DB")

    @transaction.atomic
    def handle(self, *args, **options):
        ocr_path = Path(options["ocr_json_path"])
        if not ocr_path.exists():
            raise CommandError(f"File not found: {ocr_path}")

        data = json.loads(ocr_path.read_text(encoding="utf-8"))
        full_text = data.get("full_text", "")
        if not full_text:
            raise CommandError("OCR JSON has no 'full_text' field. Re-run extract_affidavit_ocr.py.")

        parsed: ParsedAffidavit2026 = parse_affidavit_text(full_text)

        # CLI overrides take precedence over auto-parsed values
        candidate_name = options["candidate_name"] or parsed.name or ""
        constituency_name = options["constituency"] or parsed.constituency or ""
        party_name = options["party"] or parsed.party or "Independent"

        if not candidate_name:
            raise CommandError(
                "Could not determine candidate name from OCR text. "
                "Pass --candidate-name explicitly."
            )
        if not constituency_name:
            raise CommandError(
                "Could not determine constituency from OCR text. "
                "Pass --constituency explicitly."
            )

        self.stdout.write(f"\n--- Parsed affidavit data ---")
        self.stdout.write(f"  Name:          {candidate_name}")
        self.stdout.write(f"  Constituency:  {constituency_name}")
        self.stdout.write(f"  Party:         {party_name}")
        self.stdout.write(f"  Education:     {parsed.education or '(not found)'}")
        self.stdout.write(f"  Profession:    {parsed.profession or '(not found)'}")
        self.stdout.write(f"  PAN:           {parsed.pan or '(not found)'}")
        self.stdout.write(f"  Assets total:  {parsed.assets_total}")
        self.stdout.write(f"  Liabilities:   {parsed.liabilities_total}")
        self.stdout.write(f"  Criminal cases:{parsed.criminal_cases_count}")
        self.stdout.write(f"  Address:       {parsed.address[:80] if parsed.address else '(not found)'}")
        self.stdout.write("")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run — nothing saved."))
            return

        # 1. Ensure 2026 Election record exists
        election, _ = Election.objects.get_or_create(
            year=2026,
            defaults={"name": "Tamil Nadu Assembly Elections 2026"},
        )

        # 2. Party
        party, _ = Party.objects.get_or_create(name=party_name)

        # 3. Constituency
        constituency, _ = Constituency.objects.get_or_create(name=constituency_name)

        # 4. Candidate — update if already exists (e.g. from an earlier import)
        candidate, created = Candidate.objects.get_or_create(
            name=candidate_name,
            constituency=constituency,
            defaults={
                "party": party,
                "status": options["status"],
                "education": parsed.education,
                "profession": parsed.profession,
                "address": parsed.address,
            },
        )
        if not created:
            # Update fields that may have improved with the new OCR data
            update_fields = []
            candidate.party = party
            candidate.status = options["status"]
            update_fields += ["party", "status"]
            if parsed.education and not candidate.education:
                candidate.education = parsed.education
                update_fields.append("education")
            if parsed.profession and not candidate.profession:
                candidate.profession = parsed.profession
                update_fields.append("profession")
            if parsed.address and not candidate.address:
                candidate.address = parsed.address
                update_fields.append("address")
            candidate.save(update_fields=update_fields)

        # 5. Source document
        source_title = f"ECI Affidavit 2026 — {candidate_name}"
        source = SourceDocument.objects.create(
            title=source_title,
            url=options["source_url"],
            source_type=SourceDocument.SourceType.OFFICIAL,
        )

        # 6. Affidavit record (one per source document — idempotent via source)
        affidavit = Affidavit.objects.create(
            candidate=candidate,
            source_document=source,
            criminal_cases_count=parsed.criminal_cases_count,
            serious_criminal_cases_count=0,
            assets_total=parsed.assets_total,
            liabilities_total=parsed.liabilities_total,
            education=parsed.education,
            additional_details={"pan": parsed.pan} if parsed.pan else None,
        )

        # 7. Legal cases (if any criminal cases were parsed with details)
        for case_detail in parsed.criminal_cases_details:
            LegalCase.objects.create(
                candidate=candidate,
                source_document=source,
                case_number=case_detail.get("case_number", ""),
                court=case_detail.get("court", ""),
                sections=case_detail.get("sections", ""),
                status=case_detail.get("status", ""),
                year=case_detail.get("year"),
                description=case_detail.get("description", ""),
            )

        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(
            f"{action} candidate '{candidate_name}' (id={candidate.pk}) "
            f"with affidavit id={affidavit.pk}."
        ))
        self.stdout.write(self.style.SUCCESS(
            f"Visit /candidate/{candidate.pk}/ to view on the website."
        ))
