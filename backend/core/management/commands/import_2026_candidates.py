"""Import 2026 candidates from candidates.json + Sarvam Vision OCR markdowns.

Primary source: candidates.json (ECI data — 100% reliable for identity fields)
Secondary source: data/eci_2026/markdown/*.md (affidavit OCR — extra fields)

Usage:
    python manage.py import_2026_candidates data/eci_2026/candidates.json \\
        --md-dir data/eci_2026/markdown/

    python manage.py import_2026_candidates data/eci_2026/candidates.json --dry-run
"""
from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand
from django.db import transaction

from core.models import Affidavit, Candidate, Constituency, Election, Party, SourceDocument


class Command(BaseCommand):
    help = "Import 2026 candidates from candidates.json, enriched with affidavit OCR markdown data."

    def add_arguments(self, parser):
        parser.add_argument("json_path", help="Path to candidates.json")
        parser.add_argument("--md-dir", default="data/eci_2026/markdown/", help="Directory with .md affidavit files")
        parser.add_argument("--sarvam-api-key", default="", help="Sarvam API key for LLM fallback on missing fields")
        parser.add_argument("--sarvam-model", default="sarvam-m", help="Sarvam LLM model name")
        parser.add_argument("--dry-run", action="store_true")

    def handle(self, *args, **options):
        json_path = Path(options["json_path"])
        md_dir = Path(options["md_dir"])

        candidates_data = json.loads(json_path.read_text(encoding="utf-8"))
        self.stdout.write(f"Loaded {len(candidates_data)} candidates from {json_path}")

        api_key = options.get("sarvam_api_key", "")
        model = options.get("sarvam_model", "sarvam-m")

        if options["dry_run"]:
            self._dry_run(candidates_data, md_dir, api_key, model)
            return

        self._import(candidates_data, md_dir, api_key, model)

    def _get_md_path(self, entry: dict, md_dir: Path) -> Path | None:
        """Derive the markdown file path from the PDF path in candidates.json."""
        pdf_path = entry.get("pdf_path", "")
        if not pdf_path:
            return None
        md_name = Path(pdf_path).stem + ".md"
        md_path = md_dir / md_name
        return md_path if md_path.exists() else None

    def _parse_md(self, md_path: Path, candidate_name: str = "", api_key: str = "", model: str = "sarvam-m") -> dict:
        """Parse affidavit markdown, return dict of extra fields. Optionally use LLM fallback."""
        from core.ingestion.parse_form26_markdown import parse_form26_markdown
        try:
            p = parse_form26_markdown(md_path)
            result = {
                "education": p.education if p.education and "முழு அஞ்சல்" not in p.education and "வேட்பாளர் பெயர்" not in p.education else "",
                "profession": p.profession,
                "spouse_profession": p.spouse_profession,
                "assets_total": p.assets_total,
                "liabilities_total": p.liabilities_total,
                "criminal_cases_count": p.criminal_cases_count,
                "convictions_count": p.convictions_count,
                "phone": p.phone,
                "email": p.email,
                "social_media": p.social_media,
                "constituency_number": p.constituency_number,
                "additional_details": p.additional_details,
            }

            # LLM fallback for missing critical fields
            if api_key:
                missing = []
                if not result["education"]:
                    missing.append("education")
                if not result["profession"]:
                    missing.append("profession")
                if result["assets_total"] is None:
                    missing.append("assets_total")
                if not result["phone"]:
                    missing.append("phone")
                if not result["email"]:
                    missing.append("email")
                if not result.get("constituency_number"):
                    missing.append("constituency_number")

                if missing:
                    from core.ingestion.sarvam_llm_extract import extract_missing_fields
                    try:
                        llm_data = extract_missing_fields(md_path, missing, candidate_name, api_key, model)
                        for field, val in llm_data.items():
                            if val is not None and val != "" and not result.get(field):
                                result[field] = val
                        if llm_data:
                            self.stdout.write(f"  LLM filled {list(llm_data.keys())} for {candidate_name}")
                    except Exception as e:
                        self.stderr.write(f"  LLM fallback error for {candidate_name}: {e}")

            return result
        except Exception as e:
            self.stderr.write(f"  Parse error {md_path.name}: {e}")
            return {}

    def _dry_run(self, candidates_data: list[dict], md_dir: Path, api_key: str = "", model: str = "sarvam-m") -> None:
        with_md = without_md = 0
        for entry in candidates_data:
            md_path = self._get_md_path(entry, md_dir)
            extra = self._parse_md(md_path, entry["name"], api_key, model) if md_path else {}
            has_extra = bool(extra.get("education") or extra.get("assets_total") or extra.get("phone"))
            if md_path:
                with_md += 1
            else:
                without_md += 1
            if has_extra:
                self.stdout.write(
                    f"  OK {entry['name']:30s} | {entry['constituency']:20s} | "
                    f"Edu={bool(extra.get('education'))} Assets={extra.get('assets_total')} "
                    f"Phone={bool(extra.get('phone'))}"
                )

        self.stdout.write(f"\n{len(candidates_data)} candidates: {with_md} have markdown, {without_md} without")

    @transaction.atomic
    def _import(self, candidates_data: list[dict], md_dir: Path, api_key: str = "", model: str = "sarvam-m") -> None:
        election, _ = Election.objects.get_or_create(
            year=2026,
            defaults={"name": "Tamil Nadu Assembly Elections 2026"},
        )
        source = SourceDocument.objects.create(
            title="ECI Affidavit 2026 — candidates.json + Sarvam Vision OCR",
            source_type=SourceDocument.SourceType.OFFICIAL,
        )

        created = updated = 0
        affidavits_created = 0

        for entry in candidates_data:
            # --- Identity from candidates.json (100% reliable) ---
            name = entry["name"].strip()
            constituency_name = entry["constituency"].strip()
            party_name = entry.get("party", "").strip() or "Independent"
            age = int(entry["age"]) if entry.get("age") and entry["age"].isdigit() else None
            gender = (entry.get("gender") or "").strip().capitalize()
            address = (entry.get("address") or "").strip()
            photo_url = (entry.get("photo_url") or "").strip()
            affidavit_url = (entry.get("affidavit_url") or "").strip()

            # --- Extra fields from affidavit markdown (best effort + LLM fallback) ---
            md_path = self._get_md_path(entry, md_dir)
            extra = self._parse_md(md_path, name, api_key, model) if md_path else {}

            # Create/update records
            constituency, _ = Constituency.objects.get_or_create(name=constituency_name)
            party, _ = Party.objects.get_or_create(name=party_name)

            candidate, is_new = Candidate.objects.update_or_create(
                name=name,
                constituency=constituency,
                defaults={
                    "party": party,
                    "status": Candidate.Status.APPLIED,
                    "age": age,
                    "gender": gender,
                    "address": address,
                    "education": extra.get("education", ""),
                    "profession": extra.get("profession", ""),
                    "photo_url": photo_url,
                },
            )

            if is_new:
                created += 1
            else:
                updated += 1

            # Create affidavit if we have any affidavit-specific data
            if extra.get("assets_total") is not None or extra.get("criminal_cases_count") or extra.get("education"):
                additional = extra.get("additional_details") or {}
                if extra.get("phone") or extra.get("email"):
                    additional["contact"] = {
                        "phone": extra.get("phone", ""),
                        "email": extra.get("email", ""),
                    }
                if extra.get("social_media"):
                    additional["social_media"] = extra["social_media"]
                if extra.get("spouse_profession"):
                    additional["spouse_profession"] = extra["spouse_profession"]

                Affidavit.objects.update_or_create(
                    candidate=candidate,
                    source_document=source,
                    defaults={
                        "criminal_cases_count": extra.get("criminal_cases_count", 0),
                        "serious_criminal_cases_count": 0,
                        "assets_total": extra.get("assets_total"),
                        "liabilities_total": extra.get("liabilities_total"),
                        "education": extra.get("education", ""),
                        "additional_details": additional or None,
                    },
                )
                affidavits_created += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nImported {len(candidates_data)} candidates: "
            f"{created} created, {updated} updated, "
            f"{affidavits_created} affidavits created"
        ))
