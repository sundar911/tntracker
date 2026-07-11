"""Use Sarvam LLM to extract missing affidavit fields from OCR markdown.

When the regex parser fails to extract certain fields (due to OCR quality issues
like dropped table columns or garbled text), we send the raw markdown to
Sarvam's LLM with a targeted prompt asking for ONLY the missing fields.

API: POST https://api.sarvam.ai/v1/chat/completions
Auth: api-subscription-key header
Model: sarvam-m (or sarvam-105b for higher accuracy)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import requests

SARVAM_CHAT_URL = "https://api.sarvam.ai/v1/chat/completions"
DEFAULT_MODEL = "sarvam-m"


def _build_prompt(missing_fields: list[str], candidate_name: str) -> str:
    """Build a structured extraction prompt for the missing fields."""
    field_descriptions = {
        "education": "Highest educational qualification (degree, college/university, year). Look for Section 10 or Part B row 11.",
        "profession": "Candidate's profession/occupation. Look for Section 9(a).",
        "spouse_profession": "Spouse's profession/occupation. Look for Section 9(b).",
        "assets_total": "Total assets in rupees (sum of movable + immovable for self + spouse + dependents). Look for Part B abstract table rows A and B.",
        "liabilities_total": "Total liabilities in rupees. Look for Part B abstract table row 9.",
        "criminal_cases_count": "Number of pending criminal cases. Look for Part B row 5.",
        "phone": "Candidate's phone number. Look for Section 3.",
        "email": "Candidate's email address. Look for Section 3.",
        "social_media": "Social media accounts (Facebook, Twitter/X, Instagram, YouTube URLs). Look for Section 3.",
        "constituency_number": "Assembly constituency number (1-234). Look for Part B row 3.",
    }

    fields_needed = "\n".join(
        f"- {f}: {field_descriptions.get(f, f)}"
        for f in missing_fields
    )

    return f"""You are extracting data from an Indian election affidavit (ECI Form 26) for candidate {candidate_name}.

The affidavit text below was OCR'd and may have formatting issues. Extract ONLY these missing fields:

{fields_needed}

Rules:
- Return a JSON object with ONLY the requested field names as keys.
- For amounts, return integer values in rupees (no commas, no "Rs.", no "/-").
- For criminal_cases_count, return an integer (0 if none).
- For social_media, return a JSON object like {{"facebook": "url", "twitter": "url"}}.
- If a field truly cannot be found in the text, set its value to null.
- Do NOT hallucinate or guess values. Only extract what is clearly stated in the text.
- The text may be in Tamil or English or both.

Return ONLY valid JSON, no explanation."""


def extract_missing_fields(
    md_path: Path,
    missing_fields: list[str],
    candidate_name: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Send affidavit markdown to Sarvam LLM to extract missing fields.

    Args:
        md_path: Path to the .md file.
        missing_fields: List of field names to extract (e.g. ["education", "assets_total"]).
        candidate_name: Candidate name for context.
        api_key: Sarvam API key.
        model: Sarvam model name.

    Returns:
        Dict of extracted fields (field_name -> value). Missing fields are None.
    """
    if not missing_fields:
        return {}

    raw_text = md_path.read_text(encoding="utf-8")
    # Aggressively strip to fit within sarvam-m's 8K token context (~6K chars for text)
    text = re.sub(r"!\[Image\]\(data:image/[^)]+\)", "", raw_text)
    text = re.sub(r"\*The image[^*]+\*", "", text)
    text = re.sub(r"\*The provided image[^*]+\*", "", text)
    text = re.sub(r"\*The provided[^*]+\*", "", text)
    text = re.sub(r"\*I am an expert[^*]+\*", "", text)
    # Remove repeated page headers (QR codes, affidavit IDs, stamp descriptions)
    text = re.sub(r"Affidavit ID\s*:.*?\n", "", text)
    text = re.sub(r"Date of Print\s*:.*?\n", "", text)
    text = re.sub(r"\[QR_CODE\]", "", text)
    text = re.sub(r"I am an expert OCR[^\n]+\n", "", text)
    # Remove notary stamp descriptions
    text = re.sub(r"\*[^*]{50,500}\*", "", text)
    # Collapse blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Remove HTML table formatting noise but keep content
    text = re.sub(r"</?(?:thead|tbody|table)>", "", text)
    text = re.sub(r"<tr[^>]*>", "\n", text)
    text = re.sub(r"</tr>", "", text)
    text = re.sub(r'<t[dh][^>]*colspan="[^"]*"[^>]*>', " | ", text)
    text = re.sub(r'<t[dh][^>]*rowspan="[^"]*"[^>]*>', " | ", text)
    text = re.sub(r"<t[dh][^>]*>", " | ", text)
    text = re.sub(r"</t[dh]>", "", text)
    text = re.sub(r"<br\s*/?>", " ", text)
    text = re.sub(r"<[^>]+>", "", text)  # strip remaining HTML
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = text.strip()

    # Hard truncate to ~5500 chars to fit in 8K context with prompt
    if len(text) > 5500:
        text = text[:5500] + "\n\n[...truncated...]"

    prompt = _build_prompt(missing_fields, candidate_name)

    resp = requests.post(
        SARVAM_CHAT_URL,
        headers={
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "user", "content": f"{prompt}\n\n---\nAFFIDAVIT TEXT:\n{text}"},
            ],
            "temperature": 0.1,
        },
        timeout=120,
    )

    if not resp.ok:
        raise RuntimeError(f"Sarvam LLM API error {resp.status_code}: {resp.text[:200]}")

    data = resp.json()
    content = data["choices"][0]["message"]["content"]

    # Parse JSON from the response (may be wrapped in ```json ... ```)
    content = re.sub(r"^```json\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content.strip())

    try:
        result = json.loads(content)
    except json.JSONDecodeError:
        # Try to find JSON object in the response
        m = re.search(r"\{[^{}]+\}", content, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
        else:
            return {f: None for f in missing_fields}

    # Normalize types
    for field in missing_fields:
        val = result.get(field)
        if field in ("assets_total", "liabilities_total") and val is not None:
            if isinstance(val, str):
                cleaned = re.sub(r"[^\d]", "", val)
                result[field] = int(cleaned) if cleaned else None
            elif isinstance(val, (int, float)):
                result[field] = int(val)
        if field == "criminal_cases_count" and val is not None:
            if isinstance(val, str):
                digits = re.sub(r"[^\d]", "", val)
                result[field] = int(digits) if digits else 0
        if field == "constituency_number" and val is not None:
            if isinstance(val, str):
                digits = re.sub(r"[^\d]", "", val)
                result[field] = int(digits) if digits else None

    return {f: result.get(f) for f in missing_fields}


def fill_gaps_for_file(
    md_path: Path,
    parsed: dict,
    candidate_name: str,
    api_key: str,
    model: str = DEFAULT_MODEL,
) -> dict:
    """Identify missing fields in parsed data and fill them using LLM.

    Args:
        md_path: Path to the .md file.
        parsed: Dict from parse_form26_markdown (or equivalent).
        candidate_name: Candidate name.
        api_key: Sarvam API key.
        model: Sarvam model name.

    Returns:
        Updated parsed dict with LLM-filled values.
    """
    missing = []
    if not parsed.get("education"):
        missing.append("education")
    if not parsed.get("profession"):
        missing.append("profession")
    if parsed.get("assets_total") is None:
        missing.append("assets_total")
    if parsed.get("liabilities_total") is None:
        missing.append("liabilities_total")
    if not parsed.get("phone"):
        missing.append("phone")
    if not parsed.get("email"):
        missing.append("email")
    if not parsed.get("constituency_number"):
        missing.append("constituency_number")

    if not missing:
        return parsed

    llm_result = extract_missing_fields(md_path, missing, candidate_name, api_key, model)

    # Merge LLM results into parsed data (only overwrite None/empty values)
    for field, val in llm_result.items():
        if val is not None and val != "" and not parsed.get(field):
            parsed[field] = val

    return parsed
