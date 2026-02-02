# ruff: noqa: E501
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Candidate, Constituency, Manifesto, Party, SourceDocument


def _load_index(path_or_url: str) -> list[dict]:
    if path_or_url.startswith("http://") or path_or_url.startswith("https://"):
        with urlopen(path_or_url) as response:
            return json.loads(response.read().decode("utf-8"))
    path = Path(path_or_url)
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_date(value: str | None):
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


class Command(BaseCommand):
    help = "Import manifestos from a JSON index (URL or local path)."

    def add_arguments(self, parser):
        parser.add_argument("--index-url", type=str, default="", help="Manifesto index JSON URL")
        parser.add_argument("--index-path", type=str, default="", help="Manifesto index JSON path")

    @transaction.atomic
    def handle(self, *args, **options):
        index_url = options["index_url"]
        index_path = options["index_path"]
        if not index_url and not index_path:
            raise ValueError("Provide either --index-url or --index-path")

        entries = _load_index(index_url or index_path)
        imported = 0
        for entry in entries:
            party_name = (entry.get("party") or "").strip()
            constituency_name = (entry.get("constituency") or "").strip()
            candidate_name = (entry.get("candidate") or "").strip()
            if not party_name:
                self.stdout.write(self.style.WARNING("Skipping manifesto entry without party name."))
                continue

            source_type = (entry.get("source_type") or "official").lower()
            if source_type not in SourceDocument.SourceType.values:
                source_type = SourceDocument.SourceType.OFFICIAL

            source = SourceDocument.objects.create(
                title=entry.get("source_title") or f"Manifesto: {party_name}",
                url=entry.get("source_url") or entry.get("document_url", ""),
                source_type=source_type,
                published_at=_parse_date(entry.get("published_at")),
            )

            party, _ = Party.objects.get_or_create(name=party_name)
            constituency = None
            candidate = None
            if constituency_name:
                constituency, _ = Constituency.objects.get_or_create(name=constituency_name)
            if candidate_name:
                if not constituency:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping candidate-only manifesto without constituency: {candidate_name}"
                        )
                    )
                else:
                    candidate, _ = Candidate.objects.get_or_create(
                        name=candidate_name,
                        constituency=constituency,
                        defaults={"party": party, "status": Candidate.Status.CONTESTING},
                    )

            Manifesto.objects.update_or_create(
                party=party,
                constituency=constituency,
                candidate=candidate,
                source_document=source,
                defaults={
                    "summary": entry.get("summary", ""),
                    "summary_ta": entry.get("summary_ta", ""),
                    "document_url": entry.get("document_url", ""),
                },
            )
            imported += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} manifestos."))
