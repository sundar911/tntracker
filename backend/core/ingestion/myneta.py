from __future__ import annotations

from dataclasses import dataclass
import gzip
import re
from urllib.request import Request
from typing import Iterable
from urllib.request import urlopen

from bs4 import BeautifulSoup


@dataclass
class MynetaCase:
    case_number: str
    sections: str
    status: str
    court: str
    year: int | None
    description: str


@dataclass
class MynetaProfile:
    name: str
    constituency: str
    party: str
    criminal_cases: int | None
    serious_cases: int | None
    cases: list[MynetaCase]


def _get_text(node) -> str:
    return " ".join(node.get_text(" ", strip=True).split())


def _parse_int(value: str) -> int | None:
    value = value.replace(",", "").strip()
    return int(value) if value.isdigit() else None


def fetch_html(url: str) -> str:
    request = Request(url, headers={"User-Agent": "tntracker/1.0"})
    with urlopen(request) as response:
        raw = response.read()
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        if "gzip" in encoding:
            raw = gzip.decompress(raw)
        return raw.decode("utf-8", errors="ignore")


def _find_value_by_label(soup: BeautifulSoup, label: str) -> str | None:
    label_cells = soup.find_all("td", string=lambda text: text and label.lower() in text.lower())
    for cell in label_cells:
        sibling = cell.find_next_sibling("td")
        if sibling:
            return _get_text(sibling)
    return None


def _parse_cases_tables(tables: Iterable) -> list[MynetaCase]:
    cases: list[MynetaCase] = []
    for table in tables:
        headers = [_get_text(th) for th in table.find_all("th")]
        if not headers:
            continue
        if not any("Case" in header or "Section" in header for header in headers):
            continue
        rows = table.find_all("tr")[1:]
        for row in rows:
            cells = [_get_text(td) for td in row.find_all("td")]
            if len(cells) < 3:
                continue
            case_number = cells[0]
            sections = cells[1] if len(cells) > 1 else ""
            status = cells[2] if len(cells) > 2 else ""
            court = cells[3] if len(cells) > 3 else ""
            year = _parse_int(cells[4]) if len(cells) > 4 else None
            description = " | ".join(cells[1:])
            cases.append(
                MynetaCase(
                    case_number=case_number,
                    sections=sections,
                    status=status,
                    court=court,
                    year=year,
                    description=description,
                )
            )
    return cases


def parse_myneta_profile(html: str) -> MynetaProfile:
    soup = BeautifulSoup(html, "html.parser")
    name = "Unknown"
    party = "Independent"
    constituency = "Unknown"

    title_text = soup.title.get_text(strip=True) if soup.title else ""
    header = soup.find(["h1", "h2"])
    header_text = _get_text(header) if header else ""

    title_source = title_text or header_text
    if title_source:
        match = re.search(r"^(.*?)\((.*?)\):Constituency-\s*([^(]+)", title_source)
        if match:
            name = match.group(1).strip()
            party = match.group(2).strip()
            constituency = match.group(3).strip()

    constituency = _find_value_by_label(soup, "Constituency") or constituency
    party = _find_value_by_label(soup, "Party") or party

    criminal_cases = None
    serious_cases = None
    crime_match = re.search(r"Number of Criminal Cases:\s*(\d+)", soup.get_text(" ", strip=True))
    if crime_match:
        criminal_cases = int(crime_match.group(1))

    criminal_cases = criminal_cases or _parse_int(_find_value_by_label(soup, "Criminal Cases") or "")
    serious_cases = _parse_int(_find_value_by_label(soup, "Serious Criminal Cases") or "")

    cases = _parse_cases_tables(soup.find_all("table"))

    return MynetaProfile(
        name=name,
        constituency=constituency,
        party=party,
        criminal_cases=criminal_cases,
        serious_cases=serious_cases,
        cases=cases,
    )
