from __future__ import annotations

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Sync official 2021 data and overlay any available 2026 updates."

    def add_arguments(self, parser):
        parser.add_argument(
            "--with-geojson",
            action="store_true",
            help="Download constituency boundaries from GeoJSON URL",
        )
        parser.add_argument(
            "--geojson-url",
            default="https://raw.githubusercontent.com/baskicanvas/tamilnadu-assembly-constituency-maps/main/tn_ac_2021.geojson",
            help="GeoJSON URL",
        )
        parser.add_argument("--form21e-timeout", type=int, default=30, help="Form 21E download timeout")
        parser.add_argument("--form21e-retries", type=int, default=2, help="Form 21E download retries")
        parser.add_argument("--form21e-backoff", type=float, default=1.5, help="Form 21E retry backoff")
        parser.add_argument("--form21e-sleep", type=float, default=0, help="Seconds to sleep between PDFs")
        parser.add_argument(
            "--form21e-skip-existing",
            action="store_true",
            help="Skip downloading Form 21E PDFs already on disk",
        )
        parser.add_argument(
            "--form21e-continue-on-error",
            action="store_true",
            help="Continue if Form 21E downloads fail",
        )
        parser.add_argument(
            "--with-form21e",
            action="store_true",
            help="Enable Form 21E PDF downloads (disabled by default)",
        )
        parser.add_argument(
            "--skip-form21e",
            action="store_true",
            help="Skip Form 21E PDF downloads entirely",
        )
        parser.add_argument("--manifesto-index-url", type=str, default="", help="Manifesto index JSON URL")
        parser.add_argument("--manifesto-index-path", type=str, default="", help="Manifesto index JSON path")
        parser.add_argument("--results-csv-path", type=str, default="", help="Results CSV path (official)")
        parser.add_argument("--results-csv-url", type=str, default="", help="Results CSV URL (official)")
        parser.add_argument(
            "--results-csv-source-url",
            type=str,
            default="",
            help="Source URL for the results CSV provenance",
        )

    def handle(self, *args, **options):
        if options["with_geojson"]:
            try:
                call_command(
                    "sync_constituencies_geojson",
                    url=options["geojson_url"],
                    source_url="https://github.com/baskicanvas/tamilnadu-assembly-constituency-maps",
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"GeoJSON sync failed: {exc}"))

        if options["with_form21e"] and not options["skip_form21e"]:
            try:
                call_command(
                    "sync_tnla2021_form21e",
                    timeout=options["form21e_timeout"],
                    retries=options["form21e_retries"],
                    backoff=options["form21e_backoff"],
                    sleep=options["form21e_sleep"],
                    skip_existing=options["form21e_skip_existing"],
                    continue_on_error=options["form21e_continue_on_error"],
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Form 21E sync failed: {exc}"))
        if options["results_csv_path"] or options["results_csv_url"]:
            try:
                call_command(
                    "import_results_csv",
                    csv_path=options["results_csv_path"],
                    csv_url=options["results_csv_url"],
                    source_url=options["results_csv_source_url"],
                    year=2021,
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Results CSV import failed: {exc}"))
        try:
            call_command("sync_ntk_2026_announcements")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"2026 NTK announcements failed: {exc}"))
        try:
            call_command("sync_myneta_tn2021")
        except Exception as exc:
            self.stdout.write(self.style.WARNING(f"MyNeta 2021 sync failed: {exc}"))
        manifesto_index_url = options["manifesto_index_url"]
        manifesto_index_path = options["manifesto_index_path"]
        if manifesto_index_url or manifesto_index_path:
            try:
                call_command(
                    "sync_manifestos_index",
                    index_url=manifesto_index_url,
                    index_path=manifesto_index_path,
                )
            except Exception as exc:
                self.stdout.write(self.style.WARNING(f"Manifesto sync failed: {exc}"))

        self.stdout.write(self.style.SUCCESS("Sync complete."))
