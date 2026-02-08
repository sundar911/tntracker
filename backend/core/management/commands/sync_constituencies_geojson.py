from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core.ingestion.download import download_file
from core.ingestion.geoimport import iter_constituency_features, load_geojson
from core.models import Constituency, SourceDocument


class Command(BaseCommand):
    help = "Download and import constituency boundaries from a GeoJSON URL."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="GeoJSON URL to download")
        parser.add_argument("--source-title", type=str, default="Constituency GeoJSON")
        parser.add_argument("--source-url", type=str, default="")

    @transaction.atomic
    def handle(self, *args, **options):
        url = options["url"]
        source = SourceDocument.objects.create(
            title=options["source_title"],
            url=options["source_url"] or url,
            source_type=SourceDocument.SourceType.OFFICIAL,
        )

        local_path = download_file(url, "data/constituencies.geojson")
        geojson = load_geojson(local_path)
        updated = 0
        for feature in iter_constituency_features(geojson):
            if not feature["name"]:
                continue
            constituency, _ = Constituency.objects.get_or_create(name=feature["name"])
            constituency.number = feature["number"] or constituency.number
            constituency.district = feature.get("district") or constituency.district
            constituency.boundary_geojson = feature["geometry"]
            constituency.save()
            updated += 1

        # Propagate district to matching uppercase Constituency records
        # (created by CSV imports) that lack district info.
        for c in Constituency.objects.filter(boundary_geojson__isnull=False).exclude(district=""):
            Constituency.objects.filter(
                name__iexact=c.name,
                district="",
            ).exclude(pk=c.pk).update(district=c.district, number=c.number)

        self.stdout.write(self.style.SUCCESS(f"Imported {updated} constituency boundaries."))
