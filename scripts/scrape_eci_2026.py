"""
Scrape 2026 TN Assembly candidate affidavit PDFs from the ECI portal.

Two stages:
  list     — Playwright: filter by TN + 2026, paginate through all candidate cards,
              save name/party/constituency/affidavit_url to data/eci_2026/candidates.json
  download — For each candidate, visit their affidavit page, find the PDF download
              link, and save to data/eci_2026/pdfs/

Resumable: progress is tracked in data/.scrape_eci_2026_progress.json.

NOTE: The ECI affidavit portal (affidavit.eci.gov.in) is protected by Akamai WAF which
blocks headless browsers at the TLS layer. The only reliable bypass is to connect this
script to your real running Chrome via CDP (Chrome DevTools Protocol):

  Step 1 — quit Brave (or Chrome) completely, then relaunch with remote debugging:
      /Applications/Brave\ Browser.app/Contents/MacOS/Brave\ Browser --remote-debugging-port=9222
      # or Chrome:
      /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222

  Step 2 — in that Chrome window, navigate to https://affidavit.eci.gov.in/ and confirm
            it loads (you should see the homepage, not an error).

  Step 3 — run the scraper:
      python scripts/scrape_eci_2026.py --cdp --stage list

The script connects to localhost:9222 and drives your real Chrome session.

Usage:
    python scripts/scrape_eci_2026.py --cdp --stage list
    python scripts/scrape_eci_2026.py --cdp --stage download
    python scripts/scrape_eci_2026.py --cdp --stage all     # default
    python scripts/scrape_eci_2026.py --cdp --limit 5
    python scripts/scrape_eci_2026.py --cdp --delay 2.0
    python scripts/scrape_eci_2026.py --cdp --dry-run
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

ECI_HOME = "https://affidavit.eci.gov.in/"
ECI_DOMAIN = "affidavit.eci.gov.in"
STATE_TARGET = "Tamil Nadu"
YEAR_TARGET = "2026"

OUT_DIR = Path("data/eci_2026")
PDF_DIR = OUT_DIR / "pdfs"
CANDIDATES_JSON = OUT_DIR / "candidates.json"
PROGRESS_JSON = Path("data/.scrape_eci_2026_progress.json")

DEFAULT_DELAY = 1.5  # seconds between page loads
MAX_RETRIES = 3
BACKOFF_BASE = 5.0
SAVE_EVERY = 10


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_cookies_from_file(cookie_file: str) -> list[dict]:
    """
    Parse a Netscape-format cookies.txt file (exported by browser extensions like
    'Get cookies.txt LOCALLY') and return a list of Playwright cookie dicts.
    """
    cookies = []
    path = Path(cookie_file)
    if not path.exists():
        print(f"[eci] WARNING: cookie file not found: {cookie_file}", file=sys.stderr)
        return cookies

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, _, path_val, secure, expires, name, value = parts[:7]
        cookies.append({
            "name": name,
            "value": value,
            "domain": domain.lstrip("."),
            "path": path_val,
            "secure": secure.upper() == "TRUE",
            "httpOnly": False,
        })

    print(f"[eci] loaded {len(cookies)} cookies from {cookie_file}")
    return cookies


def _safe_filename(text: str) -> str:
    """Convert arbitrary text to a safe filename segment."""
    return re.sub(r"[^\w\-]", "_", text.strip())[:80]


def _load_progress() -> dict:
    if PROGRESS_JSON.exists():
        return json.loads(PROGRESS_JSON.read_text())
    return {"downloaded": []}


def _save_progress(progress: dict) -> None:
    PROGRESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(json.dumps(progress, indent=2, ensure_ascii=False))


def _load_candidates() -> list[dict]:
    if CANDIDATES_JSON.exists():
        return json.loads(CANDIDATES_JSON.read_text())
    return []


def _save_candidates(candidates: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CANDIDATES_JSON.write_text(json.dumps(candidates, indent=2, ensure_ascii=False))
    print(f"[eci] saved {len(candidates)} candidates to {CANDIDATES_JSON}")


# ---------------------------------------------------------------------------
# Stage 1: Listing
# ---------------------------------------------------------------------------


def _select_option_containing(page, text: str, delay: float, label: str) -> bool:
    """
    Find any visible <select> whose options contain `text` and select it.
    Returns True on success.
    """
    selects = page.locator("select")
    for i in range(selects.count()):
        sel = selects.nth(i)
        options = sel.locator("option")
        for j in range(options.count()):
            opt_text = options.nth(j).inner_text().strip()
            if text in opt_text:
                sel.select_option(label=opt_text)
                print(f"[eci] {label}: selected '{opt_text}' in select[{i}]")
                time.sleep(delay)
                return True
    return False


def _navigate_to_candidate_filter(page, delay: float) -> None:
    """
    Apply ECI homepage filters to show Tamil Nadu 2026 candidates.

    The homepage has these selects (discovered by inspection):
      #electionType  — election group (pick the one containing "2026")
      #election      — AC - GENERAL / AC - BYE
      #states        — Tamil Nadu
      #phase         — (leave as default)
      #constId       — (leave as default = all constituencies)
    Then click the Filter button.
    """
    def _wait_and_select(sel_id: str, text: str, label: str) -> bool:
        """Select an option containing `text` in the select with the given id."""
        try:
            page.wait_for_selector(f"#{sel_id}", timeout=8000)
        except PlaywrightTimeout:
            print(f"[eci] WARNING: #{sel_id} not found", file=sys.stderr)
            return False

        select = page.locator(f"#{sel_id}")
        options = select.locator("option")
        for i in range(options.count()):
            opt_text = options.nth(i).inner_text().strip()
            if text.lower() in opt_text.lower():
                select.select_option(label=opt_text)
                print(f"[eci] {label}: selected '{opt_text}'")
                time.sleep(delay)
                return True
        print(f"[eci] WARNING: no option containing '{text}' in #{sel_id}", file=sys.stderr)
        return False

    # Step 1: Election group — pick the 2026 one
    _wait_and_select("electionType", YEAR_TARGET, "electionType")
    page.wait_for_timeout(int(delay * 1000))

    # Step 2: Election type — AC - GENERAL
    _wait_and_select("election", "AC - GENERAL", "election")
    page.wait_for_timeout(int(delay * 1000))

    # Step 3: State — Tamil Nadu
    _wait_and_select("states", STATE_TARGET, "states")
    page.wait_for_timeout(int(delay * 1500))

    # Step 4: Click Filter button
    for btn_text in ("Filter", "Search", "Go", "Submit"):
        btn = page.locator(f"button:has-text('{btn_text}'), input[value='{btn_text}']")
        if btn.count() > 0:
            btn.first.click()
            print(f"[eci] clicked '{btn_text}' button")
            page.wait_for_load_state("networkidle")
            time.sleep(delay * 2)
            break

    print(f"[eci] current URL after filter: {page.url}")


def _extract_cards(page) -> list[dict]:
    """
    Extract candidate info from all visible entries on the current page.

    Each candidate is in a <tr> row that contains a "View more" link pointing
    to /show-profile/.... The row text has lines like:
        CANDIDATE NAME
        Party : Thakkam Katchi
        Status : Applied
        State : Tamil Nadu
        Constituency : TIRUTTANI
        View more
    """
    candidates = []

    # Use JS to extract structured data efficiently — walk show-profile links,
    # deduplicate by href, then parse the parent <tr> text.
    raw = page.evaluate("""
    (function() {
        var links = document.querySelectorAll('a[href*="show-profile"]');
        var seen = {};
        var results = [];
        for (var i = 0; i < links.length; i++) {
            var href = links[i].href;
            if (seen[href]) continue;
            seen[href] = true;

            // Walk up to find the <tr> container
            var container = links[i];
            for (var j = 0; j < 10; j++) {
                if (!container.parentElement) break;
                container = container.parentElement;
                if (container.tagName === 'TR') break;
            }
            var text = container.innerText || '';
            results.push({href: href, text: text});
        }
        return results;
    })()
    """)

    for item in raw:
        href = item.get("href", "")
        text = item.get("text", "")
        if not href:
            continue

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        name = ""
        party = ""
        constituency = ""
        status = ""
        for line in lines:
            lower = line.lower()
            if "party" in lower and ":" in line:
                party = line.split(":", 1)[-1].strip()
            elif "constituency" in lower and ":" in line:
                constituency = line.split(":", 1)[-1].strip()
            elif "status" in lower and ":" in line:
                status = line.split(":", 1)[-1].strip()
            elif lower in ("view more",) or "state" in lower:
                continue
            elif not name:
                # First unrecognized non-empty line is the candidate name
                name = line.strip()

        candidates.append({
            "name": name,
            "party": party,
            "constituency": constituency,
            "status": status,
            "affidavit_url": href,
        })

    print(f"[eci] found {len(candidates)} candidates on this page")
    return candidates


def _make_browser_and_page(playwright, cdp: bool, cookie_file: str | None):
    """
    Return (browser, context, page).
    cdp=True  → connect to a real Chrome at localhost:9222 (bypasses Akamai).
    cdp=False → launch a headless Playwright Chromium (blocked by Akamai).
    """
    if cdp:
        print("[eci] connecting to Chrome via CDP at ws://localhost:9222")
        browser = playwright.chromium.connect_over_cdp("http://localhost:9222")
        # Use the first existing browser context (has real cookies/session)
        contexts = browser.contexts
        if contexts:
            context = contexts[0]
        else:
            context = browser.new_context()
        pages = context.pages
        page = pages[0] if pages else context.new_page()
        return browser, context, page

    # Fallback headless launch (likely blocked by Akamai)
    cookies = _load_cookies_from_file(cookie_file) if cookie_file else []
    browser = playwright.chromium.launch(
        headless=True,
        args=["--disable-blink-features=AutomationControlled"],
    )
    context = browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
    )
    if cookies:
        context.add_cookies(cookies)
    page = context.new_page()
    page.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined})")
    return browser, context, page


def stage_list(delay: float, limit: int | None, cookie_file: str | None = None, cdp: bool = False) -> list[dict]:
    """
    Stage 1: Navigate from the ECI homepage by clicking through the UI,
    apply State=Tamil Nadu + Election=2026 filters, paginate through all
    candidate cards, and save to data/eci_2026/candidates.json.
    """
    all_candidates: list[dict] = []

    with sync_playwright() as playwright:
        browser, context, page = _make_browser_and_page(playwright, cdp, cookie_file)

        print(f"[eci] navigating to homepage: {ECI_HOME}")
        page.goto(ECI_HOME, wait_until="networkidle", timeout=60000)
        time.sleep(delay)

        _navigate_to_candidate_filter(page, delay)

        # Wait for candidate cards to load
        page.wait_for_timeout(int(delay * 2000))

        page_num = 0
        while True:
            page_num += 1
            print(f"[eci] extracting cards from page {page_num}...")
            cards = _extract_cards(page)
            print(f"[eci] page {page_num}: {len(cards)} candidates")

            if not cards:
                print("[eci] no candidates found on this page, stopping")
                break

            all_candidates.extend(cards)

            if limit and len(all_candidates) >= limit:
                all_candidates = all_candidates[:limit]
                print(f"[eci] limit reached ({limit})")
                break

            # Pagination: find the Next link. ECI uses Bootstrap pagination:
            # <li class="page-item"><a class="page-link" href="...">Next »</a></li>
            # The parent <li> gets class "disabled" when there are no more pages.
            next_link = page.locator(
                "a.page-link:has-text('Next'), "
                "li.page-item:not(.disabled) > a:has-text('Next'), "
                "a:has-text('Next »'), a:has-text('Next›'), "
                "[aria-label='Next']:not([disabled])"
            )
            # Filter out any that are inside a disabled <li>
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
                print("[eci] no active Next button — pagination complete")
                break

            active_next.click()
            time.sleep(delay + random.uniform(0.5, 1.5))
            page.wait_for_load_state("networkidle")

        browser.close()

    _save_candidates(all_candidates)
    return all_candidates


# ---------------------------------------------------------------------------
# Stage 2: Detail scrape + PDF download
# ---------------------------------------------------------------------------


def _get_label_value(page, label: str) -> str:
    """
    Find a label text on the ECI detail page and return its adjacent value.
    Tries multiple layout patterns used across ECI pages.
    """
    # Pattern 1: Bootstrap col pairs — "Party Name:" in one col, value in next sibling col
    # Use JS to walk the DOM efficiently
    result = page.evaluate(f"""
    (function() {{
        var label = {json.dumps(label.lower())};
        // Walk all elements, find one whose trimmed text matches label
        var all = document.querySelectorAll('p, span, div, td, dt, li');
        for (var i = 0; i < all.length; i++) {{
            var el = all[i];
            // Only look at leaf-ish nodes
            if (el.children.length > 3) continue;
            var txt = el.innerText ? el.innerText.trim().replace(/:$/, '').toLowerCase() : '';
            if (txt === label || txt.endsWith(label)) {{
                // Try next sibling
                var sib = el.nextElementSibling;
                if (sib) return sib.innerText.trim();
                // Try parent's next sibling
                if (el.parentElement) {{
                    var psib = el.parentElement.nextElementSibling;
                    if (psib) return psib.innerText.trim();
                }}
            }}
        }}
        return '';
    }})()
    """)
    if result:
        return result

    # Pattern 2: table <td> pairs
    rows = page.locator("tr")
    for i in range(rows.count()):
        cells = rows.nth(i).locator("td")
        if cells.count() >= 2:
            header = cells.nth(0).inner_text().strip().rstrip(":")
            if label.lower() in header.lower():
                return cells.nth(1).inner_text().strip()

    return ""


def _scrape_detail_page(page, affidavit_url: str, delay: float, no_pdf: bool = False) -> dict:
    """
    Navigate to a candidate's detail page, extract all available fields,
    and intercept the PDF download. Returns a dict of extra fields + pdf_path.
    """
    # Navigate with retries
    for attempt in range(MAX_RETRIES):
        try:
            page.goto(affidavit_url, wait_until="networkidle", timeout=60000)
            time.sleep(delay)
            break
        except PlaywrightTimeout:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"[eci] timeout, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"[eci] failed after {MAX_RETRIES} attempts", file=sys.stderr)
                return {}

    extra: dict = {}

    # --- Extract detail fields (top section: Candidate Details) ---
    # Party name (page shows English + Hindi/Tamil sub-text — take first non-empty line)
    for label in ("Party Name", "Party"):
        val = _get_label_value(page, label)
        if val:
            extra["party_name_detail"] = val.splitlines()[0].strip()
            break

    for label in ("Assembly Constituency", "Constituency"):
        val = _get_label_value(page, label)
        if val:
            extra["assembly_constituency"] = val
            break

    for label in ("State",):
        val = _get_label_value(page, label)
        if val:
            extra["state"] = val

    for label in ("Application Uploaded", "Uploaded"):
        val = _get_label_value(page, label)
        if val:
            extra["application_uploaded"] = val
            break

    for label in ("Current Status", "Status"):
        val = _get_label_value(page, label)
        if val:
            extra["current_status"] = val
            break

    for label in ("Download Count",):
        val = _get_label_value(page, label)
        if val:
            extra["download_count"] = val.splitlines()[0].strip()

    for label in ("Affidavit Uploaded On", "Affidavit Uploaded"):
        val = _get_label_value(page, label)
        if val:
            extra["affidavit_uploaded_on"] = val
            break

    # --- Personal details section (.detail-person) ---
    for label in ("Father's / Husband's Name", "Father's/Husband's Name", "Father"):
        val = _get_label_value(page, label)
        if val:
            extra["fathers_name"] = val.splitlines()[0].strip()
            break

    for label in ("Address",):
        val = _get_label_value(page, label)
        if val:
            # Multi-line address — join into single line with commas
            lines = [ln.strip() for ln in val.splitlines() if ln.strip()]
            extra["address"] = ", ".join(lines)

    for label in ("Gender",):
        val = _get_label_value(page, label)
        if val:
            extra["gender"] = val.strip()

    for label in ("Age",):
        val = _get_label_value(page, label)
        if val:
            extra["age"] = val.strip()

    # Photo URL — use .avatar-preview img which is the actual ECI class
    photo = page.locator(".avatar-preview img, img[src*='candprofile']")
    if photo.count() > 0:
        src = photo.first.get_attribute("src") or ""
        if src and not src.startswith("data:"):
            if src.startswith("/"):
                src = "https://affidavit.eci.gov.in" + src
            extra["photo_url"] = src

    print(f"[eci] detail fields: {list(extra.keys())}")

    # --- PDF URL extraction + optional download ---
    download_btn = page.locator(
        "a:has-text('Download'), button:has-text('Download'), "
        "[class*='download'], [id*='download']"
    )

    # The ECI download flow:
    #   1. onclick calls increaseDownloadCount(candidateId)
    #   2. That POSTs to /increaseDownloadCount with CSRF + nomid + candidateid etc.
    #   3. On success, redirects browser to /affidavit-pdf-download/{pdfUrlEncoded}
    # We replicate this directly using the hidden input values already on the page.

    def _attr(selector: str, attr: str, timeout: int = 3000) -> str:
        try:
            return page.locator(selector).first.get_attribute(attr, timeout=timeout) or ""
        except Exception:
            return ""

    candidate_id_attr = _attr("[id^=pdfUrl]", "id")
    candidate_id = candidate_id_attr.replace("pdfUrl", "")  # e.g. "5608"
    pdf_url_enc = _attr(f"#pdfUrl{candidate_id}", "value") if candidate_id else ""
    nomid = _attr("#nomidHidden", "value")
    cons_type = _attr("#consTypedHidden", "value")
    election_id = _attr("#electionIdhidden", "value")
    db_name = _attr("#db_name", "value")
    csrf = _attr("meta[name='csrf-token']", "content")

    if candidate_id and pdf_url_enc:
        pdf_download_url = f"https://affidavit.eci.gov.in/affidavit-pdf-download/{pdf_url_enc}"
        extra["affidavit_pdf_url"] = pdf_download_url

        if no_pdf:
            print(f"[eci] --no-pdf: skipping download (URL stored)")
        else:
            print(f"[eci] using increaseDownloadCount flow (candidate_id={candidate_id})")
            try:
                page.request.post(
                    "https://affidavit.eci.gov.in/increaseDownloadCount",
                    form={
                        "_token": csrf,
                        "nomid": nomid,
                        "candidateid": candidate_id,
                        "db_name": db_name,
                        "electionId": election_id,
                        "consType": cons_type,
                    },
                )
                extra["_pdf_url_fallback"] = pdf_download_url
                print(f"[eci] PDF URL ready: /affidavit-pdf-download/{pdf_url_enc[:30]}...")
            except Exception as exc:
                print(f"[eci] increaseDownloadCount POST failed: {exc}", file=sys.stderr)
    elif download_btn.count() > 0 and not no_pdf:
        # Fallback: intercept the download button click
        try:
            with page.context.expect_page(timeout=10000) as popup_info:
                download_btn.first.click()
            popup = popup_info.value
            popup.wait_for_load_state("domcontentloaded", timeout=15000)
            extra["_pdf_url_fallback"] = popup.url
            popup.close()
            print(f"[eci] PDF URL from popup: {extra['_pdf_url_fallback'][:80]}")
        except Exception as exc:
            print(f"[eci] popup intercept failed: {exc}", file=sys.stderr)
    else:
        print(f"[eci] WARNING: no download mechanism found on {affidavit_url}", file=sys.stderr)

    return extra


def _deduplicate_candidates(candidates: list[dict]) -> list[dict]:
    """
    Deduplicate candidates by (name, constituency). For duplicates (e.g. UVARANI
    SURESH who filed twice), keep the entry that has an affidavit (pdf_path set),
    or the most recently uploaded one. Store alternate URLs in alt_affidavit_urls.
    """
    from collections import defaultdict
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for c in candidates:
        key = (c.get("name", ""), c.get("constituency", ""))
        groups[key].append(c)

    deduped: list[dict] = []
    for key, entries in groups.items():
        if len(entries) == 1:
            deduped.append(entries[0])
            continue

        # Pick the best entry: prefer one with pdf_path, then most recent upload
        entries.sort(key=lambda e: (
            bool(e.get("pdf_path")),  # True > False
            e.get("affidavit_uploaded_on", ""),  # later date string wins
        ), reverse=True)

        best = entries[0]
        alt_urls = [e["affidavit_url"] for e in entries[1:] if e.get("affidavit_url")]
        if alt_urls:
            best["alt_affidavit_urls"] = alt_urls
        # Merge any fields the best is missing from alternates
        for other in entries[1:]:
            for k, v in other.items():
                if k not in ("affidavit_url", "pdf_path", "alt_affidavit_urls") and not best.get(k) and v:
                    best[k] = v

        deduped.append(best)
        print(f"[eci] dedup: {key[0]} ({key[1]}) — kept 1 of {len(entries)}, alt_urls={len(alt_urls)}")

    print(f"[eci] dedup: {len(candidates)} → {len(deduped)} candidates")
    return deduped


def _cleanup_orphaned_pdfs(candidates: list[dict]) -> None:
    """Delete any PDFs in data/eci_2026/pdfs/ not referenced by candidates.json."""
    if not PDF_DIR.exists():
        return
    referenced = set()
    for c in candidates:
        p = c.get("pdf_path", "")
        if p:
            referenced.add(Path(p).name)

    removed = 0
    for pdf_file in PDF_DIR.iterdir():
        if pdf_file.suffix == ".pdf" and pdf_file.name not in referenced:
            pdf_file.unlink()
            removed += 1

    if removed:
        print(f"[eci] cleanup: removed {removed} orphaned PDFs from {PDF_DIR}")
    else:
        print(f"[eci] cleanup: no orphaned PDFs found")


def stage_download(
    candidates: list[dict],
    delay: float,
    limit: int | None,
    cookie_file: str | None = None,
    cdp: bool = False,
    no_pdf: bool = False,
) -> None:
    """
    Stage 2: For each candidate, visit their detail page, scrape all fields
    (including personal details: address, gender, age, father's name),
    download the affidavit PDF, deduplicate, and clean up orphaned PDFs.
    """
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    progress = _load_progress()
    downloaded_set = set(progress["downloaded"])

    targets = candidates
    if limit:
        targets = candidates[:limit]

    with sync_playwright() as playwright:
        browser, context, page = _make_browser_and_page(playwright, cdp, cookie_file)

        for idx, candidate in enumerate(targets, start=1):
            affidavit_url = candidate.get("affidavit_url", "")
            if not affidavit_url:
                print(f"[eci] [{idx}/{len(targets)}] skipping — no affidavit_url")
                continue

            if affidavit_url in downloaded_set:
                print(f"[eci] [{idx}/{len(targets)}] already done, skipping")
                continue

            name = candidate.get("name", f"candidate_{idx}")
            constituency = candidate.get("constituency", "unknown")
            filename = _safe_filename(f"{constituency}_{name}") + ".pdf"
            dest_path = PDF_DIR / filename

            print(f"[eci] [{idx}/{len(targets)}] {name} ({constituency})")
            extra = _scrape_detail_page(page, affidavit_url, delay, no_pdf=no_pdf)

            # Merge extra fields into candidate record
            for k, v in extra.items():
                if not k.startswith("_"):
                    candidate[k] = v

            # Skip PDF download if file already exists
            if dest_path.exists() and dest_path.stat().st_size > 0:
                print(f"[eci] [{idx}/{len(targets)}] PDF already on disk")
                candidate["pdf_path"] = str(dest_path)
                downloaded_set.add(affidavit_url)
                progress["downloaded"] = list(downloaded_set)
                if idx % SAVE_EVERY == 0:
                    _save_progress(progress)
                    _save_candidates(candidates)
                time.sleep(delay + random.uniform(0.3, 0.8))
                continue

            # Download PDF if available
            tmp = extra.get("_download_tmp")
            if tmp and Path(tmp).exists():
                Path(tmp).rename(dest_path)
                candidate["pdf_path"] = str(dest_path)
                print(f"[eci] saved {dest_path} ({dest_path.stat().st_size} bytes)")
                downloaded_set.add(affidavit_url)
            elif extra.get("_pdf_url_fallback"):
                pdf_url = extra["_pdf_url_fallback"]
                print(f"[eci] fetching PDF via browser: {pdf_url[:80]}")
                dl_page = context.new_page()
                try:
                    with dl_page.expect_download(timeout=60000) as dl_info:
                        dl_page.evaluate(f"window.location.href = '{pdf_url}'")
                    dl = dl_info.value
                    dl.save_as(str(dest_path))
                except Exception as exc:
                    print(f"[eci] PDF fetch failed: {exc}", file=sys.stderr)
                finally:
                    try:
                        dl_page.close()
                    except Exception:
                        pass

                if dest_path.exists() and dest_path.stat().st_size > 0:
                    candidate["pdf_path"] = str(dest_path)
                    downloaded_set.add(affidavit_url)
                    print(f"[eci] saved {dest_path} ({dest_path.stat().st_size} bytes)")
            else:
                print(f"[eci] [{idx}/{len(targets)}] no affidavit available")

            progress["downloaded"] = list(downloaded_set)
            if idx % SAVE_EVERY == 0:
                _save_progress(progress)
                _save_candidates(candidates)

            time.sleep(delay + random.uniform(0.5, 1.5))

        browser.close()

    _save_progress(progress)

    # Smart dedup: collapse duplicates, keeping the one with the affidavit
    deduped = _deduplicate_candidates(candidates)
    _save_candidates(deduped)

    # Clean up orphaned PDFs
    _cleanup_orphaned_pdfs(deduped)

    with_pdf = sum(1 for c in deduped if c.get("pdf_path"))
    print(f"[eci] stage 2 complete — {len(deduped)} unique candidates, {with_pdf} with PDFs.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape ECI 2026 TN affidavit PDFs")
    parser.add_argument(
        "--stage",
        choices=["list", "download", "all"],
        default="all",
        help="Which stage to run (default: all)",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max candidates to process")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="Seconds between requests")
    parser.add_argument("--dry-run", action="store_true", help="Run list stage only, skip downloads")
    parser.add_argument(
        "--cookie-file",
        default=None,
        metavar="PATH",
        help="Netscape cookies.txt (fallback, less reliable — Akamai blocks at TLS layer).",
    )
    parser.add_argument(
        "--cdp",
        action="store_true",
        help=(
            "Connect to your real browser via CDP (recommended). "
            "First launch Brave with: "
            "/Applications/Brave\\ Browser.app/Contents/MacOS/Brave\\ Browser --remote-debugging-port=9222 "
            "or Chrome with: "
            "/Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222"
        ),
    )
    parser.add_argument(
        "--no-pdf",
        action="store_true",
        help="Skip PDF downloads — only scrape detail page fields. Stores PDF URL in affidavit_pdf_url.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    stage = "list" if args.dry_run else args.stage

    if not args.cdp and not args.cookie_file:
        print(
            "[eci] WARNING: neither --cdp nor --cookie-file provided.\n"
            "       affidavit.eci.gov.in blocks headless browsers via Akamai WAF.\n"
            "       Recommended: launch Chrome with --remote-debugging-port=9222, then use --cdp.",
            file=sys.stderr,
        )

    candidates: list[dict] = []

    if stage in ("list", "all"):
        candidates = stage_list(delay=args.delay, limit=args.limit, cookie_file=args.cookie_file, cdp=args.cdp)
        print(f"[eci] listing complete: {len(candidates)} candidates")

    if stage in ("download", "all") and not args.dry_run:
        if not candidates:
            candidates = _load_candidates()
        if not candidates:
            print("[eci] ERROR: no candidates.json found. Run --stage list first.", file=sys.stderr)
            sys.exit(1)
        stage_download(candidates, delay=args.delay, limit=args.limit, cookie_file=args.cookie_file, cdp=args.cdp, no_pdf=args.no_pdf)

    print("[eci] done.")


if __name__ == "__main__":
    main()
