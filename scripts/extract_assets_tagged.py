#!/usr/bin/env python3
"""Pass 2: Extract financial data from Part B pages identified by page tags.

Uses page_tags.json (from tag_affidavit_pages.py) to send only the correct pages
to Claude for accurate asset/liability extraction.

Usage:
    python scripts/extract_assets_tagged.py --api-key $ANTHROPIC_API_KEY
    python scripts/extract_assets_tagged.py --api-key $ANTHROPIC_API_KEY --limit 10
    python scripts/extract_assets_tagged.py --api-key $ANTHROPIC_API_KEY --update-csv
"""
from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import anthropic

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_JSON = REPO_ROOT / "data" / "eci_2026" / "candidates.json"
TAGS_FILE = REPO_ROOT / "data" / "eci_2026" / "page_tags.json"
ASSETS_OUTPUT = REPO_ROOT / "data" / "eci_2026" / "assets_extracted.json"
CSV_FILE = REPO_ROOT / "data" / "fct_candidates_26.csv"

FINANCIAL_PROMPT = """This is the Part B summary table from an Indian election affidavit (ECI Form 26, Tamil Nadu 2026).

Extract these values from the table rows:

Return ONLY a JSON object:
{
  "criminal_cases": Row 5 — total pending criminal cases (integer, 0 if nil/not applicable),
  "total_movable_assets_rs": Row 8A — total movable assets across ALL columns (integer),
  "total_immovable_assets_rs": Row 8B section III — approximate current market price across ALL columns, both self-acquired AND inherited (integer),
  "total_assets_rs": Row 8A + Row 8B section III summed across all columns (integer),
  "liabilities_rs": Row 9 — total of (i) government dues + (ii) loans from banks/institutions across ALL columns (integer),
  "education": Row 11 — educational qualification (string, translate Tamil to English)
}

CRITICAL — INDIAN NUMBER FORMAT:
Indian numbers use lakh/crore comma grouping, NOT Western thousands grouping.
  ₹1,00,000 = 100000 (one lakh)
  ₹1,00,00,000 = 10000000 (one crore)
  ₹4,04,58,57,196 = 4045857196 (four hundred four crore)
  ₹92,76,03,409 = 927603409 (ninety-two crore)
  ₹2,50,000 = 250000 (two and a half lakh)
To convert: REMOVE ALL COMMAS from the printed number. The remaining digits are the integer.
Do NOT reinterpret Indian commas as Western thousands separators.

RULES:
- Return ONLY valid JSON. No explanation, no markdown.
- "இல்லை" / "Nil" / "Not Applicable" / "பொருந்தாது" = 0 for numbers, empty string for text.
- Sum across ALL columns: self + spouse + HUF + every dependent listed.
- For Row 8B, use section III (approximate current market price), NOT section I or II."""


def pdf_pages_to_images(pdf_path: Path, pages: list[int], tmp_dir: Path, dpi: int = 100) -> list[Path]:
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

    import re
    try:
        result = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            result = json.loads(m.group(0))
        else:
            return {}

    return {**result, "_in": response.usage.input_tokens, "_out": response.usage.output_tokens}


def select_pages(pdf_tags: dict) -> list[int]:
    """Select Part B summary pages from tags. Fallback to last 3 pages."""
    part_b = pdf_tags.get("part_b_pages", [])
    if part_b:
        return sorted(part_b)
    # Fallback
    total = pdf_tags["total_pages"]
    return list(range(max(1, total - 2), total + 1))


def main():
    parser = argparse.ArgumentParser(description="Extract financial data from tagged Part B pages")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--tags-file", type=Path, default=TAGS_FILE)
    parser.add_argument("--limit", type=int, default=0, help="0=all tagged PDFs")
    parser.add_argument("--dpi", type=int, default=100)
    parser.add_argument("--output", type=Path, default=ASSETS_OUTPUT)
    parser.add_argument("--update-csv", action="store_true", help="Merge results into fct_candidates_26.csv")
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=args.api_key)

    # Load tags
    if not args.tags_file.exists():
        print(f"ERROR: Tags file not found: {args.tags_file}", file=sys.stderr)
        print("Run tag_affidavit_pages.py first.", file=sys.stderr)
        sys.exit(1)
    tags = json.loads(args.tags_file.read_text(encoding="utf-8"))
    print(f"Loaded tags for {len(tags)} PDFs", flush=True)

    # Load candidates for mapping PDF → candidate
    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    pdf_to_candidate = {}
    for c in candidates:
        p = c.get("pdf_path", "")
        if p:
            pdf_to_candidate[Path(p).name] = (c["name"].strip(), c.get("constituency", "").strip())

    # Load existing results (resumable)
    results = {}
    if args.output.exists():
        results = json.loads(args.output.read_text(encoding="utf-8"))

    total_in = total_out = processed = errors = 0

    for pdf_name, pdf_tags in tags.items():
        # Skip already extracted
        if pdf_name in results:
            continue

        if args.limit and processed >= args.limit:
            break

        pdf_path = REPO_ROOT / "data" / "eci_2026" / "pdfs" / pdf_name
        if not pdf_path.exists():
            continue

        pages = select_pages(pdf_tags)
        cand_name, constituency = pdf_to_candidate.get(pdf_name, (pdf_name, ""))

        print(f"[{processed+1}] {cand_name:35s} | {constituency:20s} | pages {pages}", end="", flush=True)

        try:
            with tempfile.TemporaryDirectory() as tmp:
                images = pdf_pages_to_images(pdf_path, pages, Path(tmp), args.dpi)
                if not images:
                    print(f" | NO IMAGES", flush=True)
                    continue

                data = call_claude(client, images, FINANCIAL_PROMPT, args.model)

            if data:
                in_tok = data.pop("_in", 0)
                out_tok = data.pop("_out", 0)
                total_in += in_tok
                total_out += out_tok

                assets = data.get("total_assets_rs", 0) or 0
                liab = data.get("liabilities_rs", 0) or 0
                crore = assets / 1e7

                results[pdf_name] = {
                    "pages_sent": pages,
                    "extracted_at": datetime.now(timezone.utc).isoformat(),
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    **data,
                }

                print(f" | Assets={assets:>15,} ({crore:>7.1f} Cr) | Liab={liab:>12,} | {in_tok}tok", flush=True)
            else:
                print(f" | FAILED", flush=True)
                errors += 1

            processed += 1

        except Exception as e:
            if "credit balance" in str(e):
                print(f"\n\nCREDIT EXHAUSTED after {processed}. Writing partial results.", flush=True)
                break
            print(f" | ERROR: {e}", file=sys.stderr, flush=True)
            errors += 1
            processed += 1

        # Save every 10
        if processed % 10 == 0:
            args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Final save
    args.output.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    # Cost (Sonnet: $3/M input, $15/M output)
    cost = total_in / 1e6 * 3 + total_out / 1e6 * 15
    print(f"\n{'='*60}", flush=True)
    print(f"DONE: {processed} extracted, {errors} errors", flush=True)
    print(f"Tokens: {total_in:,} in + {total_out:,} out", flush=True)
    print(f"Cost: ${cost:.2f} (~₹{cost*85:.0f})", flush=True)
    print(f"Wrote {args.output}", flush=True)

    # Merge into CSV if requested
    if args.update_csv:
        merge_into_csv(results, pdf_to_candidate)


def merge_into_csv(results: dict, pdf_to_candidate: dict):
    """Merge extracted financial data into fct_candidates_26.csv."""
    if not CSV_FILE.exists():
        print(f"CSV not found: {CSV_FILE}", file=sys.stderr)
        return

    # Read existing CSV
    with open(CSV_FILE, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    # Build lookup: (candidate, constituency) → result
    lookup = {}
    for pdf_name, data in results.items():
        key = pdf_to_candidate.get(pdf_name)
        if key:
            lookup[key] = data

    updated = 0
    for row in rows:
        key = (row.get("candidate", ""), row.get("constituency", ""))
        if key in lookup:
            data = lookup[key]
            assets = data.get("total_assets_rs")
            liab = data.get("liabilities_rs")
            cases = data.get("criminal_cases")

            if assets is not None and assets != 0:
                row["total_assets_rs"] = str(int(assets))
            if liab is not None and liab != 0:
                row["liabilities_rs"] = str(int(liab))
            if cases is not None:
                row["criminal_cases"] = str(int(cases))
            updated += 1

    with open(CSV_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} rows in {CSV_FILE}", flush=True)


if __name__ == "__main__":
    main()
