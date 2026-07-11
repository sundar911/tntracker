"""
Recover image-hidden asset/liability totals for MyNeta 2026 candidates.

MyNeta renders SOME candidates' Total Assets / Liabilities cells as images
(image_v2.php) on listing and comparison pages — those scraped as "" in
fct_candidates_26_myneta.csv (1,258 rows). The candidate detail page always
carries the figures as selectable text:

    "Assets & Liabilities" headline panel ->  Assets: Rs N | Liabilities: Nil
    movable/immovable tables             ->  "Totals (Calculated as Sum of Values) Rs N"

This script visits candidate.php for every row missing total_assets_rs or
liabilities_rs, parses those values, and updates the CSV in place (backup
kept, resumable sidecar — same discipline as scrape_myneta_2026.py stage 2).
It also records the movable/immovable split into new columns when parseable
(immovable derived as total - movable when its own row is unreadable).

Usage:
    python scripts/scrape_myneta_missing_assets_26.py --dry-run
    python scripts/scrape_myneta_missing_assets_26.py --limit 3
    python scripts/scrape_myneta_missing_assets_26.py          # full run
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

OUTPUT_DIR = Path("data")
TARGET_CSV = OUTPUT_DIR / "fct_candidates_26_myneta.csv"
PROGRESS = OUTPUT_DIR / ".scrape_myneta_missing_assets_26_progress.json"

REQUEST_DELAY = 2.5
MAX_RETRIES = 4
BACKOFF_BASE = 3.0
SAVE_EVERY = 10

NEW_COLUMNS = ["movable_assets_rs", "immovable_assets_rs"]


def _get_text(element) -> str:
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split()).replace("\xa0", " ")


def _rs_to_int(text: str) -> int | None:
    """'Rs 2,34,491' / '2,34,491' -> 234491; 'Nil' -> 0; unparseable -> None."""
    t = (text or "").strip()
    if not t:
        return None
    if re.fullmatch(r"(?i)nil|n/?a", t):
        return 0
    m = re.search(r"([0-9][0-9,]*)", t)
    return int(m.group(1).replace(",", "")) if m else None


def parse_detail_page(html: str) -> dict:
    """Extract total assets, liabilities and the movable/immovable split."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, int | None] = {
        "total": None, "liabilities": None, "movable": None, "immovable": None,
    }

    # Headline "Assets & Liabilities" panel: Assets: Rs N ... Liabilities: Nil
    h = soup.find("h3", string=re.compile(r"Assets\s*&\s*Liabilities", re.I))
    if h:
        section = []
        panel = h.find_parent("div") or h
        for el in [panel] + list(panel.next_siblings):
            if getattr(el, "name", None) == "h3" and el is not h:
                break
            if hasattr(el, "get_text"):
                section.append(_get_text(el))
            if sum(len(s) for s in section) > 600:
                break
        text = " ".join(section)
        m = re.search(r"(?<![A-Za-z])Assets\s*:\s*(Rs\s*[0-9,]+|Nil)", text, re.I)
        if m:
            out["total"] = _rs_to_int(m.group(1))
        m = re.search(r"Liabilities\s*:\s*(Rs\s*[0-9,]+|Nil)", text, re.I)
        if m:
            out["liabilities"] = _rs_to_int(m.group(1))

    # Movable/immovable "Totals (Calculated as Sum of Values)" rows, in page
    # order: movable table first, immovable second.
    totals = []
    for tr in soup.find_all("tr"):
        row_text = _get_text(tr)
        if "Totals (Calculated as Sum of Values)" in row_text:
            m = re.search(r"Totals \(Calculated as Sum of Values\)\s*(?:Rs\s*)?([0-9,]*)",
                          row_text)
            totals.append(_rs_to_int(m.group(1)) if m and m.group(1) else None)
    if totals:
        out["movable"] = totals[0]
    if len(totals) > 1:
        out["immovable"] = totals[1]
    if out["immovable"] is None and out["total"] is not None and out["movable"] is not None:
        derived = out["total"] - out["movable"]
        if derived >= 0:
            out["immovable"] = derived
    return out


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


def _fmt_display(rupees: int) -> str:
    """Match MyNeta listing display: 'Rs 36,420,864-style Indian grouping ~ 3 Crore+'."""
    s = f"{rupees:,}"
    # Indian digit grouping
    digits = str(rupees)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    if rupees >= 10**7:
        approx = f"~ {rupees // 10**7} Crore+"
    elif rupees >= 10**5:
        approx = f"~ {rupees // 10**5} Lacs+"
    elif rupees >= 10**3:
        approx = f"~ {rupees // 10**3} Thou+"
    else:
        approx = ""
    return f"Rs {s} {approx}".strip()


def main() -> None:
    ap = argparse.ArgumentParser(description="Recover image-hidden MyNeta 2026 asset totals")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--delay", type=float, default=REQUEST_DELAY)
    args = ap.parse_args()

    with TARGET_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in NEW_COLUMNS:
        if col not in fieldnames:
            fieldnames.append(col)
    for row in rows:
        for col in NEW_COLUMNS:
            row.setdefault(col, "")

    targets = [r for r in rows
               if r.get("myneta_url", "").strip()
               and (not r.get("total_assets_rs", "").strip()
                    or not r.get("liabilities_rs", "").strip())]
    completed = _load_progress()
    remaining = [r for r in targets if r["myneta_url"] not in completed]
    print(f"{len(rows)} rows, {len(targets)} missing totals, "
          f"completed: {len(targets) - len(remaining)}, remaining: {len(remaining)}")

    if args.dry_run:
        print(f"dry-run: ~{len(remaining) * (args.delay + 1.5) / 3600:.1f} hours")
        return
    if args.limit > 0:
        remaining = remaining[:args.limit]

    errors = []
    if remaining:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            try:
                for n, row in enumerate(remaining, 1):
                    url = row["myneta_url"]
                    try:
                        html = _fetch_html(page, url)
                        parsed = parse_detail_page(html)
                        completed[url] = parsed
                        got = {k: v for k, v in parsed.items() if v is not None}
                        print(f"{n}/{len(remaining)} {row.get('candidate','')[:35]:35s} {got}")
                        if parsed["total"] is None:
                            errors.append(f"{url}: no total parsed")
                    except Exception as exc:
                        errors.append(f"{url}: {exc}")
                        print(f"ERROR {url}: {exc}", file=sys.stderr)
                    if n % SAVE_EVERY == 0:
                        _save_progress(completed)
                    time.sleep(args.delay)
            finally:
                browser.close()
        _save_progress(completed)

    # Apply recovered values (fill-only) and rewrite the CSV.
    shutil.copy2(TARGET_CSV, TARGET_CSV.with_suffix(".csv.assets_bak"))
    filled = 0
    for row in rows:
        rec = completed.get(row.get("myneta_url", ""))
        if not rec:
            continue
        changed = False
        if rec.get("total") is not None and not row.get("total_assets_rs", "").strip():
            row["total_assets_rs"] = str(rec["total"])
            if not row.get("total_assets", "").strip():
                row["total_assets"] = _fmt_display(rec["total"])
            changed = True
        if rec.get("liabilities") is not None and not row.get("liabilities_rs", "").strip():
            row["liabilities_rs"] = str(rec["liabilities"])
            if not row.get("liabilities", "").strip():
                row["liabilities"] = _fmt_display(rec["liabilities"])
            changed = True
        # The detail tables' "Totals (Calculated as Sum of Values)" rows are
        # self-column-only when spouse/dependents declared assets, so prefer
        # total - immovable (both MyNeta DB aggregates) over the parsed value.
        total_rs = (row.get("total_assets_rs") or "").strip()
        immovable_rs = (row.get("immovable_assets_rs") or "").strip()
        if total_rs.isdigit() and immovable_rs.isdigit() \
                and int(total_rs) >= int(immovable_rs):
            row["movable_assets_rs"] = str(int(total_rs) - int(immovable_rs))
            changed = True
        elif rec.get("movable") is not None and not row.get("movable_assets_rs", "").strip():
            row["movable_assets_rs"] = str(rec["movable"])
            changed = True
        if rec.get("immovable") is not None and not row.get("immovable_assets_rs", "").strip():
            row["immovable_assets_rs"] = str(rec["immovable"])
            changed = True
        if changed:
            filled += 1

    with TARGET_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"done. {filled} rows updated in {TARGET_CSV} "
          f"(backup: {TARGET_CSV.with_suffix('.csv.assets_bak')})")
    if errors:
        print(f"{len(errors)} errors:", file=sys.stderr)
        for e in errors[:20]:
            print(f"  {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
