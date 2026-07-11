"""
Download candidate photos from ECI URLs to local static storage.

Reads data/fct_candidates_26.csv, downloads each photo_url to
backend/core/static/core/candidate-photos/ with a deterministic filename,
and updates the CSV row to point to the local path.

Works around iOS/mobile browsers refusing to load ECI images cross-origin.

Usage:
    python manage.py download_candidate_photos
    python manage.py download_candidate_photos --retry-failed
"""

import csv
import re
import time
from pathlib import Path

import requests
from django.conf import settings
from django.core.management.base import BaseCommand


def _slug(text: str, max_len: int = 40) -> str:
    """Filename-safe slug."""
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", (text or "").strip()).strip("_")
    return cleaned[:max_len] or "unknown"


class Command(BaseCommand):
    help = "Download candidate photos locally and update CSV"

    def add_arguments(self, parser):
        parser.add_argument("--retry-failed", action="store_true",
                            help="Re-attempt photos where the CSV still has an http URL")

    def handle(self, *args, **options):
        retry_failed = options["retry_failed"]
        data_dir = Path(settings.BASE_DIR).parent / "data"
        csv_path = data_dir / "fct_candidates_26.csv"
        photos_dir = Path(settings.BASE_DIR) / "core" / "static" / "core" / "candidate-photos"
        photos_dir.mkdir(parents=True, exist_ok=True)

        with open(csv_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            rows = list(reader)

        downloaded = 0
        skipped = 0
        failed = 0
        total = len(rows)

        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        })

        for i, row in enumerate(rows):
            url = (row.get("photo_url") or "").strip()
            if not url:
                continue

            # Skip if already a local path
            if url.startswith("/static/"):
                if not retry_failed:
                    skipped += 1
                    continue

            # Derive filename from constituency + candidate
            const = _slug(row.get("constituency", ""))
            name = _slug(row.get("candidate", ""))
            filename = f"{const}__{name}.jpg"
            local_path = photos_dir / filename
            local_url = f"/static/core/candidate-photos/{filename}"

            # Already downloaded → just point CSV to it
            if local_path.exists() and local_path.stat().st_size > 0:
                row["photo_url"] = local_url
                skipped += 1
                continue

            # If URL is local and file missing → keep trying remote from the backup
            remote_url = url if url.startswith("http") else ""
            if not remote_url:
                continue

            try:
                resp = session.get(remote_url, timeout=15)
                resp.raise_for_status()
                if len(resp.content) < 100:
                    raise ValueError("Photo too small, likely an error page")
                local_path.write_bytes(resp.content)
                row["photo_url"] = local_url
                downloaded += 1
                if downloaded % 50 == 0:
                    self.stdout.write(f"  Downloaded {downloaded}/{total}  (skipped {skipped}, failed {failed})")
                    # Checkpoint save
                    with open(csv_path, "w", encoding="utf-8", newline="") as f:
                        writer = csv.DictWriter(f, fieldnames=fieldnames)
                        writer.writeheader()
                        writer.writerows(rows)
                time.sleep(0.05)
            except Exception as exc:
                failed += 1
                self.stderr.write(f"  FAIL [{row.get('candidate','?')}] {exc}")

        # Final save
        with open(csv_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

        self.stdout.write(self.style.SUCCESS(
            f"Done. Downloaded {downloaded}, skipped {skipped}, failed {failed}."
        ))
