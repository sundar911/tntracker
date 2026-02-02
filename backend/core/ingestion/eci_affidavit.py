from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass
class AffidavitRecord:
    name: str
    constituency: str
    party: str
    status: str
    criminal_cases: int | None
    serious_cases: int | None
    assets_total: int | None
    liabilities_total: int | None
    education: str


def _parse_int(value: str | None) -> int | None:
    if value is None:
        return None
    cleaned = value.replace(",", "").strip()
    if not cleaned or cleaned.lower() in {"nil", "na", "n/a"}:
        return None
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _get_first(row: dict, keys: Iterable[str]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value:
            return str(value).strip()
    return None


def load_affidavit_csv(path: str | Path) -> list[AffidavitRecord]:
    records: list[AffidavitRecord] = []
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            name = _get_first(row, ["Candidate Name", "Name", "candidate_name"])
            constituency = _get_first(row, ["Constituency", "AC Name", "constituency"])
            party = _get_first(row, ["Party", "Party Name", "party"])
            status = _get_first(row, ["Status", "Candidate Status", "status"]) or "unknown"
            if not name or not constituency:
                continue

            record = AffidavitRecord(
                name=name,
                constituency=constituency,
                party=party or "Independent",
                status=status,
                criminal_cases=_parse_int(_get_first(row, ["Criminal Cases", "criminal_cases"])),
                serious_cases=_parse_int(_get_first(row, ["Serious Criminal Cases", "serious_cases"])),
                assets_total=_parse_int(_get_first(row, ["Total Assets", "assets_total"])),
                liabilities_total=_parse_int(_get_first(row, ["Total Liabilities", "liabilities_total"])),
                education=_get_first(row, ["Education", "education"]) or "",
            )
            records.append(record)
    return records
