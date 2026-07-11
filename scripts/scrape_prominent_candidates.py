"""
Scrape detail pages + download affidavit PDFs for prominent candidates only.

Matches prominent_candidates_26.csv against candidates.json (7599 ECI entries),
visits each matched candidate's detail page, extracts all fields, and downloads
the affidavit PDF to data/prominent_candidates_affidavits/.

Requires Brave running with --remote-debugging-port=9222.

Usage:
    python scripts/scrape_prominent_candidates.py --cdp
    python scripts/scrape_prominent_candidates.py --cdp --limit 5
    python scripts/scrape_prominent_candidates.py --cdp --no-pdf    # detail scrape only
    python scripts/scrape_prominent_candidates.py --cdp --status Accepted  # only accepted
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

from rapidfuzz import fuzz

# Reuse scraper functions from the main ECI scraper
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.scrape_eci_2026 import (
    _make_browser_and_page,
    _scrape_detail_page,
    _safe_filename,
    _deduplicate_candidates,
    SAVE_EVERY,
)
from playwright.sync_api import sync_playwright

CANDIDATES_JSON = Path("data/eci_2026/candidates.json")
PROMINENT_CSV = Path("data/prominent_candidates_26.csv")
PROMINENT_JSON = Path("data/prominent_candidates_26_detailed.json")
PDF_DIR = Path("data/prominent_candidates_affidavits")
PROGRESS_JSON = Path("data/.scrape_prominent_progress.json")

PARTY_MAP = {
    "DMK": "Dravida Munnetra Kazhagam",
    "AIADMK": "All India Anna Dravida Munnetra Kazhagam",
    "TVK": "Tamilaga Vettri Kazhagam",
    "INC": "Indian National Congress",
    "BJP": "Bharatiya Janata Party",
    "CPIM": "Communist Party of India (Marxist)",
    "CPIML": "Communist Party of India (Marxist-Leninist) (Liberation)",
    "VCK": "Viduthalai Chiruthaigal Katchi",
    "PMK": "Pattali Makkal Katchi",
    "CPI": "Communist Party of India",
    "NTK": "Naam Tamilar Katchi",
}


def _load_progress() -> dict:
    if PROGRESS_JSON.exists():
        return json.loads(PROGRESS_JSON.read_text())
    return {"done": []}


def _save_progress(progress: dict) -> None:
    PROGRESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(json.dumps(progress, indent=2, ensure_ascii=False))


def _save_results(results: list[dict]) -> None:
    PROMINENT_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROMINENT_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[prominent] saved {len(results)} candidates to {PROMINENT_JSON}")


def match_prominent_to_eci(status_filter: str | None = None) -> list[dict]:
    """
    Match prominent_candidates_26.csv against candidates.json.
    Returns list of ECI candidate dicts with extra 'prominent_party' field.
    """
    eci = json.loads(CANDIDATES_JSON.read_text())
    prominent = list(csv.DictReader(PROMINENT_CSV.open()))

    # Index ECI by (constituency_upper, party_full)
    eci_index: dict[tuple[str, str], list[dict]] = {}
    for c in eci:
        key = (c["constituency"].upper().strip(), c["party"])
        eci_index.setdefault(key, []).append(c)

    matched = []
    unmatched = []

    for p in prominent:
        party_short = p["party"]
        party_full = PARTY_MAP.get(party_short, "")
        const = p["constituency"].upper().strip()
        candidate_name = p["candidate"]

        candidates = eci_index.get((const, party_full), [])

        # Fuzzy constituency match if exact fails
        if not candidates:
            best_key = None
            best_score = 0
            for key in eci_index:
                if key[1] == party_full:
                    score = fuzz.ratio(const, key[0])
                    if score > best_score:
                        best_score = score
                        best_key = key
            if best_key and best_score >= 75:
                candidates = eci_index[best_key]

        if candidates:
            best = max(
                candidates,
                key=lambda c: fuzz.token_sort_ratio(candidate_name.upper(), c["name"].upper()),
            )
            best["prominent_party"] = party_short
            best["prominent_name"] = candidate_name
            matched.append(best)
        else:
            unmatched.append(p)

    if unmatched:
        print(f"[prominent] {len(unmatched)} candidates not found in ECI:")
        for u in unmatched:
            print(f"  {u['party']} {u['constituency']}: {u['candidate']}")

    # Apply status filter
    if status_filter:
        before = len(matched)
        matched = [m for m in matched if m.get("status", "").lower() == status_filter.lower()]
        print(f"[prominent] filtered to status={status_filter}: {before} → {len(matched)}")

    print(f"[prominent] {len(matched)} candidates matched for scraping")
    return matched


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape prominent candidates from ECI")
    parser.add_argument("--cdp", action="store_true")
    parser.add_argument("--cookie-file", default=None)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--no-pdf", action="store_true")
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument(
        "--status",
        default=None,
        help="Filter by status: Accepted, Rejected, Applied (default: all)",
    )
    args = parser.parse_args()

    # Match prominent candidates to ECI data
    targets = match_prominent_to_eci(status_filter=args.status)
    if not targets:
        print("[prominent] no candidates to process")
        return

    if args.limit:
        targets = targets[: args.limit]

    PDF_DIR.mkdir(parents=True, exist_ok=True)
    progress = _load_progress()
    done_set = set(progress["done"])

    # Load existing results to append to
    results: list[dict] = []
    if PROMINENT_JSON.exists():
        results = json.loads(PROMINENT_JSON.read_text())

    existing_urls = {r.get("affidavit_url") for r in results}

    with sync_playwright() as playwright:
        browser, context, page = _make_browser_and_page(playwright, args.cdp, args.cookie_file)

        for idx, candidate in enumerate(targets, start=1):
            url = candidate.get("affidavit_url", "")
            if not url:
                continue

            if url in done_set:
                print(f"[prominent] [{idx}/{len(targets)}] already done, skipping")
                continue

            name = candidate.get("name", f"candidate_{idx}")
            constituency = candidate.get("constituency", "unknown")
            party = candidate.get("prominent_party", candidate.get("party", ""))

            print(f"[prominent] [{idx}/{len(targets)}] {name} ({party}, {constituency})")

            extra = _scrape_detail_page(page, url, args.delay, no_pdf=args.no_pdf)

            # Merge extra fields
            for k, v in extra.items():
                if not k.startswith("_"):
                    candidate[k] = v

            # Download PDF
            filename = _safe_filename(f"{constituency}_{name}") + ".pdf"
            dest_path = PDF_DIR / filename

            if dest_path.exists() and dest_path.stat().st_size > 0:
                print(f"[prominent] [{idx}/{len(targets)}] PDF already on disk")
                candidate["pdf_path"] = str(dest_path)
            elif extra.get("_pdf_url_fallback") and not args.no_pdf:
                pdf_url = extra["_pdf_url_fallback"]
                print(f"[prominent] fetching PDF: {pdf_url[:80]}")
                dl_page = context.new_page()
                try:
                    with dl_page.expect_download(timeout=60000) as dl_info:
                        dl_page.evaluate(f"window.location.href = '{pdf_url}'")
                    dl = dl_info.value
                    dl.save_as(str(dest_path))
                except Exception as exc:
                    print(f"[prominent] PDF fetch failed: {exc}", file=sys.stderr)
                finally:
                    try:
                        dl_page.close()
                    except Exception:
                        pass

                if dest_path.exists() and dest_path.stat().st_size > 0:
                    candidate["pdf_path"] = str(dest_path)
                    print(f"[prominent] saved {dest_path} ({dest_path.stat().st_size} bytes)")
            elif not args.no_pdf:
                print(f"[prominent] [{idx}/{len(targets)}] no affidavit available")

            done_set.add(url)
            progress["done"] = list(done_set)

            # Add to results (avoid duplicates)
            if url not in existing_urls:
                results.append(candidate)
                existing_urls.add(url)
            else:
                # Update existing entry
                for i, r in enumerate(results):
                    if r.get("affidavit_url") == url:
                        results[i] = candidate
                        break

            if idx % SAVE_EVERY == 0:
                _save_progress(progress)
                _save_results(results)

            time.sleep(args.delay + random.uniform(0.5, 1.5))

        browser.close()

    _save_progress(progress)

    # Deduplicate
    deduped = _deduplicate_candidates(results)
    _save_results(deduped)

    with_pdf = sum(1 for c in deduped if c.get("pdf_path"))
    print(f"[prominent] complete — {len(deduped)} candidates, {with_pdf} with PDFs")


if __name__ == "__main__":
    main()
