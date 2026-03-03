"""Process candidate education and legal history text for cleaner display.

Adds two new columns to data/fct_candidates_21.csv:
  - education_formatted: "<degree> | <institute> | <year>" (one line per qualification)
  - legal_summary_short: Concise plain-language summary of criminal charges

No API keys needed — uses keyword matching and pattern parsing.

Usage:
    python scripts/process_candidate_text.py                # both education + legal
    python scripts/process_candidate_text.py --education     # education only
    python scripts/process_candidate_text.py --legal         # legal only
    python scripts/process_candidate_text.py --dry-run       # preview without writing
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parent.parent / "data" / "fct_candidates_21.csv"


# ═══════════════════════════════════════════════════════════════════════════
# LEGAL SUMMARY — keyword-category matching (no comma splitting needed)
# ═══════════════════════════════════════════════════════════════════════════

# (label, keywords, severity 1=highest)
# We search the FULL text for each keyword — avoids comma-splitting issues.
CHARGE_CATEGORIES = [
    ("attempted murder",        ["Attempt To Murder", "Attempt To Commit Culpable Homicide"], 1),
    ("murder",                  ["Culpable Homicide Not Amounting To Murder", "Murder"], 1),
    ("rape",                    ["Rape"], 1),
    ("kidnapping",              ["Kidnapping", "Abducting"], 2),
    ("dacoity",                 ["Dacoity"], 2),
    ("robbery",                 ["Robbery"], 2),
    ("extortion",               ["Extortion"], 2),
    ("sedition",                ["Sedition"], 3),
    ("promoting communal enmity", ["Promoting Enmity Between Different Groups"], 3),
    ("sexual harassment",       ["Sexual Harassment", "Modesty Of A Woman", "Sexually Coloured"], 3),
    ("cruelty against women",   ["Husband Or Relative Of Husband Of A Woman Subjecting Her To Cruelty"], 3),
    ("causing grievous hurt",   ["Causing Grievous Hurt"], 3),
    ("assault with weapons",    ["Voluntarily Causing Hurt By Dangerous Weapons"], 3),
    ("assault on public servant", ["Voluntarily Causing Hurt To Deter Public Servant",
                                   "Assault Or Criminal Force To Deter Public Servant"], 4),
    ("assault",                 ["Voluntarily Causing Hurt", "Assault Or Criminal Force"], 5),
    ("criminal breach of trust", ["Criminal Breach Of Trust"], 4),
    ("fraud/cheating",          ["Cheating And Dishonestly", "Cheating By Personation", "Cheating"], 4),
    ("forgery",                 ["Forgery", "Forged Document"], 4),
    ("criminal intimidation",   ["Criminal Intimidation"], 4),
    ("arson",                   ["Mischief By Fire", "Mischief By Explosive"], 4),
    ("property damage",         ["Mischief Causing Damage", "Mischief By Destroying"], 5),
    ("trespass",                ["House-Trespass", "Criminal Trespass", "Lurking House"], 5),
    ("theft",                   ["Theft"], 5),
    ("defamation",              ["Defamation"], 6),
    ("rioting",                 ["Rioting"], 6),
    ("unlawful assembly",       ["Unlawful Assembly"], 6),
    ("wrongful restraint",      ["Wrongful Restraint", "Wrongful Confinement"], 6),
    ("disobeying public orders", ["Disobedience To Order Duly Promulgated"], 7),
    ("disease/quarantine violations", ["Quarantine", "Spread Infection Of Disease"], 7),
    ("public nuisance",         ["Public Nuisance"], 7),
    ("election offences",       ["False Statement In Connection With An Election",
                                 "Illegal Payments In Connection With An Election",
                                 "Undue Influence At Elections", "Personation At Elections"], 7),
    ("obstruction of justice",  ["Disappearance Of Evidence", "False Information",
                                 "Concealment Of Stolen", "Screen Offender"], 7),
    ("criminal conspiracy",     ["Criminal Conspiracy"], 6),
    ("abetment",                ["Abetment"], 7),
    # IPC section references (not always spelled out)
    ("statements conducing to public mischief", ["Section 505(1)", "Section 505(2)",
                                                  "Statements Conducing To Public Mischief"], 6),
    ("promoting communal enmity", ["Section 153A"], 3),  # may already match via full text
]


def summarize_legal(text: str) -> str:
    """Generate a short plain-language summary from verbose IPC charge descriptions."""
    if not text or not text.strip():
        return ""

    text_lower = text.lower()
    matched: list[tuple[int, str]] = []
    seen: set[str] = set()

    for label, keywords, severity in CHARGE_CATEGORIES:
        if label in seen:
            continue
        for kw in keywords:
            if kw.lower() in text_lower:
                # Dedup overlapping categories
                if label == "murder" and "attempted murder" in seen:
                    continue
                if label == "assault" and seen & {"assault with weapons", "causing grievous hurt", "assault on public servant"}:
                    continue
                if label == "unlawful assembly" and "rioting" in seen:
                    continue
                matched.append((severity, label))
                seen.add(label)
                break

    if not matched:
        # Fallback — just shorten the raw text
        short = text[:80].rstrip(",. ")
        return short

    matched.sort(key=lambda x: x[0])
    labels = [m[1] for m in matched]

    # Cap at 5 for brevity
    if len(labels) > 5:
        labels = labels[:5]

    if len(labels) == 1:
        return f"Charged with {labels[0]}"
    return "Charged with " + ", ".join(labels[:-1]) + " and " + labels[-1]


# ═══════════════════════════════════════════════════════════════════════════
# EDUCATION FORMATTING — multi-pattern parser
# ═══════════════════════════════════════════════════════════════════════════

# Patterns that identify the START of a degree/qualification
DEGREE_RE = re.compile(
    r'(?:'
    # Doctorates
    r'Ph\.?D\.?'
    r'|M\.?B\.?B\.?S\.?'
    r'|M\.?S\.?\b'
    r'|M\.?D\.?\b'
    # Masters
    r'|M\.?Tech\.?'
    r'|M\.?E\.?\b'
    r'|M\.?C\.?A\.?\b'
    r'|M\.?B\.?A\.?\b'
    r'|M\.?A\.?\b'
    r'|M\.?Sc\.?\b'
    r'|M\.?S\.?W\.?\b'
    r'|M\.?Com\.?\b'
    r'|M\.?Phil\.?\b'
    r'|M\.?Ed\.?\b'
    r'|Master\s+of\s+\w+'
    # Bachelors
    r'|B\.?Tech\.?'
    r'|B\.?E\.?\b'
    r'|B\.?C\.?A\.?\b'
    r'|B\.?B\.?A\.?\b'
    r'|B\.?B\.?M\.?\b'
    r'|B\.?A\.?\b'
    r'|B\.?Sc\.?\b'
    r'|B\.?Com\.?\b'
    r'|B\.?Ed\.?\b'
    r'|B\.?L\.?\b'
    r'|B\.?S\.?W\.?\b'
    r'|Bachelors?\s+(?:Degree\s+)?(?:of|in)\s+\w+'
    # Law
    r'|L\.?L\.?B\.?'
    r'|L\.?L\.?M\.?'
    # Professional / Diploma
    r'|Diploma\s+in\s+\w+'
    r'|ITI\b'
    r'|D\.?Pharm\.?'
    r'|DCO-?OP\b'
    r'|Marine\s+Engineer'
    r'|C\.?A\.?\b'
    # School levels
    r'|S\.?S\.?L\.?C\.?\b'
    r'|HSL\.?\b'
    r'|HSC\.?\b'
    r'|PUC\b'
    r'|SSLC\.?\b'
    r'|12th(?:\s+(?:Std\.?|Standard|Class|Pass(?:ed)?|Discontinued))?'
    r'|11th(?:\s+(?:Std\.?|Standard|Class|Pass(?:ed)?|Discontinued))?'
    r'|10th(?:\s+(?:Std\.?|Standard|Class|Pass(?:ed)?))?'
    r'|10-?[Ss]td'
    r'|9th(?:\s+(?:Std\.?|Standard|Pass(?:ed)?))?'
    r'|9-?[Ss]td'
    r'|8th(?:\s+(?:Std\.?|Standard|Class(?:\s+(?:Pass|Fail))?))?'
    r'|7th(?:\s+(?:Std\.?|Standard|[Ss]td))?'
    r'|6th(?:\s+(?:Std\.?|Standard|Class))?'
    r'|5th(?:\s+(?:Std\.?|Standard|Pass))?'
    r'|4th(?:\s+(?:Std\.?|Standard))?'
    r'|3rd(?:\s+(?:Std\.?|Standard))?'
    # Specials
    r'|Literate'
    r'|Non-Educated'
    r'|Illiterate'
    r'|\+2\b'
    r'|Law\s+Degree'
    r')',
    re.IGNORECASE,
)

# Year or year-range (but NOT dates like 2008/04/11)
YEAR_RANGE_RE = re.compile(r'\b((?:19|20)\d{2})\s*[-–]\s*((?:19|20)?\d{2,4})\b')
# Full date: YYYY/MM/DD or DD/MM/YYYY — extract just the year
DATE_FULL_RE = re.compile(r'\b((?:19|20)\d{2})/\d{1,2}/\d{1,2}\b|\b\d{1,2}/\d{1,2}/((?:19|20)\d{2})\b')
YEAR_SINGLE_RE = re.compile(r'\b((?:19|20)\d{2})\b')


def _split_qualifications(text: str) -> list[str]:
    """Split text containing multiple qualifications into parts."""
    # Split on numbered patterns: "1)" "2)" ". 2)" "2." — but NOT at start
    parts = re.split(r'(?:\.\s*|\,\s*|\s+)(?:\d+[\)\.]\s+)', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return parts

    # Split on "; "
    if "; " in text:
        parts = [p.strip() for p in text.split(";") if p.strip()]
        if len(parts) > 1:
            return parts

    # Split on ", " ONLY if followed by a recognized degree pattern
    # This handles: "M.A 1992, B.A 1989, ..." and "BE from X, Diploma in Y"
    segments = re.split(r',\s*', text)
    if len(segments) > 1:
        merged = []
        current = segments[0]
        for seg in segments[1:]:
            seg_stripped = seg.strip()
            # Check if this segment starts with a degree-like pattern
            if DEGREE_RE.match(seg_stripped) or re.match(r'\+2\b', seg_stripped):
                merged.append(current.strip())
                current = seg
            else:
                current += ", " + seg
        merged.append(current.strip())
        if len(merged) > 1:
            return merged

    return [text]


def _extract_year(text: str) -> tuple[str, str]:
    """Extract year/year-range from text, return (year_str, text_without_year)."""
    # First, handle full dates like 2008/04/11 or 15/09/2014 → just extract year
    dm = DATE_FULL_RE.search(text)
    if dm:
        year = dm.group(1) or dm.group(2)
        remaining = text[:dm.start()] + text[dm.end():]
        return year, remaining

    # Try year range: 1985-1992 (not slash-separated, which could be dates)
    m = YEAR_RANGE_RE.search(text)
    if m:
        y1 = m.group(1)
        y2 = m.group(2)
        if len(y2) == 2:
            y2 = y1[:2] + y2
        remaining = text[:m.start()] + text[m.end():]
        return f"{y1}-{y2}", remaining

    # Try single year
    matches = YEAR_SINGLE_RE.findall(text)
    if matches:
        year = matches[-1]  # take last occurrence
        # Remove all year occurrences
        remaining = YEAR_SINGLE_RE.sub("", text)
        return year, remaining

    return "", text


def _remove_empty_parens(text: str) -> str:
    """Remove empty parentheses left after year extraction."""
    return re.sub(r'\s*\(\s*\)', '', text)


def _clean_connectors(text: str) -> str:
    """Remove connecting words and clean up whitespace/punctuation."""
    # Remove month names left over after year extraction
    text = re.sub(
        r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b\s*[-–]?\s*',
        '', text, flags=re.IGNORECASE)
    # Strip trailing commas/dashes first (so connectors at end become exposed)
    # Note: periods are handled separately below to preserve abbreviations
    text = re.sub(r'\s*[-–,]+\s*$', '', text)
    # Remove trailing connectors (after punctuation is stripped)
    text = re.sub(r'\s*\b(?:in\s+the\s+year|[Ii]n|IN|[Oo]n|ON|[Yy]ear|YEAR|[Ff]rom|FROM)\s*[-–]?\s*$', '', text)
    # Strip trailing separators again (connectors may expose new ones)
    text = re.sub(r'\s*[-–,]+\s*$', '', text)
    # Strip trailing period ONLY if not an abbreviation (not preceded by single uppercase letter)
    if text.endswith('.') and not re.search(r'[A-Z]\.$', text):
        text = text.rstrip('.')
    # Second pass: after period strip, connectors may be exposed again
    text = re.sub(r'\s*\b(?:in\s+the\s+year|[Ii]n|IN|[Oo]n|ON|[Yy]ear|YEAR|[Ff]rom|FROM)\s*[-–]?\s*$', '', text)
    text = re.sub(r'\s*[-–,]+\s*$', '', text)
    # Strip leading separators
    text = re.sub(r'^\s*[-–,\.]+\s*', '', text)
    # Remove "Passed from" / "Pass from" / "Fail from"
    text = re.sub(r'\b(?:Passed?|Fail)\s+(?:from|in)\b', '', text, flags=re.IGNORECASE)
    # Collapse whitespace
    text = re.sub(r'\s{2,}', ' ', text)
    return text.strip()


def _format_single(text: str) -> str:
    """Format a single education qualification into degree | institute | year."""
    text = text.strip().rstrip(".")
    if not text:
        return ""

    # Simple cases: just a degree name with nothing else
    stripped = text.strip(" .,")
    if stripped.lower() in (
        "literate", "non-educated", "illiterate", "iti", "sslc",
        "s.s.l.c.", "s.s.l.c", "s.s.lc.", "s.s.lc", "hsl.", "hsc.",
    ):
        return stripped

    if re.fullmatch(r'[A-Z][A-Z.]+\s*(?:\([^)]*\))?\s*$', stripped):
        # Just an abbreviation like "M.A" or "B.A. (Lit)"
        return stripped

    # 1. Extract year
    year_str, rest = _extract_year(text)
    rest = _remove_empty_parens(rest)

    # 2. Separate degree from institute
    rest = _clean_connectors(rest)

    # Handle degree+subject glued together: "M.Sc.Finance" → "M.Sc. Finance"
    # But NOT "B.Tech(IT)" or "B.E.Mechanical" — those have the subject as part of degree
    # Only split when the abbreviation part has 2+ dot-separated segments
    rest = re.sub(
        r'^([A-Z][a-zA-Z]*\.[A-Z][a-zA-Z]*\.)([A-Z][a-z]{2,})',
        r'\1 \2', rest,
    )

    # Handle "DISCONTINUE/Discontinued" — keep with degree
    disc_match = re.search(r'\b(DISCONTINUE[D]?|Discontinue[d]?)\b', rest)
    if disc_match:
        rest = rest[:disc_match.start()] + rest[disc_match.end():]
        rest = rest.strip()

    # Try "from" as separator (including common typo "form")
    from_match = re.search(r'\b(?:from|FROM|From|form)\b', rest)
    if from_match:
        degree_part = rest[:from_match.start()].strip()
        institute_part = rest[from_match.end():].strip()
    else:
        # Try comma as separator when degree is a school level (e.g. "8th Std,School Name")
        if re.match(r'(?:\d+(?:th|st|nd|rd)?\s*(?:Std\.?|Standard|Class|Pass)?)\s*$',
                     rest.split(',')[0], re.IGNORECASE) and ',' in rest:
            comma_pos = rest.index(',')
            degree_part = rest[:comma_pos].strip()
            institute_part = rest[comma_pos + 1:].strip()
            # Skip to building output below
            degree_part = _clean_connectors(degree_part)
            degree_part = degree_part.strip(" ,-")
            if degree_part.endswith(".") and not re.search(r'[A-Z]\.$', degree_part):
                degree_part = degree_part.rstrip(".")
            institute_part = _clean_connectors(institute_part)
            institute_part = institute_part.strip(" .,-")
            parts = []
            if degree_part:
                parts.append(degree_part)
            if institute_part:
                parts.append(institute_part)
            if year_str:
                parts.append(year_str)
            return " | ".join(parts) if parts else text.strip()

        # Try "in" as separator (only if followed by non-year text)
        in_match = re.search(r'\b(?:in|In|IN)\b(?!\s*\d)', rest)
        if in_match and in_match.start() > 3:
            degree_part = rest[:in_match.start()].strip()
            institute_part = rest[in_match.end():].strip()
        else:
            # Try to identify degree at the start
            m = DEGREE_RE.match(rest)
            if m:
                degree_part = m.group(0).strip()
                institute_part = rest[m.end():].strip()
            else:
                # Can't parse — return lightly cleaned
                parts = [rest]
                if year_str:
                    parts.append(year_str)
                return " | ".join(p for p in parts if p)

    # Clean up degree
    degree_part = _clean_connectors(degree_part)
    degree_part = degree_part.strip(" ,-")
    # Strip trailing period only if not an abbreviation (e.g. keep "B.L." but strip "College.")
    if degree_part.endswith(".") and not re.search(r'[A-Z]\.$', degree_part):
        degree_part = degree_part.rstrip(".")

    # Clean up institute
    institute_part = _clean_connectors(institute_part)
    institute_part = re.sub(r'^(?:in|at|from|FROM)\s+', '', institute_part, flags=re.IGNORECASE)
    institute_part = re.sub(r'\s*\b(?:in|In|on|On)\s*$', '', institute_part)
    institute_part = institute_part.strip(" .,-")

    # Handle degree with subject in parentheses that got split
    # e.g., "B.A." degree and "(Lit) Mumbai University" institute
    paren_match = re.match(r'^\(([^)]+)\)\s*(.*)', institute_part)
    if paren_match and len(degree_part) < 12:
        degree_part = f"{degree_part} ({paren_match.group(1)})"
        institute_part = paren_match.group(2).strip(" .,")

    # Build output
    parts = []
    if degree_part:
        parts.append(degree_part)
    if institute_part:
        parts.append(institute_part)
    if year_str:
        parts.append(year_str)

    if not parts:
        return text.strip()

    return " | ".join(parts)


def format_education(text: str) -> str:
    """Format education text into <degree> | <institute> | <year> lines."""
    if not text or not text.strip():
        return ""

    qualifications = _split_qualifications(text.strip())
    results = []
    for q in qualifications:
        formatted = _format_single(q)
        if formatted:
            results.append(formatted)

    return "\n".join(results) if results else text.strip()


# ═══════════════════════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Process candidate text fields")
    parser.add_argument("--education", action="store_true", help="Process education only")
    parser.add_argument("--legal", action="store_true", help="Process legal only")
    parser.add_argument("--dry-run", action="store_true", help="Preview results, don't write")
    args = parser.parse_args()

    do_education = args.education or (not args.education and not args.legal)
    do_legal = args.legal or (not args.education and not args.legal)

    if not CSV_PATH.exists():
        print(f"CSV not found: {CSV_PATH}")
        sys.exit(1)

    with CSV_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows = list(reader)

    print(f"Read {len(rows)} rows from {CSV_PATH.name}")

    for col in ["education_formatted", "legal_summary_short"]:
        if col not in fieldnames:
            fieldnames.append(col)

    for row in rows:
        row.setdefault("education_formatted", "")
        row.setdefault("legal_summary_short", "")

    if do_legal:
        print("Processing legal summaries...")
        count = 0
        for row in rows:
            src = row.get("criminal_cases_summary", "").strip()
            if src and not row.get("legal_summary_short", "").strip():
                row["legal_summary_short"] = summarize_legal(src)
                count += 1
        print(f"  Generated {count} legal summaries.")

    if do_education:
        print("Processing education formatting...")
        count = 0
        for row in rows:
            src = row.get("education_details_clean", "").strip()
            if src and not row.get("education_formatted", "").strip():
                row["education_formatted"] = format_education(src)
                count += 1
        print(f"  Formatted {count} education entries.")

    if args.dry_run:
        print("\n=== DRY RUN — sample legal summaries ===")
        shown = 0
        for row in rows:
            if row.get("legal_summary_short", "").strip() and shown < 15:
                print(f"  IN:  {row['criminal_cases_summary'][:120]}...")
                print(f"  OUT: {row['legal_summary_short']}")
                print()
                shown += 1

        print("=== DRY RUN — sample education formatting ===")
        shown = 0
        for row in rows:
            if row.get("education_formatted", "").strip() and shown < 15:
                print(f"  IN:  {row['education_details_clean'][:120]}")
                print(f"  OUT: {row['education_formatted']}")
                print()
                shown += 1
        return

    with CSV_PATH.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} rows to {CSV_PATH.name}")


if __name__ == "__main__":
    main()
