#!/usr/bin/env python3
"""Fill missing candidate data using page tags + Sarvam Vision + Sonnet.

Three strategies based on what's missing:
  1. Financial (assets/liabilities): Sarvam Vision OCR on Part B pages → regex parse
  2. Phone: Sonnet at 150 DPI on Page 2
  3. Education/profession/cases: Sonnet on last 5 pages + Page 2

Requires page_tags.json from tag_affidavit_pages.py.

Usage:
    python scripts/fill_missing_data.py --anthropic-key $KEY --sarvam-key $KEY
    python scripts/fill_missing_data.py --anthropic-key $KEY --sarvam-key $KEY --limit 10
    python scripts/fill_missing_data.py --anthropic-key $KEY --sarvam-key $KEY --only financial
    python scripts/fill_missing_data.py --anthropic-key $KEY --sarvam-key $KEY --only phone
    python scripts/fill_missing_data.py --anthropic-key $KEY --sarvam-key $KEY --only text
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import re
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from core.ingestion.sarvam_ocr import ocr_zip_batch  # noqa: E402
from core.ingestion.parse_form26_markdown import (  # noqa: E402
    _parse_part_b_abstract,
    _strip_images,
)

CANDIDATES_JSON = REPO_ROOT / "data" / "eci_2026" / "candidates.json"
TAGS_FILE = REPO_ROOT / "data" / "eci_2026" / "page_tags.json"
CSV_FILE = REPO_ROOT / "data" / "fct_candidates_26.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get_pdf_page_count(pdf_path: Path) -> int:
    r = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if "Pages:" in line:
            return int(line.split(":")[1].strip())
    return 0


def pdf_pages_to_images(pdf_path: Path, pages: list[int], tmp_dir: Path, dpi: int = 100) -> list[Path]:
    images = []
    for page in pages:
        prefix = tmp_dir / f"p{page:03d}"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-jpeg", str(pdf_path), str(prefix)],
            capture_output=True,
        )
        for f in sorted(tmp_dir.glob(f"p{page:03d}-*.jpg")):
            images.append(f)
            break
    return images


def call_claude(client: anthropic.Anthropic, images: list[Path], prompt: str, model: str) -> dict:
    content = []
    for img_path in images:
        img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
        })
    content.append({"type": "text", "text": prompt})

    for attempt in range(5):
        try:
            response = client.messages.create(
                model=model, max_tokens=256,
                messages=[{"role": "user", "content": content}],
            )
            break
        except anthropic.RateLimitError:
            wait = 30 * (attempt + 1)
            print(f"  Rate limited, waiting {wait}s...", flush=True)
            time.sleep(wait)
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e):
                raise
            print(f"  Bad request: {e}", file=sys.stderr, flush=True)
            return {}
    else:
        return {}

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
        else:
            return {}

    return {**result, "_in": response.usage.input_tokens, "_out": response.usage.output_tokens}


def select_part_b_pages(pdf_name: str, tags: dict) -> list[int]:
    """Get Part B pages from tags, fallback to last 3."""
    entry = tags.get(pdf_name, {})
    part_b = entry.get("part_b_pages", [])
    if part_b:
        return sorted(part_b)
    total = entry.get("total_pages", 0)
    if total:
        return list(range(max(1, total - 2), total + 1))
    return []


# ---------------------------------------------------------------------------
# Strategy 1: Sarvam Vision OCR for financial data
# ---------------------------------------------------------------------------

FINANCIAL_MARKDOWN_PROMPT = """You are reading OCR'd text from the Part B summary table of an Indian election affidavit (ECI Form 26).

The table has these rows with amounts for Self / Spouse / HUF / Dependents:
- Row 8A: Total movable assets (அசையும் சொத்து மொத்த மதிப்பு / Moveable Assets Total)
- Row 8B section III: Immovable assets at current market price (நடப்பு சந்தை விலை / Current Market Price)
  - Sub-row (a): Self-acquired (சுயமாக வாங்கிய)
  - Sub-row (b): Inherited (பூர்வீக சொத்து)
- Row 9(i): Government dues (அரசுக்கு செலுத்த வேண்டிய)
- Row 9(ii): Bank/institution loans (வங்கி நிதி நிறுவனங்கள்)

TASK: Find ALL amounts in these rows across ALL persons (self, spouse, HUF, dependents), then compute:
- total_assets = sum of ALL amounts in Row 8A + ALL amounts in Row 8B(III) across all persons
  (equivalently: 8A + 8B(a) + 8B(b) across all persons)
- total_liabilities = sum of ALL amounts in Row 9(i) + Row 9(ii) across all persons

INDIAN NUMBER FORMAT — CRITICAL:
These numbers use Indian lakh/crore comma grouping. To convert to integer:
STEP 1: Remove "ரூ.", "Rs.", "₹", "/-" and any spaces
STEP 2: Remove ALL commas
STEP 3: The remaining digits are the integer — DO NOT add or remove any digits

WORKED EXAMPLES:
  "ரூ.404,58,57,196/-" → remove prefix/suffix → "404,58,57,196" → remove commas → 4045857196
  "ரூ.15,51,79,421/-" → 155179421
  "ரூ.220,15,62,010/-" → 2201562010
  "ரூ.21,83,69,000/-" → 218369000
  "ரூ.25,00,000/-" → 2500000
  "1,00,000" → 100000
  "ரூ.6,13,520" → 613520

COMMON MISTAKE: Do NOT treat Indian commas as Western thousands separators.
"404,58,57,196" has 10 digits after removing commas = 4045857196 (NOT 404585719600).

"இல்லை" / "Nil" / "பொருந்தாது" / "ஏதுமில்லை" = 0.

Return ONLY valid JSON:
{
  "movable_self": integer or 0,
  "movable_spouse": integer or 0,
  "movable_others": integer or 0,
  "immovable_self": integer or 0,
  "immovable_spouse": integer or 0,
  "immovable_others": integer or 0,
  "total_assets_rs": integer (sum of all above),
  "liabilities_govt": integer or 0,
  "liabilities_bank": integer or 0,
  "liabilities_rs": integer (sum of both),
  "criminal_cases": integer or 0,
  "education": "string from Row 11 or empty"
}

No explanation. ONLY JSON."""


def extract_financial_sarvam(pdf_path: Path, pages: list[int], sarvam_key: str,
                             claude_client: anthropic.Anthropic | None = None,
                             model: str = "claude-sonnet-4-20250514") -> dict:
    """OCR Part B pages with Sarvam Vision, then parse with Sonnet for accuracy.

    Pipeline: PDF pages → Sarvam OCR → markdown text → Sonnet reads text → JSON
    This is robust to OCR cell misalignment because Sonnet reads semantically.
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        images = pdf_pages_to_images(pdf_path, pages, tmp_dir, dpi=150)
        if not images:
            return {}

        # Create ZIP of images for Sarvam
        zip_path = tmp_dir / "pages.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            for img in images:
                zf.write(img, img.name)

        try:
            markdown = ocr_zip_batch(zip_path, sarvam_key, language="ta-IN")
        except Exception as e:
            print(f"  Sarvam error: {e}", file=sys.stderr, flush=True)
            return {}

    if not markdown or len(markdown.strip()) < 50:
        return {}

    # Strip images to reduce token count
    text = _strip_images(markdown)

    # If we have a Claude client, use Sonnet to parse (more accurate)
    if claude_client:
        result = _parse_with_sonnet(claude_client, text, model)
        if result:
            result["_markdown_len"] = len(markdown)
            return result

    # Fallback: regex parser
    abstract = _parse_part_b_abstract(text, skip_header_check=True)
    result = {}
    if abstract.get("assets_total") is not None:
        result["total_assets_rs"] = abstract["assets_total"]
    if abstract.get("liabilities_total") is not None:
        result["liabilities_rs"] = abstract["liabilities_total"]
    if abstract.get("criminal_cases_count") is not None:
        result["criminal_cases"] = abstract["criminal_cases_count"]
    if abstract.get("education"):
        result["education"] = abstract["education"]
    result["_markdown_len"] = len(markdown)
    return result


def _parse_with_sonnet(client: anthropic.Anthropic, markdown_text: str, model: str) -> dict:
    """Send Sarvam markdown text to Sonnet for financial extraction."""
    # Truncate to ~8000 chars to keep costs tiny (Part B is usually 3-5K chars)
    if len(markdown_text) > 8000:
        markdown_text = markdown_text[:8000]

    for attempt in range(3):
        try:
            response = client.messages.create(
                model=model, max_tokens=256,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": f"OCR'd affidavit text:\n\n{markdown_text}"},
                    {"type": "text", "text": FINANCIAL_MARKDOWN_PROMPT},
                ]}],
            )
            break
        except anthropic.RateLimitError:
            time.sleep(30 * (attempt + 1))
        except anthropic.BadRequestError as e:
            if "credit balance" in str(e):
                raise
            return {}
    else:
        return {}

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    text = text.strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            data = json.loads(m.group(0))
        else:
            return {}

    result = {
        "total_assets_rs": data.get("total_assets_rs", 0),
        "liabilities_rs": data.get("liabilities_rs", 0),
        "_in": response.usage.input_tokens,
        "_out": response.usage.output_tokens,
    }
    if data.get("criminal_cases") is not None:
        result["criminal_cases"] = data["criminal_cases"]
    if data.get("education"):
        result["education"] = data["education"]

    # Validation: cross-check breakdown vs total
    breakdown_sum = sum(data.get(k, 0) or 0 for k in [
        "movable_self", "movable_spouse", "movable_others",
        "immovable_self", "immovable_spouse", "immovable_others",
    ])
    if breakdown_sum > 0 and abs(breakdown_sum - result["total_assets_rs"]) > breakdown_sum * 0.05:
        # Prefer the breakdown sum over the stated total
        result["total_assets_rs"] = breakdown_sum

    return result


# ---------------------------------------------------------------------------
# Strategy 2: Sonnet for phone numbers
# ---------------------------------------------------------------------------

CONTACT_PROMPT = """Extract contact info and social media from this page of an Indian election affidavit (Section 3 — contact information).

Return ONLY a JSON object:
{
  "phone": "10-digit Indian mobile number, digits only. Empty string if not found.",
  "email": "Email address. Empty string if not found.",
  "facebook": "Facebook URL. Empty string if nil/not found.",
  "twitter": "Twitter/X URL. Empty string if nil/not found.",
  "instagram": "Instagram URL. Empty string if nil/not found.",
  "youtube": "YouTube URL. Empty string if nil/not found."
}

RULES:
- Phone must be exactly 10 digits (Indian mobile). No country code, no spaces.
- If multiple phones, pick the mobile number (starts with 6/7/8/9).
- For social media: return full URL if visible. "Nil"/"இல்லை"/"Not Applicable" = empty string.
- Return ONLY valid JSON. No explanation."""


def extract_contact_sonnet(client: anthropic.Anthropic, pdf_path: Path, model: str) -> dict:
    """Send Page 2 at 150 DPI to Sonnet for contact + social media extraction."""
    with tempfile.TemporaryDirectory() as tmp:
        images = pdf_pages_to_images(pdf_path, [2], Path(tmp), dpi=150)
        if not images:
            return {}
        return call_claude(client, images, CONTACT_PROMPT, model)


# ---------------------------------------------------------------------------
# Strategy 3: Sonnet for missing text fields
# ---------------------------------------------------------------------------

TEXT_PROMPT = """Extract data from this Indian election affidavit (ECI Form 26, Tamil Nadu 2026).

You are seeing ALL pages of the affidavit. Extract ONLY the fields I ask for.

Look for:
- Education: Section 10 or Part B row 11
- Profession: Section 9 Part A — look for (a)/(அ) self profession and (b)/(ஆ) spouse profession
- Criminal cases: Part B row 5 or Section 5

Return ONLY a JSON object with these keys:
{fields_placeholder}

RULES:
- Return ONLY valid JSON. No explanation.
- "இல்லை" / "Nil" / "Not Applicable" = empty string for text, 0 for numbers.
- Translate Tamil to English for all text fields.
"""

FIELD_DEFS = {
    "education": '"education": "Highest qualification with college/university if visible. Translate Tamil degree names to English."',
    "self_profession": '"self_profession": "Candidate profession in English. e.g. Farmer, Lawyer, Business, Teacher, Doctor, Housewife"',
    "spouse_profession": '"spouse_profession": "Spouse profession in English. Empty string if not found."',
    "criminal_cases": '"criminal_cases": "Integer count of pending criminal cases. 0 if none."',
}


def extract_text_sonnet(client: anthropic.Anthropic, pdf_path: Path, missing_fields: list[str],
                        tags: dict, model: str) -> dict:
    """Send ALL pages at 72 DPI to Sonnet for missing text fields.

    Only ~28 candidates need this, so sending all pages is affordable (~$0.05 each).
    """
    total_pages = get_pdf_page_count(pdf_path)
    if total_pages == 0:
        return {}

    pages = list(range(1, total_pages + 1))

    # Build field-specific prompt
    field_lines = ",\n  ".join(FIELD_DEFS[f] for f in missing_fields if f in FIELD_DEFS)
    prompt = TEXT_PROMPT.replace("{fields_placeholder}", "{\n  " + field_lines + "\n}")

    with tempfile.TemporaryDirectory() as tmp:
        images = pdf_pages_to_images(pdf_path, pages, Path(tmp), dpi=72)
        if not images:
            return {}
        return call_claude(client, images, prompt, model)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fill missing candidate data")
    parser.add_argument("--anthropic-key", required=True)
    parser.add_argument("--sarvam-key", default="")
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--limit", type=int, default=0, help="Max candidates to process per strategy")
    parser.add_argument("--only", choices=["financial", "phone", "text"], default="",
                        help="Run only one strategy")
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=args.anthropic_key)

    # Load data
    tags = {}
    if TAGS_FILE.exists():
        tags = json.loads(TAGS_FILE.read_text(encoding="utf-8"))
    print(f"Tags available for {len(tags)} PDFs", flush=True)

    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    pdf_map = {}  # (name, constituency) → pdf_path
    for c in candidates:
        p = c.get("pdf_path", "")
        if p:
            full = REPO_ROOT / p
            if full.exists():
                pdf_map[(c["name"].strip(), c.get("constituency", "").strip())] = full

    # Load CSV
    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    total_in = total_out = 0

    # -----------------------------------------------------------------------
    # Strategy 1: Financial data via Sarvam Vision
    # -----------------------------------------------------------------------
    if not args.only or args.only == "financial":
        if not args.sarvam_key:
            print("\nSKIPPING financial (no --sarvam-key)", flush=True)
        else:
            print(f"\n{'='*60}", flush=True)
            print("STRATEGY 1: Sarvam Vision OCR for financial data", flush=True)
            print(f"{'='*60}", flush=True)

            count = 0
            updated = 0
            for row in rows:
                assets_val = row.get("total_assets_rs", "").strip()
                if assets_val and assets_val != "0":
                    continue  # Already has assets

                key = (row.get("candidate", ""), row.get("constituency", ""))
                pdf_path = pdf_map.get(key)
                if not pdf_path:
                    continue

                pages = select_part_b_pages(pdf_path.name, tags)
                if not pages:
                    continue

                if args.limit and count >= args.limit:
                    break

                print(f"  [{count+1}] {key[0]:35s} | {key[1]:20s} | pages {pages}", end="", flush=True)

                data = extract_financial_sarvam(pdf_path, pages, args.sarvam_key,
                                               claude_client=client, model=args.model)

                if data.get("total_assets_rs"):
                    row["total_assets_rs"] = str(data["total_assets_rs"])
                    if data.get("liabilities_rs"):
                        row["liabilities_rs"] = str(data["liabilities_rs"])
                    if data.get("criminal_cases") is not None and not row.get("criminal_cases", "").strip():
                        row["criminal_cases"] = str(data["criminal_cases"])
                    if data.get("education") and not row.get("education", "").strip():
                        row["education"] = data["education"]
                    total_in += data.pop("_in", 0)
                    total_out += data.pop("_out", 0)
                    updated += 1
                    crore = data["total_assets_rs"] / 1e7
                    print(f" | Assets=₹{data['total_assets_rs']:>15,} ({crore:.1f} Cr) | md={data.get('_markdown_len', 0)}", flush=True)
                else:
                    print(f" | NO DATA", flush=True)

                count += 1

            print(f"Financial: {count} processed, {updated} updated", flush=True)

    # -----------------------------------------------------------------------
    # Strategy 2: Contact info + social media via Sonnet
    # -----------------------------------------------------------------------
    if not args.only or args.only == "phone":
        print(f"\n{'='*60}", flush=True)
        print("STRATEGY 2: Sonnet for phone + social media (150 DPI)", flush=True)
        print(f"{'='*60}", flush=True)

        count = 0
        updated = 0
        for row in rows:
            # Process if missing phone OR missing any social media
            has_phone = bool(row.get("phone", "").strip())
            has_social = any(row.get(f, "").strip() for f in ["facebook", "twitter", "instagram", "youtube"])
            if has_phone and has_social:
                continue

            key = (row.get("candidate", ""), row.get("constituency", ""))
            pdf_path = pdf_map.get(key)
            if not pdf_path:
                continue

            if args.limit and count >= args.limit:
                break

            print(f"  [{count+1}] {key[0]:35s} | {key[1]:20s}", end="", flush=True)

            data = extract_contact_sonnet(client, pdf_path, args.model)

            if data:
                in_tok = data.pop("_in", 0)
                out_tok = data.pop("_out", 0)
                total_in += in_tok
                total_out += out_tok

                filled = []
                phone = data.get("phone", "").strip()
                if phone and re.match(r"^\d{10}$", phone) and not has_phone:
                    row["phone"] = phone
                    filled.append(f"phone={phone}")

                email = data.get("email", "").strip()
                if email and "@" in email and not row.get("email", "").strip():
                    row["email"] = email
                    filled.append("email")

                for field in ["facebook", "twitter", "instagram", "youtube"]:
                    val = data.get(field, "").strip()
                    if val and not row.get(field, "").strip():
                        row[field] = val
                        filled.append(field)

                if filled:
                    updated += 1
                print(f" | {', '.join(filled) if filled else '-'} | {in_tok}tok", flush=True)
            else:
                print(f" | FAILED", flush=True)

            count += 1

        print(f"Contact: {count} processed, {updated} updated", flush=True)

    # -----------------------------------------------------------------------
    # Strategy 3: Text fields via Sonnet
    # -----------------------------------------------------------------------
    if not args.only or args.only == "text":
        print(f"\n{'='*60}", flush=True)
        print("STRATEGY 3: Sonnet for missing education/profession/cases", flush=True)
        print(f"{'='*60}", flush=True)

        count = 0
        updated = 0
        for row in rows:
            missing = []
            if not row.get("education", "").strip():
                missing.append("education")
            if not row.get("self_profession", "").strip():
                missing.append("self_profession")
            if not row.get("criminal_cases", "").strip():
                missing.append("criminal_cases")

            if not missing:
                continue

            key = (row.get("candidate", ""), row.get("constituency", ""))
            pdf_path = pdf_map.get(key)
            if not pdf_path:
                continue

            if args.limit and count >= args.limit:
                break

            print(f"  [{count+1}] {key[0]:35s} | {key[1]:20s} | need: {', '.join(missing)}", end="", flush=True)

            data = extract_text_sonnet(client, pdf_path, missing, tags, args.model)

            if data:
                in_tok = data.pop("_in", 0)
                out_tok = data.pop("_out", 0)
                total_in += in_tok
                total_out += out_tok

                filled = []
                for field in missing:
                    val = data.get(field, "")
                    if field == "criminal_cases":
                        if val is not None and val != "":
                            row[field] = str(int(val)) if isinstance(val, (int, float)) else str(val)
                            filled.append(field)
                    elif val:
                        row[field] = str(val)
                        filled.append(field)

                print(f" | filled: {', '.join(filled) if filled else 'NONE'} | {in_tok}tok", flush=True)
                if filled:
                    updated += 1
            else:
                print(f" | FAILED", flush=True)

            count += 1

        print(f"Text: {count} processed, {updated} updated", flush=True)

    # -----------------------------------------------------------------------
    # Write CSV
    # -----------------------------------------------------------------------
    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    cost = total_in / 1e6 * 3 + total_out / 1e6 * 15
    print(f"\n{'='*60}", flush=True)
    print(f"Anthropic tokens: {total_in:,} in + {total_out:,} out (${cost:.2f})", flush=True)
    print(f"Wrote {CSV_FILE}", flush=True)


if __name__ == "__main__":
    main()
