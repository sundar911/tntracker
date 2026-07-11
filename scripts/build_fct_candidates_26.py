#!/usr/bin/env python3
"""Build/update data/fct_candidates_26.csv from candidates.json + affidavit markdowns.

This is the single source of truth for 2026 candidate data. It:
  1. Loads identity fields from candidates.json (100% reliable from ECI)
  2. Parses affidavit markdowns for extra fields (education, profession, assets, etc.)
  3. Optionally uses Sarvam LLM to fill gaps the regex parser missed
  4. Runs data quality checks
  5. Writes fct_candidates_26.csv

Usage:
    python scripts/build_fct_candidates_26.py
    python scripts/build_fct_candidates_26.py --sarvam-api-key $SARVAM_API_KEY
    python scripts/build_fct_candidates_26.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path

# Allow running from repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from core.ingestion.parse_form26_markdown import parse_form26_markdown


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
CANDIDATES_JSON = REPO_ROOT / "data" / "eci_2026" / "candidates.json"
MD_DIR = REPO_ROOT / "data" / "eci_2026" / "markdown"
OUTPUT_CSV = REPO_ROOT / "data" / "fct_candidates_26.csv"

CSV_COLUMNS = [
    "candidate", "party", "constituency", "age", "gender", "address", "fathers_name",
    "criminal_cases", "education", "self_profession", "spouse_profession",
    "total_assets_rs", "liabilities_rs",
    "phone", "email", "facebook", "twitter", "instagram", "youtube",
    "photo_url", "affidavit_url", "current_status", "pdf_path",
]

# Education values that are section headers, not actual qualifications
BAD_EDUCATION = [
    "is as under", "வருமாறு", "பின் வருமாறு", "கீழ் வருமாறு",
    "கள் வருமாறு", "முழு அஞ்சல்",
    "வேட்பாளர் பெயர்", "qualification", "கல்வித் தகுதி",
    "Full postal address", "Name of",
    "கல்வி விவரங்கள்", "அளிக்கவும்",  # instruction text, not education
]


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

def _clean_education(edu) -> str:
    """Strip section headers and garbage from education text."""
    if not edu:
        return ""
    edu = str(edu)
    for bad in BAD_EDUCATION:
        if bad.lower() in edu.lower():
            # Try stripping the bad prefix
            cleaned = re.sub(
                rf"^.*?(?:{re.escape(bad)})[:\-–\s]*",
                "", edu, flags=re.IGNORECASE,
            ).strip()
            if cleaned and len(cleaned) > 3:
                edu = cleaned
            else:
                return ""
    # Strip markdown artifacts
    edu = re.sub(r"^#+\s*", "", edu)
    edu = re.sub(r"^>\s*", "", edu)
    edu = re.sub(r"^\*+\s*", "", edu)  # leading asterisks
    edu = re.sub(r"\s*\*+$", "", edu)  # trailing asterisks
    edu = edu.strip().strip('"').strip("'").strip()
    if len(edu) < 4:
        return ""
    return edu


def _clean_phone(phone) -> str:
    """Ensure phone is 10-12 digits only."""
    if not phone:
        return ""
    digits = re.sub(r"[^\d]", "", str(phone))
    if 10 <= len(digits) <= 12:
        return digits
    return ""


def _clean_amount(val) -> str:
    """Ensure amount is a positive integer or empty."""
    if val is None or val == "":
        return ""
    try:
        n = int(val)
        return str(n) if n >= 0 else ""
    except (ValueError, TypeError):
        return ""


def _validate_row(row: dict, entry: dict) -> list[str]:
    """Run quality checks on a row. Returns list of warnings."""
    warnings = []
    name = row["candidate"]

    # Education should not contain section headers
    if row["education"]:
        for bad in BAD_EDUCATION:
            if bad.lower() in row["education"].lower():
                warnings.append(f"{name}: education contains '{bad}' — clearing")
                row["education"] = ""
                break

    # Assets should be reasonable (not negative, not absurdly high)
    if row["total_assets_rs"]:
        try:
            v = int(row["total_assets_rs"])
            if v < 0:
                warnings.append(f"{name}: negative assets {v} — clearing")
                row["total_assets_rs"] = ""
            elif v > 50_000_000_000:  # > 5000 crore is suspicious
                warnings.append(f"{name}: suspiciously high assets {v}")
        except ValueError:
            warnings.append(f"{name}: non-numeric assets '{row['total_assets_rs']}' — clearing")
            row["total_assets_rs"] = ""

    # Phone should be 10-12 digits
    if row["phone"] and not re.match(r"^\d{10,12}$", row["phone"]):
        warnings.append(f"{name}: invalid phone '{row['phone']}' — clearing")
        row["phone"] = ""

    # Email basic check
    if row["email"] and "@" not in row["email"]:
        warnings.append(f"{name}: invalid email '{row['email']}' — clearing")
        row["email"] = ""

    # Criminal cases should be a small non-negative integer (no candidate has 200+ cases)
    if row["criminal_cases"]:
        try:
            v = int(row["criminal_cases"])
            if v < 0:
                row["criminal_cases"] = "0"
            elif v > 200:
                warnings.append(f"{name}: criminal_cases={v} is impossibly high — clearing")
                row["criminal_cases"] = ""
        except ValueError:
            row["criminal_cases"] = ""

    return warnings


# ---------------------------------------------------------------------------
# LLM fallback
# ---------------------------------------------------------------------------

def _fill_gaps_with_llm(md_path: Path, parsed: dict, candidate_name: str, api_key: str) -> dict:
    """Use Sarvam LLM to fill missing fields."""
    from core.ingestion.sarvam_llm_extract import extract_missing_fields

    missing = []
    if not parsed.get("education"):
        missing.append("education")
    if not parsed.get("profession"):
        missing.append("profession")
    if not parsed.get("assets_total"):
        missing.append("assets_total")
    if not parsed.get("phone"):
        missing.append("phone")
    if not parsed.get("email"):
        missing.append("email")

    if not missing:
        return parsed

    try:
        llm_data = extract_missing_fields(md_path, missing, candidate_name, api_key)
        for field, val in llm_data.items():
            if val is not None and val != "" and not parsed.get(field):
                parsed[field] = val
    except Exception as e:
        print(f"  LLM error for {candidate_name}: {e}", file=sys.stderr)

    return parsed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def build_csv(api_key: str = "", dry_run: bool = False) -> None:
    candidates = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
    print(f"Loaded {len(candidates)} candidates from {CANDIDATES_JSON.name}")

    md_files = {f.stem: f for f in MD_DIR.glob("*.md")}
    print(f"Found {len(md_files)} markdown files in {MD_DIR}")

    rows = []
    all_warnings = []
    stats = {"total": 0, "with_md": 0, "edu": 0, "prof": 0, "assets": 0, "phone": 0, "llm_fills": 0}

    for entry in candidates:
        stats["total"] += 1
        name = entry["name"].strip()

        # --- Identity from candidates.json ---
        row = {
            "candidate": name,
            "party": entry.get("party", "").strip(),
            "constituency": entry.get("constituency", "").strip(),
            "age": entry.get("age", "").strip(),
            "gender": (entry.get("gender") or "").strip(),
            "address": (entry.get("address") or "").strip(),
            "fathers_name": (entry.get("fathers_name") or "").strip(),
            "photo_url": (entry.get("photo_url") or "").strip(),
            "affidavit_url": (entry.get("affidavit_url") or "").strip(),
            "current_status": (entry.get("current_status") or "").strip(),
            "pdf_path": (entry.get("pdf_path") or "").strip(),
            # Defaults for affidavit fields
            "education": "",
            "self_profession": "",
            "spouse_profession": "",
            "total_assets_rs": "",
            "liabilities_rs": "",
            "criminal_cases": "",
            "phone": "",
            "email": "",
            "facebook": "",
            "twitter": "",
            "instagram": "",
            "youtube": "",
        }

        # --- Affidavit fields from markdown ---
        pdf_path = entry.get("pdf_path", "")
        md_stem = Path(pdf_path).stem if pdf_path else ""
        md_path = md_files.get(md_stem)

        if md_path:
            stats["with_md"] += 1
            try:
                p = parse_form26_markdown(md_path)
                parsed = {
                    "education": _clean_education(p.education),
                    "profession": p.profession,
                    "spouse_profession": p.spouse_profession,
                    "assets_total": p.assets_total,
                    "liabilities_total": p.liabilities_total,
                    "criminal_cases_count": p.criminal_cases_count,
                    "phone": _clean_phone(p.phone),
                    "email": p.email,
                    "social_media": p.social_media,
                }

                # LLM fallback for missing critical fields
                if api_key:
                    before = sum(1 for k in ["education", "profession", "assets_total", "phone", "email"] if not parsed.get(k))
                    parsed = _fill_gaps_with_llm(md_path, parsed, name, api_key)
                    after = sum(1 for k in ["education", "profession", "assets_total", "phone", "email"] if not parsed.get(k))
                    if before > after:
                        stats["llm_fills"] += before - after

                row["education"] = _clean_education(parsed.get("education", ""))
                row["self_profession"] = parsed.get("profession", "")
                row["spouse_profession"] = parsed.get("spouse_profession", "")
                row["total_assets_rs"] = _clean_amount(parsed.get("assets_total"))
                row["liabilities_rs"] = _clean_amount(parsed.get("liabilities_total"))
                row["criminal_cases"] = str(parsed.get("criminal_cases_count", ""))
                row["phone"] = _clean_phone(parsed.get("phone", ""))
                row["email"] = (parsed.get("email") or "").strip()
                row["facebook"] = (parsed.get("social_media") or {}).get("facebook", "")
                row["twitter"] = (parsed.get("social_media") or {}).get("twitter", "")
                row["instagram"] = (parsed.get("social_media") or {}).get("instagram", "")
                row["youtube"] = (parsed.get("social_media") or {}).get("youtube", "")

            except Exception as e:
                print(f"  Parse error {md_stem}: {e}", file=sys.stderr)

        # Quality checks
        warnings = _validate_row(row, entry)
        all_warnings.extend(warnings)

        # Track stats
        if row["education"]:
            stats["edu"] += 1
        if row["self_profession"]:
            stats["prof"] += 1
        if row["total_assets_rs"]:
            stats["assets"] += 1
        if row["phone"]:
            stats["phone"] += 1

        rows.append(row)

    # Print stats
    n = stats["total"]
    n_md = stats["with_md"]
    print(f"\n{'='*60}")
    print(f"EXTRACTION RESULTS ({n} candidates, {n_md} with markdowns)")
    print(f"{'='*60}")
    print(f"  Education:  {stats['edu']:>4}/{n_md} ({stats['edu']*100//max(n_md,1)}% of candidates with markdowns)")
    print(f"  Profession: {stats['prof']:>4}/{n_md} ({stats['prof']*100//max(n_md,1)}%)")
    print(f"  Assets:     {stats['assets']:>4}/{n_md} ({stats['assets']*100//max(n_md,1)}%)")
    print(f"  Phone:      {stats['phone']:>4}/{n_md} ({stats['phone']*100//max(n_md,1)}%)")
    if api_key:
        print(f"  LLM fills:  {stats['llm_fills']}")

    if all_warnings:
        print(f"\nDATA QUALITY WARNINGS ({len(all_warnings)}):")
        for w in all_warnings[:20]:
            print(f"  ⚠ {w}")
        if len(all_warnings) > 20:
            print(f"  ... and {len(all_warnings) - 20} more")

    if dry_run:
        print(f"\nDry run — nothing written.")
        return

    # Write CSV
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {OUTPUT_CSV}")

    # MANDATORY post-write quality gate
    errors = run_quality_gate(OUTPUT_CSV)
    if errors:
        print(f"\n{'='*60}")
        print(f"QUALITY GATE FAILED — {len(errors)} issues found:")
        print(f"{'='*60}")
        for e in errors:
            print(f"  FAIL: {e}")
        print(f"\nThe CSV was written but contains bad data. Fix the parser and re-run.")
        sys.exit(1)
    else:
        print(f"\nQuality gate PASSED — all rows clean.")


# ---------------------------------------------------------------------------
# Mandatory quality gate — runs on every CSV write
# ---------------------------------------------------------------------------

def run_quality_gate(csv_path: Path) -> list[str]:
    """Validate every row in the CSV. Returns list of error strings (empty = pass)."""
    errors = []
    with open(csv_path, encoding="utf-8") as f:
        for i, row in enumerate(csv.DictReader(f), start=2):  # line 2 = first data row
            name = row["candidate"]

            # --- Education checks ---
            edu = row.get("education", "")
            if edu:
                # Must not contain section headers / instruction text
                for bad in BAD_EDUCATION:
                    if bad.lower() in edu.lower():
                        errors.append(f"Row {i} {name}: education contains '{bad}': [{edu[:60]}]")
                        break
                # Must not start with markdown artifacts
                if re.match(r"^[*#>]", edu):
                    errors.append(f"Row {i} {name}: education starts with markdown artifact: [{edu[:40]}]")
                # Must not be just a candidate name part
                cand_parts = [p for p in name.lower().split() if len(p) > 3]
                if len(edu) < 15 and any(p in edu.lower() for p in cand_parts):
                    errors.append(f"Row {i} {name}: education looks like candidate name: [{edu}]")

            # --- Profession checks ---
            prof = row.get("self_profession", "")
            if prof:
                if re.match(r"^[*#>]", prof):
                    errors.append(f"Row {i} {name}: profession starts with markdown artifact: [{prof[:40]}]")
                cand_parts = [p for p in name.lower().split() if len(p) > 3]
                if any(p in prof.lower() for p in cand_parts):
                    errors.append(f"Row {i} {name}: profession contains candidate name: [{prof[:40]}]")

            # --- Criminal cases checks ---
            cc = row.get("criminal_cases", "")
            if cc:
                try:
                    v = int(cc)
                    if v < 0:
                        errors.append(f"Row {i} {name}: negative criminal_cases: {v}")
                    if v > 100:
                        errors.append(f"Row {i} {name}: criminal_cases={v} impossibly high")
                except ValueError:
                    errors.append(f"Row {i} {name}: criminal_cases not a number: [{cc}]")

            # --- Assets checks ---
            assets = row.get("total_assets_rs", "")
            if assets:
                try:
                    v = int(assets)
                    if v < 0:
                        errors.append(f"Row {i} {name}: negative assets: {v}")
                    if v > 50_000_000_000:
                        errors.append(f"Row {i} {name}: assets={v} over 5000 crore — suspicious")
                except ValueError:
                    errors.append(f"Row {i} {name}: assets not a number: [{assets}]")

            # --- Phone checks ---
            phone = row.get("phone", "")
            if phone and not re.match(r"^\d{10,12}$", phone):
                errors.append(f"Row {i} {name}: invalid phone format: [{phone}]")

            # --- Email checks ---
            email = row.get("email", "")
            if email and "@" not in email:
                errors.append(f"Row {i} {name}: invalid email: [{email}]")

            # --- Required identity fields (from candidates.json — should never be empty) ---
            if not row.get("candidate"):
                errors.append(f"Row {i}: missing candidate name")
            if not row.get("constituency"):
                errors.append(f"Row {i} {name}: missing constituency")
            if not row.get("party"):
                errors.append(f"Row {i} {name}: missing party")

    return errors


def main():
    parser = argparse.ArgumentParser(description="Build/update fct_candidates_26.csv")
    parser.add_argument("--sarvam-api-key", default=os.environ.get("SARVAM_API_KEY", ""),
                        help="Sarvam API key for LLM fallback")
    parser.add_argument("--dry-run", action="store_true", help="Parse and report without writing CSV")
    args = parser.parse_args()
    build_csv(api_key=args.sarvam_api_key, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
