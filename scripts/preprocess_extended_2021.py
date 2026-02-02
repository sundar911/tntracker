from __future__ import annotations

import csv
import re
from pathlib import Path


INPUT_CSV = Path("data/tn_2021_candidates_extended.csv")


def _extract_rs_amount(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"Rs\s*([0-9,]+)", value)
    if not match:
        return ""
    return match.group(1).replace(",", "")


def main() -> None:
    if not INPUT_CSV.exists():
        raise SystemExit(f"Missing input CSV: {INPUT_CSV}")

    with INPUT_CSV.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)

    if not rows:
        raise SystemExit("No rows found in CSV.")

    output_fields = list(reader.fieldnames or [])
    for field in ("total_assets_rs", "liabilities_rs", "sitting_MLA"):
        if field not in output_fields:
            output_fields.append(field)

    last_constituency = None
    updated_rows = []
    for row in rows:
        total_assets = row.get("total_assets", "")
        liabilities = row.get("liabilities", "")
        row["total_assets_rs"] = _extract_rs_amount(total_assets)
        row["liabilities_rs"] = _extract_rs_amount(liabilities)

        constituency = row.get("2021_constituency", "")
        row["sitting_MLA"] = "1" if constituency and constituency != last_constituency else "0"
        last_constituency = constituency

        updated_rows.append(row)

    with INPUT_CSV.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(updated_rows)

    print(f"Updated {INPUT_CSV} with new columns.")


if __name__ == "__main__":
    main()
