#!/usr/bin/env python3
"""Pass 1: Identify Part B summary pages in each affidavit PDF.

Sends low-res thumbnails of ALL pages in a single batch to Sonnet, asking which
page numbers contain the Part B abstract summary table. Much more accurate than
per-page classification because the model can compare pages.

Produces data/eci_2026/page_tags.json for use by extract_assets_tagged.py.

Usage:
    python scripts/tag_affidavit_pages.py --api-key $ANTHROPIC_API_KEY
    python scripts/tag_affidavit_pages.py --api-key $ANTHROPIC_API_KEY --limit 10
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
DEFAULT_OUTPUT = REPO_ROOT / "data" / "eci_2026" / "page_tags.json"

FIND_PART_B_PROMPT = """These are ALL pages (as thumbnails) from an Indian election affidavit (ECI Form 26).
Each image is labeled with its page number.

I need you to find ONLY the pages that contain the "Part B" abstract summary table.

Part B is a SPECIFIC table near the END of the form with these characteristics:
- Heading: "Part B" or "பகுதி-ஆ" or "Abstract of total" or "சுருக்கம்"
- Has numbered summary rows: 5, 6, 7, 8(A), 8(B), 9, 10, 11
- Row 8(A) = Total movable assets, Row 8(B) = Total immovable assets
- Row 9 = Total liabilities, Row 11 = Educational qualification
- Columns for: Self / Spouse / HUF / Dependent 1 / Dependent 2 / etc.
- Contains SUMMARY TOTALS (not individual item details)

Do NOT confuse Part B with:
- Section 7 (movable asset DETAILS — lists individual bank accounts, shares, vehicles)
- Section 8 (immovable property DETAILS — lists individual plots, buildings)
- Section 9 Part A (liability DETAILS — lists individual loans)
- Any other detailed tables from Part A sections

Part B is typically 2-3 pages near the end, right before the signature/verification page.

Return ONLY a JSON object, no explanation:
{"part_b_pages": [list of integer page numbers]}

IMPORTANT: Return ONLY the JSON. No text before or after."""


def get_pdf_page_count(pdf_path: Path) -> int:
    r = subprocess.run(["pdfinfo", str(pdf_path)], capture_output=True, text=True)
    for line in r.stdout.split("\n"):
        if "Pages:" in line:
            return int(line.split(":")[1].strip())
    return 0


def all_pages_to_thumbnails(pdf_path: Path, total_pages: int, tmp_dir: Path, dpi: int = 48) -> list[tuple[int, Path]]:
    """Convert all pages to low-res thumbnails. Returns [(page_num, image_path), ...]."""
    results = []
    for page in range(1, total_pages + 1):
        prefix = tmp_dir / f"p{page:03d}"
        result = subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-f", str(page), "-l", str(page), "-jpeg", str(pdf_path), str(prefix)],
            capture_output=True,
        )
        if result.returncode != 0:
            continue
        for f in sorted(tmp_dir.glob(f"p{page:03d}-*.jpg")):
            results.append((page, f))
            break
    return results


def find_part_b_pages(client: anthropic.Anthropic, thumbnails: list[tuple[int, Path]], model: str) -> dict:
    """Send all thumbnails in one batch, ask which pages are Part B."""
    content = []
    for page_num, img_path in thumbnails:
        content.append({"type": "text", "text": f"--- Page {page_num} ---"})
        img_data = base64.standard_b64encode(img_path.read_bytes()).decode("utf-8")
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": img_data},
        })
    content.append({"type": "text", "text": FIND_PART_B_PROMPT})

    for attempt in range(5):
        try:
            response = client.messages.create(
                model=model, max_tokens=64,
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
            return {"part_b_pages": [], "_in": 0, "_out": 0}
    else:
        return {"part_b_pages": [], "_in": 0, "_out": 0}

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
            result = {"part_b_pages": []}

    pages = result.get("part_b_pages", [])
    # Ensure all are ints
    pages = [int(p) for p in pages if str(p).isdigit()]

    return {
        "part_b_pages": pages,
        "_in": response.usage.input_tokens,
        "_out": response.usage.output_tokens,
    }


def main():
    parser = argparse.ArgumentParser(description="Identify Part B pages in affidavit PDFs")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--model", default="claude-sonnet-4-20250514")
    parser.add_argument("--limit", type=int, default=0, help="0=all PDFs")
    parser.add_argument("--dpi", type=int, default=72, help="Thumbnail DPI")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    client = anthropic.Anthropic(api_key=args.api_key)

    # Load candidates to get PDF list
    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    pdf_paths = []
    for c in candidates:
        p = c.get("pdf_path", "")
        if p:
            full = REPO_ROOT / p
            if full.exists():
                pdf_paths.append(full)
    pdf_paths = sorted(set(pdf_paths))
    print(f"Found {len(pdf_paths)} PDFs", flush=True)

    # Load existing tags (resumable)
    tags = {}
    if args.output.exists():
        tags = json.loads(args.output.read_text(encoding="utf-8"))

    total_in = total_out = 0
    processed = 0

    for pdf_path in pdf_paths:
        pdf_name = pdf_path.name

        # Skip already tagged
        if pdf_name in tags:
            continue

        if args.limit and processed >= args.limit:
            break

        total_pages = get_pdf_page_count(pdf_path)
        if total_pages == 0:
            continue

        print(f"[{processed+1}] {pdf_name} ({total_pages} pages)", end="", flush=True)

        with tempfile.TemporaryDirectory() as tmp:
            thumbnails = all_pages_to_thumbnails(pdf_path, total_pages, Path(tmp), args.dpi)
            if not thumbnails:
                continue

            result = find_part_b_pages(client, thumbnails, args.model)

        in_tok = result.pop("_in", 0)
        out_tok = result.pop("_out", 0)
        total_in += in_tok
        total_out += out_tok

        part_b = result["part_b_pages"]
        tags[pdf_name] = {
            "total_pages": total_pages,
            "tagged_at": datetime.now(timezone.utc).isoformat(),
            "part_b_pages": part_b,
        }

        print(f" | Part B: {part_b if part_b else 'NONE'} | {in_tok}tok", flush=True)
        processed += 1

        # Save every 5 PDFs
        if processed % 5 == 0:
            args.output.write_text(json.dumps(tags, indent=2, ensure_ascii=False), encoding="utf-8")

    # Final save
    args.output.write_text(json.dumps(tags, indent=2, ensure_ascii=False), encoding="utf-8")

    # Cost (Sonnet: $3/M input, $15/M output)
    cost = total_in / 1e6 * 3 + total_out / 1e6 * 15
    print(f"\n{'='*60}", flush=True)
    print(f"DONE: {processed} PDFs tagged", flush=True)
    print(f"Tokens: {total_in:,} in + {total_out:,} out", flush=True)
    print(f"Cost: ${cost:.2f} (~₹{cost*85:.0f})", flush=True)
    print(f"Wrote {args.output}", flush=True)


if __name__ == "__main__":
    main()
