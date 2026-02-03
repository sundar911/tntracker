# ruff: noqa: E501
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from urllib.request import urlopen

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils.text import slugify

from core.models import (
    Candidate,
    Coalition,
    CoalitionMembership,
    Constituency,
    Election,
    Manifesto,
    ManifestoDocument,
    ManifestoPromise,
    Party,
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


def _parse_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def _normalize_language(value: str | None) -> str:
    lang = (value or "").strip().lower()
    if lang in {"ta", "tamil"}:
        return ManifestoDocument.Language.TA
    return ManifestoDocument.Language.EN


def _unique_promise_slug(manifesto: Manifesto, base: str) -> str:
    base = (base or "").strip()[:200]
    if not base:
        base = "promise"
    base_slug = slugify(base) or "promise"
    candidate = base_slug
    suffix = 2
    while ManifestoPromise.objects.filter(manifesto=manifesto, slug=candidate).exists():
        candidate = f"{base_slug}-{suffix}"
        suffix += 1
    return candidate


def _source_type(value: str | None) -> str:
    raw = (value or "official").strip().lower()
    if raw not in SourceDocument.SourceType.values:
        return SourceDocument.SourceType.OFFICIAL
    return raw


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
            coalition_name = (entry.get("coalition") or entry.get("alliance") or "").strip()
            constituency_name = (entry.get("constituency") or "").strip()
            candidate_name = (entry.get("candidate") or "").strip()
            if not party_name and not coalition_name:
                self.stdout.write(
                    self.style.WARNING("Skipping manifesto entry without party or coalition name.")
                )
                continue

            election_year = entry.get("election_year") or entry.get("year")
            election = None
            if election_year:
                try:
                    election = Election.objects.filter(year=int(election_year)).first()
                except (TypeError, ValueError):
                    election = None

            party = None
            coalition = None
            if coalition_name:
                coalition, _ = Coalition.objects.get_or_create(
                    name=coalition_name,
                    defaults={"name_ta": (entry.get("coalition_ta") or "").strip(), "election": election},
                )
                members = entry.get("coalition_members") or entry.get("members") or []
                if isinstance(members, list):
                    for party_member in members:
                        member_name = (party_member or "").strip()
                        if not member_name:
                            continue
                        member, _ = Party.objects.get_or_create(name=member_name)
                        CoalitionMembership.objects.get_or_create(coalition=coalition, party=member)
            if party_name:
                party, _ = Party.objects.get_or_create(name=party_name)

            documents = entry.get("documents")
            primary_url = ""
            if isinstance(documents, list) and documents:
                first_doc = documents[0] or {}
                primary_url = (first_doc.get("url") or first_doc.get("document_url") or "").strip()
                source = SourceDocument.objects.create(
                    title=first_doc.get("source_title")
                    or entry.get("source_title")
                    or f"Manifesto: {party_name or coalition_name}",
                    url=(first_doc.get("source_url") or entry.get("source_url") or primary_url),
                    source_type=_source_type(first_doc.get("source_type") or entry.get("source_type")),
                    published_at=_parse_date(first_doc.get("published_at") or entry.get("published_at")),
                )
            else:
                primary_url = (entry.get("document_url") or "").strip()
                source = SourceDocument.objects.create(
                    title=entry.get("source_title") or f"Manifesto: {party_name or coalition_name}",
                    url=entry.get("source_url") or primary_url,
                    source_type=_source_type(entry.get("source_type")),
                    published_at=_parse_date(entry.get("published_at")),
                )
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

            manifesto, _ = Manifesto.objects.update_or_create(
                party=party,
                coalition=coalition,
                constituency=constituency,
                candidate=candidate,
                defaults={
                    "source_document": source,
                    "summary": entry.get("summary", ""),
                    "summary_ta": entry.get("summary_ta", ""),
                    "document_url": primary_url,
                },
            )

            if isinstance(documents, list):
                for doc in documents:
                    if not isinstance(doc, dict):
                        continue
                    url = (doc.get("url") or doc.get("document_url") or "").strip()
                    if not url:
                        continue
                    doc_source = SourceDocument.objects.create(
                        title=doc.get("source_title") or f"Manifesto: {party_name or coalition_name}",
                        url=doc.get("source_url") or url,
                        source_type=_source_type(doc.get("source_type") or entry.get("source_type")),
                        published_at=_parse_date(doc.get("published_at") or entry.get("published_at")),
                    )
                    ManifestoDocument.objects.update_or_create(
                        manifesto=manifesto,
                        language=_normalize_language(doc.get("language")),
                        defaults={
                            "url": url,
                            "source_document": doc_source,
                            "checksum": (doc.get("checksum") or "").strip(),
                            "notes": (doc.get("notes") or "").strip(),
                        },
                    )

            promises = entry.get("promises") or []
            if isinstance(promises, list):
                for idx, raw_promise in enumerate(promises):
                    if not isinstance(raw_promise, dict):
                        continue
                    text = (raw_promise.get("text") or "").strip()
                    text_ta = (raw_promise.get("text_ta") or "").strip()
                    raw_slug = (raw_promise.get("slug") or "").strip()
                    base = raw_slug or text or text_ta
                    slug = slugify(base) if raw_slug else _unique_promise_slug(manifesto, base)
                    if not slug:
                        slug = _unique_promise_slug(manifesto, "promise")
                    ManifestoPromise.objects.update_or_create(
                        manifesto=manifesto,
                        slug=slug,
                        defaults={
                            "text": text,
                            "text_ta": text_ta,
                            "category": (raw_promise.get("category") or "").strip(),
                            "position": raw_promise.get("position")
                            if raw_promise.get("position") is not None
                            else idx + 1,
                            "tags": raw_promise.get("tags"),
                            "is_key": _parse_bool(raw_promise.get("is_key", True)),
                        },
                    )
            imported += 1

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} manifestos."))
