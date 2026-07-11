"""Parse Sarvam Vision OCR markdown output from ECI Form 26 affidavits.

Handles both Tamil and English affidavit formats. The parser primarily targets:
  - Part B abstract table (Section 11) — the summary with all key data
  - Part A section 3 — contact info and social media
  - Part A section 9 — profession
  - Part A section 5/6 — criminal cases
  - Part A section 10 — education

Returns a ParsedForm26 dataclass with all extracted fields.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class ParsedForm26:
    # Identity
    name: str = ""
    constituency: str = ""
    constituency_number: Optional[int] = None
    party: str = ""
    age: Optional[int] = None
    gender: str = ""
    address: str = ""

    # Contact
    phone: str = ""
    email: str = ""
    social_media: dict = field(default_factory=dict)  # {"facebook": url, "twitter": url, ...}

    # Profile
    education: str = ""
    education_detail: dict = field(default_factory=dict)  # {degree, field, college, university, year}
    profession: str = ""
    spouse_profession: str = ""

    # Financial
    assets_total: Optional[int] = None
    liabilities_total: Optional[int] = None

    # Criminal
    criminal_cases_count: int = 0
    convictions_count: int = 0
    criminal_cases: list[dict] = field(default_factory=list)

    # Full additional details blob (stored in Affidavit.additional_details)
    additional_details: dict = field(default_factory=dict)

    # Filename for debugging
    source_file: str = ""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_AMOUNT_RE = re.compile(r"₹\s*([0-9][0-9,.]+)")


def _parse_amount(text: str) -> Optional[int]:
    """Parse an Indian-format rupee amount. Returns integer or None."""
    m = _AMOUNT_RE.search(text)
    if not m:
        return None
    cleaned = m.group(1).replace(",", "").replace(".", "").strip()
    if not cleaned:
        return None
    # Handle amounts like "500000.00" by taking integer part
    if "." in m.group(1):
        cleaned = m.group(1).replace(",", "").strip()
        try:
            return int(float(cleaned))
        except ValueError:
            return None
    try:
        return int(cleaned)
    except ValueError:
        return None


def _parse_plain_amount(text: str) -> Optional[int]:
    """Parse amount from text that may just have digits with commas, with or without ₹/ரூ/Rs."""
    # Try ₹ prefix first
    result = _parse_amount(text)
    if result is not None:
        return result
    # Try ரூ. or ரூபாய் or Rs. prefix
    m = re.search(r"(?:ரூ(?:பாய்)?\.?\s*|Rs\.?\s*)([0-9][0-9,.]+)", text)
    if m:
        cleaned = m.group(1).replace(",", "").rstrip(".")
        try:
            return int(float(cleaned))
        except ValueError:
            pass
    # Try plain number with commas: "4,45,440/-" or "51,000"
    m = re.search(r"([0-9][0-9,]+)(?:\s*/\s*-)?", text)
    if m:
        cleaned = m.group(1).replace(",", "")
        try:
            return int(cleaned)
        except ValueError:
            pass
    return None


def _strip_images(text: str) -> str:
    """Remove base64 image tags and Sarvam image descriptions."""
    text = re.sub(r"!\[Image\]\(data:image/[^)]+\)", "", text)
    text = re.sub(r"\*The image[^*]+\*", "", text)
    text = re.sub(r"\*The provided image[^*]+\*", "", text)
    return text


def _extract_table_cell_text(html: str) -> str:
    """Extract text content from an HTML table cell, stripping tags."""
    text = re.sub(r"<br\s*/?>", " ", html)
    text = re.sub(r"<[^>]+>", "", text)
    return text.strip()


# ---------------------------------------------------------------------------
# Section parsers
# ---------------------------------------------------------------------------

def _parse_candidate_intro(text: str) -> dict:
    """Parse the intro paragraph: name, age, gender, address."""
    result = {}

    # English: "I JOTHIBASU ** son/daughter/wife/ of Karuppaiyan Aged 42 years, resident of ..."
    m = re.search(
        r"I\s+([A-Z][A-Z\s.]+?)\s*\*?\*?\s*(?:son|daughter|wife)",
        text,
    )
    if m:
        result["name"] = re.sub(r"\s+", " ", m.group(1)).strip().rstrip("*").strip()

    # Tamil: "என்பவரின் மகளும், 33- வயதுடைய" or "என்பவரின் மகனும், 42 வயதுடைய"
    m_ta = re.search(r"என்பவரின்\s+(?:மகளும்|மகனும்|மனைவியும்)", text)
    if m_ta:
        if "மகளும்" in m_ta.group(0) or "மனைவியும்" in m_ta.group(0):
            result["gender"] = "Female"
        else:
            result["gender"] = "Male"

    # English gender from son/daughter
    m_gen = re.search(r"\b(son|daughter|wife)\b", text, re.IGNORECASE)
    if m_gen and "gender" not in result:
        val = m_gen.group(1).lower()
        result["gender"] = "Female" if val in ("daughter", "wife") else "Male"

    # Age: "Aged 42 years" or "33- வயதுடைய" or "42 வயதுடைய"
    m_age = re.search(r"(?:Aged|age)\s*(\d{2,3})\s*(?:years|வயது)", text, re.IGNORECASE)
    if not m_age:
        m_age = re.search(r"(\d{2,3})\s*-?\s*வயதுடைய", text)
    if m_age:
        result["age"] = int(m_age.group(1))

    return result


def _parse_contact(text: str) -> dict:
    """Parse section 3: phone, email, social media."""
    result = {"phone": "", "email": "", "social_media": {}}

    # Phone: "telephone number(s) is/are 8508489088" or "தொலைபேசி எண் ... 90032 21690"
    m = re.search(r"(?:telephone|தொலைபேசி)[^\d]{0,50}(\d[\d\s]{8,14}\d)", text, re.IGNORECASE)
    if m:
        result["phone"] = re.sub(r"\s+", "", m.group(1))

    # Email
    m = re.search(r"[\w.+-]+@[\w.-]+\.[\w]{2,}", text)
    if m:
        result["email"] = m.group(0)

    # Social media — look for URLs and platform names
    social = {}

    # Facebook
    fb = re.findall(r"(?:https?://)?(?:www\.)?facebook\.com/[\w./-]+", text)
    if fb:
        url = fb[0]
        if not url.startswith("http"):
            url = "https://" + url
        social["facebook"] = url

    # Twitter/X
    tw = re.findall(r"(?:https?://)?(?:www\.)?(?:twitter\.com|x\.com)/[\w./-]+", text)
    if tw:
        url = tw[0]
        if not url.startswith("http"):
            url = "https://" + url
        social["twitter"] = url
    else:
        # Handle "@handle (Twitter)" format
        m_tw = re.search(r"@(\w{3,30})\s*\(?\s*(?:Twitter|X)\s*\)?", text, re.IGNORECASE)
        if m_tw:
            social["twitter"] = f"https://x.com/{m_tw.group(1)}"

    # Instagram
    ig = re.findall(r"(?:https?://)?(?:www\.)?instagram\.com/[\w./-]+", text)
    if ig:
        url = ig[0]
        if not url.startswith("http"):
            url = "https://" + url
        social["instagram"] = url
    else:
        m_ig = re.search(r"(\w{3,30})\s*\(?\s*Instagram\s*\)?", text, re.IGNORECASE)
        if m_ig:
            social["instagram"] = f"https://instagram.com/{m_ig.group(1)}"

    # YouTube
    yt = re.findall(r"(?:https?://)?(?:www\.)?youtube\.com/[\w./-]+", text)
    if yt:
        url = yt[0]
        if not url.startswith("http"):
            url = "https://" + url
        social["youtube"] = url

    result["social_media"] = social
    return result


def _parse_profession(text: str) -> dict:
    """Parse section 9: profession (self and spouse)."""
    result = {"profession": "", "spouse_profession": ""}

    def _clean_profession(val) -> str:
        """Strip dots, dashes, leading/trailing junk from profession text."""
        if not val:
            return ""
        val = str(val)
        val = re.sub(r"^[\s.\-–:/*]+", "", val)
        val = re.sub(r"[\s.\-–]+$", "", val)
        val = re.sub(r"\.{2,}", "", val)  # Remove "...."
        val = val.strip()
        return val

    def _is_valid_profession(val: str) -> bool:
        if not val or len(val) <= 1:
            return False
        lower = val.lower()
        # Reject section headers and nil values
        if any(bad in lower for bad in ["details", "nil", "not applicable"]):
            return False
        if any(bad in val for bad in ["விவரங்கள்", "ஏதுமில்லை", "பொருந்தாது"]):
            return False
        # Reject if it looks like a person's name (starts with initials like "M.R." or "B.")
        # e.g. "M.R.விஜயபாஸ்கர்" or "B. ANAND"
        if re.match(r"^[A-Z]\.(?:[A-Z]\.)*\s*\S", val):
            return False
        return True

    # Self profession — English:
    # "(a) Self: Managing Director" or "(a) Self.....FARMER..." or "(a) Self Daily Wages."
    m = re.search(
        r"\(a\)\s*\.?\s*Self\s*[.:\-–\s]+(.+?)(?:\n|\r)",
        text, re.IGNORECASE,
    )
    if m:
        val = _clean_profession(m.group(1))
        if _is_valid_profession(val):
            result["profession"] = val

    # Self profession — Tamil:
    # "(a) தனி நபர்: profession" or "(அ) தனி நபர் கூலி"
    # "(a) தனிநபர் - வீட்டுவேலை" (no space in தனிநபர்)
    if not result["profession"]:
        m_ta = re.search(
            r"\((?:a|அ)\)\s*\.?\s*(?:தனி\s*நபர்|தனிநபர்|தனியர்|சுயம்|தானே)\s*[.:\-–/\s]+(.+?)(?:\n|\r)",
            text,
        )
        if m_ta:
            val = _clean_profession(m_ta.group(1))
            if _is_valid_profession(val):
                result["profession"] = val

    # Spouse — English
    m_sp = re.search(
        r"\(b\)\s*\.?\s*Spouse\s*[.:\-–\s]+(.+?)(?:\n|\r)",
        text, re.IGNORECASE,
    )
    if m_sp:
        val = _clean_profession(m_sp.group(1))
        if _is_valid_profession(val):
            result["spouse_profession"] = val

    # Spouse — Tamil: "(b) வாழ்க்கைத் துணை - profession" or "(ஆ) வாழ்க்கைத் துணை..."
    if not result["spouse_profession"]:
        m_sp_ta = re.search(
            r"\((?:b|ஆ)\)\s*\.?\s*வாழ்க்கைத்?\s*துணை\s*[.:\-–/\s]+(.+?)(?:\n|\r)",
            text,
        )
        if m_sp_ta:
            val = _clean_profession(m_sp_ta.group(1))
            if _is_valid_profession(val):
                result["spouse_profession"] = val

    return result


def _parse_education(text: str) -> dict:
    """Parse section 10 + Part B row 11: education details."""
    result = {"education": "", "education_detail": {}}

    # Strategy 1: Section 10 plain text
    # Matches all variants: (10), 10)., ## 10., or just கல்வித் தகுதி
    # Also handles inline answer: "(10) ... வருமாறு:- B.Sc., Maths"
    # Match section 10 in all known variants:
    # (10), 10)., ## 10., inside <th>, with/without parens
    # Tamil: எனது/என் கல்வித் தகுதி/தகுதிகள் பின்/கீழ் வருமாறு
    section10 = re.search(
        r"(?:^|\n)\s*(?:<th[^>]*>\s*)?(?:\#*\s*)?(?:\(?10[\).]?\s*[\).]?\s*)"
        r"[^\n]*(?:qualification|கல்வித்?\s*தகுதி)[^\n]*?(?:[-:.]*\s*)(.*?)(?:</th>)?$",
        text,
        re.IGNORECASE | re.MULTILINE,
    )
    if not section10:
        # "எனது கல்வித் தகுதி" or "என் கல்வித் தகுதிகள்" without section number
        section10 = re.search(
            r"(?:^|\n)\s*(?:\#*\s*)?(?:எனது|என்)\s+கல்வித்?\s*தகுதி(?:கள்)?\s*[^\n]*?(?:[-:.]*\s*)(.*?)$",
            text,
            re.MULTILINE,
        )

    if section10:
        inline_answer = section10.group(1).strip()
        # Clean up: remove trailing section markers that aren't actual education
        inline_answer = re.sub(r"^(?:is as under|(?:கள்\s*)?(?:பின்?\s*)?(?:கீழ்?\s*)?வருமாறு)[:\-–.\s]*$", "", inline_answer, flags=re.IGNORECASE).strip()
        # Also strip if the answer starts with a section marker followed by actual content
        inline_answer = re.sub(r"^(?:is as under|(?:கள்\s*)?(?:பின்?\s*)?(?:கீழ்?\s*)?வருமாறு)[:\-–.\s]+", "", inline_answer, flags=re.IGNORECASE).strip()
        if inline_answer and len(inline_answer) > 3:
            # Answer was on the same line: "(10) ... வருமாறு:- B.Sc., Maths"
            edu = re.sub(r"\s+", " ", inline_answer)
            result["education"] = edu
            return result

        # Answer is on subsequent lines until (11) or page break
        after = text[section10.end():]
        m_body = re.match(
            r"\n(.+?)(?:\n\s*\(?11[\).]|\nPage \d|\n---|\n\(சான்றிதழ்|\n\(Give details)",
            after,
            re.DOTALL,
        )
        if m_body:
            edu_text = m_body.group(1).strip()
            edu_text = re.sub(r"\(Give details.*", "", edu_text, flags=re.IGNORECASE).strip()
            edu_text = re.sub(r"\(சான்றிதழ்.*", "", edu_text, flags=re.IGNORECASE).strip()
            edu_text = re.sub(r"<[^>]+>", "", edu_text)
            lines = [l.strip() for l in edu_text.split("\n") if l.strip() and len(l.strip()) > 3]
            if lines:
                first = lines[0]
                first = re.sub(r"^\(\d+\)\s*", "", first)
                first = re.sub(r"^\d+[\.\)]\s*", "", first)
                first = re.sub(r"\s+", " ", first)
                if 3 < len(first) < 300:
                    result["education"] = first
                    return result

    # Strategy 2: Part B row 11 — "உயரளவு/உயர்நிலை கல்வித் தகுதி:-" or "Highest educational qualification"
    qual_m = re.search(
        r"(?:உயர(?:ளவு|்நிலை)\s*(?:க்\s*)?கல்வித்?\s*தகுதி|Highest\s+educational|Qualification)[:\-]*\s*(.*?)(?:\(சான்றிதழ்|\(Give details|</(?:td|th)>)",
        text,
        re.IGNORECASE | re.DOTALL,
    )
    if qual_m:
        edu_text = qual_m.group(1).strip()
        edu_text = re.sub(r"<br\s*/?>", " ", edu_text)
        edu_text = re.sub(r"<[^>]+>", "", edu_text)
        edu_text = re.sub(r"\s+", " ", edu_text).strip()
        if 3 < len(edu_text) < 300:
            result["education"] = edu_text
            return result

    return result


def _parse_part_b_abstract(text: str, skip_header_check: bool = False) -> dict:
    """Parse the Part B abstract table (Section 11) — the richest structured data source.

    Args:
        text: OCR markdown text to parse.
        skip_header_check: If True, treat the entire text as Part B content
            (useful when only Part B pages are OCR'd, so the header may be missing).
    """
    result = {}

    if skip_header_check:
        abstract_text = text
    else:
        # Find the abstract section
        abstract_start = re.search(
            r"(?:ABSTRACT|சுருக்கம்)", text, re.IGNORECASE
        )
        if not abstract_start:
            return result
        abstract_text = text[abstract_start.start():]

    # Helper: extract value from an abstract row.
    # Structure: <td>1.</td><td colspan="2">Label</td><td colspan="2">Value</td>
    # Tamil uses <th> instead of <td> sometimes.
    def _abstract_row(label_patterns: list[str]) -> str:
        """Find a row by label pattern and return the last cell in that row."""
        for pat in label_patterns:
            # Find ALL matches and prefer ones inside table rows
            for m in re.finditer(pat, abstract_text, re.IGNORECASE | re.DOTALL):
                # Walk backwards to find <tr> or <thead> start
                start = abstract_text.rfind("<tr", 0, m.start())
                if start == -1:
                    start = abstract_text.rfind("<thead", 0, m.start())
                if start == -1:
                    continue  # Not inside a table row, skip
                end_tag = abstract_text.find("</tr>", m.end())
                if end_tag == -1:
                    end_tag = m.end() + 500
                row_html = abstract_text[start:end_tag + 5]
                # Get all cells (both td and th)
                cells = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, re.DOTALL)
                if len(cells) >= 2:
                    # Value is the last cell
                    val = _extract_table_cell_text(cells[-1])
                    if val:
                        return val
        return ""

    # 1. Candidate name — look for the row with name label, extract last cell
    name_raw = _abstract_row([
        r"(?:Name of the [Cc]andidate|வேட்பாளர்(?:ின்)? பெயர்)",
    ])
    if name_raw:
        name = re.sub(r"^(?:Sh\./Smt\./Kum\.\s*|Sh\.\s*|Smt\.\s*|Kum\.\s*)", "", name_raw).strip()
        name = re.sub(r"^திரு/திருமதிசெல்வி\s*|^திரு\./திருமதி\./செல்வி\.\s*|^திரு\.\s*|^திருமதி\.\s*|^செல்வி\.\s*", "", name).strip()
        result["name"] = name
        # Only infer gender from prefix if it's NOT the generic "Sh./Smt./Kum."
        if "Sh./Smt./Kum." not in name_raw and "திரு./திருமதி./செல்வி." not in name_raw:
            if re.search(r"Smt\.|திருமதி|Kum\.|செல்வி", name_raw):
                result["gender"] = "Female"
            elif re.search(r"Sh\.|திரு\.", name_raw):
                result["gender"] = "Male"

    # 2. Address
    addr_raw = _abstract_row([
        r"(?:Full postal address|முழு அஞ்சல் முகவரி)",
    ])
    if addr_raw:
        result["address"] = addr_raw

    # 3. Constituency
    const_raw = _abstract_row([
        r"(?:Number and name of the constituency|தொகுதியின் எண்[\s.,;:]*பெயர்)",
    ])
    if const_raw:
        result["constituency_raw"] = const_raw
        # Parse "149-ARIYALUR, Tamil Nadu" or "038 – அரக்கோணம்" or "எண் : 08, அம்பத்தூர்"
        num_match = re.search(r"(\d{1,3})\s*[-–.]\s*([A-Z][A-Za-z\s]+)", const_raw)
        if num_match:
            result["constituency_number"] = int(num_match.group(1))
            result["constituency"] = num_match.group(2).strip().rstrip(",")
        else:
            # Tamil: "எண் : 08, அம்பத்தூர்..." or just a number at the start
            num_match = re.search(r"(?:எண்\s*:?\s*|:?\s*)?(\d{1,3})", const_raw)
            if num_match:
                result["constituency_number"] = int(num_match.group(1))

    # 4. Party
    party_raw = _abstract_row([
        r"(?:political party|அரசியல்\s*கட்சி)",
    ])
    if party_raw:
        result["party"] = party_raw

    # 5. Criminal cases count
    cases_raw = _abstract_row([
        r"(?:pending criminal cases|நிலுவையி(?:ல்|லுள்ள)\s*(?:உள்ள\s*)?குற்றவியல்)",
    ])
    if cases_raw:
        # If it's a simple number, use it directly
        simple = cases_raw.strip()
        if re.match(r"^\d{1,3}$", simple):
            result["criminal_cases_count"] = int(simple)
        elif re.search(r"(?:இல்லை|nil|none|not applicable|பொருந்தாது|0)", simple, re.IGNORECASE):
            result["criminal_cases_count"] = 0
        else:
            # Long text with case details — look for "மொத்த.*எண்ணிக்கை\s*(\d+)"
            # or "total.*pending.*(\d+)" or just the last standalone number
            m_total = re.search(r"(?:மொத்த|total)[^\d]{0,50}(\d{1,3})\b", cases_raw, re.IGNORECASE)
            if m_total:
                result["criminal_cases_count"] = int(m_total.group(1))
            else:
                # Extract all small numbers and take the first one
                nums = [int(n) for n in re.findall(r"\b(\d{1,3})\b", cases_raw) if int(n) < 200]
                result["criminal_cases_count"] = nums[0] if nums else 0

    # 6. Convictions count
    conv_raw = _abstract_row([
        r"(?:convicted|தண்டனை பெற்ற)",
    ])
    if conv_raw:
        digits = re.sub(r"[^\d]", "", conv_raw)
        result["convictions_count"] = int(digits) if digits else 0

    # 7. Income tax data
    income_tax = {}
    # Look for PAN + IT return rows
    pan_rows = re.findall(
        r"(?:Candidate|வேட்பாளர்).*?</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if pan_rows:
        pan_text = _extract_table_cell_text(pan_rows[0][0])
        year_text = _extract_table_cell_text(pan_rows[0][1])
        income_text = _extract_table_cell_text(pan_rows[0][2])
        income_tax["self"] = {
            "pan": pan_text if re.match(r"[A-Z]{5}\d{4}[A-Z]", pan_text) else "",
            "last_return_year": year_text,
            "last_return_income": _parse_plain_amount(income_text),
        }

    spouse_rows = re.findall(
        r"(?:Spouse|வாழ்க்கைத்?\s*துணை).*?</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if spouse_rows:
        pan_text = _extract_table_cell_text(spouse_rows[0][0])
        year_text = _extract_table_cell_text(spouse_rows[0][1])
        income_text = _extract_table_cell_text(spouse_rows[0][2])
        # Also try to get spouse name from PAN row
        spouse_name_m = re.search(
            r"(?:Spouse|வாழ்க்கைத்?\s*துணை).*?</td>\s*<td[^>]*>([A-Z][A-Z\s]+)</td>",
            abstract_text, re.IGNORECASE | re.DOTALL,
        )
        income_tax["spouse"] = {
            "name": _extract_table_cell_text(spouse_name_m.group(1)) if spouse_name_m else "",
            "pan": pan_text if re.match(r"[A-Z]{5}\d{4}[A-Z]", pan_text) else "",
            "last_return_year": year_text,
            "last_return_income": _parse_plain_amount(income_text),
        }

    if income_tax:
        result["income_tax"] = income_tax

    # 8. Asset totals from abstract table
    # Movable Assets row
    m_mov = re.search(
        r"(?:Moveable?\s*Assets|நகர்சொத்து).*?</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    assets_breakdown = {}
    if m_mov:
        self_movable = _parse_amount(m_mov.group(1))
        spouse_movable = _parse_amount(m_mov.group(2))
        assets_breakdown["movable_self"] = self_movable
        assets_breakdown["movable_spouse"] = spouse_movable

    # Immovable Assets — current market price row (most relevant)
    m_imm = re.search(
        r"(?:Current\s*Market\s*Price|Approximate|தற்போதைய).*?</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if m_imm:
        cell1 = m_imm.group(1)
        # Parse "Self :₹0.00 Inherited :₹1570000.00"
        self_acq = _parse_amount(re.search(r"Self\s*:?\s*(₹[0-9,.]+)", cell1).group(1)) if re.search(r"Self\s*:?\s*₹", cell1) else None
        inherited = _parse_amount(re.search(r"Inherited\s*:?\s*(₹[0-9,.]+)", cell1).group(1)) if re.search(r"Inherited\s*:?\s*₹", cell1) else None
        assets_breakdown["immovable_self_acquired"] = self_acq
        assets_breakdown["immovable_inherited"] = inherited

    if assets_breakdown:
        result["assets_breakdown"] = assets_breakdown

    # Compute total assets = sum of all movable + immovable across all persons
    def _extract_row_amounts(row_html: str) -> list[int]:
        """Extract all numeric amounts from table cells in a row."""
        amounts = []
        cells = re.findall(r"<(?:td|th)[^>]*>(.*?)</(?:td|th)>", row_html, re.DOTALL)
        for cell in cells:
            cell_text = _extract_table_cell_text(cell)
            # Skip nil/N/A variants
            if re.match(r"(?:Nil|N/A|பொருந்தாது|ஏதுமில்லை|இல்லை|A|B|9|10)\s*\.?$", cell_text, re.IGNORECASE):
                continue
            # Skip long pure-text label cells (but allow "ரூ.7,30,000/-")
            if len(cell_text) > 100 and not re.search(r"\d{2,}", cell_text):
                continue
            # Parse amount — handles ₹, Rs., ரூ., ரூபாய், and plain numbers
            val = _parse_plain_amount(cell_text)
            if val is not None and val > 0:
                amounts.append(val)
        return amounts

    all_amounts = []

    # Movable assets row: "A | Moveable Assets (Total value) | self | spouse | ..."
    movable_row = re.search(
        r"<tr[^>]*>.*?(?:Moveable?\s*Assets|அசையும்(?:\s|<br\s*/?>)*சொத்து).*?</tr>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if movable_row:
        all_amounts.extend(_extract_row_amounts(movable_row.group(0)))

    # Immovable: current market price row
    immovable_row = re.search(
        r"<tr[^>]*>.*?(?:Current\s*Market|Approximate|தற்போதைய(?:\s|<br\s*/?>)*சந்தை|B\s*\(\s*III\s*\)).*?</tr>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if immovable_row:
        all_amounts.extend(_extract_row_amounts(immovable_row.group(0)))

    if all_amounts:
        result["assets_total"] = sum(all_amounts)

    # Liabilities — look for the loans row (row 9)
    liab_amounts = []
    liab_row = re.search(
        r"<tr[^>]*>.*?(?:Liabilities|கடன்|பாக்கி).*?(?:Government|Bank|அரசு|வங்கி).*?</tr>",
        abstract_text, re.IGNORECASE | re.DOTALL,
    )
    if liab_row:
        liab_amounts = _extract_row_amounts(liab_row.group(0))
    if liab_amounts:
        result["liabilities_total"] = sum(liab_amounts)

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def parse_form26_markdown(md_path: str | Path) -> ParsedForm26:
    """Parse a Sarvam Vision OCR markdown file of an ECI Form 26 affidavit.

    Args:
        md_path: Path to the .md file in data/eci_2026/markdown/

    Returns:
        ParsedForm26 with all extracted fields.
    """
    md_path = Path(md_path)
    raw_text = md_path.read_text(encoding="utf-8")
    text = _strip_images(raw_text)

    result = ParsedForm26(source_file=md_path.name)

    # Infer constituency and candidate name from filename: CONSTITUENCY_CANDIDATE.md
    parts = md_path.stem.split("_", 1)
    if len(parts) == 2:
        result.constituency = parts[0].replace("_", " ").title()

    # Parse each section
    intro = _parse_candidate_intro(text)
    result.age = intro.get("age")
    result.gender = intro.get("gender", "")
    if intro.get("name"):
        result.name = intro["name"]

    contact = _parse_contact(text)
    result.phone = contact["phone"]
    result.email = contact["email"]
    result.social_media = contact["social_media"]

    prof = _parse_profession(text)
    result.profession = prof["profession"]
    result.spouse_profession = prof["spouse_profession"]

    edu = _parse_education(text)
    result.education = edu["education"]
    result.education_detail = edu.get("education_detail", {})

    # Part B abstract — most reliable structured source
    abstract = _parse_part_b_abstract(text)
    if abstract.get("name"):
        result.name = abstract["name"]
    if abstract.get("constituency"):
        result.constituency = abstract["constituency"]
    if abstract.get("constituency_number"):
        result.constituency_number = abstract["constituency_number"]
    if abstract.get("party"):
        result.party = abstract["party"]

    # Fallback: extract party from Section 1 text if Part B missed it
    if not result.party:
        m_party_s1 = re.search(
            r"(?:நான்|I am)\s+.*?(?:(\S+(?:\s+\S+){0,5})\s+என்னும் பெயருடைய\s*அரசியல்\s*கட்சி|"
            r"set up by\s+([A-Z][A-Za-z\s.]+?)(?:\s*\(|\s*/|\n))",
            text, re.IGNORECASE,
        )
        if m_party_s1:
            val = (m_party_s1.group(1) or m_party_s1.group(2) or "").strip()
            if val and len(val) > 2 and "அரசியல்" not in val:
                result.party = val

    if abstract.get("address"):
        result.address = abstract["address"]
    if abstract.get("gender"):
        result.gender = abstract["gender"]
    if abstract.get("criminal_cases_count") is not None:
        result.criminal_cases_count = abstract["criminal_cases_count"]
    if abstract.get("convictions_count") is not None:
        result.convictions_count = abstract["convictions_count"]
    if abstract.get("assets_total") is not None:
        result.assets_total = abstract["assets_total"]
    if abstract.get("liabilities_total") is not None:
        result.liabilities_total = abstract["liabilities_total"]

    # Build additional_details blob
    result.additional_details = {
        k: v
        for k, v in {
            "contact": {"phone": result.phone, "email": result.email},
            "social_media": result.social_media,
            "income_tax": abstract.get("income_tax"),
            "assets_breakdown": abstract.get("assets_breakdown"),
            "spouse_profession": result.spouse_profession,
            "convictions_count": result.convictions_count,
            "education_detail": result.education_detail,
        }.items()
        if v
    }

    return result
