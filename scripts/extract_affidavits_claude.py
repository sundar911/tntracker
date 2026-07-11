#!/usr/bin/env python3
"""Extract candidate data from ECI Form 26 affidavit PDFs using Claude API.

Optimized for cost: sends only the pages that contain data we need.
  - Page 2: phone, email, social media (Section 3)
  - Last 5 pages: profession (Section 9), education (Section 10),
    Part B abstract (assets total, liabilities total, criminal count, education)
  - Pages 4-5: criminal case details (ONLY for candidates with cases > 0, second pass)

Usage:
    python scripts/extract_affidavits_claude.py --api-key $ANTHROPIC_API_KEY
    python scripts/extract_affidavits_claude.py --api-key $ANTHROPIC_API_KEY --limit 50
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_JSON = REPO_ROOT / "data" / "eci_2026" / "candidates.json"
OUTPUT_CSV = REPO_ROOT / "data" / "fct_candidates_26.csv"

CSV_COLUMNS = [
    "candidate", "party", "constituency", "age", "gender", "address", "fathers_name",
    "criminal_cases", "education", "self_profession", "spouse_profession",
    "total_assets_rs", "liabilities_rs",
    "phone", "email", "facebook", "twitter", "instagram", "youtube",
    "photo_url", "affidavit_url", "current_status", "pdf_path", "legal_summary_short",
]

EXTRACTION_PROMPT = """Extract data from this Indian election affidavit (ECI Form 26, Tamil Nadu 2026).

You are seeing: Page 2 (contact info) + the last few pages (Section 9: profession, Section 10: education, Part B abstract: assets/liabilities/criminal cases summary).

Return ONLY a JSON object with these keys:
{
  "education": "Highest qualification, college, year. e.g. 'B.E, Anna University, 2010'. Translate Tamil degree names to English.",
  "self_profession": "Candidate's profession in English. Translate Tamil. e.g. 'Farmer', 'Lawyer', 'Housewife', 'Business', 'Teacher'",
  "spouse_profession": "Spouse's profession in English. Empty string if not found or nil.",
  "total_assets_rs": Integer total of ALL movable + immovable assets for self+spouse+dependents from Part B row 8. Use market value for immovable. 0 if all nil.,
  "liabilities_rs": Integer total of ALL liabilities for self+spouse+dependents from Part B row 9. 0 if all nil.,
  "criminal_cases": Integer count of pending criminal cases from Part B row 5. 0 if none/not applicable.,
  "phone": "10-digit phone number. Digits only. Empty string if not found.",
  "email": "Email address. Empty string if not found.",
  "facebook": "Facebook URL only. Empty string if nil.",
  "twitter": "Twitter/X URL only. Empty string if nil.",
  "instagram": "Instagram URL only. Empty string if nil.",
  "youtube": "YouTube URL only. Empty string if nil."
}

CRITICAL — INDIAN NUMBER FORMAT:
Indian numbers use lakh/crore comma grouping, NOT Western grouping.
  ₹1,00,000 = 1 lakh = 100000
  ₹1,00,00,000 = 1 crore = 10000000
  ₹404,58,57,196 = 4045857196 (four hundred four crore)
  ₹92,76,03,409 = 927603409 (ninety-two crore)
  ₹21,83,69,000 = 218369000 (twenty-one crore)
  ₹2,50,000 = 250000 (two and a half lakh)
To convert: simply REMOVE ALL COMMAS from the number to get the integer. Do NOT reinterpret the commas as Western thousands separators.

RULES:
- Return ONLY valid JSON. No explanation.
- For total_assets_rs: sum ALL amounts from Part B row 8A (movable total) + row 8B section III (immovable approximate current market price — both self-acquired AND inherited) across ALL columns (self + spouse + HUF + all dependents). Read each cell carefully.
- For liabilities_rs: sum ALL amounts from Part B row 9 (both government dues and bank loans) across all columns.
- "இல்லை" / "Nil" / "Not Applicable" / "பொருந்தாது" = 0 for numbers, empty string for text.
- Phone: digits only, no spaces/dashes. Must be 10 digits.
- Translate Tamil professions: விவசாயி→Farmer, இல்லத்தரசி→Housewife, வணிகம்→Business, வழக்கறிஞர்→Lawyer, ஆசிரியர்→Teacher, கூலி→Daily Wage Worker, மருத்துவர்→Doctor"""

LEGAL_PROMPT = """Extract criminal case details from this Indian election affidavit (ECI Form 26).

You are seeing the criminal cases section (Section 5 and 6). Summarize ALL pending cases in one concise English sentence.

Return ONLY a JSON object:
{
  "legal_summary_short": "One-line summary. e.g. 'Charged with assault under IPC 324 and theft under IPC 379 (2 cases pending)'. Include IPC sections if visible. Empty string if no cases."
}"""


def get_pdf_page_count(pdf_path: Path) -> int:
    r = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if "Pages:" in line:
            return int(line.split(":")[1].strip())
    return 0


def pdf_pages_to_images(pdf_path: Path, pages: list[int], tmp_dir: Path, dpi: int = 72) -> list[Path]:
    images = []
    for page in pages:
        prefix = tmp_dir / f"p{page:03d}"
        result = subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-jpeg", str(pdf_path), str(prefix)],
            capture_output=True,
        )
        if result.returncode != 0:
            continue
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
                model=model, max_tokens=512,
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

    import re
    try:
        return {**json.loads(text), "_in": response.usage.input_tokens, "_out": response.usage.output_tokens}
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return {**json.loads(m.group(0)), "_in": response.usage.input_tokens, "_out": response.usage.output_tokens}
        return {}


def extract_candidate(client: anthropic.Anthropic, pdf_path: Path, model: str) -> tuple[dict, int, int]:
    """Extract data from a single PDF. Returns (data_dict, input_tokens, output_tokens)."""
    import tempfile

    total_pages = get_pdf_page_count(pdf_path)
    if total_pages == 0:
        return {}, 0, 0

    # PASS 1: Page 2 (contact) + last 5 pages (Section 9/10 + Part B abstract)
    pages = [2]
    for p in range(max(3, total_pages - 4), total_pages + 1):
        pages.append(p)
    pages = sorted(set(pages))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        images = pdf_pages_to_images(pdf_path, pages, tmp_dir)
        if not images:
            return {}, 0, 0

        result = call_claude(client, images, EXTRACTION_PROMPT, model)
        if not result:
            return {}, 0, 0

        in_tok = result.pop("_in", 0)
        out_tok = result.pop("_out", 0)

        # PASS 2: If criminal cases > 0, get details from pages 4-5
        cases = result.get("criminal_cases", 0)
        if isinstance(cases, str):
            cases = int(cases) if cases.isdigit() else 0
        if cases > 0:
            crime_images = pdf_pages_to_images(pdf_path, [4, 5], tmp_dir)
            if crime_images:
                legal = call_claude(client, crime_images, LEGAL_PROMPT, model)
                if legal:
                    result["legal_summary_short"] = legal.get("legal_summary_short", "")
                    in_tok += legal.pop("_in", 0)
                    out_tok += legal.pop("_out", 0)

    return result, in_tok, out_tok


def main():
    parser = argparse.ArgumentParser(description="Extract affidavit data using Claude API")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--limit", type=int, default=0, help="0=all with PDFs")
    parser.add_argument("--skip-existing", action="store_true", help="Skip candidates already extracted")
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=args.api_key)
    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    print(f"Loaded {len(candidates)} candidates", flush=True)

    # Load existing CSV to preserve already-extracted data
    existing = {}
    if OUTPUT_CSV.exists() and args.skip_existing:
        with open(OUTPUT_CSV, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("candidate", ""), row.get("constituency", ""))
                if row.get("education") or row.get("total_assets_rs"):
                    existing[key] = row

    rows = []
    total_in = total_out = processed = errors = skipped = 0

    for entry in candidates:
        name = entry["name"].strip()
        constituency = entry.get("constituency", "").strip()
        pdf_path_str = entry.get("pdf_path", "")
        pdf_path = Path(pdf_path_str) if pdf_path_str else None

        row = {
            "candidate": name,
            "party": entry.get("party", "").strip(),
            "constituency": constituency,
            "age": entry.get("age", "").strip(),
            "gender": (entry.get("gender") or "").strip(),
            "address": (entry.get("address") or "").strip(),
            "fathers_name": (entry.get("fathers_name") or "").strip(),
            "photo_url": (entry.get("photo_url") or "").strip(),
            "affidavit_url": (entry.get("affidavit_url") or "").strip(),
            "current_status": (entry.get("current_status") or "").strip(),
            "pdf_path": pdf_path_str,
            "criminal_cases": "", "education": "", "self_profession": "",
            "spouse_profession": "", "total_assets_rs": "", "liabilities_rs": "",
            "phone": "", "email": "", "facebook": "", "twitter": "",
            "instagram": "", "youtube": "", "legal_summary_short": "",
        }

        key = (name, constituency)

        # Skip if already extracted
        if key in existing:
            rows.append(existing[key])
            skipped += 1
            continue

        # Skip if no PDF or hit limit
        if not pdf_path or not pdf_path.exists():
            rows.append(row)
            continue
        if args.limit and processed >= args.limit:
            rows.append(row)
            continue

        try:
            print(f"[{processed+1}] {name:35s} | {constituency:20s}", end="", flush=True)
            data, in_tok, out_tok = extract_candidate(client, pdf_path, args.model)

            if data:
                for field in ["education", "self_profession", "spouse_profession",
                              "phone", "email", "facebook", "twitter", "instagram",
                              "youtube", "legal_summary_short"]:
                    row[field] = str(data.get(field) or "")
                for field in ["total_assets_rs", "liabilities_rs", "criminal_cases"]:
                    val = data.get(field)
                    row[field] = str(int(val)) if val is not None and val != "" else ""

                total_in += in_tok
                total_out += out_tok
                print(f" | Assets={row['total_assets_rs']:>12s} Cases={row['criminal_cases']:>2s} | {in_tok}tok", flush=True)
            else:
                print(f" | FAILED", flush=True)

            processed += 1

        except Exception as e:
            if "credit balance" in str(e):
                print(f"\n\nCREDIT EXHAUSTED after {processed} candidates. Writing what we have.", flush=True)
                rows.append(row)
                break
            print(f" | ERROR: {e}", file=sys.stderr, flush=True)
            errors += 1

        rows.append(row)

    # Fill remaining candidates (not processed)
    written_keys = {(r["candidate"], r["constituency"]) for r in rows}
    for entry in candidates:
        key = (entry["name"].strip(), entry.get("constituency", "").strip())
        if key not in written_keys:
            rows.append({
                "candidate": entry["name"].strip(),
                "party": entry.get("party", "").strip(),
                "constituency": entry.get("constituency", "").strip(),
                "age": entry.get("age", "").strip(),
                "gender": (entry.get("gender") or "").strip(),
                "address": (entry.get("address") or "").strip(),
                "fathers_name": (entry.get("fathers_name") or "").strip(),
                "photo_url": (entry.get("photo_url") or "").strip(),
                "affidavit_url": (entry.get("affidavit_url") or "").strip(),
                "current_status": (entry.get("current_status") or "").strip(),
                "pdf_path": entry.get("pdf_path", ""),
                "criminal_cases": "", "education": "", "self_profession": "",
                "spouse_profession": "", "total_assets_rs": "", "liabilities_rs": "",
                "phone": "", "email": "", "facebook": "", "twitter": "",
                "instagram": "", "youtube": "", "legal_summary_short": "",
            })

    # Write CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # Cost (Sonnet: $3/M input, $15/M output)
    cost = total_in / 1e6 * 3 + total_out / 1e6 * 15

    print(f"\n{'='*60}", flush=True)
    print(f"DONE: {processed} extracted, {skipped} skipped, {errors} errors", flush=True)
    print(f"Tokens: {total_in:,} in + {total_out:,} out", flush=True)
    print(f"Cost: ${cost:.2f} (~₹{cost*85:.0f})", flush=True)
    print(f"Wrote {len(rows)} rows to {OUTPUT_CSV}", flush=True)


if __name__ == "__main__":
    main()
