from __future__ import annotations

import csv
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from urllib.request import urlopen

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Candidate, CandidateResult, Constituency, Election, Party, SourceDocument


def _normalize_header(header: str) -> str:
    return "".join(ch.lower() if ch.isalnum() else "_" for ch in header).strip("_")


def _first_value(row: dict, *keys: str):
    for key in keys:
        if key in row and row[key]:
            return row[key]
    return ""


def _parse_int(value: str):
    if value is None:
        return None
    cleaned = str(value).replace(",", "").strip()
    return int(cleaned) if cleaned.isdigit() else None


def _load_csv(path_or_url: str) -> list[dict]:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        with urlopen(path_or_url) as response:
            content = response.read().decode("utf-8", errors="ignore").splitlines()
        reader = csv.DictReader(content)
    else:
        path = Path(path_or_url)
        with path.open("r", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)

    normalized_rows = []
    for row in reader:
        normalized = {_normalize_header(k): v for k, v in row.items() if k}
        normalized_rows.append(normalized)
    return normalized_rows


class Command(BaseCommand):
    help = "Import constituency-level results from a CSV (official statistical report exports)."

    def add_arguments(self, parser):
        parser.add_argument("--csv-path", type=str, default="", help="Path to results CSV file")
        parser.add_argument("--csv-url", type=str, default="", help="URL to results CSV file")
        parser.add_argument("--source-url", type=str, default="", help="Source URL for provenance")
        parser.add_argument("--source-title", type=str, default="Official results CSV")
        parser.add_argument("--year", type=int, default=2021, help="Election year for results")

    @transaction.atomic
    def handle(self, *args, **options):
        csv_path = options["csv_path"]
        csv_url = options["csv_url"]
        if not csv_path and not csv_url:
            raise ValueError("Provide either --csv-path or --csv-url")

        source = SourceDocument.objects.create(
            title=options["source_title"],
            url=options["source_url"] or csv_url,
            source_type=SourceDocument.SourceType.OFFICIAL,
        )
        election, _ = Election.objects.get_or_create(
            year=options["year"],
            defaults={"name": "Tamil Nadu Legislative Assembly", "data_vintage_label": "Official results"},
        )
        if not election.source_document:
            election.source_document = source
            election.save()

        rows = _load_csv(csv_url or csv_path)
        if not rows:
            self.stdout.write(self.style.WARNING("No rows found in results CSV."))
            return

        staged = []
        for row in rows:
            constituency_name = _first_value(
                row,
                "constituency",
                "constituency_name",
                "assembly_constituency",
                "ac_name",
                "ac",
            )
            candidate_name = _first_value(row, "candidate", "candidate_name", "name")
            party_name = _first_value(row, "party", "party_name")
            votes = _parse_int(_first_value(row, "votes", "vote", "valid_votes"))
            position = _parse_int(_first_value(row, "position", "rank", "place"))
            is_winner_raw = _first_value(row, "is_winner", "winner", "won")
            is_winner = str(is_winner_raw).strip().lower() in {"1", "true", "yes", "y", "winner", "won"}
            total_votes = _parse_int(_first_value(row, "total_votes", "total_valid_votes"))

            if not constituency_name or not candidate_name:
                continue

            staged.append(
                {
                    "constituency": constituency_name.strip(),
                    "candidate": candidate_name.strip(),
                    "party": party_name.strip() if party_name else "Independent",
                    "votes": votes,
                    "position": position,
                    "is_winner": is_winner,
                    "total_votes": total_votes,
                }
            )

        if not staged:
            self.stdout.write(self.style.WARNING("No usable rows found in results CSV."))
            return

        totals_by_constituency = {}
        for row in staged:
            if row["total_votes"] is not None:
                totals_by_constituency[row["constituency"]] = row["total_votes"]
        if not totals_by_constituency:
            for row in staged:
                totals_by_constituency.setdefault(row["constituency"], 0)
                totals_by_constituency[row["constituency"]] += row["votes"] or 0

        imported = 0
        for row in staged:
            constituency, _ = Constituency.objects.get_or_create(name=row["constituency"])
            party, _ = Party.objects.get_or_create(name=row["party"])
            candidate, _ = Candidate.objects.get_or_create(
                name=row["candidate"],
                constituency=constituency,
                defaults={"party": party, "status": Candidate.Status.CONTESTING},
            )
            candidate.party = party
            candidate.save()

            total_votes = totals_by_constituency.get(row["constituency"]) or 0
            vote_share = None
            if row["votes"] is not None and total_votes:
                vote_share = (
                    Decimal(row["votes"]) * Decimal("100.0") / Decimal(total_votes)
                ).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)

            CandidateResult.objects.update_or_create(
                candidate=candidate,
                election=election,
                defaults={
                    "votes": row["votes"],
                    "vote_share": vote_share,
                    "position": row["position"],
                    "is_winner": row["is_winner"],
                },
            )
            imported += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} results rows."))
