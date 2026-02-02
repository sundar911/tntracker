from __future__ import annotations

import time
from decimal import Decimal, ROUND_HALF_UP

from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.download import download_file
from core.ingestion.form21e import parse_form21e_pdf
from core.models import Candidate, CandidateResult, Constituency, Election, Party, SourceDocument


class Command(BaseCommand):
    help = "Download and import TNLA 2021 Form 21E PDFs to populate candidates/results."

    def add_arguments(self, parser):
        parser.add_argument("--start", type=int, default=1, help="Starting AC number")
        parser.add_argument("--end", type=int, default=234, help="Ending AC number")
        parser.add_argument(
            "--base-url",
            default="https://elections.tn.gov.in/Form21E_TNLA2021/AC{num:03d}.pdf",
            help="Form 21E URL template",
        )
        parser.add_argument("--timeout", type=int, default=30, help="Download timeout in seconds")
        parser.add_argument("--retries", type=int, default=2, help="Retry count for downloads")
        parser.add_argument("--backoff", type=float, default=1.5, help="Retry backoff multiplier")
        parser.add_argument("--sleep", type=float, default=0, help="Sleep seconds between downloads")
        parser.add_argument(
            "--skip-existing",
            action="store_true",
            help="Skip downloading PDFs that already exist on disk",
        )
        parser.add_argument(
            "--continue-on-error",
            action="store_true",
            help="Continue if a PDF download or parse fails",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        start = options["start"]
        end = options["end"]
        base_url = options["base_url"]
        timeout = options["timeout"]
        retries = options["retries"]
        backoff = options["backoff"]
        sleep_seconds = options["sleep"]
        skip_existing = options["skip_existing"]
        continue_on_error = options["continue_on_error"]

        election_source = SourceDocument.objects.create(
            title="TNLA 2021 Form 21E (Official)",
            url="https://www.elections.tn.gov.in/Form21E_TNLA2021.aspx",
            source_type=SourceDocument.SourceType.OFFICIAL,
        )
        election, _ = Election.objects.get_or_create(
            year=2021,
            defaults={"name": "Tamil Nadu Legislative Assembly", "data_vintage_label": "Official 2021 Form 21E"},
        )
        if not election.source_document:
            election.source_document = election_source
            election.save()

        imported_candidates = 0
        for ac_no in range(start, end + 1):
            url = base_url.format(num=ac_no)
            try:
                pdf_path = download_file(
                    url,
                    f"data/form21e/AC{ac_no:03d}.pdf",
                    timeout=timeout,
                    retries=retries,
                    backoff=backoff,
                    skip_existing=skip_existing,
                )
            except Exception as exc:
                if continue_on_error:
                    self.stdout.write(
                        self.style.WARNING(f"Failed to download AC{ac_no:03d}: {exc}")
                    )
                    continue
                raise
            source = SourceDocument.objects.create(
                title=f"Form 21E AC{ac_no:03d}",
                url=url,
                source_type=SourceDocument.SourceType.OFFICIAL,
            )
            try:
                parsed = parse_form21e_pdf(pdf_path)
            except Exception as exc:
                if continue_on_error:
                    self.stdout.write(
                        self.style.WARNING(f"Failed to parse AC{ac_no:03d}: {exc}")
                    )
                    continue
                raise
            if not parsed.candidates:
                self.stdout.write(self.style.WARNING(f"No candidates parsed for AC{ac_no:03d}"))
                continue
            constituency_name = parsed.constituency or f"AC {ac_no:03d}"
            constituency, _ = Constituency.objects.get_or_create(name=constituency_name)

            total_votes = sum(row.votes or 0 for row in parsed.candidates)
            for idx, row in enumerate(parsed.candidates, start=1):
                party, _ = Party.objects.get_or_create(name=row.party or "Independent")
                candidate, _ = Candidate.objects.get_or_create(
                    name=row.name,
                    constituency=constituency,
                    defaults={"party": party, "status": Candidate.Status.CONTESTING},
                )
                candidate.party = party
                candidate.save()
                vote_share = None
                if row.votes is not None and total_votes:
                    vote_share = (
                        Decimal(row.votes) * Decimal("100.0") / Decimal(total_votes)
                    ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

                CandidateResult.objects.update_or_create(
                    candidate=candidate,
                    election=election,
                    defaults={
                        "votes": row.votes,
                        "vote_share": vote_share,
                        "position": idx,
                        "is_winner": idx == 1,
                    },
                )
                imported_candidates += 1

            if sleep_seconds:
                self.stdout.write(f"Sleeping {sleep_seconds}s before next PDF...")
                time.sleep(sleep_seconds)

        self.stdout.write(self.style.SUCCESS(f"Imported {imported_candidates} candidate results."))
