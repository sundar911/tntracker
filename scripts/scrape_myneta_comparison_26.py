"""
Scrape MyNeta TamilNadu 2026 comparison charts (new page type, not in 2021).

comparisonchart.php?constituency_id=N lists every candidate in the
constituency (independents included, despite the "Political Party Candidates"
title) with fields the listing pages don't have:

    movable assets, immovable assets (the split), PAN Given (Y/N),
    Serious IPC Counts

Each candidate is TWO <tr>s: a 10-cell main row (name+candidate.php link,
age, party code, criminal cases, education level, movable, immovable, total,
liabilities, PAN) followed by a 1-cell row "Serious IPC Counts: N".

MyNeta anti-scraping quirk: SOME value cells are rendered as images
(<img src=".../image_v2.php?...&col=ma|ia|ta|lia">) instead of text, per
candidate per column. Those are recorded as empty with a *_img flag set to 1;
scripts/scrape_myneta_missing_assets_26.py recovers the figures from the
candidate detail pages, where values are always selectable text.

Same fetch discipline as scrape_myneta_2026.py: Playwright headless chromium,
one persistent tab, networkidle, 2.5s delay, resumable sidecar, and a
soft-block guard (a real constituency always has >=2 candidates).

Usage:
    python scripts/scrape_myneta_comparison_26.py --dry-run
    python scripts/scrape_myneta_comparison_26.py --constituency-ids "42" --output data/_smoke_cc.csv
    python scripts/scrape_myneta_comparison_26.py          # full run
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

MYNETA_2026_BASE = "https://myneta.info/TamilNadu2026/"
OUTPUT_DIR = Path("data")
DEFAULT_OUTPUT = OUTPUT_DIR / "fct_candidates_26_myneta_comparison.csv"
PROGRESS = OUTPUT_DIR / ".scrape_myneta_comparison_26_progress.json"

REQUEST_DELAY = 2.5
MAX_RETRIES = 4
BACKOFF_BASE = 3.0
SAVE_EVERY = 10
MIN_CANDIDATES = 2
SOFTBLOCK_RETRIES = 5
SOFTBLOCK_BACKOFF = 15.0

COLUMNS = [
    "candidate_id", "myneta_url", "candidate", "constituency_id", "constituency",
    "age", "party_code", "criminal_cases_flag", "education_level",
    "movable_assets", "movable_assets_rs", "movable_assets_img",
    "immovable_assets", "immovable_assets_rs", "immovable_assets_img",
    "total_assets", "total_assets_rs", "total_assets_img",
    "liabilities", "liabilities_rs", "liabilities_img",
    "pan_given", "serious_ipc_counts",
]


def _get_text(element) -> str:
    if element is None:
        return ""
    text = " ".join(element.get_text(" ", strip=True).split())
    return text.replace("\xa0", " ")


def _amount_cell(cell) -> tuple[str, str, str]:
    """Return (display_text, plain_rupees, is_image) for an asset/liability cell.

    Comparison-chart cells have no 'Rs' prefix: '6,76,80,000 ~ 6 Crore+'.
    Image-rendered cells (image_v2.php) yield ('', '', '1').
    """
    if cell is None:
        return "", "", ""
    if cell.find("img", src=re.compile(r"image_v2\.php")):
        return "", "", "1"
    text = _get_text(cell)
    if not text or text.lower() in ("nil", "n/a", "not available"):
        return text, "0" if text.lower() == "nil" else "", ""
    m = re.search(r"([0-9][0-9,]*)", text)
    return text, (m.group(1).replace(",", "") if m else ""), ""


def _parse_comparison_page(html: str, cid: int) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")

    constituency = ""
    if soup.title:
        m = re.match(r"\s*(.+?)\s+Constituency Comparison Chart", _get_text(soup.title))
        if m:
            constituency = m.group(1).strip()

    table = None
    for t in soup.find_all("table"):
        th = t.find("th")
        if th and _get_text(th) == "Name":
            table = t
            break
    if table is None:
        return []

    rows: list[dict] = []
    for tr in table.find_all("tr"):
        cells = tr.find_all("td")
        if len(cells) >= 10:
            link = cells[0].find("a", href=re.compile(r"candidate\.php"))
            href = urljoin(MYNETA_2026_BASE, link["href"]) if link else ""
            cand_id = ""
            m = re.search(r"candidate_id=(\d+)", href)
            if m:
                cand_id = m.group(1)
            mov = _amount_cell(cells[5])
            imm = _amount_cell(cells[6])
            tot = _amount_cell(cells[7])
            lia = _amount_cell(cells[8])
            rows.append({
                "candidate_id": cand_id,
                "myneta_url": href,
                "candidate": _get_text(cells[0]),
                "constituency_id": str(cid),
                "constituency": constituency,
                "age": _get_text(cells[1]),
                "party_code": _get_text(cells[2]),
                "criminal_cases_flag": _get_text(cells[3]),
                "education_level": _get_text(cells[4]),
                "movable_assets": mov[0], "movable_assets_rs": mov[1], "movable_assets_img": mov[2],
                "immovable_assets": imm[0], "immovable_assets_rs": imm[1], "immovable_assets_img": imm[2],
                "total_assets": tot[0], "total_assets_rs": tot[1], "total_assets_img": tot[2],
                "liabilities": lia[0], "liabilities_rs": lia[1], "liabilities_img": lia[2],
                "pan_given": _get_text(cells[9]),
                "serious_ipc_counts": "",
            })
        elif len(cells) == 1 and rows:
            m = re.search(r"Serious IPC Counts\s*:?\s*(\d+)", _get_text(cells[0]))
            if m:
                rows[-1]["serious_ipc_counts"] = m.group(1)
    return rows


def _fetch_html(page, url: str) -> str:
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


def _load_progress() -> dict:
    if PROGRESS.exists():
        return json.loads(PROGRESS.read_text(encoding="utf-8")).get("completed", {})
    return {}


def _save_progress(completed: dict) -> None:
    PROGRESS.write_text(json.dumps({"completed": completed}, indent=2, ensure_ascii=False),
                        encoding="utf-8")


def _constituency_ids(page) -> list[int]:
    """All constituency ids from the base page (same discovery as stage 1)."""
    html = _fetch_html(page, MYNETA_2026_BASE)
    ids = set()
    for m in re.finditer(r"show_candidates&(?:amp;)?constituency_id=(\d+)", html):
        ids.add(int(m.group(1)))
    return sorted(ids)


def _write_csv(output: Path, completed: dict) -> int:
    rows = []
    for cid in sorted(completed, key=int):
        rows.extend(completed[cid])
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape MyNeta 2026 comparison charts")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--constituency-ids", default="")
    args = ap.parse_args()

    wanted = [int(x) for x in args.constituency_ids.split(",") if x.strip()]

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            ids = wanted or _constituency_ids(page)
            print(f"{len(ids)} constituencies")
            if not wanted and len(ids) != 234:
                print(f"WARN: expected 234 constituency ids, got {len(ids)}", file=sys.stderr)

            completed = _load_progress()
            remaining = [c for c in ids if str(c) not in completed]
            print(f"already completed: {len(ids) - len(remaining)}, remaining: {len(remaining)}")

            if args.dry_run:
                print(f"dry-run: ~{len(remaining) * (args.delay + 1.5) / 60:.0f} min")
                return
            if args.limit > 0:
                remaining = remaining[:args.limit]

            errors = []
            for n, cid in enumerate(remaining, 1):
                url = f"{MYNETA_2026_BASE}comparisonchart.php?constituency_id={cid}"
                try:
                    cands = []
                    for attempt in range(SOFTBLOCK_RETRIES):
                        html = _fetch_html(page, url)
                        cands = _parse_comparison_page(html, cid)
                        if len(cands) >= MIN_CANDIDATES:
                            break
                        if attempt < SOFTBLOCK_RETRIES - 1:
                            wait = SOFTBLOCK_BACKOFF * (2 ** attempt)
                            print(f"cid={cid} got {len(cands)} candidates (soft-block?) — "
                                  f"backoff {wait}s", file=sys.stderr)
                            time.sleep(wait)
                    completed[str(cid)] = cands
                    flag = "" if len(cands) >= MIN_CANDIDATES else "  <-- STILL LOW"
                    print(f"{n}/{len(remaining)} cid={cid} -> {len(cands)} candidates{flag}")
                    if len(cands) < MIN_CANDIDATES:
                        errors.append(f"cid={cid}: only {len(cands)} after retries")
                except Exception as exc:
                    errors.append(f"cid={cid}: {exc}")
                    print(f"ERROR cid={cid}: {exc}", file=sys.stderr)
                if n % SAVE_EVERY == 0:
                    _save_progress(completed)
                time.sleep(args.delay)

            _save_progress(completed)
            total = _write_csv(args.output, completed)
            print(f"done. {total} rows -> {args.output}")
            if errors:
                print(f"{len(errors)} errors:", file=sys.stderr)
                for e in errors:
                    print(f"  {e}", file=sys.stderr)
        finally:
            browser.close()


if __name__ == "__main__":
    main()
