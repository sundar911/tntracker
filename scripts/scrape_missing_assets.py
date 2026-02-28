"""
Scrape missing assets/liabilities from MyNeta candidate pages using Playwright.

Reads fct_candidates_21_copy.csv, finds rows with empty total_assets but valid
myneta_url, scrapes each page with a headless browser, and writes the updated CSV.

Resumable: progress is saved to a JSON sidecar file every 10 candidates.

Usage:
    python scripts/scrape_missing_assets.py                  # full run
    python scripts/scrape_missing_assets.py --dry-run        # just count targets
    python scripts/scrape_missing_assets.py --limit 5        # scrape first 5 only
    python scripts/scrape_missing_assets.py --delay 2.0      # 2s between requests
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

CSV_PATH = Path("data/fct_candidates_21_copy.csv")
PROGRESS_PATH = Path("data/.scrape_missing_assets_progress.json")
BACKUP_PATH = CSV_PATH.with_suffix(".csv.bak")

REQUEST_DELAY = 1.0
MAX_RETRIES = 3
BACKOFF_BASE = 5.0


# ---------------------------------------------------------------------------
# Parsing helpers (adapted from scrape_2016_candidates.py with fixes)
# ---------------------------------------------------------------------------


def _get_text(element) -> str:
    """Extract clean text from a BeautifulSoup element."""
    if element is None:
        return ""
    text = " ".join(element.get_text(" ", strip=True).split())
    return text.replace("\xa0", " ")


def _format_amount(value: str) -> str:
    """Format amount string to match CSV format like 'Rs X,XX,XXX ~ X Crore+'."""
    if not value or "nil" in value.lower():
        return "Rs 0 ~"

    value = value.strip().replace("\xa0", " ")

    if value.startswith("Rs"):
        # Normalize ~ separator to " ~ " (space-tilde-space)
        value = re.sub(r"\s*~\s*", " ~ ", value)
        return value.rstrip()

    # Value without Rs prefix — add it
    match = re.match(r"([0-9,]+)\s+(.+)", value)
    if match:
        return f"Rs {match.group(1)} ~ {match.group(2)}"

    return value


def _extract_rs_amount(value: str) -> str:
    """Extract numeric amount from Rs string like 'Rs 1,54,26,000 ~ 1 Crore+'."""
    if not value:
        return ""
    match = re.search(r"Rs\s*([0-9,]+)", value)
    if match:
        return match.group(1).replace(",", "")
    match = re.search(r"([0-9,]+)", value)
    if match:
        return match.group(1).replace(",", "")
    return ""


def _extract_assets_liabilities(soup: BeautifulSoup) -> tuple[str, str]:
    """Extract assets and liabilities from the w3-table tables."""
    assets = ""
    liabilities = ""

    tables = soup.find_all("table", class_=lambda c: c and "w3-table" in c)
    for table in tables:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = _get_text(cells[0]).lower().strip()
                value = _get_text(cells[1])

                if label.startswith("assets") and not assets:
                    assets = _format_amount(value)
                elif label.startswith("liabilities") and not liabilities:
                    liabilities = _format_amount(value)

        if assets and liabilities:
            break

    return assets, liabilities


# ---------------------------------------------------------------------------
# Playwright-based fetching
# ---------------------------------------------------------------------------


def _fetch_html(page, url: str) -> str:
    """Fetch a URL using a Playwright page and return rendered HTML."""
    for attempt in range(MAX_RETRIES):
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            return page.content()
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2**attempt)
                print(
                    f"    Retry {attempt + 1}/{MAX_RETRIES} after {wait}s: {exc}",
                    file=sys.stderr,
                )
                time.sleep(wait)
            else:
                raise
    return ""


def _parse_page(html: str) -> tuple[str, str, str, str]:
    """Parse HTML and return (total_assets, liabilities, total_assets_rs, liabilities_rs)."""
    soup = BeautifulSoup(html, "lxml")
    assets, liabilities = _extract_assets_liabilities(soup)
    return (
        assets,
        liabilities,
        _extract_rs_amount(assets),
        _extract_rs_amount(liabilities),
    )


# ---------------------------------------------------------------------------
# Progress tracking (resumability)
# ---------------------------------------------------------------------------


def _load_progress() -> dict[str, dict[str, str]]:
    """Load dict mapping myneta_url -> scraped column values."""
    if PROGRESS_PATH.exists():
        data = json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
        return data.get("completed", {})
    return {}


def _save_progress(completed: dict[str, dict[str, str]]) -> None:
    """Persist scraped data for resumability."""
    PROGRESS_PATH.write_text(
        json.dumps({"completed": completed}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(*, dry_run: bool = False, limit: int = 0, delay: float = REQUEST_DELAY):
    # 1. Read CSV into memory
    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # 2. Identify target rows (empty total_assets with a valid myneta_url)
    targets = [
        (i, row)
        for i, row in enumerate(rows)
        if not row.get("total_assets", "").strip()
        and row.get("myneta_url", "").strip()
    ]
    print(f"Total rows: {len(rows)}, targets to scrape: {len(targets)}")

    if dry_run:
        return

    # 3. Load progress for resumability
    completed = _load_progress()
    remaining = [(i, row) for i, row in targets if row["myneta_url"] not in completed]
    print(
        f"Already completed: {len(targets) - len(remaining)}, remaining: {len(remaining)}"
    )

    if limit > 0:
        remaining = remaining[:limit]
        print(f"Limited to first {limit} remaining targets")

    if not remaining:
        print("Nothing to scrape. All targets already completed.")
    else:
        # 4. Launch Playwright browser and scrape
        errors: list[tuple[int, str, str]] = []

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()

            for count, (i, row) in enumerate(remaining, start=1):
                url = row["myneta_url"]
                name = row.get("candidate", "?")
                print(f"[{count}/{len(remaining)}] {name} -- {url}")

                try:
                    html = _fetch_html(page, url)
                    assets, liabilities, assets_rs, liabilities_rs = _parse_page(html)
                    completed[url] = {
                        "total_assets": assets,
                        "liabilities": liabilities,
                        "total_assets_rs": assets_rs,
                        "liabilities_rs": liabilities_rs,
                    }
                    print(f"    Assets: {assets} | Liabilities: {liabilities}")
                except Exception as exc:
                    errors.append((i, url, str(exc)))
                    print(f"    ERROR: {exc}", file=sys.stderr)

                # Save progress every 10 candidates
                if count % 10 == 0:
                    _save_progress(completed)
                    print(f"    [progress saved: {len(completed)} completed]")

                # Rate limiting
                if count < len(remaining):
                    time.sleep(delay)

            browser.close()

        # Final progress save
        _save_progress(completed)

        if errors:
            print(f"\n{len(errors)} errors encountered:")
            for i, url, err in errors:
                print(f"  Row {i}: {url} -- {err}")

    # 5. Apply all completed data (this run + previous runs) to rows
    applied = 0
    for i, row in targets:
        url = row["myneta_url"]
        if url in completed:
            row.update(completed[url])
            applied += 1

    print(f"\nApplied scraped data to {applied} rows")

    # 6. Back up original CSV, then write updated CSV
    shutil.copy2(CSV_PATH, BACKUP_PATH)
    print(f"Backup saved to {BACKUP_PATH}")

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {CSV_PATH}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape missing assets/liabilities from MyNeta"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just print target count, don't scrape",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Only scrape first N remaining targets",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=REQUEST_DELAY,
        help=f"Seconds between requests (default: {REQUEST_DELAY})",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit, delay=args.delay)
