"""
Scrape MyNeta TamilNadu 2026 (post-election candidate affidavit data).

Adapted from the 2021 scrapers (scrape_candidates.py + scrape_candidate_details.py).
Uses Playwright (headless chromium, persistent tab, networkidle) like the 2021
scrapers did. Raw requests get soft-blocked by MyNeta after ~10 rapid hits
(degraded 'winner only' pages); a real browser does not. Slower but accurate —
veracity over speed.

Two stages:
  Stage 1 — list every candidate in all 234 constituencies into a CSV:
            candidate, party, criminal_cases, education, age, total_assets,
            total_assets_rs, liabilities, liabilities_rs, 2026_constituency,
            2026_district, is_winner, myneta_url
  Stage 2 — visit each candidate.php detail page, append:
            self_profession, spouse_profession, education_details,
            criminal_cases_details, election_expenditure

Both stages are resumable via JSON sidecar progress files (saved every 10 items).

NOTE: MyNeta 2026 expense.php pages currently have no expenses table, so
election_expenditure is always "" (kept as a column for forward-compat; flip
SCRAPE_EXPENDITURE = True once MyNeta publishes expenditure data).

Usage:
    python scripts/scrape_myneta_2026.py --stage 1 --dry-run
    python scripts/scrape_myneta_2026.py --stage 1 --constituency-ids "23,1" --output data/_smoke_26.csv
    python scripts/scrape_myneta_2026.py --stage 2 --output data/_smoke_26.csv --limit 3
    python scripts/scrape_myneta_2026.py --stage all          # full run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
import time
from dataclasses import dataclass, asdict
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, NavigableString
from playwright.sync_api import sync_playwright

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MYNETA_2026_BASE = "https://myneta.info/TamilNadu2026/"
OUTPUT_DIR = Path("data")
DEFAULT_OUTPUT = OUTPUT_DIR / "fct_candidates_26_myneta.csv"
STAGE1_PROGRESS = OUTPUT_DIR / ".scrape_myneta_2026_stage1_progress.json"
STAGE2_PROGRESS = OUTPUT_DIR / ".scrape_myneta_2026_stage2_progress.json"

REQUEST_DELAY = 2.5       # polite delay between requests (MyNeta soft-blocks bursts)
MAX_RETRIES = 4
BACKOFF_BASE = 3.0        # 3, 6, 12, 24s
SAVE_EVERY = 10
# Soft-block guard: a real constituency always has >=2 candidates. Fewer means
# MyNeta returned a throttled/degraded page — re-fetch with longer backoff.
MIN_CANDIDATES = 2
SOFTBLOCK_RETRIES = 5
SOFTBLOCK_BACKOFF = 15.0  # 15, 30, 60, 120s

# MyNeta 2026 expense.php has no expenditure data yet — keep column, don't scrape.
SCRAPE_EXPENDITURE = False

STAGE1_COLUMNS = [
    "candidate", "party", "criminal_cases", "education", "age",
    "total_assets", "total_assets_rs", "liabilities", "liabilities_rs",
    "2026_constituency", "2026_district", "is_winner", "myneta_url",
]
STAGE2_COLUMNS = [
    "self_profession", "spouse_profession", "education_details",
    "criminal_cases_details", "election_expenditure",
]


@dataclass
class MynetaCandidate:
    name: str
    party: str
    criminal_cases: str
    education: str
    age: str
    total_assets: str
    liabilities: str
    constituency: str
    district: str
    myneta_url: str
    is_winner: bool


# ---------------------------------------------------------------------------
# Pure helpers (ported verbatim from the 2021 scrapers)
# ---------------------------------------------------------------------------


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split()).strip()


def _get_text(element) -> str:
    """Clean text from a BeautifulSoup element (strips \xa0)."""
    if element is None:
        return ""
    text = " ".join(element.get_text(" ", strip=True).split())
    return text.replace("\xa0", " ")


def _extract_rs_amount(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"Rs\s*([0-9,]+)", value)
    if not match:
        return ""
    return match.group(1).replace(",", "")


def _extract_professions(soup: BeautifulSoup) -> tuple[str, str]:
    self_prof = ""
    spouse_prof = ""
    self_label = soup.find("b", string=re.compile(r"Self\s+Profession", re.IGNORECASE))
    if self_label:
        nxt = self_label.next_sibling
        if nxt and isinstance(nxt, NavigableString):
            self_prof = str(nxt).replace("\xa0", " ").strip().strip('"').strip()
    spouse_label = soup.find("b", string=re.compile(r"Spouse\s+Profession", re.IGNORECASE))
    if spouse_label:
        nxt = spouse_label.next_sibling
        if nxt and isinstance(nxt, NavigableString):
            spouse_prof = str(nxt).replace("\xa0", " ").strip().strip('"').strip()
    return self_prof, spouse_prof


def _extract_education_details(soup: BeautifulSoup) -> str:
    header = soup.find("h3", string=re.compile(r"Educational\s+Details", re.IGNORECASE))
    if not header:
        return ""
    panel = header.find_parent("div", class_="w3-panel")
    if not panel:
        return ""
    full_text = _get_text(panel)
    return re.sub(r"^Educational\s+Details\s*", "", full_text, flags=re.IGNORECASE).strip()


def _extract_criminal_cases_details(soup: BeautifulSoup) -> str:
    crime_header = soup.find(string=re.compile(r"Details\s+of\s+Criminal\s+Cases", re.IGNORECASE))
    if not crime_header:
        return ""
    panel = crime_header.find_parent("div", class_="w3-panel")
    if not panel:
        return ""
    charges = []
    for sib in panel.next_siblings:
        if not hasattr(sib, "name") or not sib.name:
            continue
        if "w3-small" in sib.get("class", []):
            for li in sib.find_all("li"):
                li_text = _get_text(li)
                badge = li.find("span", class_="w3-badge")
                if badge:
                    badge_text = _get_text(badge)
                    if li_text.startswith(badge_text):
                        li_text = li_text[len(badge_text):].strip()
                if li_text:
                    charges.append(li_text)
            break
    return "; ".join(charges)


def _extract_election_expenditure(soup: BeautifulSoup) -> str:
    table = soup.find("table", id="expenses")
    if not table:
        table = soup.find("table", class_=lambda c: c and "w3-table" in c)
    if not table:
        return ""
    for b in table.find_all("b"):
        text = _get_text(b)
        if "CALCULATED GRAND TOTAL" in text.upper():
            m = re.search(r"CALCULATED\s+GRAND\s+TOTAL[:\-\s]+([\d,]+)", text, re.IGNORECASE)
            if m:
                return m.group(1).strip()
            m = re.search(r"([\d,]+)\s*$", text)
            if m:
                return m.group(1).strip()
    return ""


def _candidate_url_to_expense_url(candidate_url: str) -> str:
    return candidate_url.replace("candidate.php", "expense.php")


# ---------------------------------------------------------------------------
# Fetching (Playwright — the 2021 scrapers used this and MyNeta does not
# soft-block a real headless browser the way it throttles raw requests)
# ---------------------------------------------------------------------------


def _fetch_html(page, url: str) -> str:
    """Fetch URL via a persistent Playwright page with retries + backoff.

    Ported verbatim from scripts/scrape_candidate_details.py:72-88 — the proven
    pattern. networkidle waits for MyNeta's JS/XHR to settle before reading
    content, which is why the full table renders (raw requests got a degraded
    'winner only' page under load).
    """
    for attempt in range(MAX_RETRIES):
        try:
            page.goto(url, wait_until="networkidle", timeout=60000)
            return page.content()
        except Exception as exc:
            if attempt < MAX_RETRIES - 1:
                wait = BACKOFF_BASE * (2 ** attempt)
                print(f"    retry {attempt + 1}/{MAX_RETRIES} for {url} in {wait}s ({exc})",
                      file=sys.stderr)
                time.sleep(wait)
            else:
                raise
    return ""


# ---------------------------------------------------------------------------
# Stage 1: base + listing parsing
# ---------------------------------------------------------------------------


def _extract_district_constituency_map(html: str) -> dict[int, str]:
    """Map constituency_id -> district name from the base-page w3-dropdown blocks."""
    soup = BeautifulSoup(html, "html.parser")
    mapping: dict[int, str] = {}
    for dd in soup.find_all("div", class_=lambda v: v and "w3-dropdown-click" in v):
        btn = dd.find("button")
        district = _get_text(btn) if btn else ""
        if not district:
            continue
        for a in dd.find_all("a", href=True):
            m = re.search(r"constituency_id=(\d+)", a["href"])
            if m:
                mapping[int(m.group(1))] = district
    return mapping


def _extract_constituency_links(html: str) -> list[tuple[int, str]]:
    """Return sorted unique [(constituency_id, absolute_url)] from the base page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "show_candidates" in href and "constituency_id=" in href:
            m = re.search(r"constituency_id=(\d+)", href)
            if m:
                cid = int(m.group(1))
                seen.setdefault(cid, urljoin(MYNETA_2026_BASE, href))
    return sorted(seen.items())


def _parse_listing_page(html: str, cid: int, district_map: dict[int, str]) -> list[MynetaCandidate]:
    soup = BeautifulSoup(html, "lxml")

    # --- Constituency + district ---
    constituency = "Unknown"
    district = district_map.get(cid, "Unknown")

    # Breadcrumb panel: "Home → TamilNadu 2026 → DISTRICT → CONSTITUENCY"
    bc = soup.find("div", class_=lambda v: v and "w3-panel" in v and "w3-leftbar" in v
                   and "w3-light-gray" in v)
    if bc:
        parts = [p.strip() for p in _get_text(bc).split("→") if p.strip()]
        if len(parts) >= 2:
            constituency = parts[-1]
            if district == "Unknown" and len(parts) >= 3:
                district = parts[-2]

    # Fallback: w3-sand panel "List of Candidates - DISTRICT:CONSTITUENCY"
    if constituency == "Unknown":
        sand = soup.find("div", class_=lambda v: v and "w3-panel" in v and "w3-sand" in v)
        text = _get_text(sand) if sand else ""
        m = re.search(r"List of Candidates\s*-\s*([^:(]+):([^()\n]+)", text, re.IGNORECASE)
        if m:
            district = district if district != "Unknown" else m.group(1).strip()
            constituency = m.group(2).strip()

    # Fallback: page title "List of Candidates in CONSTITUENCY : DISTRICT TamilNadu 2026"
    if constituency == "Unknown" and soup.title:
        m = re.search(r"List of Candidates in\s+(.+?)\s*:\s*(.+?)\s+TamilNadu",
                      _get_text(soup.title), re.IGNORECASE)
        if m:
            constituency = m.group(1).strip()
            if district == "Unknown":
                district = m.group(2).strip()

    # --- Candidate table ---
    responsive = soup.find("div", class_=lambda v: v and "w3-responsive" in v)
    table = responsive.find("table") if responsive else None
    if not table:
        return []

    headers = [normalize_text(_get_text(th)) for th in table.find_all("th")]
    header_map = {name: idx for idx, name in enumerate(headers)}

    def _col_index(*names: str) -> int | None:
        for name in names:
            key = normalize_text(name)
            if key in header_map:
                return header_map[key]
        return None

    name_idx = _col_index("candidate")
    party_idx = _col_index("party")
    crime_idx = _col_index("criminal cases", "criminal case")
    edu_idx = _col_index("education")
    age_idx = _col_index("age")
    assets_idx = _col_index("total assets", "assets")
    liab_idx = _col_index("liabilities")
    if name_idx is None:  # fixed-column fallback (SNo,Candidate,Party,Crime,Edu,Age,Assets,Liab)
        name_idx, party_idx, crime_idx, edu_idx, age_idx, assets_idx, liab_idx = 1, 2, 3, 4, 5, 6, 7

    candidates: list[MynetaCandidate] = []
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells or name_idx >= len(cells):
            continue
        name_cell = cells[name_idx]
        raw = _get_text(name_cell)
        if not raw:
            continue
        # Winner detection: MyNeta appends a trailing "Winner" token (elections over).
        is_winner = bool(re.search(r"\bWinner\s*$", raw))
        name = re.sub(r"\s*\bWinner\s*$", "", raw).strip()
        if not name:
            continue
        link = name_cell.find("a", href=True)
        myneta_url = urljoin(MYNETA_2026_BASE, link["href"]) if link else ""

        def cell(idx: int | None) -> str:
            return _get_text(cells[idx]) if idx is not None and idx < len(cells) else ""

        candidates.append(MynetaCandidate(
            name=name,
            party=cell(party_idx),
            criminal_cases=cell(crime_idx),
            education=cell(edu_idx),
            age=cell(age_idx),
            total_assets=cell(assets_idx),
            liabilities=cell(liab_idx),
            constituency=constituency,
            district=district,
            myneta_url=myneta_url,
            is_winner=is_winner,
        ))

    n_winners = sum(1 for c in candidates if c.is_winner)
    if candidates and n_winners != 1:
        print(f"  WARN: constituency {cid} ({constituency}) has {n_winners} winners "
              f"(expected 1)", file=sys.stderr)
    return candidates


# ---------------------------------------------------------------------------
# Progress sidecars
# ---------------------------------------------------------------------------


def _load_json(path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8")).get("completed", {})
    return {}


def _save_json(path: Path, completed: dict) -> None:
    path.write_text(json.dumps({"completed": completed}, indent=2, ensure_ascii=False),
                    encoding="utf-8")


# ---------------------------------------------------------------------------
# Stage runners
# ---------------------------------------------------------------------------


def _write_stage1_csv(output: Path, completed: dict[str, list[dict]]) -> int:
    """Flatten the stage-1 progress dict into the CSV. Idempotent."""
    rows = []
    for cid in sorted(completed, key=lambda x: int(x)):
        for c in completed[cid]:
            rows.append({
                "candidate": c["name"],
                "party": c["party"],
                "criminal_cases": c["criminal_cases"],
                "education": c["education"],
                "age": c["age"],
                "total_assets": c["total_assets"],
                "total_assets_rs": _extract_rs_amount(c["total_assets"]),
                "liabilities": c["liabilities"],
                "liabilities_rs": _extract_rs_amount(c["liabilities"]),
                "2026_constituency": c["constituency"],
                "2026_district": c["district"],
                "is_winner": 1 if c["is_winner"] else 0,
                "myneta_url": c["myneta_url"],
            })
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=STAGE1_COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def run_stage1(page, *, output: Path, dry_run: bool, limit: int,
               delay: float, constituency_ids: list[int] | None) -> None:
    print("[stage1] fetching base page...")
    base_html = _fetch_html(page, MYNETA_2026_BASE)
    district_map = _extract_district_constituency_map(base_html)
    links = _extract_constituency_links(base_html)
    print(f"[stage1] {len(links)} constituencies, {len(set(district_map.values()))} districts")
    if len(links) != 234:
        print(f"[stage1] WARN: expected 234 constituency links, got {len(links)}",
              file=sys.stderr)

    if constituency_ids:
        wanted = set(constituency_ids)
        links = [(cid, url) for cid, url in links if cid in wanted]
        print(f"[stage1] restricted to {len(links)} constituencies: {constituency_ids}")

    completed = _load_json(STAGE1_PROGRESS)
    remaining = [(cid, url) for cid, url in links if str(cid) not in completed]
    print(f"[stage1] already completed: {len(links) - len(remaining)}, "
          f"remaining: {len(remaining)}")

    if dry_run:
        print(f"[stage1] dry-run: would scrape {len(remaining)} constituencies "
              f"(~{len(remaining) * (delay + 1):.0f}s)")
        return

    if limit > 0:
        remaining = remaining[:limit]
        print(f"[stage1] limited to first {limit}")

    errors: list[str] = []
    for n, (cid, url) in enumerate(remaining, 1):
        try:
            # Soft-block guard: under rapid load MyNeta returns a degraded page
            # with only the winner row. A real constituency ALWAYS has >=2
            # candidates, so a parsed count < MIN_CANDIDATES means we were
            # throttled — re-fetch with exponential backoff before accepting.
            cands = []
            for attempt in range(SOFTBLOCK_RETRIES):
                html = _fetch_html(page, url)
                cands = _parse_listing_page(html, cid, district_map)
                if len(cands) >= MIN_CANDIDATES:
                    break
                if attempt < SOFTBLOCK_RETRIES - 1:
                    wait = SOFTBLOCK_BACKOFF * (2 ** attempt)
                    print(f"[stage1] cid={cid} got {len(cands)} candidates "
                          f"(soft-block?) — backoff {wait}s, retry "
                          f"{attempt + 1}/{SOFTBLOCK_RETRIES - 1}", file=sys.stderr)
                    time.sleep(wait)
            completed[str(cid)] = [asdict(c) for c in cands]
            flag = "" if len(cands) >= MIN_CANDIDATES else "  <-- STILL LOW"
            print(f"[stage1] {n}/{len(remaining)} cid={cid} -> {len(cands)} candidates{flag}")
            if len(cands) < MIN_CANDIDATES:
                errors.append(f"cid={cid}: only {len(cands)} candidates after retries")
        except Exception as exc:
            errors.append(f"cid={cid}: {exc}")
            print(f"[stage1] ERROR cid={cid}: {exc}", file=sys.stderr)
        if n % SAVE_EVERY == 0:
            _save_json(STAGE1_PROGRESS, completed)
        time.sleep(delay)

    _save_json(STAGE1_PROGRESS, completed)
    total = _write_stage1_csv(output, completed)
    print(f"[stage1] done. {total} candidate rows -> {output}")
    if errors:
        print(f"[stage1] {len(errors)} errors:", file=sys.stderr)
        for e in errors:
            print(f"  {e}", file=sys.stderr)


def run_stage2(page, *, output: Path, dry_run: bool, limit: int, delay: float) -> None:
    if not output.exists():
        print(f"[stage2] ERROR: {output} not found — run stage 1 first.", file=sys.stderr)
        sys.exit(1)

    with output.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    targets = [(i, r) for i, r in enumerate(rows) if r.get("myneta_url", "").strip()]
    completed = _load_json(STAGE2_PROGRESS)
    remaining = [(i, r) for i, r in targets if r["myneta_url"] not in completed]
    print(f"[stage2] {len(rows)} rows, {len(targets)} with myneta_url, "
          f"completed: {len(targets) - len(remaining)}, remaining: {len(remaining)}")

    if dry_run:
        per = delay + (delay if SCRAPE_EXPENDITURE else 0) + 1
        print(f"[stage2] dry-run: ~{len(remaining) * per / 3600:.1f} hours")
        return

    if limit > 0:
        remaining = remaining[:limit]
        print(f"[stage2] limited to first {limit}")

    errors: list[str] = []
    for n, (_i, row) in enumerate(remaining, 1):
        url = row["myneta_url"]
        result = {col: "" for col in STAGE2_COLUMNS}
        try:
            html = _fetch_html(page, url)
            soup = BeautifulSoup(html, "lxml")
            sp, spp = _extract_professions(soup)
            result["self_profession"] = sp
            result["spouse_profession"] = spp
            result["education_details"] = _extract_education_details(soup)
            result["criminal_cases_details"] = _extract_criminal_cases_details(soup)
        except Exception as exc:
            errors.append(f"{url}: {exc}")
            print(f"[stage2] ERROR {url}: {exc}", file=sys.stderr)

        if SCRAPE_EXPENDITURE:
            time.sleep(delay)
            try:
                ehtml = _fetch_html(page, _candidate_url_to_expense_url(url))
                result["election_expenditure"] = _extract_election_expenditure(
                    BeautifulSoup(ehtml, "lxml"))
            except Exception as exc:
                print(f"[stage2] WARN expense {url}: {exc}", file=sys.stderr)

        completed[url] = result
        print(f"[stage2] {n}/{len(remaining)} {row.get('candidate', '')[:40]}")
        if n % SAVE_EVERY == 0:
            _save_json(STAGE2_PROGRESS, completed)
        time.sleep(delay)

    _save_json(STAGE2_PROGRESS, completed)

    # Backup then rewrite CSV with the 5 extra columns merged in.
    shutil.copy2(output, output.with_suffix(".csv.bak"))
    for col in STAGE2_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        extra = completed.get(row.get("myneta_url", ""), {})
        for col in STAGE2_COLUMNS:
            row.setdefault(col, "")
            if extra.get(col):
                row[col] = extra[col]
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"[stage2] done. merged into {output} (backup: {output.with_suffix('.csv.bak')})")
    if errors:
        print(f"[stage2] {len(errors)} errors", file=sys.stderr)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape MyNeta TamilNadu 2026")
    ap.add_argument("--stage", choices=["1", "2", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--constituency-ids", default="",
                    help="comma-separated constituency ids to restrict stage 1")
    args = ap.parse_args()

    cids = [int(x) for x in args.constituency_ids.split(",") if x.strip()] or None

    # One headless chromium + one reused tab for the whole run (matches the
    # 2021 scrape_candidate_details.py resource model). Created unconditionally
    # because stage-1 --dry-run also needs to fetch the base page.
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            if args.stage in ("1", "all"):
                run_stage1(page, output=args.output, dry_run=args.dry_run,
                           limit=args.limit, delay=args.delay, constituency_ids=cids)
            if args.stage in ("2", "all"):
                run_stage2(page, output=args.output, dry_run=args.dry_run,
                           limit=args.limit, delay=args.delay)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
