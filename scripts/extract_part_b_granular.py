#!/usr/bin/env python3
"""Comprehensive Part B extraction from affidavit PDFs using Sonnet.

Three calls per PDF (each fresh context, no history):
  1. TAG:     All pages at 72 DPI  → contact_page + part_b_pages
  2. CONTACT: contact_page at 150 DPI → phone, email, social media
  3. PART B:  part_b_pages at 150 DPI → 62 financial/education fields

Usage:
    python scripts/extract_part_b_granular.py --api-key $KEY --limit 20
    python scripts/extract_part_b_granular.py --api-key $KEY  # all PDFs
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_JSON = REPO_ROOT / "data" / "eci_2026" / "candidates.json"
OUTPUT_JSON = REPO_ROOT / "data" / "eci_2026" / "part_b_extracted.json"

PERSONS = ["self", "spouse", "huf", "dep1", "dep2", "dep3"]
ASSET_TYPES = ["movable", "imm_purchase", "imm_dev", "imm_market_acq", "imm_market_inh"]
LIAB_TYPES = ["govt", "bank"]

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

TAG_PROMPT = """These are ALL pages (as thumbnails) from an Indian election affidavit (ECI Form 26).
Each image is labeled with its page number.

Find TWO things:

1. "contact_page": The page containing Section 3 — candidate's phone number, email, and social media accounts.
   Look for: "My contact telephone number(s)" / "என் தொடர்பு தொலைபேசி எண்"

2. "part_b_pages": The pages containing the Part B summary table.
   Look for: "PART-B" / "பகுதி - B" heading, with numbered rows 5 through 11,
   a wide table with columns Self/Spouse/HUF/Dependents containing rupee amounts.

Return ONLY JSON: {"contact_page": integer, "part_b_pages": [integers]}
IMPORTANT: Return ONLY the JSON. No text before or after."""

CONTACT_PROMPT = """Extract contact information from this page of an Indian election affidavit (Part A, Section 3).

Return ONLY a JSON object:
{
  "phone": "10-digit Indian mobile number, digits only. Empty string if not found.",
  "email": "Email address. Empty string if not found.",
  "facebook": "Facebook URL or handle. Empty string if nil.",
  "twitter": "Twitter/X URL or handle. Empty string if nil.",
  "instagram": "Instagram URL or handle. Empty string if nil.",
  "youtube": "YouTube URL or handle. Empty string if nil."
}

RULES:
- Phone: digits only, 10 digits. If multiple, pick mobile (starts with 6/7/8/9).
- Social media: return whatever is written (URL or handle). "Nil"/"இல்லை" = empty string.
- Return ONLY valid JSON. No explanation."""

PART_B_PROMPT = """You are reading the Part B summary table from an Indian election affidavit (ECI Form 26).
The table has columns: Self (Candidate) | Spouse | HUF | Dependent-1 | Dependent-2 | Dependent-3

Extract ALL values from rows 5-11. Return ONLY a JSON object with these keys:

Row 5: "criminal_cases": integer (total pending cases, 0 if nil)
Row 6: "convictions": integer (0 if nil)

Row 7 — Total Income Shown (last FY, i.e. 2024-25):
  "income_self", "income_spouse", "income_huf", "income_dep1", "income_dep2", "income_dep3": integers

Row 8 — Assets (6 columns each):
  8(A) Movable total: "assets_movable_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  8(B)(I) Immovable purchase price: "assets_imm_purchase_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  8(B)(II) Development cost: "assets_imm_dev_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  8(B)(III)(a) Market value self-acquired: "assets_imm_market_acq_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  8(B)(III)(b) Market value inherited: "assets_imm_market_inh_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3

Row 9 — Liabilities:
  (i) Government dues: "liab_govt_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  (ii) Bank/institution loans: "liab_bank_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3

Row 10 — Disputed liabilities:
  (i) Government dues: "disputed_govt_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3
  (ii) Bank/institution loans: "disputed_bank_self", ..._spouse, ..._huf, ..._dep1, ..._dep2, ..._dep3

Row 11: "education": string (highest qualification, translate Tamil to English if needed)

INDIAN NUMBER FORMAT — CRITICAL:
STEP 1: Remove "ரூ.", "Rs.", "₹", "/-" and spaces
STEP 2: Remove ALL commas
STEP 3: The remaining digits ARE the integer — DO NOT add or remove any digits

Examples: "Rs.2,42,260" → 242260 | "55,46,717" → 5546717 | "Rs.3,94,93,305" → 39493305 | "ரூ.3,30,53,486/-" → 33053486
"Nil"/"NIL"/"இல்லை"/"பொருந்தாது"/"ஏதுமில்லை" = 0. Missing column = 0.
Return ONLY valid JSON. No explanation."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def call_sonnet(client: anthropic.Anthropic, images: list[Path], prompt: str,
                model: str, max_tokens: int = 1024, page_labels: list[int] | None = None) -> tuple[dict, int, int]:
    """Fresh single-turn API call. Returns (parsed_json, input_tokens, output_tokens)."""
    content = []
    for i, img_path in enumerate(images):
        if page_labels:
            content.append({"type": "text", "text": f"--- Page {page_labels[i]} ---"})
        img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
        })
    content.append({"type": "text", "text": prompt})

    for attempt in range(5):
        try:
            response = client.messages.create(
                model=model, max_tokens=max_tokens,
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
            return {}, 0, 0
    else:
        return {}, 0, 0

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
            return {}, response.usage.input_tokens, response.usage.output_tokens

    return result, response.usage.input_tokens, response.usage.output_tokens


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Comprehensive Part B extraction using Sonnet")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--limit", type=int, default=0, help="0=all PDFs")
    parser.add_argument("--output", type=Path, default=OUTPUT_JSON)
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=args.api_key)

    # Load candidates
    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    pdf_list = []
    for c in candidates:
        p = c.get("pdf_path", "")
        if p:
            full = REPO_ROOT / p
            if full.exists():
                pdf_list.append((c["name"].strip(), c.get("constituency", "").strip(), full))
    pdf_list = sorted(set(pdf_list))
    print(f"Found {len(pdf_list)} PDFs", flush=True)

    # Load existing results (resumable)
    results = {}
    if args.output.exists():
        results = json.loads(args.output.read_text(encoding="utf-8"))

    total_in = total_out = processed = 0

    for name, constituency, pdf_path in pdf_list:
        pdf_name = pdf_path.name

        if pdf_name in results:
            continue
        if args.limit and processed >= args.limit:
            break

        total_pages = get_pdf_page_count(pdf_path)
        if total_pages == 0:
            continue

        print(f"\n[{processed+1}] {name:35s} | {constituency:20s} | {total_pages} pages", flush=True)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                tmp_dir = Path(tmp)

                # --- CALL 1: Tag pages ---
                all_pages = list(range(1, total_pages + 1))
                thumbnails = pdf_pages_to_images(pdf_path, all_pages, tmp_dir, dpi=72)
                if not thumbnails:
                    print(f"  SKIP: no images from PDF (corrupt?)", flush=True)
                    results[pdf_name] = {"_error": "corrupt_pdf"}
                    processed += 1
                    continue

                tags, t_in, t_out = call_sonnet(
                    client, thumbnails, TAG_PROMPT, args.model,
                    max_tokens=64, page_labels=all_pages,
                )
                total_in += t_in
                total_out += t_out

                contact_page = tags.get("contact_page")
                part_b_pages = tags.get("part_b_pages", [])
                part_b_pages = [int(p) for p in part_b_pages if str(p).isdigit()]
                print(f"  TAG: contact={contact_page}, part_b={part_b_pages} ({t_in}tok)", flush=True)

                # --- CALL 2: Extract contact ---
                contact_data = {}
                if contact_page and isinstance(contact_page, int):
                    contact_imgs = pdf_pages_to_images(pdf_path, [contact_page], tmp_dir, dpi=150)
                    if contact_imgs:
                        contact_data, c_in, c_out = call_sonnet(
                            client, contact_imgs, CONTACT_PROMPT, args.model, max_tokens=128,
                        )
                        total_in += c_in
                        total_out += c_out
                        filled = [k for k, v in contact_data.items() if v]
                        print(f"  CONTACT: {', '.join(filled) if filled else 'none'} ({c_in}tok)", flush=True)

                # --- CALL 3: Extract Part B ---
                part_b_data = {}
                if part_b_pages:
                    part_b_imgs = pdf_pages_to_images(pdf_path, part_b_pages, tmp_dir, dpi=150)
                    if part_b_imgs:
                        part_b_data, p_in, p_out = call_sonnet(
                            client, part_b_imgs, PART_B_PROMPT, args.model, max_tokens=1024,
                        )
                        total_in += p_in
                        total_out += p_out
                        assets_total = sum(
                            part_b_data.get(f"assets_movable_{p}", 0) or 0 for p in PERSONS
                        ) + sum(
                            part_b_data.get(f"assets_imm_market_acq_{p}", 0) or 0 for p in PERSONS
                        ) + sum(
                            part_b_data.get(f"assets_imm_market_inh_{p}", 0) or 0 for p in PERSONS
                        )
                        crore = assets_total / 1e7
                        print(f"  PART B: assets={assets_total:,} ({crore:.1f} Cr) | cases={part_b_data.get('criminal_cases', '-')} ({p_in}tok)", flush=True)
                else:
                    print(f"  PART B: NO PAGES FOUND", flush=True)

                # Merge results
                entry = {
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "total_pages": total_pages,
                    "contact_page": contact_page,
                    "part_b_pages": part_b_pages,
                    **{f"contact_{k}": v for k, v in contact_data.items()},
                    **part_b_data,
                }
                results[pdf_name] = entry

        except anthropic.BadRequestError as e:
            if "credit balance" in str(e):
                print(f"\n\nCREDIT EXHAUSTED after {processed} PDFs. Saving partial results.", flush=True)
                break
            results[pdf_name] = {"_error": str(e)}

        processed += 1

        # Save every 5
        if processed % 5 == 0:
            args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Final save
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    cost = total_in / 1e6 * 3 + total_out / 1e6 * 15
    print(f"\n{'='*60}", flush=True)
    print(f"DONE: {processed} PDFs processed", flush=True)
    print(f"Tokens: {total_in:,} in + {total_out:,} out", flush=True)
    print(f"Cost: ${cost:.2f}", flush=True)
    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
