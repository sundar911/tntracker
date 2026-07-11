"""
Merge MyNeta financial/education/criminal data into fct_candidates_26.csv.

The ECI-derived fct_candidates_26.csv has photos, affidavit links and
Tamil-ready names but no financial data. fct_candidates_26_myneta.csv has
assets, liabilities, criminal cases, education and professions but no photos
and different name formatting. This command joins them by
(normalized_name, normalized_constituency) and enriches the ECI CSV in place.

Matching ladder (each ECI row):
  1. exact (sorted-token name, aliased constituency) — unique myneta row
  2. exact but ambiguous (namesake candidates) — tiebreak by party, then age
  3. fuzzy fallback — rapidfuzz token_sort_ratio >= 88 within the same
     constituency, against still-unclaimed myneta rows; ties are skipped
A myneta row is claimed at most once; unresolvable duplicates are skipped
rather than guessed.

Usage:
    python manage.py merge_myneta_data --dry-run
    python manage.py merge_myneta_data --backup
"""

import csv
import re
import shutil
from collections import defaultdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand

from rapidfuzz import fuzz

# Mirror of views._CONSTITUENCY_ALIASES / _normalize_constituency_name,
# extended with the 16 ECI<->MyNeta 2026 spelling pairs (ECI spelling on the
# left, MyNeta spelling normalized to on the right).
_CONSTITUENCY_ALIASES = {
    "CHEPAUK THIRUVALLIKENI": "CHEPAUK THIRUVALLIKEN",
    "COLACHAL": "COLACHEL",
    "PAPPIREDDIPATTI": "PAPPIREDDIPPATTI",
    "THIRUVOTTIYUR": "TIRUVOTTIYUR",
    "TIRUPPATTUR": "TIRUPATTUR",
    "TIRUCHIRAPPALLI EAST": "TIRUCHIRAPPALLI",
    "ARUPPUKKOTTAI": "ARUPPUKOTTAI",
    "BODINAYAKANUR": "BODINAYAKKANUR",
    "GANDHARVAKOTTAI": "GANDARVAKOTTAI",
    "MADAVARAM": "MADHAVARAM",
    "MADURAVOYAL": "MADHURAVOYAL",
    "METTUPPALAYAM": "METTUPALAYAM",
    "MUDHUKULATHUR": "MUDUKULATHUR",
    "PALACODU": "PALACODE",
    "PARAMATHI VELUR": "PARAMATHIVELUR",
    "SHOLINGUR": "SHOLINGHUR",
    "SHOZHINGANALLUR": "SHOLINGANALLUR",
    "THALLI": "THALLY",
    "THOOTHUKKUDI": "THOOTHUKUDI",
    "VEDARANYAM": "VEDHARANYAM",
    "VILUPPURAM": "VILLUPURAM",
    "VRIDDHACHALAM": "VRIDHACHALAM",
}

# ECI full party names -> MyNeta abbreviations, for namesake tiebreaks.
_PARTY_ALIASES = {
    "ALL INDIA ANNA DRAVIDA MUNNETRA KAZHAGAM": "AIADMK",
    "DRAVIDA MUNNETRA KAZHAGAM": "DMK",
    "INDEPENDENT": "IND",
    "NAAM TAMILAR KATCHI": "NTK",
    "TAMILAGA VETTRI KAZHAGAM": "TVK",
    "BHARATIYA JANATA PARTY": "BJP",
    "INDIAN NATIONAL CONGRESS": "INC",
    "BAHUJAN SAMAJ PARTY": "BSP",
    "COMMUNIST PARTY OF INDIA": "CPI",
    "COMMUNIST PARTY OF INDIA (MARXIST)": "CPI(M)",
    "PATTALI MAKKAL KATCHI": "PMK",
    "DESIYA MURPOKKU DRAVIDA KAZHAGAM": "DMDK",
    "MARUMALARCHI DRAVIDA MUNNETRA KAZHAGAM": "MDMK",
    "VIDUTHALAI CHIRUTHAIGAL KATCHI": "VCK",
}

FUZZY_THRESHOLD = 88

# Fixes for candidates MyNeta never analyzed, applied after the merge.
# Keyed by (candidate, constituency) exactly as they appear in the ECI CSV.
# Thiruvallur winner T. Arunkumar (TVK, 92,190 votes, margin 24,760) is not
# on MyNeta, so their winner flag misses him — confirmed via
# https://en.wikipedia.org/wiki/T._Arunkumar
_MANUAL_PATCHES = {
    ("DR. T. ARUNKUMAR", "THIRUVALLUR"): {"is_winner": "1"},
}


def _norm_const(name: str) -> str:
    raw = (name or "").strip().upper()
    raw = re.sub(r"\s*:\s*BYE ELECTION.*$", "", raw)
    n = re.sub(r"[^A-Z0-9]+", " ", raw)
    n = re.sub(r"\s+", " ", n).strip()
    n = re.sub(r"\s+(SC|ST)$", "", n)
    return _CONSTITUENCY_ALIASES.get(n, n)


def _split_relation(name: str) -> tuple[str, str]:
    """MyNeta disambiguates namesakes as 'Loganathan. M S/O Mani' — split
    into (base name, relation name). ECI carries the relation separately in
    fathers_name, so the suffix must not poison name matching."""
    m = re.split(r"\b[SWD]\s*/\s*O\b\.?", name or "", maxsplit=1, flags=re.IGNORECASE)
    if len(m) == 2:
        return m[0].strip(), m[1].strip()
    return (name or "").strip(), ""


def _norm_name(name: str) -> str:
    """Uppercase, drop alias markers and punctuation, sort tokens so
    initials order doesn't matter ("R. Kumar" == "Kumar R")."""
    n = (name or "").upper()
    # "(A)" / "@" / "ALIAS" all mark aka-names on ECI/MyNeta respectively
    n = re.sub(r"\(A\)|@|\bALIAS\b", " ", n)
    n = re.sub(r"[^A-Z ]", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    return " ".join(sorted(n.split()))


def _norm_party(name: str) -> str:
    p = re.sub(r"\s+", " ", (name or "").upper()).strip()
    return _PARTY_ALIASES.get(p, p)


# myneta column -> eci CSV column (fill only if eci value is empty)
FIELD_MAP = {
    "criminal_cases": "criminal_cases",
    "education": "education_category",
    "education_details": "education_details",
    "total_assets_rs": "total_assets_rs",
    "liabilities_rs": "liabilities_rs",
    "self_profession": "self_profession",
    "spouse_profession": "spouse_profession",
    "criminal_cases_details": "legal_summary_short",
    "myneta_url": "myneta_url",
    "is_winner": "is_winner",
    # comparison-chart columns (present once scrape_myneta_comparison_26 has
    # been merged into the myneta CSV; harmless no-ops until then)
    "pan_given": "pan_given",
    "serious_ipc_counts": "serious_ipc_counts",
    "movable_assets_rs": "movable_assets_rs",
    "immovable_assets_rs": "immovable_assets_rs",
}


class Command(BaseCommand):
    help = "Merge MyNeta data into fct_candidates_26.csv by name+constituency"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--backup", action="store_true")
        parser.add_argument("--rebuild", action="store_true",
                            help="clear all MyNeta-derived columns first, then re-merge "
                                 "(purges assignments made by older matcher versions)")

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        backup = options["backup"]
        data_dir = Path(settings.BASE_DIR).parent / "data"
        eci_path = data_dir / "fct_candidates_26.csv"
        myneta_path = data_dir / "fct_candidates_26_myneta.csv"

        if not myneta_path.exists():
            self.stderr.write(self.style.ERROR(f"Not found: {myneta_path}"))
            return

        with open(myneta_path, "r", encoding="utf-8") as f:
            myneta_rows = list(csv.DictReader(f))
        with open(eci_path, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            fieldnames = list(reader.fieldnames)
            eci_rows = list(reader)

        # Ensure target columns exist (only for sources the myneta CSV has)
        available_srcs = set(myneta_rows[0].keys()) if myneta_rows else set()
        for src, dst in FIELD_MAP.items():
            if src in available_srcs and dst not in fieldnames:
                fieldnames.append(dst)

        if options["rebuild"]:
            cleared = set(FIELD_MAP.values()) & set(fieldnames)
            for row in eci_rows:
                for col in cleared:
                    row[col] = ""
            self.stdout.write(f"Rebuild: cleared {sorted(cleared)}")

        by_key: dict[tuple, list] = defaultdict(list)
        by_const: dict[str, list] = defaultdict(list)
        relations: dict[int, str] = {}  # id(myneta row) -> S/O|W/O|D/O name
        for r in myneta_rows:
            base, relation = _split_relation(r.get("candidate", ""))
            relations[id(r)] = relation
            key = (_norm_name(base),
                   _norm_const(r.get("2026_constituency", "")))
            by_key[key].append(r)
            by_const[key[1]].append(r)

        claimed: set[int] = set()  # id() of claimed myneta rows
        stats = defaultdict(int)
        matches: list[tuple[dict, dict, str]] = []  # (eci_row, myneta_row, how)

        def _claim(eci_row, m, how):
            claimed.add(id(m))
            matches.append((eci_row, m, how))
            stats[how] += 1

        # Pass 1: exact key, with party/age tiebreaks for namesakes
        fuzzy_queue = []
        for row in eci_rows:
            key = (_norm_name(row.get("candidate", "")),
                   _norm_const(row.get("constituency", "")))
            pool = [m for m in by_key.get(key, []) if id(m) not in claimed]
            if len(pool) > 1:
                by_party = [m for m in pool
                            if _norm_party(m.get("party")) == _norm_party(row.get("party"))]
                if len(by_party) == 1:
                    _claim(row, by_party[0], "exact+party")
                    continue
                narrowed = by_party or pool
                by_age = [m for m in narrowed
                          if (m.get("age") or "").strip() == (row.get("age") or "").strip()]
                if len(by_age) == 1:
                    _claim(row, by_age[0], "exact+age")
                    continue
                # MyNeta's S/O|W/O|D/O suffix vs ECI fathers_name (which also
                # holds husband's name for W/O entries)
                father = _norm_name(row.get("fathers_name", ""))
                by_rel = [m for m in (by_age or narrowed)
                          if relations.get(id(m))
                          and fuzz.token_sort_ratio(_norm_name(relations[id(m)]), father) >= 85]
                if len(by_rel) == 1:
                    _claim(row, by_rel[0], "exact+relation")
                    continue
                stats["ambiguous_skipped"] += 1
                continue
            if len(pool) == 1:
                _claim(row, pool[0], "exact")
                continue
            fuzzy_queue.append((row, key))

        # Pass 2: fuzzy within constituency against unclaimed rows only
        for row, key in fuzzy_queue:
            nn, nc = key
            pool = [m for m in by_const.get(nc, []) if id(m) not in claimed]
            scored = sorted(
                ((fuzz.token_sort_ratio(nn, _norm_name(m.get("candidate", ""))), m)
                 for m in pool),
                key=lambda t: -t[0])
            if scored and scored[0][0] >= FUZZY_THRESHOLD:
                # skip ties — two myneta rows equally close is a guess, not a match
                if len(scored) > 1 and scored[1][0] == scored[0][0]:
                    stats["fuzzy_tie_skipped"] += 1
                    continue
                _claim(row, scored[0][1], "fuzzy")
            else:
                stats["unmatched"] += 1

        enriched = age_repairs = 0
        for row, m, _how in matches:
            filled_any = False
            for src, dst in FIELD_MAP.items():
                if src not in available_srcs:
                    continue
                if (row.get(dst) or "").strip():
                    continue  # don't overwrite existing
                val = (m.get(src) or "").strip()
                if val:
                    row[dst] = val
                    filled_any = True
            if filled_any:
                enriched += 1
            # Repair impossible ECI ages (e.g. "341") from the matched MyNeta row
            eci_age = (row.get("age") or "").strip()
            my_age = (m.get("age") or "").strip()
            if my_age.isdigit() and 18 <= int(my_age) <= 120 and not (
                    eci_age.isdigit() and 18 <= int(eci_age) <= 120):
                self.stdout.write(f"Age repair: {row.get('candidate')} "
                                  f"({row.get('constituency')}) {eci_age!r} -> {my_age}")
                row["age"] = my_age
                age_repairs += 1

        # rows never touched by any pass need the new columns present
        for row in eci_rows:
            for src, dst in FIELD_MAP.items():
                if src in available_srcs:
                    row.setdefault(dst, "")

        patched = 0
        for row in eci_rows:
            patch = _MANUAL_PATCHES.get(
                (row.get("candidate", "").strip(), row.get("constituency", "").strip()))
            if patch:
                for col, val in patch.items():
                    if col in fieldnames:
                        row[col] = val
                        patched += 1
        if patched:
            self.stdout.write(f"Manual patches applied: {patched}")

        matched = len(matches)
        self.stdout.write(
            f"MyNeta rows: {len(myneta_rows)} | ECI rows: {len(eci_rows)}\n"
            f"Matched: {matched} "
            f"(exact {stats['exact']}, exact+party {stats['exact+party']}, "
            f"exact+age {stats['exact+age']}, exact+relation {stats['exact+relation']}, "
            f"fuzzy {stats['fuzzy']})\n"
            f"Enriched: {enriched} | Age repairs: {age_repairs} | "
            f"Ambiguous skipped: {stats['ambiguous_skipped']} | "
            f"Fuzzy ties skipped: {stats['fuzzy_tie_skipped']} | "
            f"Unmatched ECI: {stats['unmatched']}"
        )

        if dry_run:
            self.stdout.write("Dry run — no files written.")
            return

        if backup:
            bak = eci_path.with_suffix(".csv.myneta_bak")
            shutil.copy2(eci_path, bak)
            self.stdout.write(f"Backed up to {bak}")

        with open(eci_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(eci_rows)

        self.stdout.write(self.style.SUCCESS(f"Updated {eci_path}"))
