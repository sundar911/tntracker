"""Translate manifesto promise JSON files to Tamil using Sarvam Translate API.

Reads curated manifesto JSON files, translates English text fields to Tamil,
and updates text_ta and summary_ta fields in-place.

Usage:
    python scripts/extract_manifesto_promises.py
    python scripts/extract_manifesto_promises.py --party DMK
    python scripts/extract_manifesto_promises.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

import requests

SARVAM_TRANSLATE_URL = "https://api.sarvam.ai/translate"
MIN_REQUEST_GAP = 0.2  # seconds between API calls

PARTIES = ["dmk", "aiadmk", "ntk"]


def _load_api_key() -> str:
    key = os.environ.get("SARVAM_API_KEY", "")
    if key:
        return key
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("SARVAM_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def _translate(api_key: str, text: str) -> str:
    """Translate English to Tamil via Sarvam Translate API."""
    if not text.strip():
        return ""
    resp = requests.post(
        SARVAM_TRANSLATE_URL,
        headers={
            "api-subscription-key": api_key,
            "Content-Type": "application/json",
        },
        json={
            "input": text,
            "source_language_code": "en-IN",
            "target_language_code": "ta-IN",
        },
        timeout=30,
    )
    if not resp.ok:
        print(f"    Translate error {resp.status_code}: {resp.text[:200]}")
        return ""
    return resp.json().get("translated_text", "").strip()


def main():
    parser = argparse.ArgumentParser(description="Translate manifesto promises to Tamil")
    parser.add_argument("--party", choices=PARTIES, help="Translate only this party")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done")
    args = parser.parse_args()

    api_key = _load_api_key()
    if not api_key and not args.dry_run:
        print("ERROR: SARVAM_API_KEY not set")
        return

    data_dir = Path(__file__).resolve().parent.parent / "data"
    parties = [args.party] if args.party else PARTIES

    for party in parties:
        json_path = data_dir / f"manifesto_{party}_2026.json"
        if not json_path.exists():
            print(f"Skipping {party}: {json_path} not found")
            continue

        print(f"\n{'=' * 50}")
        print(f"Translating {party.upper()}")
        print(f"{'=' * 50}")

        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        manifesto = data[0]
        promises = manifesto.get("promises", [])
        needs_translation = []

        # Check which promises need translation
        for p in promises:
            if not p.get("text_ta", "").strip():
                needs_translation.append(p)

        need_summary = not manifesto.get("summary_ta", "").strip()

        print(f"  {len(promises)} total promises, {len(needs_translation)} need Tamil translation")
        if need_summary:
            print(f"  Summary also needs translation")

        if args.dry_run:
            for p in needs_translation[:5]:
                print(f"  Would translate: {p['text'][:80]}...")
            if len(needs_translation) > 5:
                print(f"  ... and {len(needs_translation) - 5} more")
            continue

        last_call = 0.0

        # Translate summary
        if need_summary and manifesto.get("summary"):
            elapsed = time.time() - last_call
            if elapsed < MIN_REQUEST_GAP:
                time.sleep(MIN_REQUEST_GAP - elapsed)
            tamil = _translate(api_key, manifesto["summary"])
            last_call = time.time()
            if tamil:
                manifesto["summary_ta"] = tamil
                print(f"  Summary translated")

        # Translate promises
        for i, p in enumerate(needs_translation):
            elapsed = time.time() - last_call
            if elapsed < MIN_REQUEST_GAP:
                time.sleep(MIN_REQUEST_GAP - elapsed)

            tamil = _translate(api_key, p["text"])
            last_call = time.time()
            p["text_ta"] = tamil

            if (i + 1) % 10 == 0 or i == len(needs_translation) - 1:
                print(f"  Translated {i + 1}/{len(needs_translation)}")

        # Save back
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"  Saved to {json_path}")

        # Print for review
        translated_count = sum(1 for p in promises if p.get("text_ta", "").strip())
        print(f"  {translated_count}/{len(promises)} promises now have Tamil text")


if __name__ == "__main__":
    main()
