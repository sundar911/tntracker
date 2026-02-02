from __future__ import annotations

from urllib.parse import urljoin

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.myneta import fetch_html, parse_myneta_profile
from core.ingestion.myneta_import import upsert_myneta_profile


BASE_URL = "https://www.myneta.info/TamilNadu2021/"


def _extract_constituency_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "action=show_candidates" in href and "constituency_id=" in href:
            if "BYE ELECTION" in link.get_text(" ", strip=True).upper():
                continue
            links.append(urljoin(BASE_URL, href))
    return sorted(set(links))


def _extract_candidate_links(html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "candidate.php?candidate_id=" in href:
            links.append(urljoin(BASE_URL, href))
    return sorted(set(links))


def _extract_constituency_name(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    header = soup.find(["h1", "h2", "h3"])
    if header:
        text = " ".join(header.get_text(" ", strip=True).split())
        if text:
            return text
    text = soup.get_text(" ", strip=True)
    match = re.search(r"List of Candidates in\s*([A-Za-z\s]+)", text)
    if match:
        return match.group(1).strip()
    return None


class Command(BaseCommand):
    help = "Import ADR/MyNeta legal history for Tamil Nadu 2021 candidates."

    def add_arguments(self, parser):
        parser.add_argument("--limit", type=int, default=0, help="Limit number of candidate profiles")

    @transaction.atomic
    def handle(self, *args, **options):
        limit = options["limit"]
        try:
            base_html = fetch_html(BASE_URL)
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"Failed to load MyNeta index: {exc}"))
            return
        constituency_links = _extract_constituency_links(base_html)

        imported = 0
        for constituency_url in constituency_links:
            try:
                page_html = fetch_html(constituency_url)
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Failed constituency page: {constituency_url} ({exc})"))
                continue
            candidate_links = _extract_candidate_links(page_html)
            constituency_name = _extract_constituency_name(page_html) or constituency_url.split("constituency_id=")[-1]
            for candidate_url in candidate_links:
                try:
                    profile_html = fetch_html(candidate_url)
                    profile = parse_myneta_profile(profile_html)
                    upsert_myneta_profile(profile, candidate_url)
                except Exception as exc:
                    self.stdout.write(self.style.WARNING(f"Failed candidate: {candidate_url} ({exc})"))
                    continue
                imported += 1
                if limit and imported >= limit:
                    self.stdout.write(self.style.WARNING("Limit reached; stopping early."))
                    self.stdout.write(self.style.SUCCESS(f"Imported {imported} MyNeta profiles."))
                    return

            self.stdout.write(
                self.style.SUCCESS(
                    f"Scraped constituency {constituency_name} ({len(candidate_links)} candidates)"
                )
            )

        self.stdout.write(self.style.SUCCESS(f"Imported {imported} MyNeta profiles."))
