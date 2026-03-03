"""
Scrape detailed candidate information from MyNeta candidate and expense pages.

Reads fct_candidates_21_copy.csv, scrapes each candidate's detail page and
expense page, and adds 5 new columns: self_profession, spouse_profession,
education_details, criminal_cases_details, election_expenditure.

Resumable: progress is saved to a JSON sidecar file every 10 candidates.

Usage:
    python scripts/scrape_candidate_details.py                  # full run
    python scripts/scrape_candidate_details.py --dry-run        # just count targets
    python scripts/scrape_candidate_details.py --limit 5        # scrape first 5 only
    python scripts/scrape_candidate_details.py --delay 0.5      # 0.5s between page loads
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

from bs4 import BeautifulSoup, NavigableString
from playwright.sync_api import sync_playwright

CSV_PATH = Path("data/fct_candidates_21_copy.csv")
PROGRESS_PATH = Path("data/.scrape_candidate_details_progress.json")
BACKUP_PATH = CSV_PATH.with_suffix(".csv.bak_details")

NEW_COLUMNS = [
    "self_profession",
    "spouse_profession",
    "education_details",
    "criminal_cases_details",
    "election_expenditure",
]

REQUEST_DELAY = 0.5
MAX_RETRIES = 3
BACKOFF_BASE = 5.0
SAVE_EVERY = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_text(element) -> str:
    """Extract clean text from a BeautifulSoup element."""
    if element is None:
        return ""
    text = " ".join(element.get_text(" ", strip=True).split())
    return text.replace("\xa0", " ")


def _candidate_url_to_expense_url(candidate_url: str) -> str:
    """Convert candidate.php?candidate_id=X to expense.php?candidate_id=X."""
    return candidate_url.replace("candidate.php", "expense.php")


# ---------------------------------------------------------------------------
# Playwright fetching
# ---------------------------------------------------------------------------


def _fetch_html(page, url: str) -> str:
    """Fetch URL via Playwright page with retries + exponential backoff."""
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


# ---------------------------------------------------------------------------
# Extraction functions
# ---------------------------------------------------------------------------


def _extract_professions(soup: BeautifulSoup) -> tuple[str, str]:
    """Extract (self_profession, spouse_profession) from candidate page."""
    self_prof = ""
    spouse_prof = ""

    self_label = soup.find(
        "b", string=re.compile(r"Self\s+Profession", re.IGNORECASE)
    )
    if self_label:
        next_sib = self_label.next_sibling
        if next_sib and isinstance(next_sib, NavigableString):
            self_prof = str(next_sib).replace("\xa0", " ").strip().strip('"').strip()

    spouse_label = soup.find(
        "b", string=re.compile(r"Spouse\s+Profession", re.IGNORECASE)
    )
    if spouse_label:
        next_sib = spouse_label.next_sibling
        if next_sib and isinstance(next_sib, NavigableString):
            spouse_prof = (
                str(next_sib).replace("\xa0", " ").strip().strip('"').strip()
            )

    return self_prof, spouse_prof


def _extract_education_details(soup: BeautifulSoup) -> str:
    """Extract full educational details text from candidate page."""
    header = soup.find(
        "h3", string=re.compile(r"Educational\s+Details", re.IGNORECASE)
    )
    if not header:
        return ""

    panel = header.find_parent("div", class_="w3-panel")
    if not panel:
        return ""

    full_text = _get_text(panel)
    # Remove the heading itself from the text
    full_text = re.sub(
        r"^Educational\s+Details\s*", "", full_text, flags=re.IGNORECASE
    ).strip()
    return full_text


def _extract_criminal_cases_details(soup: BeautifulSoup) -> str:
    """Extract semicolon-separated criminal charge descriptions."""
    # Find the "Details of Criminal Cases" heading
    crime_header = soup.find(
        string=re.compile(r"Details\s+of\s+Criminal\s+Cases", re.IGNORECASE)
    )
    if not crime_header:
        return ""

    # The heading is inside a div.w3-panel, but the actual charges list
    # is in a div.w3-small that is a SIBLING of that panel, not a child.
    panel = crime_header.find_parent("div", class_="w3-panel")
    if not panel:
        return ""

    # Look for div.w3-small among the panel's following siblings
    charges = []
    for sib in panel.next_siblings:
        if not hasattr(sib, "name") or not sib.name:
            continue
        classes = sib.get("class", [])
        if "w3-small" in classes:
            for li in sib.find_all("li"):
                li_text = _get_text(li)
                badge = li.find("span", class_="w3-badge")
                if badge:
                    badge_text = _get_text(badge)
                    if li_text.startswith(badge_text):
                        li_text = li_text[len(badge_text) :].strip()
                if li_text:
                    charges.append(li_text)
            break  # only need the first w3-small sibling

    return "; ".join(charges)


def _extract_election_expenditure(soup: BeautifulSoup) -> str:
    """Extract CALCULATED GRAND TOTAL amount from expense page."""
    # Try to find the expenses table by id
    table = soup.find("table", id="expenses")
    if not table:
        # Fallback: any w3-table
        table = soup.find("table", class_=lambda c: c and "w3-table" in c)

    if not table:
        return ""

    # Find <b> containing "CALCULATED GRAND TOTAL"
    for b in table.find_all("b"):
        text = _get_text(b)
        if "CALCULATED GRAND TOTAL" in text.upper():
            match = re.search(
                r"CALCULATED\s+GRAND\s+TOTAL[:\-\s]+([\d,]+)", text, re.IGNORECASE
            )
            if match:
                return match.group(1).strip()
            # Fallback: extract trailing number
            match = re.search(r"([\d,]+)\s*$", text)
            if match:
                return match.group(1).strip()

    return ""


# ---------------------------------------------------------------------------
# Page-level parsing
# ---------------------------------------------------------------------------


def _parse_candidate_page(html: str) -> dict[str, str]:
    """Parse candidate.php HTML -> dict with 4 keys."""
    soup = BeautifulSoup(html, "lxml")
    self_prof, spouse_prof = _extract_professions(soup)
    edu = _extract_education_details(soup)
    cases = _extract_criminal_cases_details(soup)
    return {
        "self_profession": self_prof,
        "spouse_profession": spouse_prof,
        "education_details": edu,
        "criminal_cases_details": cases,
    }


def _parse_expense_page(html: str) -> dict[str, str]:
    """Parse expense.php HTML -> dict with 1 key."""
    soup = BeautifulSoup(html, "lxml")
    expenditure = _extract_election_expenditure(soup)
    return {"election_expenditure": expenditure}


def _scrape_one_candidate(
    page, candidate_url: str, delay: float
) -> dict[str, str]:
    """Scrape both pages for one candidate. Returns dict with all 5 values."""
    result = {col: "" for col in NEW_COLUMNS}

    # Candidate page (4 columns)
    try:
        html = _fetch_html(page, candidate_url)
        result.update(_parse_candidate_page(html))
    except Exception as exc:
        print(f"    WARN (candidate page): {exc}", file=sys.stderr)

    time.sleep(delay)

    # Expense page (1 column)
    expense_url = _candidate_url_to_expense_url(candidate_url)
    try:
        html = _fetch_html(page, expense_url)
        result.update(_parse_expense_page(html))
    except Exception as exc:
        print(f"    WARN (expense page): {exc}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# Progress tracking
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
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    # 2. Identify targets: all rows with a non-empty myneta_url
    targets = [
        (i, row)
        for i, row in enumerate(rows)
        if row.get("myneta_url", "").strip()
    ]
    print(f"Total rows: {len(rows)}, targets to scrape: {len(targets)}")

    # 3. Load progress for resumability
    completed = _load_progress()
    remaining = [(i, row) for i, row in targets if row["myneta_url"] not in completed]
    print(
        f"Already completed: {len(targets) - len(remaining)}, remaining: {len(remaining)}"
    )

    if dry_run:
        est_seconds = len(remaining) * 2 * (delay + 2.0)
        print(f"Estimated time: {est_seconds / 3600:.1f} hours")
        return

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
                    data = _scrape_one_candidate(page, url, delay)
                    completed[url] = data
                    prof = data["self_profession"] or "(none)"
                    edu = data["education_details"][:50] or "(none)"
                    cases = data["criminal_cases_details"][:60] or "(none)"
                    exp = data["election_expenditure"] or "(none)"
                    print(f"    Prof: {prof} | Edu: {edu}")
                    print(f"    Cases: {cases} | Expenditure: {exp}")
                except Exception as exc:
                    errors.append((i, url, str(exc)))
                    print(f"    ERROR: {exc}", file=sys.stderr)

                # Save progress periodically
                if count % SAVE_EVERY == 0:
                    _save_progress(completed)
                    print(f"    [progress saved: {len(completed)} completed]")

                # Rate limiting between candidates
                if count < len(remaining):
                    time.sleep(delay)

            browser.close()

        # Final progress save
        _save_progress(completed)

        if errors:
            print(f"\n{len(errors)} errors encountered:")
            for i, url, err in errors:
                print(f"  Row {i}: {url} -- {err}")

    # 5. Add new columns to fieldnames if not present
    for col in NEW_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)

    # 6. Apply all completed data to rows
    applied = 0
    for i, row in targets:
        url = row["myneta_url"]
        if url in completed:
            row.update(completed[url])
            applied += 1

    # Ensure new columns exist with empty values for all rows
    for row in rows:
        for col in NEW_COLUMNS:
            row.setdefault(col, "")

    print(f"\nApplied scraped data to {applied} rows")

    # 7. Backup original CSV, then write updated CSV
    shutil.copy2(CSV_PATH, BACKUP_PATH)
    print(f"Backup saved to {BACKUP_PATH}")

    with CSV_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {CSV_PATH} with {len(fieldnames)} columns")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Scrape candidate details from MyNeta"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Just print target count and estimated time",
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
        help=f"Seconds between page loads (default: {REQUEST_DELAY})",
    )
    args = parser.parse_args()
    main(dry_run=args.dry_run, limit=args.limit, delay=args.delay)
