"""Bulk import 2026 candidate affidavit data from Sarvam Vision OCR markdown files.

Usage:
    python manage.py import_form26_markdowns data/eci_2026/markdown/ \\
        --source-title "ECI Affidavit 2026" --election-year 2026

    python manage.py import_form26_markdowns data/eci_2026/markdown/ --dry-run
"""
from __future__ import annotations

from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.parse_form26_markdown import parse_form26_markdown
from core.models import Affidavit, Candidate, Constituency, Election, Party, SourceDocument


class Command(BaseCommand):
    help = "Import 2026 candidate affidavit data from Sarvam Vision OCR markdown files."

    def add_arguments(self, parser):
        parser.add_argument("md_dir", help="Directory containing .md files from Sarvam Vision OCR")
        parser.add_argument("--source-title", default="ECI Affidavit 2026")
        parser.add_argument("--election-year", type=int, default=2026)
        parser.add_argument("--dry-run", action="store_true", help="Parse and print without saving to DB")

    def handle(self, *args, **options):
        md_dir = Path(options["md_dir"])
        md_files = sorted(md_dir.glob("*.md"))
        if not md_files:
            self.stderr.write(self.style.ERROR(f"No .md files found in {md_dir}"))
            return

        self.stdout.write(f"Found {len(md_files)} markdown files in {md_dir}")

        if options["dry_run"]:
            self._dry_run(md_files)
            return

        self._import(md_files, options)

    def _dry_run(self, md_files: list[Path]) -> None:
        ok = err = skip = 0
        for f in md_files:
            try:
                p = parse_form26_markdown(f)
                if not p.name or "Name of" in p.name:
                    skip += 1
                    self.stdout.write(f"  SKIP {f.name}: no name parsed")
                    continue
                ok += 1
                self.stdout.write(
                    f"  OK   {f.name}: {p.name} | {p.constituency}(#{p.constituency_number}) "
                    f"| {p.party} | Assets={p.assets_total} | Cases={p.criminal_cases_count} "
                    f"| Edu={p.education[:40] if p.education else '-'}"
                )
            except Exception as e:
                err += 1
                self.stderr.write(f"  ERR  {f.name}: {e}")

        self.stdout.write(f"\nDry run: {ok} OK, {skip} skipped, {err} errors out of {len(md_files)}")

    @transaction.atomic
    def _import(self, md_files: list[Path], options: dict) -> None:
        election, _ = Election.objects.get_or_create(
            year=options["election_year"],
            defaults={"name": f"Tamil Nadu Assembly Elections {options['election_year']}"},
        )
        source = SourceDocument.objects.create(
            title=options["source_title"],
            source_type=SourceDocument.SourceType.OFFICIAL,
        )

        created = updated = skipped = errors = 0

        for f in md_files:
            try:
                p = parse_form26_markdown(f)

                # Use filename as fallback for constituency + name
                # Filename format: CONSTITUENCY_CANDIDATENAME.md
                file_parts = f.stem.split("_", 1)
                file_constituency = file_parts[0].replace("_", " ").title() if file_parts else ""
                file_candidate = file_parts[1].replace("_", " ").title() if len(file_parts) > 1 else ""

                candidate_name = p.name if (p.name and "Name of" not in p.name and "முழு அஞ்சல்" not in p.name) else file_candidate
                constituency_name = p.constituency if (p.constituency and p.constituency_number) else file_constituency

                if not candidate_name:
                    skipped += 1
                    continue

                # Resolve or create records
                constituency, _ = Constituency.objects.get_or_create(name=constituency_name)
                if p.constituency_number and not constituency.number:
                    constituency.number = p.constituency_number
                    constituency.save(update_fields=["number"])

                party_name = p.party if (p.party and "Name of" not in p.party) else "Independent"
                party, _ = Party.objects.get_or_create(name=party_name)

                candidate, is_new = Candidate.objects.get_or_create(
                    name=candidate_name,
                    constituency=constituency,
                    defaults={
                        "party": party,
                        "status": Candidate.Status.CONTESTING,
                        "age": p.age,
                        "gender": p.gender,
                        "education": p.education,
                        "profession": p.profession,
                        "address": p.address,
                    },
                )

                if not is_new:
                    # Update fields if new data is better
                    changed = []
                    candidate.party = party
                    candidate.status = Candidate.Status.CONTESTING
                    changed += ["party", "status"]
                    for field, val in [
                        ("age", p.age),
                        ("gender", p.gender),
                        ("education", p.education),
                        ("profession", p.profession),
                        ("address", p.address),
                    ]:
                        if val and not getattr(candidate, field):
                            setattr(candidate, field, val)
                            changed.append(field)
                    candidate.save(update_fields=changed)
                    updated += 1
                else:
                    created += 1

                # Create affidavit
                Affidavit.objects.create(
                    candidate=candidate,
                    source_document=source,
                    criminal_cases_count=p.criminal_cases_count,
                    serious_criminal_cases_count=0,
                    assets_total=p.assets_total,
                    liabilities_total=p.liabilities_total,
                    education=p.education,
                    additional_details=p.additional_details or None,
                )

            except Exception as e:
                errors += 1
                self.stderr.write(self.style.ERROR(f"  ERR {f.name}: {e}"))

        self.stdout.write(self.style.SUCCESS(
            f"\nImported: {created} created, {updated} updated, "
            f"{skipped} skipped, {errors} errors out of {len(md_files)}"
        ))
