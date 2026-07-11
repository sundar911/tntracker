"""
Re-transliterate constituency names to Tamil using the Sarvam Transliterate API.

The existing Constituency.name_ta values contain bad semantic translations
(e.g. "Madhavaram" -> "மதமாறுதல்" meaning "religion change"). This command
uses the Sarvam TRANSLITERATE endpoint to get proper phonetic Tamil names.

Usage:
    python manage.py transliterate_constituencies_sarvam
    python manage.py transliterate_constituencies_sarvam --dry-run
    python manage.py transliterate_constituencies_sarvam --force  # overwrite existing
"""

import os
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand

from core.models import Constituency

SARVAM_URL = "https://api.sarvam.ai/transliterate"
MIN_GAP = 0.15


class Command(BaseCommand):
    help = "Transliterate constituency names to proper Tamil via Sarvam"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--force", action="store_true",
                            help="Overwrite existing name_ta values")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]

        api_key = os.environ.get("SARVAM_API_KEY", "")
        if not api_key:
            env_path = Path(settings.BASE_DIR).parent / ".env"
            if env_path.exists():
                for line in env_path.read_text().splitlines():
                    if line.startswith("SARVAM_API_KEY="):
                        api_key = line.split("=", 1)[1].strip()
                        break
        if not api_key and not dry_run:
            self.stderr.write(self.style.ERROR("SARVAM_API_KEY not set"))
            return

        qs = Constituency.objects.all().order_by("name")
        if not force:
            qs = qs.filter(name_ta="")
        consts = list(qs)
        self.stdout.write(f"Will transliterate {len(consts)} constituencies "
                          f"({'overwrite' if force else 'missing only'})")

        if dry_run:
            for c in consts[:20]:
                self.stdout.write(f"  {c.name}  -> (existing: {c.name_ta!r})")
            return

        if not consts:
            self.stdout.write(self.style.SUCCESS("Nothing to do."))
            return

        headers = {"api-subscription-key": api_key, "Content-Type": "application/json"}
        updated = 0
        failed = 0
        last_call = 0.0

        for i, c in enumerate(consts):
            # Clean the name: strip the (SC), (ST), (North), etc. suffix for cleaner transliteration
            # Transliterate main part, then add suffix back
            name = c.name.strip()
            for marker in ["(SC)", "(ST)", "(GEN)"]:
                if marker in name:
                    name = name.replace(marker, "").strip()
                    break
            # Sarvam transliterate is case-sensitive; UPPERCASE inputs return
            # noticeably worse results (e.g. ALANGUDI -> அலைகுடி vs
            # Alangudi -> அலங்கூடி). Normalize to title case.
            if name.isupper():
                name = " ".join(w.capitalize() for w in name.split())

            elapsed = time.time() - last_call
            if elapsed < MIN_GAP:
                time.sleep(MIN_GAP - elapsed)

            try:
                resp = requests.post(
                    SARVAM_URL,
                    headers=headers,
                    json={
                        "input": name,
                        "source_language_code": "en-IN",
                        "target_language_code": "ta-IN",
                    },
                    timeout=10,
                )
                last_call = time.time()
                resp.raise_for_status()
                tamil = resp.json().get("transliterated_text", "").strip()
                if tamil:
                    c.name_ta = tamil
                    c.save(update_fields=["name_ta"])
                    updated += 1
                else:
                    failed += 1
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  FAIL {c.name}: {exc}")
                if "429" in str(exc):
                    time.sleep(5)

            if (i + 1) % 25 == 0:
                self.stdout.write(f"  {i+1}/{len(consts)}  (updated {updated}, failed {failed})")

        self.stdout.write(self.style.SUCCESS(f"Done. Updated {updated}, failed {failed}"))
