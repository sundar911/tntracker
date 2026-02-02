from __future__ import annotations

import re
from urllib.request import urlopen

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Candidate, Constituency, Party, SourceDocument


SOURCES = [
    {
        "name": "Times of India",
        "url": "https://timesofindia.indiatimes.com/city/chennai/ntk-marches-ahead-by-declaring-candidates-commences-campaign/articleshow/125599015.cms",
        "parser": "parse_english_for_pairs",
    },
    {
        "name": "Times Now Tamil",
        "url": "https://tamil.timesnownews.com/news/tamil-nadu-election-2026-ntk-seeman-releases-first-100-candidates-list-check-star-faces-here-article-153251924",
        "parser": "parse_tamil_pairs",
    },
    {
        "name": "News Today",
        "url": "https://newstodaynet.com/2025/12/06/2026-polls-ntk-releases-first-list-of-100-candidates/",
        "parser": "parse_english_for_pairs",
    },
]


def fetch_text(url: str) -> str:
    with urlopen(url) as response:
        return response.read().decode("utf-8", errors="ignore")


def normalize_space(value: str) -> str:
    return " ".join(value.split()).strip()


def parse_english_for_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for match in re.findall(r"([A-Z][A-Za-z\.\s]+)\s+for\s+([A-Z][A-Za-z\s\-]+)", text):
        name, constituency = match
        name = normalize_space(name)
        constituency = normalize_space(constituency)
        if len(name.split()) >= 2 and len(constituency.split()) >= 1:
            pairs.append((name, constituency))
    return pairs


def parse_tamil_pairs(text: str) -> list[tuple[str, str]]:
    pairs = []
    for match in re.findall(r"([\u0B80-\u0BFFA-Za-z\.\s]+)-\s*([\u0B80-\u0BFFA-Za-z\s]+)தொகுதி", text):
        name, constituency = match
        name = normalize_space(name)
        constituency = normalize_space(constituency)
        if name and constituency:
            pairs.append((name, constituency))
    return pairs


class Command(BaseCommand):
    help = "Import 2026 NTK candidate announcements from credible news sources."

    @transaction.atomic
    def handle(self, *args, **options):
        ntk, _ = Party.objects.get_or_create(
            name="Naam Tamilar Katchi",
            defaults={"abbreviation": "NTK", "name_ta": "நாம் தமிழர் கட்சி"},
        )

        total_created = 0
        for source in SOURCES:
            try:
                html = fetch_text(source["url"])
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"{source['name']} fetch failed: {exc}"))
                continue
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(" ", strip=True)
            parser = globals()[source["parser"]]
            pairs = parser(text)

            if not pairs:
                self.stdout.write(self.style.WARNING(f"No candidates parsed from {source['name']}"))
                continue

            SourceDocument.objects.create(
                title=f"NTK 2026 announcements - {source['name']}",
                url=source["url"],
                source_type=SourceDocument.SourceType.MEDIA,
            )

            created = 0
            for name, constituency_name in pairs:
                constituency, _ = Constituency.objects.get_or_create(name=constituency_name)
                candidate, created_flag = Candidate.objects.get_or_create(
                    name=name,
                    constituency=constituency,
                    defaults={"party": ntk, "status": Candidate.Status.ANNOUNCED},
                )
                candidate.party = ntk
                candidate.status = Candidate.Status.ANNOUNCED
                candidate.save()
                if created_flag:
                    created += 1
            total_created += created
            self.stdout.write(self.style.SUCCESS(f"{source['name']}: imported {created} candidates."))

        self.stdout.write(self.style.SUCCESS(f"Total NTK candidates imported: {total_created}"))
