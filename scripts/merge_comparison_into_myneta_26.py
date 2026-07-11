"""
Fold comparison-chart fields into fct_candidates_26_myneta.csv.

Joins data/fct_candidates_26_myneta_comparison.csv (scrape_myneta_comparison_26.py)
into data/fct_candidates_26_myneta.csv by myneta_url (exact key — both files
carry candidate.php links).

  - pan_given, serious_ipc_counts: new columns, always set
  - movable_assets_rs / immovable_assets_rs: new columns, set where the
    comparison chart had text (image-hidden cells stay empty for
    scrape_myneta_missing_assets_26.py to recover)
  - total_assets_rs / liabilities_rs: fill-if-empty only — image-hiding
    differs per page type, so the comparison chart has text for some values
    the listing pages hid (and vice versa)

Where both files have a text value for the same figure, discrepancies are
reported (should be ~zero; anything else is a parsing bug or MyNeta edit).

Usage:
    python scripts/merge_comparison_into_myneta_26.py [--dry-run]
"""

from __future__ import annotations

import argparse
import csv
import re
import shutil
from pathlib import Path

DATA = Path("data")
MYNETA_CSV = DATA / "fct_candidates_26_myneta.csv"
COMPARISON_CSV = DATA / "fct_candidates_26_myneta_comparison.csv"

NEW_ALWAYS = {"pan_given": "pan_given", "serious_ipc_counts": "serious_ipc_counts"}
NEW_IF_TEXT = {"movable_assets_rs": "movable_assets_rs",
               "immovable_assets_rs": "immovable_assets_rs"}
FILL_IF_EMPTY = {"total_assets_rs": "total_assets_rs",
                 "liabilities_rs": "liabilities_rs"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    # Join by candidate_id — the comparison pages emit a mix of http/https
    # absolute links, so raw URLs don't line up with the stage-1 URLs.
    def _cand_id(url: str) -> str:
        m = re.search(r"candidate_id=(\d+)", url or "")
        return m.group(1) if m else ""

    with COMPARISON_CSV.open("r", encoding="utf-8") as f:
        comparison = {r["candidate_id"]: r for r in csv.DictReader(f) if r.get("candidate_id")}
    with MYNETA_CSV.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)

    for col in list(NEW_ALWAYS.values()) + list(NEW_IF_TEXT.values()):
        if col not in fieldnames:
            fieldnames.append(col)

    matched = filled_totals = discrepancies = 0
    unmatched_comparison = set(comparison)
    for row in rows:
        for col in list(NEW_ALWAYS.values()) + list(NEW_IF_TEXT.values()):
            row.setdefault(col, "")
        cid = _cand_id(row.get("myneta_url", ""))
        c = comparison.get(cid)
        if not c:
            continue
        matched += 1
        unmatched_comparison.discard(cid)
        for src, dst in NEW_ALWAYS.items():
            if (c.get(src) or "").strip():
                row[dst] = c[src].strip()
        for src, dst in NEW_IF_TEXT.items():
            if (c.get(src) or "").strip() and not (row.get(dst) or "").strip():
                row[dst] = c[src].strip()
        for src, dst in FILL_IF_EMPTY.items():
            cval = (c.get(src) or "").strip()
            mine = (row.get(dst) or "").strip()
            if cval and not mine:
                row[dst] = cval
                filled_totals += 1
            elif cval and mine and cval != mine:
                discrepancies += 1
                print(f"DISCREPANCY {row['candidate']} ({row['2026_constituency']}) "
                      f"{dst}: listing={mine} comparison={cval}")

    # The comparison chart image-hides movable+total for every candidate but
    # shows immovable as text, so movable is derivable where the listing gave
    # us the total.
    derived_movable = 0
    for row in rows:
        if (row.get("movable_assets_rs") or "").strip():
            continue
        try:
            total = int(row.get("total_assets_rs") or "")
            immovable = int(row.get("immovable_assets_rs") or "")
        except ValueError:
            continue
        if total - immovable >= 0:
            row["movable_assets_rs"] = str(total - immovable)
            derived_movable += 1

    print(f"myneta rows: {len(rows)} | comparison rows: {len(comparison)} | "
          f"matched: {matched} | totals filled from comparison: {filled_totals} | "
          f"movable derived (total - immovable): {derived_movable} | "
          f"discrepancies: {discrepancies} | comparison-only urls: {len(unmatched_comparison)}")
    if unmatched_comparison:
        for u in sorted(unmatched_comparison)[:10]:
            print(f"  comparison-only: {u}")

    if args.dry_run:
        print("Dry run — no files written.")
        return

    shutil.copy2(MYNETA_CSV, MYNETA_CSV.with_suffix(".csv.comparison_bak"))
    with MYNETA_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(rows)
    print(f"Updated {MYNETA_CSV}")


if __name__ == "__main__":
    main()
