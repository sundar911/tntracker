#!/usr/bin/env python3
"""Extract text from a scanned affidavit PDF using the Sarvam Vision OCR API.

Usage:
    python scripts/extract_affidavit_ocr.py <pdf_path> \\
        --api-key <SARVAM_API_KEY> \\
        --out <output.json>

The script:
  1. Converts each PDF page to a JPEG (via pdftoppm from poppler-utils).
  2. Packs pages into batches of ≤10 and creates a ZIP per batch.
  3. Submits each ZIP to the Sarvam Document Intelligence API.
  4. Concatenates the resulting markdown text.
  5. Writes a JSON file: {"full_text": "...", "batch_texts": [...]}

Requirements:
  - poppler-utils (pdftoppm): brew install poppler
  - requests: already in requirements.txt
  - SARVAM_API_KEY env var or --api-key flag
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

# Allow running from repo root without installing as a package
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.ingestion.sarvam_ocr import ocr_zip_batch

BATCH_SIZE = 10  # Sarvam limit: max 10 images per ZIP


def pdf_to_jpegs(pdf_path: Path, out_dir: Path, dpi: int = 150) -> list[Path]:
    """Convert every page of a PDF to a JPEG file using pdftoppm."""
    prefix = out_dir / "page"
    subprocess.run(
        ["pdftoppm", "-r", str(dpi), "-jpeg", str(pdf_path), str(prefix)],
        check=True,
        capture_output=True,
    )
    pages = sorted(out_dir.glob("page-*.jpg"))
    if not pages:
        raise RuntimeError(f"pdftoppm produced no output in {out_dir}")
    return pages


def build_zip(pages: list[Path], zip_path: Path) -> None:
    """Pack a list of JPEG files into a flat ZIP archive."""
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for page in pages:
            zf.write(page, arcname=page.name)


def main() -> None:
    parser = argparse.ArgumentParser(description="OCR a scanned affidavit PDF via Sarvam AI")
    parser.add_argument("pdf_path", help="Path to the scanned affidavit PDF")
    parser.add_argument("--api-key", default=os.environ.get("SARVAM_API_KEY"), help="Sarvam API key")
    parser.add_argument("--out", default="affidavit_ocr.json", help="Output JSON path")
    parser.add_argument("--dpi", type=int, default=150, help="Image resolution for PDF conversion")
    parser.add_argument("--language", default="ta-IN", help="BCP-47 language code (default: ta-IN)")
    args = parser.parse_args()

    if not args.api_key:
        sys.exit("Error: SARVAM_API_KEY not set. Pass --api-key or set the env var.")

    pdf_path = Path(args.pdf_path)
    if not pdf_path.exists():
        sys.exit(f"Error: PDF not found: {pdf_path}")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        print(f"Converting {pdf_path.name} to JPEGs at {args.dpi} dpi…")
        pages = pdf_to_jpegs(pdf_path, tmp_dir, dpi=args.dpi)
        print(f"  → {len(pages)} pages")

        batches = [pages[i : i + BATCH_SIZE] for i in range(0, len(pages), BATCH_SIZE)]
        batch_texts: list[str] = []

        for idx, batch in enumerate(batches, start=1):
            zip_path = tmp_dir / f"batch_{idx:02d}.zip"
            build_zip(batch, zip_path)
            page_range = f"{batch[0].stem}–{batch[-1].stem}"
            print(f"Batch {idx}/{len(batches)} ({page_range}): submitting to Sarvam OCR…")
            text = ocr_zip_batch(zip_path, args.api_key, language=args.language)
            batch_texts.append(text)
            print(f"  → {len(text)} characters extracted")

    full_text = "\n\n".join(batch_texts)
    result = {"full_text": full_text, "batch_texts": batch_texts}
    out_path = Path(args.out)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nDone. OCR output written to {out_path} ({len(full_text)} chars total)")


if __name__ == "__main__":
    main()
