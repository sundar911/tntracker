from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.myneta import fetch_html, parse_myneta_profile
from core.ingestion.myneta_import import upsert_myneta_profile


class Command(BaseCommand):
    help = "Import ADR/MyNeta legal history for a candidate URL."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="MyNeta candidate URL")

    @transaction.atomic
    def handle(self, *args, **options):
        url = options["url"]
        html = fetch_html(url)
        profile = parse_myneta_profile(html)
        created_cases = upsert_myneta_profile(profile, url)
        self.stdout.write(self.style.SUCCESS(f"Imported MyNeta profile with {created_cases} cases."))
