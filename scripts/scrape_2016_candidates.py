"""
Scrape individual MyNeta 2016 candidate detail pages for MLAs missing 2021 data.

These candidates have 2016 records but no 2021 records on myneta.
"""

from __future__ import annotations

import csv
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

OUTPUT_DIR = Path("data")
OUTPUT_FILE = OUTPUT_DIR / "fct_candidates_16.csv"

# Candidates with 2016 data to scrape (2021 records missing)
CANDIDATES_2016 = [
    {
        "name": "Ambethkumar S",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=1509",
        "constituency": "VANDAVASI (SC)",
        "district": "TIRUVANNAMALAI",
        "party": "DMK",
    },
    {
        "name": "Chezhiaan Govi.",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=1557",
        "constituency": "THIRUVIDAIMARUDUR (SC)",
        "district": "THANJAVUR",
        "party": "DMK",
    },
    {
        "name": "Ruby R Manoharan",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=4668",
        "constituency": "NANGUNERI",
        "district": "TIRUNELVELI",
        "party": "INC",
    },
    {
        "name": "Subramanian. Ma",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=699",
        "constituency": "SAIDAPET",
        "district": "CHENNAI",
        "party": "DMK",
    },
    {
        "name": "T.M.Anbarasan",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=2343",
        "constituency": "ALANDUR",
        "district": "KANCHEEPURAM",
        "party": "DMK",
    },
    {
        "name": "Raajendran V.G.",
        "url": "https://www.myneta.info/tamilnadu2016/candidate.php?candidate_id=52",
        "constituency": "THIRUVALLUR",
        "district": "THIRUVALLUR",
        "party": "DMK",
    },
]


@dataclass
class CandidateData:
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


def _get_text(element) -> str:
    """Extract clean text from a BeautifulSoup element."""
    if element is None:
        return ""
    return " ".join(element.get_text(" ", strip=True).split())


def _extract_rs_amount(value: str) -> str:
    """Extract numeric amount from Rs string like 'Rs 1,54,26,000 ~1 Crore+'."""
    if not value:
        return ""
    # Try to find Rs amount first
    match = re.search(r"Rs\s*([0-9,]+)", value)
    if match:
        return match.group(1).replace(",", "")
    # If no Rs prefix, try to find any number sequence
    match = re.search(r"([0-9,]+)", value)
    if match:
        return match.group(1).replace(",", "")
    return ""


def _fetch_html(url: str) -> str:
    """Fetch HTML content from a URL using Playwright for JavaScript rendering."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=60000)
        html = page.content()
        browser.close()
    return html


def _extract_criminal_cases(soup: BeautifulSoup) -> str:
    """Extract criminal cases count from the Crime-O-Meter section."""
    # Look for the green panel with Crime-O-Meter
    crime_panel = soup.find("h3", string=re.compile(r"Crime-O-Meter", re.IGNORECASE))
    if crime_panel:
        parent = crime_panel.find_parent("div", class_="w3-panel")
        if parent:
            # Check for "No criminal cases"
            no_cases = parent.find("div", string=re.compile(r"No criminal cases", re.IGNORECASE))
            if no_cases:
                return "0"
            # Look for "Number of Criminal Cases: X"
            text = _get_text(parent)
            match = re.search(r"Number of Criminal Cases:\s*(\d+)", text, re.IGNORECASE)
            if match:
                return match.group(1)
    return ""


def _extract_education(soup: BeautifulSoup) -> str:
    """Extract education category from Educational Details section."""
    edu_header = soup.find("h3", string=re.compile(r"Educational Details", re.IGNORECASE))
    if edu_header:
        parent = edu_header.find_parent("div", class_="w3-panel")
        if parent:
            text = _get_text(parent)
            # Look for "Category: X" pattern
            match = re.search(r"Category:\s*([^M][^\n]+?)(?:\s+[A-Z]|$)", text)
            if match:
                edu = match.group(1).strip()
                return _normalize_education(edu)
            # Alternative: just get text after "Category:"
            match = re.search(r"Category:\s*(\S+(?:\s+\S+)?)", text)
            if match:
                edu = match.group(1).strip()
                return _normalize_education(edu)
    return ""


def _normalize_education(edu: str) -> str:
    """Normalize education string to match fct_candidates_21.csv format."""
    edu = edu.strip()
    # Add "Pass" suffix for grade-based education
    if re.match(r"^\d+(th|st|nd|rd)$", edu, re.IGNORECASE):
        return f"{edu} Pass"
    # Map common variations
    edu_map = {
        "Graduate": "Graduate Professional",
        "Post Graduate": "Post Graduate",
        "Doctorate": "Doctorate",
        "Others": "Others",
        "Literate": "Literate",
        "Illiterate": "Illiterate",
    }
    return edu_map.get(edu, edu)


def _extract_age(soup: BeautifulSoup) -> str:
    """Extract age from the candidate info section."""
    # Look for <b>Age:</b> followed by text
    age_label = soup.find("b", string=re.compile(r"Age:", re.IGNORECASE))
    if age_label:
        # Get the next sibling text or parent text
        parent = age_label.parent
        if parent:
            text = _get_text(parent)
            match = re.search(r"Age:\s*(\d+)", text)
            if match:
                return match.group(1)
    return ""


def _extract_assets_liabilities(soup: BeautifulSoup) -> tuple[str, str]:
    """Extract assets and liabilities from the Assets & Liabilities table."""
    assets = ""
    liabilities = ""
    
    # Find the table with w3-table w3-striped class
    tables = soup.find_all("table", class_=lambda c: c and "w3-table" in c)
    
    for table in tables:
        rows = table.find_all("tr")
        for row in rows:
            cells = row.find_all("td")
            if len(cells) >= 2:
                label = _get_text(cells[0]).lower()
                value_cell = cells[1]
                # Get the full text which contains Rs amount and ~X Crore+ notation
                value = _get_text(value_cell)
                
                if "assets" in label and "liabilities" not in label:
                    assets = _format_amount(value)
                elif "liabilities" in label:
                    liabilities = _format_amount(value)
    
    return assets, liabilities


def _format_amount(value: str) -> str:
    """Format amount string to match fct_candidates_21.csv format like 'Rs X,XX,XXX ~X Crore+'."""
    if not value or value.lower() == "nil" or "nil" in value.lower():
        return ""
    
    value = value.strip()
    
    # If it starts with Rs, it's already in good format - just ensure ~ has space
    if value.startswith("Rs"):
        # Normalize the ~ separator to have space before it
        value = re.sub(r"\s*~\s*", " ~", value)
        return value
    
    # If it doesn't start with Rs but has numbers, add Rs prefix
    # Format: "75,258 75 Thou+" -> "Rs 75,258 ~75 Thou+"
    match = re.match(r"([0-9,]+)\s+(.+)", value)
    if match:
        amount = match.group(1)
        suffix = match.group(2)
        return f"Rs {amount} ~{suffix}"
    
    # Return original value if no pattern matched
    return value


def scrape_candidate_page(url: str) -> dict:
    """Scrape a single candidate detail page and extract all data."""
    html = _fetch_html(url)
    soup = BeautifulSoup(html, "lxml")
    
    criminal_cases = _extract_criminal_cases(soup)
    education = _extract_education(soup)
    age = _extract_age(soup)
    assets, liabilities = _extract_assets_liabilities(soup)
    
    return {
        "criminal_cases": criminal_cases,
        "education": education,
        "age": age,
        "total_assets": assets,
        "liabilities": liabilities,
    }


def write_csv(candidates: list[CandidateData], output_path: Path = OUTPUT_FILE):
    """Write candidates to CSV file in the same format as fct_candidates_21.csv."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "candidate",
                "party",
                "criminal_cases",
                "education",
                "age",
                "total_assets",
                "liabilities",
                "2021_constituency",
                "2021_district",
                "myneta_url",
                "total_assets_rs",
                "liabilities_rs",
                "sitting_MLA",
                "bye_election",
                "const_off",
            ]
        )
        for candidate in candidates:
            # Derive const_off from constituency name (title case, clean)
            const_off = candidate.constituency.replace("(SC)", "").replace("(ST)", "").strip()
            const_off = const_off.title()
            if "(SC)" in candidate.constituency:
                const_off += " (SC)"
            if "(ST)" in candidate.constituency:
                const_off += " (ST)"
            
            writer.writerow(
                [
                    candidate.name,
                    candidate.party,
                    candidate.criminal_cases,
                    candidate.education,
                    candidate.age,
                    candidate.total_assets,
                    candidate.liabilities,
                    candidate.constituency,
                    candidate.district,
                    candidate.myneta_url,
                    _extract_rs_amount(candidate.total_assets),
                    _extract_rs_amount(candidate.liabilities),
                    1,  # sitting_MLA - all these are sitting MLAs
                    0,  # bye_election
                    const_off,
                ]
            )


def main():
    """Main entry point - scrape all 2016 candidates and write to CSV."""
    candidates: list[CandidateData] = []
    
    for idx, candidate_info in enumerate(CANDIDATES_2016, start=1):
        url = candidate_info["url"]
        print(f"[{idx}/{len(CANDIDATES_2016)}] Scraping {candidate_info['name']} from {url}...")
        
        try:
            scraped_data = scrape_candidate_page(url)
            
            candidate = CandidateData(
                name=candidate_info["name"],
                party=candidate_info["party"],
                criminal_cases=scraped_data["criminal_cases"],
                education=scraped_data["education"],
                age=scraped_data["age"],
                total_assets=scraped_data["total_assets"],
                liabilities=scraped_data["liabilities"],
                constituency=candidate_info["constituency"],
                district=candidate_info["district"],
                myneta_url=url,
            )
            candidates.append(candidate)
            
            print(f"    Criminal cases: {candidate.criminal_cases}")
            print(f"    Education: {candidate.education}")
            print(f"    Age: {candidate.age}")
            print(f"    Assets: {candidate.total_assets}")
            print(f"    Liabilities: {candidate.liabilities}")
            
        except Exception as exc:
            print(f"    ERROR: {exc}", file=sys.stderr)
    
    write_csv(candidates)
    print(f"\nWrote {len(candidates)} candidates to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
