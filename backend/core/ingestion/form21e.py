from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader


@dataclass
class Form21ECandidateRow:
    name: str
    party: str
    votes: int | None


@dataclass
class Form21EParseResult:
    constituency: str | None
    candidates: list[Form21ECandidateRow]


_CONSTITUENCY_RE = re.compile(r"Name of Assembly Constituency\s*:\s*(.+)", re.IGNORECASE)


def _parse_votes(value: str) -> int | None:
    cleaned = value.replace(",", "").strip()
    if not cleaned.isdigit():
        return None
    return int(cleaned)


def parse_form21e_pdf(path: str | Path) -> Form21EParseResult:
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    constituency = None
    match = _CONSTITUENCY_RE.search(text)
    if match:
        constituency = match.group(1).strip()

    candidates: list[Form21ECandidateRow] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start_index = None
    for idx, line in enumerate(lines):
        if "Name of Candidate" in line and "Party" in line:
            start_index = idx + 1
            break

    if start_index is not None:
        for line in lines[start_index:]:
            if line.lower().startswith("total"):
                break
            parts = re.split(r"\s{2,}", line)
            if len(parts) < 2:
                continue
            name = parts[0]
            party = parts[1] if len(parts) > 1 else ""
            votes = _parse_votes(parts[-1]) if parts else None
            if name and party:
                candidates.append(Form21ECandidateRow(name=name, party=party, votes=votes))

    return Form21EParseResult(constituency=constituency, candidates=candidates)
