# ruff: noqa: E501
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import (
    Coalition,
    Constituency,
    Election,
    Manifesto,
    ManifestoPromise,
    Party,
    PartyFulfilmentClaim,
    PromiseAssessment,
    PromiseEvidence,
    SourceDocument,
)


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


def _source_type(value: str | None) -> str:
    raw = (value or "media").strip().lower()
    if raw not in SourceDocument.SourceType.values:
        return SourceDocument.SourceType.MEDIA
    return raw


def _get_or_create_election(year: int | None) -> Election | None:
    if not year:
        return None
    election, _ = Election.objects.get_or_create(
        year=year,
        defaults={"name": f"Tamil Nadu Legislative Assembly Election {year}"},
    )
    return election


def _find_manifesto_for_owner(party: Party | None, coalition: Coalition | None) -> Manifesto | None:
    qs = Manifesto.objects.filter(constituency__isnull=True, candidate__isnull=True)
    if party:
        qs = qs.filter(party=party)
    if coalition:
        qs = qs.filter(coalition=coalition)
    return qs.order_by("-last_updated", "-id").first()


class Command(BaseCommand):
    help = "Import promise fulfilment assessments and (optional) party-level fulfilment claims."

    def add_arguments(self, parser):
        parser.add_argument("--index-url", type=str, default="", help="Promise assessments index JSON URL")
        parser.add_argument("--index-path", type=str, default="", help="Promise assessments index JSON path")

    @transaction.atomic
    def handle(self, *args, **options):
        index_url = options["index_url"]
        index_path = options["index_path"]
        if not index_url and not index_path:
            raise ValueError("Provide either --index-url or --index-path")

        entries = _load_index(index_url or index_path)
        imported_claims = 0
        imported_assessments = 0
        imported_evidence = 0

        for entry in entries:
            if not isinstance(entry, dict):
                continue

            year = entry.get("year") or entry.get("election_year")
            election = None
            try:
                election = _get_or_create_election(int(year)) if year else None
            except (TypeError, ValueError):
                election = None

            # 1) Party-level fulfilment claim (e.g., CM claims X% delivered)
            if entry.get("claimed_percent") is not None:
                party_name = (entry.get("party") or "").strip()
                if not party_name:
                    self.stdout.write(self.style.WARNING("Skipping claim entry without party name."))
                    continue
                party, _ = Party.objects.get_or_create(name=party_name)

                src = entry.get("source") or {}
                if not isinstance(src, dict):
                    src = {}
                source_document = SourceDocument.objects.create(
                    title=(src.get("title") or entry.get("source_title") or f"Fulfilment claim: {party_name}").strip(),
                    url=(src.get("url") or entry.get("source_url") or "").strip(),
                    source_type=_source_type(src.get("source_type") or entry.get("source_type") or "media"),
                    published_at=_parse_date(src.get("published_at") or entry.get("published_at")),
                    notes=(src.get("notes") or "").strip(),
                )

                claim, created = PartyFulfilmentClaim.objects.update_or_create(
                    party=party,
                    election=election,
                    as_of=_parse_date(entry.get("as_of")),
                    source_document=source_document,
                    defaults={
                        "claimed_percent": entry.get("claimed_percent"),
                        "claimed_by": (entry.get("claimed_by") or "").strip(),
                        "snippet": (src.get("quote") or entry.get("snippet") or "").strip(),
                    },
                )
                imported_claims += 1 if created else 1
                continue

            # 2) Promise-level assessment (state or constituency scope)
            promise_slug = (entry.get("promise_slug") or entry.get("slug") or "").strip()
            if not promise_slug:
                self.stdout.write(self.style.WARNING("Skipping entry without claimed_percent or promise_slug."))
                continue

            party_name = (entry.get("party") or entry.get("manifesto_party") or "").strip()
            coalition_name = (entry.get("coalition") or entry.get("manifesto_coalition") or "").strip()
            party = Party.objects.filter(name=party_name).first() if party_name else None
            coalition = Coalition.objects.filter(name=coalition_name).first() if coalition_name else None

            manifesto = _find_manifesto_for_owner(party=party, coalition=coalition)
            if not manifesto:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping assessment: manifesto not found for party='{party_name}' coalition='{coalition_name}'."
                    )
                )
                continue

            promise = ManifestoPromise.objects.filter(manifesto=manifesto, slug=promise_slug).first()
            if not promise:
                self.stdout.write(
                    self.style.WARNING(
                        f"Skipping assessment: promise '{promise_slug}' not found for manifesto '{manifesto}'."
                    )
                )
                continue

            scope = (entry.get("scope") or "").strip().lower()
            if scope not in PromiseAssessment.Scope.values:
                self.stdout.write(self.style.WARNING(f"Unknown scope '{scope}' for {promise_slug}; defaulting to 'state'."))
                scope = PromiseAssessment.Scope.STATE

            status = (entry.get("status") or "").strip().lower() or PromiseAssessment.Status.UNKNOWN
            if status not in PromiseAssessment.Status.values:
                status = PromiseAssessment.Status.UNKNOWN

            target_party = None
            target_constituency = None
            if scope == PromiseAssessment.Scope.STATE:
                target_party = party or manifesto.party
                if not target_party:
                    self.stdout.write(
                        self.style.WARNING(
                            f"Skipping state assessment without party target: {promise_slug} ({party_name or coalition_name})"
                        )
                    )
                    continue
            if scope == PromiseAssessment.Scope.CONSTITUENCY:
                constituency_name = (entry.get("constituency") or "").strip()
                if not constituency_name:
                    self.stdout.write(self.style.WARNING(f"Skipping constituency assessment without constituency: {promise_slug}"))
                    continue
                target_constituency, _ = Constituency.objects.get_or_create(name=constituency_name)

            assessment, _ = PromiseAssessment.objects.update_or_create(
                promise=promise,
                scope=scope,
                party=target_party,
                constituency=target_constituency,
                as_of=_parse_date(entry.get("as_of")),
                defaults={
                    "status": status,
                    "score": entry.get("score"),
                    "summary": (entry.get("summary") or "").strip(),
                    "summary_ta": (entry.get("summary_ta") or "").strip(),
                },
            )
            imported_assessments += 1

            evidence_list = entry.get("evidence") or []
            if not isinstance(evidence_list, list):
                evidence_list = []
            for ev in evidence_list:
                if not isinstance(ev, dict):
                    continue
                ev_url = (ev.get("url") or ev.get("source_url") or "").strip()
                ev_source = SourceDocument.objects.create(
                    title=(ev.get("source_title") or ev.get("title") or f"Evidence for {promise_slug}").strip(),
                    url=ev_url,
                    source_type=_source_type(ev.get("source_type") or "media"),
                    published_at=_parse_date(ev.get("published_at")),
                    notes=(ev.get("notes") or "").strip(),
                )
                _, created = PromiseEvidence.objects.get_or_create(
                    assessment=assessment,
                    source_document=ev_source,
                    url=ev_url,
                    quote=(ev.get("quote") or ev.get("snippet") or "").strip(),
                    published_at=_parse_date(ev.get("published_at")),
                )
                if created:
                    imported_evidence += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Imported {imported_assessments} promise assessments, {imported_evidence} evidence rows, {imported_claims} fulfilment claims."
            )
        )

