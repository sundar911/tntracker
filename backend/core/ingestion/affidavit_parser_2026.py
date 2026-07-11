"""Parse OCR-extracted text from an ECI Form 26 affidavit (2026 Tamil Nadu elections).

ECI affidavits are bilingual (Tamil + English). Key sections:
  - Candidate info: name, address, PAN, social media
  - Section A: Movable assets (bank, investments, vehicles, jewellery)
  - Section B: Immovable assets (land, property)
  - Section C: Liabilities (loans)
  - Criminal cases (குற்ற வழக்கு)
  - Education (கல்வித்தகுதி / [10])
  - Profession (தொழில்)

The parser returns a dict with structured fields ready for DB import.
Amounts are parsed from Indian number format (e.g. "1,23,45,678" → 12345678).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedAffidavit2026:
    # Candidate identity
    name: str = ""
    name_ta: str = ""
    pan: str = ""
    constituency: str = ""
    party: str = ""
    address: str = ""

    # Profile
    education: str = ""
    profession: str = ""
    age: Optional[int] = None
    gender: str = ""

    # Financial (latest year)
    assets_total: Optional[int] = None
    liabilities_total: Optional[int] = None

    # Criminal cases
    criminal_cases_count: int = 0
    criminal_cases_details: list[dict] = field(default_factory=list)

    # Raw text kept for debugging
    raw_text: str = ""


# ---------------------------------------------------------------------------
# Amount parsing helpers
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(
    r"(?:Rs\.?|₹)\s*([0-9][0-9,]*(?:\.[0-9]+)?)\s*(?:/-|/)?",
    re.IGNORECASE,
)

def _parse_amount(text: str) -> Optional[int]:
    """Return the first rupee amount found in text as an integer, or None."""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    cleaned = m.group(1).replace(",", "")
    try:
        return int(float(cleaned))
    except ValueError:
        return None


def _parse_largest_amount(text: str) -> Optional[int]:
    """Return the largest rupee amount in text (used for totals section)."""
    amounts = []
    for m in _AMOUNT_RE.finditer(text):
        cleaned = m.group(1).replace(",", "")
        try:
            amounts.append(int(float(cleaned)))
        except ValueError:
            pass
    return max(amounts) if amounts else None


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------

def _extract_pan(text: str) -> str:
    m = re.search(r"\b([A-Z]{5}[0-9]{4}[A-Z])\b", text)
    return m.group(1) if m else ""


def _extract_education(text: str) -> str:
    """Look for education section marker [10] or கல்வித்தகுதி."""
    # English education line e.g. "B.Sc" / "B.E" / "M.A"
    patterns = [
        r"\[10\][^\n]*\n(.*?)(?=\n\s*\n|\[11\])",
        r"கல்வித்தகுதி[:\s]+(.*?)(?=\n|\r)",
        r"(B\.(?:Sc|E|Tech|A|Com|Ed)[^,\n]{0,60})",
        r"(M\.(?:Sc|E|Tech|A|Com|Phil)[^,\n]{0,60})",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            edu = m.group(1).strip()
            edu = re.sub(r"\s+", " ", edu)
            if 3 < len(edu) < 200:
                return edu
    return ""


def _extract_profession(text: str) -> str:
    patterns = [
        r"தொழில்[:\s]+(.*?)(?=\n|\r)",
        r"Profession[:\s]+(.*?)(?=\n|\r)",
        r"occupation[:\s]+(.*?)(?=\n|\r)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            prof = m.group(1).strip()
            if 2 < len(prof) < 150:
                return prof
    return ""


def _extract_assets_total(text: str) -> Optional[int]:
    """Find total movable + immovable assets in the assets summary section."""
    # Look for grand total line (மொத்த சொத்துகள் / Total Assets)
    total_patterns = [
        r"மொத்த சொத்துகள்.*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
        r"Total Assets.*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
        r"(?:Grand\s+)?Total.*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
        r"மொத்தம்.*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
    ]
    for pat in total_patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            cleaned = m.group(1).replace(",", "")
            try:
                val = int(float(cleaned))
                if val > 100:  # sanity: more than ₹100
                    return val
            except ValueError:
                pass
    return None


def _extract_liabilities_total(text: str) -> Optional[int]:
    patterns = [
        r"மொத்த(?:\s+|)(?:கடன்|பாக்கி|கடனாளி).*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
        r"Total Liabilit.*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
        r"(?:கடன்|Liabilit).*?(?:Rs\.?|₹)\s*([0-9][0-9,]+)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE | re.DOTALL)
        if m:
            cleaned = m.group(1).replace(",", "")
            try:
                val = int(float(cleaned))
                if val > 0:
                    return val
            except ValueError:
                pass
    return None


def _extract_criminal_cases(text: str) -> tuple[int, list[dict]]:
    """Return (count, [case_detail_dicts]).

    ECI Form 26 Section 5 / 6 lists criminal cases.
    If the section says "இல்லை" / "Nil" / "None" → count = 0.
    """
    # Quick nil check
    nil_pattern = re.compile(
        r"(?:குற்ற வழக்கு|criminal case)[^\n]{0,80}(?:இல்லை|nil|none|no case)",
        re.IGNORECASE,
    )
    if nil_pattern.search(text):
        return 0, []

    # Count numeric entries in the criminal-cases table
    section_pattern = re.compile(
        r"(?:குற்ற வழக்கு|Section\s+\d+.*?IPC|criminal case)(.*?)(?=\n{2,}|\Z)",
        re.IGNORECASE | re.DOTALL,
    )
    m = section_pattern.search(text)
    if not m:
        return 0, []

    section_text = m.group(1)
    if re.search(r"(?:இல்லை|nil|none)", section_text, re.IGNORECASE):
        return 0, []

    # Try to count numbered rows like "1.", "2.", etc.
    rows = re.findall(r"^\s*\d+[\.\)]\s+", section_text, re.MULTILINE)
    count = max(len(rows), 1) if section_text.strip() else 0
    return count, []


def _extract_constituency(text: str) -> str:
    patterns = [
        r"(?:சட்டமன்றத் தொகுதி|Assembly Constituency)[:\s]+([\w\s]+?)(?:\n|,|\d)",
        r"constituency[:\s]+([\w\s]+?)(?:\n|,)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if 2 < len(val) < 100:
                return val
    return ""


def _extract_party(text: str) -> str:
    # Look for known 2026 party names first
    known = [
        "Tamilaga Vettri Kazhagam",
        "தமிழக வெற்றி கழகம்",
        "TVK",
        "AIADMK",
        "DMK",
        "BJP",
        "INC",
        "PMK",
    ]
    for name in known:
        if name.lower() in text.lower():
            return name
    patterns = [
        r"(?:கட்சி|Party)[:\s]+([\w\s]+?)(?:\n|,|\d)",
    ]
    for pat in patterns:
        m = re.search(pat, text, re.IGNORECASE)
        if m:
            val = m.group(1).strip()
            if 2 < len(val) < 100:
                return val
    return ""


def _extract_address(text: str) -> str:
    # The notarial certificate contains address in English
    m = re.search(
        r"(?:residing at|address)[:\s]*(No\.?\s*\d+.*?Chennai.*?(?:\d{6}|\n))",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if m:
        addr = re.sub(r"\s+", " ", m.group(1)).strip().rstrip(",.")
        if len(addr) < 300:
            return addr
    return ""


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_affidavit_text(ocr_text: str) -> ParsedAffidavit2026:
    """Parse OCR-extracted affidavit text into structured fields.

    Args:
        ocr_text: Concatenated markdown/plain text output from Sarvam OCR.

    Returns:
        ParsedAffidavit2026 with best-effort field extraction.
    """
    result = ParsedAffidavit2026(raw_text=ocr_text)

    result.pan = _extract_pan(ocr_text)
    result.education = _extract_education(ocr_text)
    result.profession = _extract_profession(ocr_text)
    result.assets_total = _extract_assets_total(ocr_text)
    result.liabilities_total = _extract_liabilities_total(ocr_text)
    result.criminal_cases_count, result.criminal_cases_details = _extract_criminal_cases(ocr_text)
    result.constituency = _extract_constituency(ocr_text)
    result.party = _extract_party(ocr_text)
    result.address = _extract_address(ocr_text)

    return result
