"""
Scrape all 4023 "Contesting" candidates after nominations closed, with detail
page data. For prominent candidates only, download the LATEST affidavit PDF
(detail pages now list multiple affidavits like "Affidavit 1", "Affidavit 2").

Outputs:
  - data/eci_2026/candidates_final.json  (all ~4023 entries with full fields)
  - data/candidates_final/               (latest PDFs for prominent candidates)

Requires Brave running with --remote-debugging-port=9222.

Usage:
    python scripts/scrape_final_candidates.py --cdp --stage list
    python scripts/scrape_final_candidates.py --cdp --stage download --limit 5   # test
    python scripts/scrape_final_candidates.py --cdp --stage download             # full run
    python scripts/scrape_final_candidates.py --cdp --stage all                  # both
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.scrape_eci_2026 import (
    ECI_HOME,
    _extract_cards,
    _get_label_value,
    _make_browser_and_page,
    _navigate_to_candidate_filter,
    _safe_filename,
    SAVE_EVERY,
    MAX_RETRIES,
    BACKOFF_BASE,
)
from scripts.scrape_prominent_candidates import PARTY_MAP

CANDIDATES_FINAL_JSON = Path("data/eci_2026/candidates_final.json")
PROMINENT_CSV = Path("data/prominent_candidates_26.csv")
PDF_DIR = Path("data/candidates_final")
PROGRESS_JSON = Path("data/.scrape_final_progress.json")


# ---------------------------------------------------------------------------
# Progress helpers
# ---------------------------------------------------------------------------


def _load_progress() -> dict:
    if PROGRESS_JSON.exists():
        return json.loads(PROGRESS_JSON.read_text())
    return {"done": []}


def _save_progress(progress: dict) -> None:
    PROGRESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(json.dumps(progress, indent=2, ensure_ascii=False))


def _save_results(results: list[dict]) -> None:
    CANDIDATES_FINAL_JSON.parent.mkdir(parents=True, exist_ok=True)
    CANDIDATES_FINAL_JSON.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"[final] saved {len(results)} candidates to {CANDIDATES_FINAL_JSON}")


# ---------------------------------------------------------------------------
# Stage 1: Listing — click Contesting tile after filter
# ---------------------------------------------------------------------------


def _click_contesting_tile(page, delay: float) -> bool:
    """
    After applying the electionType/election/state filters, click the "Contesting"
    form-submit button (light blue tile showing ~4023). It's a real form submit:
        <button type="submit" name="submitName" value="100"> ... Contesting ... </button>
    Clicking it re-submits the parent form with submitName=100 which the server
    uses to filter candidates whose status is "Contesting" (Accepted + verified).
    """
    selector = "button[name='submitName'][value='100']"
    try:
        btn = page.locator(selector).first
        if btn.count() > 0:
            btn.click(timeout=8000)
            print("[final] clicked Contesting button (submitName=100)")
            page.wait_for_load_state("networkidle")
            time.sleep(delay * 2)
            return True
    except Exception as exc:
        print(f"[final] Contesting button click failed: {exc}", file=sys.stderr)

    print("[final] WARNING: could not click Contesting button", file=sys.stderr)
    return False


def _pick_filter_page(context):
    """Find or create a Playwright page positioned on the ECI filter site."""
    for pg in context.pages:
        try:
            u = pg.url
        except Exception:
            continue
        if "affidavit.eci.gov.in" in u:
            return pg
    return context.new_page()


def stage_list(delay: float, limit: int | None, cdp: bool, cookie_file: str | None) -> list[dict]:
    """Scrape all contesting candidates from the listing pages."""
    all_candidates: list[dict] = []

    with sync_playwright() as playwright:
        browser, context, _ignored = _make_browser_and_page(playwright, cdp, cookie_file)
        page = _pick_filter_page(context)

        print(f"[final] navigating to {ECI_HOME}")
        page.goto(ECI_HOME, wait_until="networkidle", timeout=60000)
        time.sleep(delay)

        _navigate_to_candidate_filter(page, delay)
        page.wait_for_timeout(int(delay * 1500))

        # Click the "Contesting" tile to filter to ~4023
        _click_contesting_tile(page, delay)

        page_num = 0
        while True:
            page_num += 1
            print(f"[final] extracting page {page_num}...")
            cards = _extract_cards(page)
            print(f"[final] page {page_num}: {len(cards)} candidates")

            if not cards:
                break

            all_candidates.extend(cards)

            if limit and len(all_candidates) >= limit:
                all_candidates = all_candidates[:limit]
                print(f"[final] limit reached ({limit})")
                break

            # Pagination — reuse pattern from main scraper
            next_link = page.locator(
                "a.page-link:has-text('Next'), "
                "li.page-item:not(.disabled) > a:has-text('Next'), "
                "a:has-text('Next »'), [aria-label='Next']:not([disabled])"
            )
            active_next = None
            for i in range(next_link.count()):
                el = next_link.nth(i)
                parent_class = page.evaluate(
                    "el => el.parentElement ? el.parentElement.className : ''",
                    el.element_handle(),
                )
                if "disabled" not in (parent_class or ""):
                    active_next = el
                    break

            if active_next is None:
                print("[final] no active Next — pagination complete")
                break

            active_next.click()
            time.sleep(delay + random.uniform(0.5, 1.5))
            page.wait_for_load_state("networkidle")

        browser.close()

    _save_results(all_candidates)
    return all_candidates


# ---------------------------------------------------------------------------
# Stage 2: Detail scraping with latest affidavit detection
# ---------------------------------------------------------------------------


def _scrape_detail_with_latest_affidavit(page, profile_url: str, delay: float) -> dict:
    """
    Navigate to profile page, extract all personal details, detect multiple
    affidavit sections (if any), and return the LATEST affidavit's PDF URL.

    Returns dict with: party_name_detail, assembly_constituency, state,
    application_uploaded, current_status, fathers_name, address, gender, age,
    photo_url, affidavit_count, latest_affidavit_uploaded_on,
    latest_affidavit_pdf_url, all_affidavit_pdf_urls (list).
    """
    for attempt in range(MAX_RETRIES):
        try:
            page.goto(profile_url, wait_until="networkidle", timeout=60000)
            time.sleep(delay)
            break
        except PlaywrightTimeout:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"[final] timeout, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                return {}

    extra: dict = {}

    # --- Personal detail fields (reuse label-based extraction) ---
    for label, key in [
        ("Party Name", "party_name_detail"),
        ("Assembly Constituency", "assembly_constituency"),
        ("State", "state"),
        ("Application Uploaded", "application_uploaded"),
        ("Current Status", "current_status"),
    ]:
        val = _get_label_value(page, label)
        if val:
            # Clean multi-line labels — take first line only
            extra[key] = val.splitlines()[0].strip()

    for label in ("Father's / Husband's Name", "Father's/Husband's Name", "Father"):
        val = _get_label_value(page, label)
        if val:
            extra["fathers_name"] = val.splitlines()[0].strip()
            break

    val = _get_label_value(page, "Address")
    if val:
        lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
        extra["address"] = ", ".join(lines)

    for label, key in [("Gender", "gender"), ("Age", "age")]:
        val = _get_label_value(page, label)
        if val:
            extra[key] = val.strip()

    # Photo URL
    photo = page.locator(".avatar-preview img, img[src*='candprofile']")
    if photo.count() > 0:
        src = photo.first.get_attribute("src") or ""
        if src and not src.startswith("data:"):
            if src.startswith("/"):
                src = "https://affidavit.eci.gov.in" + src
            extra["photo_url"] = src

    # --- Detect all affidavit sections + find the LATEST ---
    # The detail page has hidden inputs like <input id="pdfUrl{candidate_id}" value="...">
    # Each affidavit version has its own candidate_id.
    # Alongside, there are visible "Affidavit N" headings and "Affidavit Uploaded On: DATE" fields.
    affidavit_info = page.evaluate("""
        () => {
            // Find all pdfUrl hidden inputs — each represents an affidavit version
            const inputs = document.querySelectorAll('input[id^="pdfUrl"]');
            const affidavits = [];
            for (const inp of inputs) {
                const candidate_id = inp.id.replace('pdfUrl', '');
                const pdf_url_enc = inp.value;
                affidavits.push({candidate_id, pdf_url_enc});
            }

            // Try to extract dates next to each affidavit section
            // Look for text "Affidavit Uploaded On" and grab the adjacent value
            const dateMatches = [];
            const allElems = document.querySelectorAll('p, span, div, td, label, li');
            for (const el of allElems) {
                const t = (el.innerText || '').trim();
                if (/affidavit\\s+uploaded\\s+on/i.test(t)) {
                    // Sibling or nearby element has the date
                    let dateEl = el.nextElementSibling;
                    if (!dateEl && el.parentElement) {
                        dateEl = el.parentElement.nextElementSibling;
                    }
                    if (dateEl) {
                        dateMatches.push(dateEl.innerText.trim());
                    }
                }
            }

            return {affidavits, dateMatches};
        }
    """)

    affidavits = affidavit_info.get("affidavits", [])
    date_matches = affidavit_info.get("dateMatches", [])

    extra["affidavit_count"] = len(affidavits)

    if affidavits:
        # Collect the PDF URLs for all affidavits
        all_pdf_urls = [
            f"https://affidavit.eci.gov.in/affidavit-pdf-download/{a['pdf_url_enc']}"
            for a in affidavits
            if a.get("pdf_url_enc")
        ]
        extra["all_affidavit_pdf_urls"] = all_pdf_urls

        # The LAST affidavit section on the page is the latest one
        # (Affidavit 1 listed first, Affidavit 2 below it, etc.)
        latest = affidavits[-1]
        extra["latest_affidavit_pdf_url"] = (
            f"https://affidavit.eci.gov.in/affidavit-pdf-download/{latest['pdf_url_enc']}"
        )
        extra["_latest_candidate_id"] = latest["candidate_id"]
        extra["_latest_pdf_url_enc"] = latest["pdf_url_enc"]

        # Latest date is the last one in date_matches (matches the last affidavit section)
        if date_matches:
            extra["latest_affidavit_uploaded_on"] = date_matches[-1].splitlines()[0].strip()

    # Hidden inputs for increaseDownloadCount POST
    def _attr(sel, attr_name, tmo=3000):
        try:
            return page.locator(sel).first.get_attribute(attr_name, timeout=tmo) or ""
        except Exception:
            return ""

    extra["_nomid"] = _attr("#nomidHidden", "value")
    extra["_cons_type"] = _attr("#consTypedHidden", "value")
    extra["_election_id"] = _attr("#electionIdhidden", "value")
    extra["_db_name"] = _attr("#db_name", "value")
    extra["_csrf"] = _attr("meta[name='csrf-token']", "content")

    print(f"[final] fields: {[k for k in extra.keys() if not k.startswith('_')]}")
    return extra


def _download_latest_pdf(page, context, candidate: dict, dest_path: Path) -> bool:
    """POST to increaseDownloadCount then fetch the latest PDF."""
    pdf_url = candidate.get("latest_affidavit_pdf_url", "")
    candidate_id = candidate.get("_latest_candidate_id", "")
    if not pdf_url or not candidate_id:
        return False

    # Call increaseDownloadCount first (matches the ECI JS flow)
    try:
        page.request.post(
            "https://affidavit.eci.gov.in/increaseDownloadCount",
            form={
                "_token": candidate.get("_csrf", ""),
                "nomid": candidate.get("_nomid", ""),
                "candidateid": candidate_id,
                "db_name": candidate.get("_db_name", ""),
                "electionId": candidate.get("_election_id", ""),
                "consType": candidate.get("_cons_type", ""),
            },
        )
    except Exception as exc:
        print(f"[final] increaseDownloadCount failed: {exc}", file=sys.stderr)

    # Download via a new tab
    dl_page = context.new_page()
    success = False
    try:
        with dl_page.expect_download(timeout=60000) as dl_info:
            dl_page.evaluate(f"window.location.href = '{pdf_url}'")
        dl = dl_info.value
        dl.save_as(str(dest_path))
        success = dest_path.exists() and dest_path.stat().st_size > 0
    except Exception as exc:
        print(f"[final] PDF fetch failed: {exc}", file=sys.stderr)
    finally:
        try:
            dl_page.close()
        except Exception:
            pass
    return success


# ---------------------------------------------------------------------------
# Prominent matching
# ---------------------------------------------------------------------------


def build_prominent_set() -> set[tuple[str, str]]:
    """
    Return a set of (constituency_upper, party_eci_full) keys representing
    prominent candidates we want to download PDFs for.
    """
    prominent = list(csv.DictReader(PROMINENT_CSV.open()))
    keys: set[tuple[str, str]] = set()
    for p in prominent:
        short = p["party"]
        full = PARTY_MAP.get(short, "")
        if not full:
            continue
        keys.add((p["constituency"].upper().strip(), full))
    return keys


def is_prominent(candidate: dict, prominent_keys: set[tuple[str, str]]) -> bool:
    const = candidate.get("constituency", "").upper().strip()
    party = candidate.get("party", "")
    if (const, party) in prominent_keys:
        return True
    # Fuzzy match against constituency (in case of spelling variants)
    for pk_const, pk_party in prominent_keys:
        if pk_party == party and fuzz.ratio(const, pk_const) >= 85:
            return True
    return False


# ---------------------------------------------------------------------------
# Stage 2 orchestration
# ---------------------------------------------------------------------------


def stage_download(
    candidates: list[dict],
    delay: float,
    limit: int | None,
    cdp: bool,
    cookie_file: str | None,
    prominent_only_pdf: bool = True,
) -> None:
    """
    Visit each candidate's detail page, extract all fields, and for prominent
    candidates download the LATEST affidavit PDF to data/candidates_final/.
    """
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    progress = _load_progress()
    done_set = set(progress.get("done", []))

    prominent_keys = build_prominent_set()
    print(f"[final] prominent key count: {len(prominent_keys)}")

    targets = candidates[:limit] if limit else candidates

    # Load existing results so we can update them
    results: list[dict] = []
    if CANDIDATES_FINAL_JSON.exists():
        existing_data = json.loads(CANDIDATES_FINAL_JSON.read_text())
        if isinstance(existing_data, list):
            results = existing_data
    results_by_url = {r.get("affidavit_url"): r for r in results if r.get("affidavit_url")}

    with sync_playwright() as playwright:
        browser, context, _ignored = _make_browser_and_page(playwright, cdp, cookie_file)
        page = _pick_filter_page(context)

        for idx, candidate in enumerate(targets, start=1):
            url = candidate.get("affidavit_url", "")
            if not url:
                continue
            if url in done_set:
                print(f"[final] [{idx}/{len(targets)}] already done, skipping")
                continue

            name = candidate.get("name", f"candidate_{idx}")
            constituency = candidate.get("constituency", "unknown")
            party = candidate.get("party", "?")
            prominent_flag = is_prominent(candidate, prominent_keys)
            print(
                f"[final] [{idx}/{len(targets)}] {name} ({party}, {constituency}) "
                f"{'[PROMINENT]' if prominent_flag else ''}"
            )

            extra = _scrape_detail_with_latest_affidavit(page, url, delay)
            # Merge extra fields into the candidate record (skip internal _ keys in output)
            for k, v in extra.items():
                candidate[k] = v

            candidate["is_prominent"] = prominent_flag

            # Download latest PDF if prominent
            if prominent_flag and prominent_only_pdf and extra.get("latest_affidavit_pdf_url"):
                filename = _safe_filename(f"{constituency}_{name}") + ".pdf"
                dest_path = PDF_DIR / filename
                if dest_path.exists() and dest_path.stat().st_size > 0:
                    print(f"[final] PDF already on disk: {dest_path.name}")
                    candidate["pdf_path"] = str(dest_path)
                else:
                    ok = _download_latest_pdf(page, context, candidate, dest_path)
                    if ok:
                        candidate["pdf_path"] = str(dest_path)
                        print(f"[final] saved {dest_path.name} ({dest_path.stat().st_size} bytes)")

            # Strip internal _ fields before saving
            clean = {k: v for k, v in candidate.items() if not k.startswith("_")}

            if url in results_by_url:
                # update in place
                idx_in_results = results.index(results_by_url[url])
                results[idx_in_results] = clean
            else:
                results.append(clean)
                results_by_url[url] = clean

            done_set.add(url)
            progress["done"] = sorted(done_set)

            if idx % SAVE_EVERY == 0:
                _save_progress(progress)
                _save_results(results)

            time.sleep(delay + random.uniform(0.3, 0.8))

        browser.close()

    _save_progress(progress)
    _save_results(results)
    with_pdf = sum(1 for r in results if r.get("pdf_path"))
    print(f"[final] stage 2 complete — {len(results)} candidates, {with_pdf} with PDFs")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Scrape all contesting candidates + prominent PDFs")
    parser.add_argument("--stage", choices=["list", "download", "all"], default="all")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--delay", type=float, default=1.5)
    parser.add_argument("--cdp", action="store_true")
    parser.add_argument("--cookie-file", default=None)
    args = parser.parse_args()

    candidates: list[dict] = []

    if args.stage in ("list", "all"):
        candidates = stage_list(args.delay, args.limit, args.cdp, args.cookie_file)
        print(f"[final] listing complete: {len(candidates)} candidates")

    if args.stage in ("download", "all"):
        if not candidates:
            if CANDIDATES_FINAL_JSON.exists():
                candidates = json.loads(CANDIDATES_FINAL_JSON.read_text())
            else:
                print("[final] ERROR: no candidates_final.json — run --stage list first", file=sys.stderr)
                sys.exit(1)
        stage_download(candidates, args.delay, args.limit, args.cdp, args.cookie_file)

    print("[final] done.")


if __name__ == "__main__":
    main()
