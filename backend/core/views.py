import csv
import json
import difflib
import re
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from django.conf import settings
from django.db.models import Prefetch, Q
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from rest_framework import viewsets, filters

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import (
    Candidate,
    CandidateResult,
    CoalitionMembership,
    Constituency,
    Election,
    Feedback,
    Manifesto,
    ManifestoPromise,
    Party,
    PartyFulfilmentClaim,
    PromiseAssessment,
)
from .serializers import CandidateSerializer, ConstituencySerializer, ManifestoSerializer, PartySerializer
from .tamil_translations import (
    COLUMN_LABEL_TA,
    EDUCATION_TA,
    PARTY_NAME_TA,
    PROMISE_CATEGORY_TA,
    RESOURCE_CATEGORY_TA,
    RESOURCE_TITLE_TA,
    STATUS_TA,
    GENDER_TA,
)
from .templatetags.indian_numbers import short_indian


def robots_txt(request):
    lines = [
        "User-agent: *",
        "Allow: /",
        "Disallow: /admin/",
        "Disallow: /api/",
        "Disallow: /set-lang/",
        "Disallow: /feedback/",
        "Disallow: /map/data/",
        "",
        "Sitemap: https://tntracker.in/sitemap.xml",
    ]
    return HttpResponse("\n".join(lines), content_type="text/plain")


def home(request):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"
    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("fct_candidates_26.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(str(csv_path))
    stats = _compute_overview_stats(rows)

    return render(request, "core/home.html", {
        "year": year,
        "total_parties": stats["total_parties"],
        "total_candidates": stats["total_candidates"],
        "overall_avg_cases": stats["overall_avg_cases"],
        "overall_avg_age": stats["overall_avg_age"],
        "overall_avg_assets": stats["overall_avg_assets"],
        "overall_avg_expenditure": stats["overall_avg_expenditure"],
    })


@csrf_exempt
@require_POST
def submit_feedback(request):
    """Accept anonymous feedback from the floating widget."""
    ease_raw = request.POST.get("ease_of_use")
    ease_of_use = int(ease_raw) if ease_raw and ease_raw.isdigit() and 1 <= int(ease_raw) <= 5 else None
    Feedback.objects.create(
        ease_of_use=ease_of_use,
        helps_inform=request.POST.get("helps_inform", "")[:20],
        would_return=request.POST.get("would_return", "")[:20],
        suggestion=request.POST.get("suggestion", "")[:2000],
        page_url=request.POST.get("page_url", "")[:500],
    )
    return JsonResponse({"ok": True})


def resources(request):
    """External resources page with curated links to election data sources."""
    resources_list = [
        {
            "title": "MyNeta - Tamil Nadu 2026 Candidate Affidavits",
            "title_ta": RESOURCE_TITLE_TA.get("MyNeta - Tamil Nadu 2026 Candidate Affidavits", ""),
            "url": "https://myneta.info/TamilNadu2026/",
            "description": "Full affidavit data for all 2026 Tamil Nadu assembly election candidates — criminal cases, assets, liabilities, education and income tax details, constituency by constituency.",
            "description_ta": "2026 தமிழ்நாடு சட்டமன்றத் தேர்தல் வேட்பாளர்கள் அனைவரின் முழுமையான வாக்குமூலத் தரவு — குற்ற வழக்குகள், சொத்துக்கள், கடன்கள், கல்வி மற்றும் வருமான வரி விவரங்கள், தொகுதி வாரியாக.",
            "category": "Affidavits",
            "category_ta": RESOURCE_CATEGORY_TA.get("Affidavits", ""),
        },
        {
            "title": "MyNeta - Tamil Nadu Assembly Elections",
            "title_ta": RESOURCE_TITLE_TA.get("MyNeta - Tamil Nadu Assembly Elections", ""),
            "url": "https://myneta.info/state_assembly.php?state=Tamil%20Nadu",
            "description": "Candidate affidavit data from Election Commission archives, including criminal records, assets, liabilities, and educational qualifications for all candidates.",
            "description_ta": "தேர்தல் ஆணையக் காப்பகங்களிலிருந்து வேட்பாளர் வாக்குறுதி தரவு, குற்றப் பதிவுகள், சொத்துக்கள், கடன்கள் மற்றும் கல்வித் தகுதிகள் உள்ளிட்டவை.",
            "category": "Affidavits",
            "category_ta": RESOURCE_CATEGORY_TA.get("Affidavits", ""),
        },
        {
            "title": "MyNeta - Asset Comparison of Re-contesting Winners",
            "title_ta": RESOURCE_TITLE_TA.get("MyNeta - Asset Comparison of Re-contesting Winners", ""),
            "url": "https://myneta.info/TamilNadu2021/index.php?action=recontestAssetsComparison",
            "description": "Asset comparison of 143 MLAs who won in both 2016 and 2021, showing how their wealth changed over the five-year period between elections.",
            "description_ta": "2016 மற்றும் 2021 இரண்டிலும் வெற்றி பெற்ற 143 சட்டமன்ற உறுப்பினர்களின் சொத்து ஒப்பீடு, ஐந்தாண்டு காலத்தில் அவர்களின் செல்வம் எவ்வாறு மாறியது என்பதைக் காட்டுகிறது.",
            "category": "Affidavits",
            "category_ta": RESOURCE_CATEGORY_TA.get("Affidavits", ""),
        },
        {
            "title": "MLA Election Expenditure Analysis 2021 (ADR)",
            "title_ta": RESOURCE_TITLE_TA.get("MLA Election Expenditure Analysis 2021 (ADR)", ""),
            "url": "https://adrindia.org/sites/default/files/Analysis_of_Election_Expenditure_Statements_of_MLA_Tamil_Nadu_Assembly_2021_English.pdf",
            "description": "Association for Democratic Reforms analysis of election expenditure statements filed by MLAs in Tamil Nadu 2021, including party-wise spending patterns.",
            "description_ta": "தமிழ்நாடு 2021-ல் சட்டமன்ற உறுப்பினர்கள் தாக்கல் செய்த தேர்தல் செலவு அறிக்கைகளின் ஜனநாயக சீர்திருத்த சங்கம் பகுப்பாய்வு.",
            "category": "Expenditure",
            "category_ta": RESOURCE_CATEGORY_TA.get("Expenditure", ""),
        },
        {
            "title": "Tamil Nadu Economic Survey 2025-26",
            "title_ta": RESOURCE_TITLE_TA.get("Tamil Nadu Economic Survey 2025-26", ""),
            "url": "https://tamildigitallibrary.in/assets/docs/uploads/catalogue_article_file/BOK/upload/2026/02/TVA_TVA_BOK_062955/upload_primary_20260216113955411_20260216113539.pdf",
            "description": "Official economic survey of Tamil Nadu for 2025-26, covering the state's economic performance, sectoral analysis, and development indicators.",
            "description_ta": "தமிழ்நாட்டின் 2025-26 அதிகாரப்பூர்வ பொருளாதார ஆய்வு, மாநிலத்தின் பொருளாதார செயல்திறன், துறை வாரியான பகுப்பாய்வு மற்றும் வளர்ச்சி குறியீடுகள்.",
            "category": "Economy",
            "category_ta": RESOURCE_CATEGORY_TA.get("Economy", ""),
        },
        {
            "title": "NITI Aayog - Tamil Nadu Fiscal Landscape",
            "title_ta": RESOURCE_TITLE_TA.get("NITI Aayog - Tamil Nadu Fiscal Landscape", ""),
            "url": "https://www.niti.gov.in/sites/default/files/2025-03/Macro-and-Fiscal-Landscape-of-the-State-of-Tamil-Nadu.pdf",
            "description": "NITI Aayog report on Tamil Nadu's macroeconomic and fiscal landscape, providing context on state finances and development indicators.",
            "description_ta": "தமிழ்நாட்டின் பொருளாதார மற்றும் நிதி நிலப்பரப்பு குறித்த நிதி ஆயோக் அறிக்கை, மாநில நிதி மற்றும் வளர்ச்சி குறியீடுகள் பற்றிய சூழல்.",
            "category": "Economy",
            "category_ta": RESOURCE_CATEGORY_TA.get("Economy", ""),
        },
        {
            "title": "PRS - Tamil Nadu Budget Analysis 2025-26",
            "title_ta": RESOURCE_TITLE_TA.get("PRS - Tamil Nadu Budget Analysis 2025-26", ""),
            "url": "https://prsindia.org/budgets/states/tamil-nadu-budget-analysis-2025-26",
            "description": "PRS Legislative Research analysis of Tamil Nadu's 2025-26 state budget, covering expenditure, receipts, deficit targets, and key policy initiatives.",
            "description_ta": "தமிழ்நாட்டின் 2025-26 மாநில வரவு செலவுத் திட்டத்தின் PRS சட்டமன்ற ஆராய்ச்சி பகுப்பாய்வு, செலவுகள், வருவாய், பற்றாக்குறை இலக்குகள் மற்றும் முக்கிய கொள்கை முன்முயற்சிகள்.",
            "category": "Economy",
            "category_ta": RESOURCE_CATEGORY_TA.get("Economy", ""),
        },
        {
            "title": "16th Tamil Nadu Legislative Assembly Members",
            "title_ta": RESOURCE_TITLE_TA.get("16th Tamil Nadu Legislative Assembly Members", ""),
            "url": "https://assembly.tn.gov.in/16thassembly/members.php",
            "description": "Official list of current MLAs from the Tamil Nadu Legislative Assembly website, with contact details and constituency information.",
            "description_ta": "தமிழ்நாடு சட்டமன்ற இணையதளத்திலிருந்து தற்போதைய சட்டமன்ற உறுப்பினர்களின் அதிகாரப்பூர்வ பட்டியல், தொடர்பு விவரங்கள் மற்றும் தொகுதி தகவல்கள்.",
            "category": "Official",
            "category_ta": RESOURCE_CATEGORY_TA.get("Official", ""),
        },
        {
            "title": "PRS - Profile of 16th Tamil Nadu Assembly",
            "title_ta": RESOURCE_TITLE_TA.get("PRS - Profile of 16th Tamil Nadu Assembly", ""),
            "url": "https://prsindia.org/legislatures/state/vital-stats/profile-of-the-16th-tamil-nadu-legislative-assembly",
            "description": "PRS Legislative Research profile of the 16th Tamil Nadu Assembly, covering MLA demographics, party composition, gender representation, education, and age distribution.",
            "description_ta": "16-ஆவது தமிழ்நாடு சட்டமன்றத்தின் PRS சட்டமன்ற ஆராய்ச்சி விவரக்குறிப்பு, சட்டமன்ற உறுப்பினர்களின் மக்கள்தொகை, கட்சி அமைப்பு, பாலின பிரதிநிதித்துவம், கல்வி மற்றும் வயது விநியோகம்.",
            "category": "Analytics",
            "category_ta": RESOURCE_CATEGORY_TA.get("Analytics", ""),
        },
        {
            "title": "IndiaVotes - Tamil Nadu 2021 Results",
            "title_ta": RESOURCE_TITLE_TA.get("IndiaVotes - Tamil Nadu 2021 Results", ""),
            "url": "https://www.indiavotes.com/vidhan-sabha/2021/tamil-nadu/283/40",
            "description": "Comprehensive assembly-wise election results for Tamil Nadu 2021, including vote counts and margins for all constituencies.",
            "description_ta": "தமிழ்நாடு 2021 தொகுதி வாரியான தேர்தல் முடிவுகள், வாக்கு எண்ணிக்கை மற்றும் வெற்றி வித்தியாசம் உள்ளிட்ட தகவல்கள்.",
            "category": "Results",
            "category_ta": RESOURCE_CATEGORY_TA.get("Results", ""),
        },
        {
            "title": "Tamil Nadu Assembly Elections Visual Analytics",
            "title_ta": RESOURCE_TITLE_TA.get("Tamil Nadu Assembly Elections Visual Analytics", ""),
            "url": "https://data-analytics.github.io/Election_Data/tamil_nadu.html?extra_year=2021#individual_hold",
            "description": "Interactive visualizations of Tamil Nadu assembly elections from 1967 to 2021, with party-wise trends and constituency-level analysis.",
            "description_ta": "1967 முதல் 2021 வரையிலான தமிழ்நாடு சட்டமன்றத் தேர்தல்களின் ஊடாடும் காட்சிப்படுத்தல்கள், கட்சி வாரியான போக்குகள் மற்றும் தொகுதி நிலை பகுப்பாய்வு.",
            "category": "Analytics",
            "category_ta": RESOURCE_CATEGORY_TA.get("Analytics", ""),
        },
    ]
    return render(request, "core/resources.html", {"resources": resources_list})


def map_view(request):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"
    return render(request, "core/map.html", {
        "year": year,
        "party_label_ta_json": json.dumps(PARTY_NAME_TA, ensure_ascii=False),
    })


@lru_cache(maxsize=2)
def _load_smla_rows(year: str = "2026") -> list[dict]:
    data_dir = settings.BASE_DIR.parent / "data"
    if year == "2026":
        csv_path = data_dir / "fct_candidates_26.csv"
    else:
        csv_path = data_dir / "fct_candidates_21.csv"
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


@lru_cache(maxsize=1)
def _constituency_district_lookup() -> dict[str, str]:
    """Normalized constituency name -> district name from DB (used for 2026 data lacking district col)."""
    lookup = {}
    for name, district in Constituency.objects.values_list("name", "district"):
        key = _normalize_constituency_name(name)
        if key and district:
            lookup[key] = district.strip()
    return lookup


def _get_district_for_row(row: dict, year: str) -> str:
    """Return the district for a CSV row, deriving from DB for 2026 which lacks a district column."""
    if year == "2021":
        return _row_value(row, ("2021_district", "district"))
    # 2026: try the row first, then fall back to DB lookup
    direct = (row.get("district") or "").strip()
    if direct:
        return direct
    constituency = (row.get("constituency") or "").strip()
    if not constituency:
        return ""
    key = _normalize_constituency_name(constituency)
    return _constituency_district_lookup().get(key, "")


_CONSTITUENCY_ALIASES = {
    # ECI 2026 name → GeoJSON/DB canonical name (after normalization)
    "CHEPAUK THIRUVALLIKENI": "CHEPAUK THIRUVALLIKEN",
    "COLACHAL": "COLACHEL",
    "PAPPIREDDIPATTI": "PAPPIREDDIPPATTI",
    "THIRUVOTTIYUR": "TIRUVOTTIYUR",
    "TIRUPPATTUR": "TIRUPATTUR",
    "TIRUCHIRAPPALLI EAST": "TIRUCHIRAPPALLI",
}


def _normalize_constituency_name(name: Optional[str]) -> str:
    raw = (name or "").strip().upper()
    raw = re.sub(r"\s*:\s*BYE ELECTION.*$", "", raw)
    normalized = re.sub(r"[^A-Z0-9]+", " ", raw)
    normalized = re.sub(r"\s+", " ", normalized).strip()
    # Strip trailing reservation markers (SC/ST) so "ARAKKONAM SC" == "ARAKKONAM"
    normalized = re.sub(r"\s+(SC|ST)$", "", normalized)
    return _CONSTITUENCY_ALIASES.get(normalized, normalized)


def _is_2016_row(row: dict) -> bool:
    candidate_name = (row.get("candidate") or "").strip().lower()
    constituency_name = _normalize_constituency_name(
        row.get("2021_constituency") or row.get("constituency")
    )
    return candidate_name == "ambethkumar s" and constituency_name == "VANDAVASI"


@lru_cache(maxsize=1)
def _load_party_symbol_map() -> dict[str, str]:
    symbols_path = Path(__file__).resolve().parent / "static" / "core" / "party-symbols" / "party_symbols.json"
    if not symbols_path.exists():
        return {}
    try:
        return json.loads(symbols_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}

@lru_cache(maxsize=1)
def _load_official_constituencies() -> dict[str, str]:
    csv_path = settings.BASE_DIR.parent / "data" / "fct_candidates_21.csv"
    if not csv_path.exists():
        return {}
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        mapping: dict[str, str] = {}
        for row in reader:
            raw = (row.get("2021_constituency") or "").strip()
            official = (row.get("const_off") or "").strip()
            key = _normalize_constituency_name(raw)
            if key and official and key not in mapping:
                mapping[key] = official
        return mapping


def _match_constituency_key(key: str, candidates: set[str], cutoff: float = 0.9) -> str:
    if key in candidates:
        return key
    for suffix in (" SC", " ST"):
        if key.endswith(suffix):
            trimmed = key[: -len(suffix)].strip()
            if trimmed in candidates:
                return trimmed
        else:
            expanded = f"{key}{suffix}"
            if expanded in candidates:
                return expanded
    matches = difflib.get_close_matches(key, candidates, n=1, cutoff=cutoff)
    return matches[0] if matches else key


def _resolve_constituency_key(
    raw_key: str,
    district_key: str,
    constituency_seen: set[str],
    district_candidates: dict[str, set[str]],
    alias_by_district: dict[str, dict[str, str]],
    cutoff: float = 0.85,
) -> str:
    if district_key and district_key in alias_by_district:
        mapped = alias_by_district[district_key].get(raw_key)
        if mapped:
            return mapped
    if raw_key in alias_by_district.get("", {}):
        mapped = alias_by_district[""].get(raw_key)
        if mapped:
            return mapped
    if district_key and district_key in district_candidates:
        return _match_constituency_key(raw_key, district_candidates[district_key], cutoff=cutoff)
    return _match_constituency_key(raw_key, constituency_seen, cutoff=cutoff)


def _format_indian_number(value: Optional[float]) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        value = round(value, 2) if value % 1 else int(value)
    text = str(abs(int(value))) if isinstance(value, int) or float(value).is_integer() else str(value)
    negative = str(value).startswith("-")
    if "." in text:
        integer_part, decimal_part = text.split(".", 1)
    else:
        integer_part, decimal_part = text, ""
    if len(integer_part) > 3:
        last_three = integer_part[-3:]
        rest = integer_part[:-3]
        groups = []
        while rest:
            groups.insert(0, rest[-2:])
            rest = rest[:-2]
        integer_part = ",".join(groups + [last_three])
    formatted = integer_part
    if decimal_part:
        decimal_part = decimal_part.rstrip("0")
        if decimal_part:
            formatted = f"{integer_part}.{decimal_part}"
    return f"-{formatted}" if negative else formatted


def _display_party_name(party_name: str) -> str:
    cleaned = (party_name or "").strip()
    if cleaned == "IND":
        return "Independent"
    return cleaned


@lru_cache(maxsize=1)
def _load_names_ta() -> dict:
    """Load the Tamil name translations from data/names_ta.json."""
    names_path = Path(settings.BASE_DIR).parent / "data" / "names_ta.json"
    if names_path.exists():
        with open(names_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _get_tamil_name(english_name: str) -> str:
    """Look up Tamil transliteration for a name. Falls back to English."""
    ta_map = _load_names_ta()
    return ta_map.get(english_name) or ta_map.get(english_name.title()) or english_name


def _display_party_name_ta(party_name: str) -> str:
    """Return Tamil display name for a party."""
    cleaned = (party_name or "").strip()
    # Check static translations first
    ta = PARTY_NAME_TA.get(cleaned)
    if ta:
        return ta
    # Check DB-based translations
    ta_map = _load_names_ta()
    ta = ta_map.get(cleaned)
    if ta:
        return ta
    # Fallback to English
    if cleaned == "IND":
        return "சுயேச்சை"
    return cleaned


PROMINENT_PARTIES = {
    "DMK", "Dravida Munnetra Kazhagam",
    "AIADMK", "All India Anna Dravida Munnetra Kazhagam",
    "BJP", "Bharatiya Janata Party",
    "INC", "Indian National Congress",
    "CPI", "Communist Party of India",
    "CPI(M)", "Communist Party of India (Marxist)",
    "Communist Party of India (Marxist-Leninist) (Liberation)",
    "Pattali Makkal Katchi", "PMK",
    "Viduthalai Chiruthaigal Katchi", "VCK",
    "Naam Tamilar Katchi", "NTK",
    "Tamilaga Vettri Kazhagam", "TVK",
    "Makkal Needhi Maiam", "MNM",
    "Desiya Murpokku Dravida Kazhagam", "DMDK",
    "Amma Makkal Munnettra Kazagam", "AMMK",
}

PARTY_ALIASES: dict[str, list[str]] = {
    "DMK": ["Dravida Munnetra Kazhagam"],
    "AIADMK": ["All India Anna Dravida Munnetra Kazhagam"],
    "BJP": ["Bharatiya Janata Party"],
    "INC": ["Indian National Congress", "Congress"],
    "Pattali Makkal Katchi": ["PMK"],
    "Viduthalai Chiruthaigal Katchi": ["VCK"],
    "CPI": ["Communist Party of India"],
    "CPI(M)": ["Communist Party of India (Marxist)", "CPIM"],
    "CPI(ML)(L)": ["Communist Party of India (Marxist-Leninist) (Liberation)"],
    "DMDK": ["Desiya Murpokku Dravida Kazhagam"],
    "Amma Makkal Munnettra Kazagam": ["AMMK"],
    "Makkal Needhi Maiam": ["MNM"],
    "Naam Tamilar Katchi": ["NTK"],
    "Tamilaga Vettri Kazhagam": ["TVK"],
    "Dravida Munnetra Kazhagam": ["DMK"],
    "All India Anna Dravida Munnetra Kazhagam": ["AIADMK"],
    "Bharatiya Janata Party": ["BJP"],
    "Indian National Congress": ["INC", "Congress"],
    "Communist Party of India (Marxist-Leninist) (Liberation)": ["CPI(ML)(L)", "CPIML"],
}

CANDIDATE_ALIASES: dict[str, list[str]] = {
    "O.Panneerselvam": ["OPS", "O Panneerselvam"],
    "Palaniswami K": ["EPS", "Edappadi Palaniswami", "Edappadi K Palaniswami"],
    "M.K. Stalin": ["MKS", "Stalin"],
    "Udhayanidhi Stalin": ["Udhayanidhi"],
}

PARTY_COLORS = {
    "DMK": "#D7263D",
    "Dravida Munnetra Kazhagam": "#D7263D",
    "AIADMK": "#2E8B57",
    "All India Anna Dravida Munnetra Kazhagam": "#2E8B57",
    "BJP": "#E4572E",
    "Bharatiya Janata Party": "#E4572E",
    "INC": "#1D4ED8",
    "Indian National Congress": "#1D4ED8",
    "Pattali Makkal Katchi": "#D4A017",
    "PMK": "#D4A017",
    "Viduthalai Chiruthaigal Katchi": "#5B2A86",
    "VCK": "#5B2A86",
    "CPI": "#C1121F",
    "Communist Party of India": "#C1121F",
    "CPI(M)": "#8A1C1C",
    "Communist Party of India (Marxist)": "#8A1C1C",
    "CPI(ML)(L)": "#8A1C1C",
    "DMDK": "#F59E0B",
    "Desiya Murpokku Dravida Kazhagam": "#F59E0B",
    "Amma Makkal Munnettra Kazagam": "#059669",
    "Makkal Needhi Maiam": "#0E7490",
    "Naam Tamilar Katchi": "#6D28D9",
    "TVK": "#EAB308",
    "Tamilaga Vettri Kazhagam": "#EAB308",
    "IUML": "#166534",
    "Indian Union Muslim League": "#166534",
    "IND": "#64748B",
    "Independent": "#64748B",
}

# Short display names for the map legend (2026 CSV carries full party names).
PARTY_ABBR = {
    "Tamilaga Vettri Kazhagam": "TVK",
    "Dravida Munnetra Kazhagam": "DMK",
    "All India Anna Dravida Munnetra Kazhagam": "AIADMK",
    "Indian National Congress": "INC",
    "Pattali Makkal Katchi": "PMK",
    "Communist Party of India": "CPI",
    "Communist Party of India (Marxist)": "CPI(M)",
    "Viduthalai Chiruthaigal Katchi": "VCK",
    "Indian Union Muslim League": "IUML",
    "Bharatiya Janata Party": "BJP",
    "Desiya Murpokku Dravida Kazhagam": "DMDK",
    "Amma Makkal Munnettra Kazagam": "AMMK",
    "Naam Tamilar Katchi": "NTK",
    "Independent": "IND",
}

LOCAL_PARTY_SYMBOLS = {
    # DMK
    "dmk": "dmk.png",
    "dravida munnetra kazhagam": "dmk.png",
    # AIADMK
    "aiadmk": "aiadmk_final.png",
    "all india anna dravida munnetra kazhagam": "aiadmk_final.png",
    # BJP
    "bjp": "bjp.png",
    "bharatiya janata party": "bjp.png",
    # INC
    "inc": "inc.png",
    "indian national congress": "inc.png",
    # NTK
    "ntk": "ntk_final.png",
    "naam tamilar katchi": "ntk_final.png",
    # TVK
    "tvk": "tvk_symbol_final.jpeg",
    "tamilaga vettri kazhagam": "tvk_symbol_final.jpeg",
    # PMK
    "pmk": "pmk.svg",
    "pattali makkal katchi": "pmk.svg",
    # CPI
    "cpi": "cpi.svg",
    "communist party of india": "cpi.svg",
    # CPI(M)
    "cpi(m)": "cpim.svg",
    "cpi m": "cpim.svg",
    "cpim": "cpim.svg",
    "communist party of india (marxist)": "cpim.svg",
    # VCK
    "vck": "vck.svg",
    "viduthalai chiruthaigal katchi": "vck.svg",
    # MNM
    "mnm": "mnm.svg",
    "makkal needhi maiam": "mnm.svg",
    # AMMK
    "ammk": "ammk.svg",
    "amma makkal munnettra kazagam": "ammk.svg",
    # DMDK
    "dmdk": "dmdk.svg",
    "desiya murpokku dravida kazhagam": "dmdk.svg",
    # Independent
    "ind": "independent.svg",
    "independent": "independent.svg",
}


def _party_color(party_name: Optional[str]) -> Optional[str]:
    if not party_name:
        return None
    return PARTY_COLORS.get(party_name.strip())


def _party_symbol_url(party_name: Optional[str]) -> Optional[str]:
    if not party_name:
        return None
    key = party_name.strip().lower()
    filename = LOCAL_PARTY_SYMBOLS.get(key)
    if filename:
        return f"/static/core/party-symbols/{filename}"
    return None


def _candidate_count_color(count: int) -> str:
    if count == 0:
        return "#E2E8F0"
    if count <= 2:
        return "#BFDBFE"
    if count <= 5:
        return "#60A5FA"
    return "#2563EB"


def map_data(request):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"

    if year == "2026":
        return _map_data_2026(request)
    return _map_data_2021(request)


# 2026 CSV (ECI spellings) -> Constituency DB spellings, for map keying.
_MAP_ALIASES_2026 = {
    "PALACODU": "PALACODE",
    "SHOZHINGANALLUR": "SHOLINGANALLUR",
    "VANDAVASI": "VANDAVASI SC",
}


def _map_data_2026(request):
    """Post-election map: constituencies coloured by the winning party."""
    rows = _load_smla_rows("2026")
    winner_lookup: dict[str, dict] = {}
    constituency_seen: set[str] = set()
    seat_counts: Counter = Counter()
    for row in rows:
        key = _normalize_constituency_name(row.get("constituency"))
        key = _MAP_ALIASES_2026.get(key, key)
        if not key:
            continue
        constituency_seen.add(key)
        if str(row.get("is_winner", "")).strip() != "1":
            continue
        party_name = (row.get("party") or "").strip()
        winner_lookup[key] = {
            "party": party_name,
            "party_color": _party_color(party_name),
        }
        seat_counts[party_name] += 1

    features = []
    for constituency in Constituency.objects.exclude(boundary_geojson__isnull=True):
        raw_key = _normalize_constituency_name(constituency.name)
        lookup = winner_lookup.get(raw_key)
        if lookup is None:
            # fuzzy fallback for residual spelling drift between DB and ECI names
            match = difflib.get_close_matches(raw_key, list(winner_lookup.keys()), n=1, cutoff=0.85)
            lookup = winner_lookup.get(match[0]) if match else None
        lookup = lookup or {}
        is_unknown = not lookup
        features.append({
            "type": "Feature",
            "properties": {
                "id": constituency.id,
                "name": constituency.name,
                "name_ta": constituency.name_ta or "",
                "district": constituency.district or "",
                "district_ta": constituency.district_ta or "",
                "party": lookup.get("party", ""),
                "party_color": lookup.get("party_color"),
                "vacant": False,
                "unknown": is_unknown,
            },
            "geometry": constituency.boundary_geojson,
        })
    legend = [
        {
            "party": PARTY_ABBR.get(party, party),
            "party_ta": _display_party_name_ta(party) or "",
            "color": _party_color(party) or "#64748B",
        }
        for party, _count in seat_counts.most_common()
        if party
    ]
    return JsonResponse({"type": "FeatureCollection", "features": features, "legend": legend, "year": "2026"})


def _map_data_2021(request):
    rows = _load_smla_rows("2021")
    sitting_lookup: dict[str, dict] = {}
    constituency_seen: set[str] = set()
    official_lookup = _load_official_constituencies()
    district_lookup: dict[str, str] = {}
    district_candidates: dict[str, set[str]] = defaultdict(set)
    alias_by_district: dict[str, dict[str, str]] = defaultdict(dict)
    for row in rows:
        constituency_key = _normalize_constituency_name(row.get("2021_constituency"))
        district_key = _normalize_constituency_name(row.get("2021_district"))
        if constituency_key:
            constituency_seen.add(constituency_key)
            if district_key:
                district_candidates[district_key].add(constituency_key)
                district_lookup.setdefault(constituency_key, (row.get("2021_district") or "").strip())
            official_key = _normalize_constituency_name(row.get("const_off"))
            if official_key:
                alias_by_district[district_key][official_key] = constituency_key
                alias_by_district[""].setdefault(official_key, constituency_key)
        if str(row.get("sitting_MLA", "")).strip() != "1":
            continue
        party_name = (row.get("party") or "").strip()
        sitting_lookup[constituency_key] = {
            "party": party_name,
            "party_color": _party_color(party_name),
        }

    # Explicit spelling aliases for map/CSV mismatches.
    explicit_aliases = {
        "PALACODU": "PALACODE",
        "THALLI": "THALLY",
        "SHOZHINGANALLUR": "SHOLINGANALLUR",
        "VANDAVASI": "VANDAVASI SC",
        "VANDAVASI SC": "VANDAVASI SC",
    }
    for raw, mapped in explicit_aliases.items():
        alias_by_district[""].setdefault(raw, mapped)
        for district_key in district_candidates.keys():
            alias_by_district[district_key].setdefault(raw, mapped)
    explicit_district_aliases = {
        "TIRUVANNAMALAI": {"VANDAVASI SC": "VANDAVASI SC", "VANDAVASI": "VANDAVASI SC"},
        "TIRUPATHUR": {"TIRUPPATTUR": "TIRUPATTUR"},
        "SIVAGANGA": {"TIRUPPATTUR": "TIRUPPATHUR"},
    }
    for district_key, mapping in explicit_district_aliases.items():
        for raw, mapped in mapping.items():
            alias_by_district[district_key].setdefault(raw, mapped)

    features = []
    for constituency in Constituency.objects.exclude(boundary_geojson__isnull=True):
        raw_key = _normalize_constituency_name(constituency.name)
        district_key = _normalize_constituency_name(constituency.district)
        constituency_key = _resolve_constituency_key(
            raw_key,
            district_key,
            constituency_seen,
            district_candidates,
            alias_by_district,
            cutoff=0.85,
        )
        lookup = sitting_lookup.get(constituency_key, {})
        is_vacant = constituency_key in constituency_seen and not lookup
        is_unknown = constituency_key not in constituency_seen
        official_name = official_lookup.get(constituency_key) or official_lookup.get(raw_key) or constituency.name
        display_district = constituency.district or district_lookup.get(constituency_key, "")
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": constituency.id,
                    "name": official_name,
                    "name_ta": constituency.name_ta or "",
                    "district": display_district,
                    "district_ta": constituency.district_ta or "",
                    "party": lookup.get("party", ""),
                    "party_color": lookup.get("party_color"),
                    "vacant": is_vacant,
                    "unknown": is_unknown,
                },
                "geometry": constituency.boundary_geojson,
            }
        )
    legend = [
        {"party": party, "color": color}
        for party, color in sorted(
            {(value["party"], value["party_color"]) for value in sitting_lookup.values() if value.get("party")},
            key=lambda item: item[0],
        )
        if color
    ]
    return JsonResponse({"type": "FeatureCollection", "features": features, "legend": legend, "year": "2021"})


def _calculate_bounds(boundary_geojson):
    """Extract bounding box from GeoJSON geometry."""
    if not boundary_geojson:
        return None
    try:
        coords = []
        
        def extract_coords(obj):
            if isinstance(obj, list):
                if len(obj) >= 2 and isinstance(obj[0], (int, float)) and isinstance(obj[1], (int, float)):
                    coords.append((obj[1], obj[0]))  # lat, lng
                else:
                    for item in obj:
                        extract_coords(item)
        
        if "coordinates" in boundary_geojson:
            extract_coords(boundary_geojson["coordinates"])
        
        if coords:
            lats = [c[0] for c in coords]
            lngs = [c[1] for c in coords]
            return [[min(lats), min(lngs)], [max(lats), max(lngs)]]
    except (KeyError, TypeError, ValueError):
        pass
    return None


def _fuzzy_match_score(query: str, target: str, threshold: float = 0.6) -> float:
    """
    Calculate similarity score between query and target string.
    Returns a score between 0 and 1, where 1 is exact match.
    Uses a combination of substring matching and character-level similarity.
    """
    if not query or not target:
        return 0.0
    
    query = query.lower().strip()
    target = target.lower().strip()
    
    # Exact match
    if query == target:
        return 1.0
    
    # Query is substring of target (starts with gets higher score)
    if target.startswith(query):
        return 0.95
    if query in target:
        return 0.85
    
    # Target is substring of query
    if query.startswith(target):
        return 0.9
    if target in query:
        return 0.8
    
    # Use difflib for fuzzy matching
    ratio = difflib.SequenceMatcher(None, query, target).ratio()
    
    # Also check if words match
    query_words = set(query.split())
    target_words = set(target.split())
    if query_words & target_words:  # Any common words
        ratio = max(ratio, 0.7)
    
    return ratio if ratio >= threshold else 0.0


def map_search(request):
    """Return constituencies matching search query for map autocomplete with fuzzy matching."""
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})
    
    query_lower = query.lower()
    query_normalized = _normalize_constituency_name(query)
    
    constituencies = Constituency.objects.exclude(boundary_geojson__isnull=True)
    
    # Build list of all searchable items with scores
    scored_results = []
    
    # Track districts for district-level results
    district_constituencies: dict[str, list] = {}
    
    for constituency in constituencies:
        # Score against constituency name
        name_score = max(
            _fuzzy_match_score(query_lower, (constituency.name or "").lower()),
            _fuzzy_match_score(query_normalized, _normalize_constituency_name(constituency.name)),
        )
        
        # Score against Tamil name
        name_ta_score = _fuzzy_match_score(query_lower, (constituency.name_ta or "").lower())
        
        # Score against district
        district = (constituency.district or "").strip()
        district_score = _fuzzy_match_score(query_lower, district.lower())
        district_ta_score = _fuzzy_match_score(query_lower, (constituency.district_ta or "").lower())
        
        # Best score for this constituency
        best_score = max(name_score, name_ta_score, district_score, district_ta_score)
        
        if best_score > 0:
            # Determine match type
            if name_score >= district_score and name_score >= district_ta_score:
                match_type = "name"
            elif name_ta_score >= district_score and name_ta_score >= district_ta_score:
                match_type = "name"
            else:
                match_type = "district"
            
            scored_results.append({
                "id": constituency.id,
                "name": constituency.name,
                "name_ta": constituency.name_ta or "",
                "district": district,
                "district_ta": constituency.district_ta or "",
                "bounds": _calculate_bounds(constituency.boundary_geojson),
                "match_type": match_type,
                "score": best_score,
                "is_district_result": False,
            })
        
        # Group by district for district-level results
        if district:
            if district not in district_constituencies:
                district_constituencies[district] = []
            district_constituencies[district].append(constituency)
    
    # Add district-level results (shows "X constituencies in District")
    for district, const_list in district_constituencies.items():
        district_lower = district.lower()
        district_score = _fuzzy_match_score(query_lower, district_lower)
        
        # Also check Tamil district name from first constituency
        district_ta = const_list[0].district_ta or "" if const_list else ""
        district_ta_score = _fuzzy_match_score(query_lower, district_ta.lower())
        
        best_district_score = max(district_score, district_ta_score)
        
        if best_district_score >= 0.7:  # Higher threshold for district-level results
            # Calculate combined bounds for all constituencies in district
            all_bounds = [_calculate_bounds(c.boundary_geojson) for c in const_list]
            all_bounds = [b for b in all_bounds if b]
            
            if all_bounds:
                min_lat = min(b[0][0] for b in all_bounds)
                min_lng = min(b[0][1] for b in all_bounds)
                max_lat = max(b[1][0] for b in all_bounds)
                max_lng = max(b[1][1] for b in all_bounds)
                combined_bounds = [[min_lat, min_lng], [max_lat, max_lng]]
            else:
                combined_bounds = None
            
            scored_results.append({
                "id": None,  # District-level result, no single ID
                "name": f"{district} District",
                "name_ta": f"{district_ta} மாவட்டம்" if district_ta else "",
                "district": district,
                "district_ta": district_ta,
                "bounds": combined_bounds,
                "match_type": "district_group",
                "score": best_district_score + 0.1,  # Slight boost for district groups
                "is_district_result": True,
                "constituency_count": len(const_list),
            })
    
    # Sort by score (descending), then by match type, then alphabetically
    def sort_key(item):
        # Priority: higher score first, then name matches, then district matches
        type_priority = {"name": 0, "district_group": 1, "district": 2}
        return (
            -item["score"],
            type_priority.get(item["match_type"], 3),
            item["name"].lower(),
        )
    
    scored_results.sort(key=sort_key)
    
    # Remove duplicates (keep highest scored version)
    seen_ids = set()
    seen_districts = set()
    unique_results = []
    
    for result in scored_results:
        if result["is_district_result"]:
            if result["district"] not in seen_districts:
                seen_districts.add(result["district"])
                unique_results.append(result)
        else:
            if result["id"] not in seen_ids:
                seen_ids.add(result["id"])
                unique_results.append(result)
    
    # Limit results
    unique_results = unique_results[:15]
    
    # Clean up internal fields before returning
    for result in unique_results:
        result.pop("score", None)
        result.pop("is_district_result", None)
    
    return JsonResponse({"results": unique_results})


def party_dashboard_search(request):
    """Return parties and candidates matching search query for party dashboard autocomplete."""
    query = request.GET.get("q", "").strip()
    if len(query) < 2:
        return JsonResponse({"results": []})

    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"

    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("fct_candidates_26.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(str(csv_path))
    if not rows:
        return JsonResponse({"results": []})

    query_lower = query.lower()
    district_key = ("2021_district", "district")
    constituency_key = ("2021_constituency", "constituency")

    # Build Tamil lookup maps
    names_ta = _load_names_ta()
    constituency_ta_map = dict(
        Constituency.objects.exclude(name_ta="").values_list("name", "name_ta")
    )

    # Score parties
    party_names: dict[str, str] = {}  # raw name -> display name
    for row in rows:
        party = (row.get("party") or "").strip() or "Independent / Unknown"
        if party not in party_names:
            party_names[party] = _display_party_name(party)

    scored: list[dict] = []
    for party, display in party_names.items():
        ta_display = _display_party_name_ta(party)
        score = max(
            _fuzzy_match_score(query_lower, display.lower()),
            _fuzzy_match_score(query_lower, party.lower()),
        )
        # Also match against Tamil party name
        if ta_display:
            score = max(score, _fuzzy_match_score(query_lower, ta_display.lower()))
        for alias in PARTY_ALIASES.get(party, []):
            score = max(score, _fuzzy_match_score(query_lower, alias.lower()))
        if score > 0:
            scored.append({
                "type": "party",
                "name": party,
                "display_name": display,
                "display_name_ta": ta_display,
                "symbol_url": _party_symbol_url(party),
                "score": score,
            })

    # Score districts
    district_ta_map_d = dict(
        Constituency.objects.exclude(district_ta="").values_list("district", "district_ta")
    )
    district_set: dict[str, str] = {}
    for row in rows:
        d = _get_district_for_row(row, year)
        if d and d not in district_set:
            district_set[d] = district_ta_map_d.get(d, "")

    for district_en, district_ta in district_set.items():
        score = _fuzzy_match_score(query_lower, district_en.lower())
        if district_ta:
            score = max(score, _fuzzy_match_score(query_lower, district_ta.lower()))
        if score > 0:
            scored.append({
                "type": "district",
                "name": district_en,
                "name_ta": district_ta,
                "score": score,
            })

    # Score constituencies
    constituency_set: dict[str, tuple[str, str]] = {}
    for row in rows:
        c = _row_value(row, constituency_key)
        if c and c not in constituency_set:
            d = _get_district_for_row(row, year)
            constituency_set[c] = (constituency_ta_map.get(c, ""), d)

    for const_en, (const_ta, const_district) in constituency_set.items():
        score = _fuzzy_match_score(query_lower, const_en.lower())
        if const_ta:
            score = max(score, _fuzzy_match_score(query_lower, const_ta.lower()))
        if score > 0:
            scored.append({
                "type": "constituency",
                "name": const_en,
                "name_ta": const_ta,
                "district": const_district,
                "score": score,
            })

    # Score candidates (deduplicate by name+party)
    seen_candidates: set[tuple[str, str]] = set()
    for row in rows:
        candidate = (row.get("candidate") or "").strip()
        party = (row.get("party") or "").strip() or "Independent / Unknown"
        if not candidate or (candidate, party) in seen_candidates:
            continue
        seen_candidates.add((candidate, party))
        name_ta = names_ta.get(candidate, "")
        score = _fuzzy_match_score(query_lower, candidate.lower())
        # Also match against Tamil name
        if name_ta:
            score = max(score, _fuzzy_match_score(query_lower, name_ta.lower()))
        # Also match against popular aliases/nicknames
        for alias in CANDIDATE_ALIASES.get(candidate, []):
            score = max(score, _fuzzy_match_score(query_lower, alias.lower()))
        if score > 0:
            constituency_en = _row_value(row, constituency_key)
            scored.append({
                "type": "candidate",
                "name": candidate,
                "name_ta": name_ta,
                "party": party,
                "party_display": _display_party_name(party),
                "party_display_ta": _display_party_name_ta(party),
                "constituency": constituency_en,
                "constituency_ta": constituency_ta_map.get(constituency_en, ""),
                "district": _get_district_for_row(row, year),
                "score": score,
            })

    scored.sort(key=lambda x: (-x["score"], x["name"].lower()))
    results = scored[:10]
    for r in results:
        r.pop("score", None)
    return JsonResponse({"results": results})


def set_language(request, language: str):
    if language not in {"en", "ta"}:
        language = "en"
    request.session["language"] = language
    next_url = request.GET.get("next") or reverse("home")
    return redirect(next_url)

def constituency_detail(request, constituency_id: int):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"
    constituency = get_object_or_404(
        Constituency.objects.all(),
        pk=constituency_id,
    )
    party_symbols: dict[str, str] = {}
    for party in Party.objects.exclude(symbol_url="").only("name", "abbreviation", "symbol_url"):
        if party.name:
            party_symbols[party.name.strip().lower()] = party.symbol_url
        if party.abbreviation:
            party_symbols[party.abbreviation.strip().lower()] = party.symbol_url

    if year == "2026":
        rows = _load_smla_rows("2026")
        norm_key = _normalize_constituency_name(constituency.name)
        candidates = [
            row for row in rows
            if _normalize_constituency_name(row.get("constituency")) == norm_key
        ]
        district_name = constituency.district or ""
    else:
        rows = _load_smla_rows("2021")
        constituency_seen: set[str] = set()
        district_candidates: dict[str, set[str]] = defaultdict(set)
        alias_by_district: dict[str, dict[str, str]] = defaultdict(dict)
        for row in rows:
            constituency_key = _normalize_constituency_name(row.get("2021_constituency"))
            district_key = _normalize_constituency_name(row.get("2021_district"))
            if constituency_key:
                constituency_seen.add(constituency_key)
                if district_key:
                    district_candidates[district_key].add(constituency_key)
                official_key = _normalize_constituency_name(row.get("const_off"))
                if official_key:
                    alias_by_district[district_key][official_key] = constituency_key
                    alias_by_district[""].setdefault(official_key, constituency_key)
        explicit_aliases = {
            "PALACODU": "PALACODE",
            "THALLI": "THALLY",
            "SHOZHINGANALLUR": "SHOLINGANALLUR",
            "VANDAVASI": "VANDAVASI SC",
            "VANDAVASI SC": "VANDAVASI SC",
        }
        for raw, mapped in explicit_aliases.items():
            alias_by_district[""].setdefault(raw, mapped)
            for district_key in district_candidates.keys():
                alias_by_district[district_key].setdefault(raw, mapped)
        explicit_district_aliases = {
            "TIRUVANNAMALAI": {"VANDAVASI SC": "VANDAVASI SC", "VANDAVASI": "VANDAVASI SC"},
            "VILUPPURAM": {"VANDAVASI SC": "VANDAVASI SC", "VANDAVASI": "VANDAVASI SC"},
            "TIRUPATHUR": {"TIRUPPATTUR": "TIRUPATTUR"},
            "VELLORE": {"TIRUPPATTUR": "TIRUPATTUR"},
            "SIVAGANGA": {"TIRUPPATTUR": "TIRUPPATHUR"},
        }
        for district_key, mapping in explicit_district_aliases.items():
            for raw, mapped in mapping.items():
                alias_by_district[district_key].setdefault(raw, mapped)

        # Force correct global alias for Tirupathur (overrides any auto-generated alias)
        alias_by_district[""]["TIRUPPATTUR"] = "TIRUPATTUR"

        resolved_key = _resolve_constituency_key(
            _normalize_constituency_name(constituency.name),
            _normalize_constituency_name(constituency.district),
            constituency_seen,
            district_candidates,
            alias_by_district,
            cutoff=0.85,
        )
        candidates = [
            row for row in rows
            if _normalize_constituency_name(row.get("2021_constituency")) == resolved_key
        ]
        district_name = (candidates[0].get("2021_district") if candidates else None) or constituency.district
    current_language = request.session.get("language", "en")

    REGION_TA = {
        "Northern TN": "வடக்கு தமிழ்நாடு",
        "Southern TN": "தெற்கு தமிழ்நாடு",
        "Western TN": "மேற்கு தமிழ்நாடு",
        "Central TN": "மத்திய தமிழ்நாடு",
        "Chennai": "சென்னை",
        "Delta": "டெல்டா",
    }
    region_raw = constituency.region or ""
    region_display = REGION_TA.get(region_raw, region_raw) if current_language == "ta" else region_raw

    # Attach key promises for each party/coalition.
    party_lookup: dict[str, Party] = {}
    coalition_by_party_id: dict[int, int] = {}
    manifesto_by_party_id: dict[int, Manifesto] = {}
    manifesto_by_coalition_id: dict[int, Manifesto] = {}
    promise_chips_by_manifesto_id: dict[int, list[dict]] = {}
    all_promises_by_manifesto_id: dict[int, list[dict]] = {}
    state_assessment_by_party_promise: dict[tuple[int, int], PromiseAssessment] = {}
    constituency_assessment_by_promise: dict[int, PromiseAssessment] = {}
    claim_by_party_id: dict[int, PartyFulfilmentClaim] = {}

    for party in Party.objects.all().only("id", "name", "abbreviation"):
        if party.name:
            party_lookup[party.name.strip().lower()] = party
        if party.abbreviation:
            party_lookup[party.abbreviation.strip().lower()] = party

    party_ids: set[int] = set()
    for row in candidates:
            raw_party = (row.get("party") or "").strip().lower()
            match = party_lookup.get(raw_party)
            if match:
                party_ids.add(match.id)

    if party_ids:
        election_obj = Election.objects.filter(year=int(year)).first()
        memberships = CoalitionMembership.objects.select_related("coalition", "coalition__election").filter(
            party_id__in=party_ids
        )
        if election_obj:
            memberships = memberships.filter(coalition__election=election_obj)
        for membership in memberships:
            if membership.party_id and membership.coalition_id:
                coalition_by_party_id.setdefault(membership.party_id, membership.coalition_id)

    if party_ids:
        coalition_ids = {cid for cid in coalition_by_party_id.values() if cid}
        manifestos = (
            Manifesto.objects.filter(constituency__isnull=True, candidate__isnull=True)
            .filter(Q(party_id__in=party_ids) | Q(coalition_id__in=coalition_ids))
            .select_related("party", "coalition")
            .order_by("-last_updated", "-id")
        )
        for manifesto in manifestos:
            if manifesto.coalition_id and manifesto.coalition_id not in manifesto_by_coalition_id:
                manifesto_by_coalition_id[manifesto.coalition_id] = manifesto
            if manifesto.party_id and manifesto.party_id not in manifesto_by_party_id:
                manifesto_by_party_id[manifesto.party_id] = manifesto

        selected_manifesto_ids = {
            m.id for m in list(manifesto_by_party_id.values()) + list(manifesto_by_coalition_id.values()) if m
        }
        if selected_manifesto_ids:
            # Key promises (up to 4 per manifesto for card chips)
            promises = (
                ManifestoPromise.objects.filter(manifesto_id__in=selected_manifesto_ids, is_key=True)
                .only("id", "manifesto_id", "slug", "text", "text_ta", "position")
                .order_by("position", "id")
            )
            for promise in promises:
                bucket = promise_chips_by_manifesto_id.setdefault(promise.manifesto_id, [])
                if len(bucket) >= 4:
                    continue
                text = (
                    promise.text_ta.strip()
                    if current_language == "ta" and promise.text_ta
                    else (promise.text.strip() if promise.text else "")
                )
                if not text:
                    continue
                bucket.append({"id": promise.id, "slug": promise.slug, "text": text})

            # All promises (for popup modal, up to 30 per manifesto)
            all_promises_qs = (
                ManifestoPromise.objects.filter(manifesto_id__in=selected_manifesto_ids)
                .only("id", "manifesto_id", "slug", "text", "text_ta", "category", "position", "is_key")
                .order_by("position", "id")
            )
            for promise in all_promises_qs:
                bucket = all_promises_by_manifesto_id.setdefault(promise.manifesto_id, [])
                if len(bucket) >= 30:
                    continue
                text = (
                    promise.text_ta.strip()
                    if current_language == "ta" and promise.text_ta
                    else (promise.text.strip() if promise.text else "")
                )
                if not text:
                    continue
                cat = promise.category or ""
                bucket.append({
                    "id": promise.id,
                    "slug": promise.slug,
                    "text": text,
                    "category": cat,
                    "category_ta": PROMISE_CATEGORY_TA.get(cat, cat),
                    "is_key": promise.is_key,
                })

            promise_ids = {chip["id"] for chips in promise_chips_by_manifesto_id.values() for chip in chips if chip.get("id")}
            if promise_ids:
                state_qs = (
                    PromiseAssessment.objects.filter(
                        scope=PromiseAssessment.Scope.STATE,
                        party_id__in=party_ids,
                        promise_id__in=promise_ids,
                    )
                    .only("id", "promise_id", "party_id", "status", "score", "summary", "summary_ta", "as_of")
                    .order_by("-as_of", "-id")
                )
                for assessment in state_qs:
                    key = (assessment.party_id or 0, assessment.promise_id)
                    if key not in state_assessment_by_party_promise:
                        state_assessment_by_party_promise[key] = assessment

                const_qs = (
                    PromiseAssessment.objects.filter(
                        scope=PromiseAssessment.Scope.CONSTITUENCY,
                        constituency=constituency,
                        promise_id__in=promise_ids,
                    )
                    .only("id", "promise_id", "status", "score", "summary", "summary_ta", "as_of")
                    .order_by("-as_of", "-id")
                )
                for assessment in const_qs:
                    if assessment.promise_id not in constituency_assessment_by_promise:
                        constituency_assessment_by_promise[assessment.promise_id] = assessment

            election_for_claims = Election.objects.filter(year=int(year)).first()
            claim_qs = PartyFulfilmentClaim.objects.filter(party_id__in=party_ids).select_related(
                "source_document", "election"
            )
            if election_for_claims:
                claim_qs = claim_qs.filter(election=election_for_claims)
            claim_qs = claim_qs.order_by("-as_of", "-id")
            for claim in claim_qs:
                if claim.party_id and claim.party_id not in claim_by_party_id:
                    claim_by_party_id[claim.party_id] = claim

    cases_values = [_parse_int(row.get("criminal_cases")) for row in candidates]
    cases_values = [value for value in cases_values if value is not None]
    age_values = [_parse_int(row.get("age")) for row in candidates]
    age_values = [value for value in age_values if value is not None]
    assets_values = [_parse_int(row.get("total_assets_rs")) for row in candidates]
    assets_values = [value for value in assets_values if value is not None]
    expenditure_values = [_parse_int(row.get("election_expenditure_rs")) for row in candidates]
    expenditure_values = [value for value in expenditure_values if value is not None]

    candidate_count = len(candidates)
    party_count = len({(row.get("party") or "").strip() for row in candidates if (row.get("party") or "").strip()})
    cases_positive = sum(1 for value in cases_values if value and value > 0)
    avg_cases = (sum(cases_values) / len(cases_values)) if cases_values else None
    avg_age = (sum(age_values) / len(age_values)) if age_values else None
    avg_assets = (sum(assets_values) / len(assets_values)) if assets_values else None
    avg_expenditure = (sum(expenditure_values) / len(expenditure_values)) if expenditure_values else None
    cases_pct = round((cases_positive / candidate_count) * 100, 1) if candidate_count else 0

    _ta = current_language == "ta"
    summary_cards = [
        {"label": "வேட்பாளர்கள்" if _ta else "Candidates", "value": _format_indian_number(candidate_count)},
        {"label": "கட்சிகள்" if _ta else "Parties", "value": _format_indian_number(party_count)},
        {"label": "சராசரி வழக்குகள்" if _ta else "Avg cases", "value": _format_indian_number(round(avg_cases, 2) if avg_cases is not None else None)},
        {"label": "சராசரி வயது" if _ta else "Avg age", "value": _format_indian_number(round(avg_age, 1) if avg_age is not None else None)},
        {
            "label": "சராசரி சொத்துகள்" if _ta else "Avg assets",
            "value": f"₹ {short_indian(round(avg_assets, 0))}" if avg_assets is not None else "N/A",
        },
    ]
    # Most common education tile
    edu_counter: Counter = Counter()
    for row in candidates:
        edu = (row.get("education_category") or row.get("education") or "").strip()
        if edu:
            edu_counter[edu] += 1
    top_edu = ""
    for edu_name, _ in edu_counter.most_common():
        if edu_name and edu_name != "Others":
            top_edu = edu_name
            break
    if not top_edu and edu_counter:
        top_edu = edu_counter.most_common(1)[0][0]
    if top_edu:
        summary_cards.append({
            "label": "அதிகம் காணப்படும் கல்வி" if _ta else "Most common education",
            "value": EDUCATION_TA.get(top_edu, top_edu) if _ta else top_edu,
        })
    if year == "2021":
        summary_cards.append({
            "label": "சராசரி தேர்தல் செலவு" if _ta else "Avg expenditure",
            "value": f"₹ {short_indian(round(avg_expenditure, 0))}" if avg_expenditure is not None else "N/A",
        })

    candidate_cards = []
    for row in candidates:
        assets_value = _parse_int(row.get("total_assets_rs"))
        liabilities_value = _parse_int(row.get("liabilities_rs"))
        raw_party = (row.get("party") or "").strip()
        party_obj = party_lookup.get(raw_party.strip().lower()) if raw_party else None
        coalition_id = coalition_by_party_id.get(party_obj.id) if party_obj else None
        manifesto = (
            manifesto_by_coalition_id.get(coalition_id) if coalition_id else None
        ) or (manifesto_by_party_id.get(party_obj.id) if party_obj else None)
        key_promises = promise_chips_by_manifesto_id.get(manifesto.id, []) if manifesto else []
        all_promises = all_promises_by_manifesto_id.get(manifesto.id, []) if manifesto else []

        state_delivery = None
        constituency_delivery = None
        if str(row.get("sitting_MLA", "")).strip() == "1" and party_obj and key_promises:
            promise_ids = [chip["id"] for chip in key_promises if chip.get("id")]
            statuses = []
            scores = []
            summary_text = ""
            summary_as_of = None
            for pid in promise_ids:
                assessment = state_assessment_by_party_promise.get((party_obj.id, pid))
                if not assessment:
                    continue
                statuses.append(assessment.status)
                if assessment.score is not None:
                    try:
                        scores.append(float(assessment.score))
                    except (TypeError, ValueError):
                        pass
                text_summary = (
                    assessment.summary_ta.strip()
                    if current_language == "ta" and assessment.summary_ta
                    else (assessment.summary.strip() if assessment.summary else "")
                )
                if text_summary and not summary_text:
                    summary_text = text_summary
                    summary_as_of = assessment.as_of

            breakdown = {key: statuses.count(key) for key in PromiseAssessment.Status.values}
            scored_count = len(scores)
            avg_score = (sum(scores) / scored_count) if scored_count else None
            claim = claim_by_party_id.get(party_obj.id)
            claim_label = None
            claim_url = None
            claim_as_of = None
            if claim and claim.claimed_percent is not None:
                claim_label = f"{claim.claimed_percent}%"
                claim_url = claim.source_document.url if getattr(claim, "source_document", None) else ""
                claim_as_of = claim.as_of

            state_delivery = {
                "claim_percent": claim_label,
                "claim_url": claim_url or "",
                "claim_as_of": claim_as_of,
                "avg_score": round(avg_score, 2) if avg_score is not None else None,
                "breakdown": breakdown,
                "summary": summary_text,
                "as_of": summary_as_of,
            }

            c_statuses = []
            c_scores = []
            c_summary = ""
            c_as_of = None
            for pid in promise_ids:
                assessment = constituency_assessment_by_promise.get(pid)
                if not assessment:
                    continue
                c_statuses.append(assessment.status)
                if assessment.score is not None:
                    try:
                        c_scores.append(float(assessment.score))
                    except (TypeError, ValueError):
                        pass
                text_summary = (
                    assessment.summary_ta.strip()
                    if current_language == "ta" and assessment.summary_ta
                    else (assessment.summary.strip() if assessment.summary else "")
                )
                if text_summary and not c_summary:
                    c_summary = text_summary
                    c_as_of = assessment.as_of
            c_breakdown = {key: c_statuses.count(key) for key in PromiseAssessment.Status.values}
            c_avg = (sum(c_scores) / len(c_scores)) if c_scores else None
            constituency_delivery = {
                "avg_score": round(c_avg, 2) if c_avg is not None else None,
                "breakdown": c_breakdown,
                "summary": c_summary,
                "as_of": c_as_of,
            }
        name_en = (row.get("candidate") or "").strip() or "Unknown"
        party_en = (row.get("party") or "").strip() or "Independent / Unknown"
        education_en = (row.get("education_category") or row.get("education") or "").strip()
        education_details_raw = (row.get("education_details") or row.get("education_formatted") or row.get("education_details_clean") or "").strip()
        candidate_cards.append(
            {
                "name": name_en,
                "name_ta": _get_tamil_name(name_en) if _ta else "",
                "party": party_en,
                "party_ta": _display_party_name_ta(raw_party) if _ta else "",
                "party_symbol": _party_symbol_url(raw_party) or party_symbols.get(raw_party.lower()),
                "is_2016": _is_2016_row(row),
                "education": education_en,
                "education_ta": EDUCATION_TA.get(education_en, "") if _ta else "",
                "age": _parse_int(row.get("age")),
                "criminal_cases": _parse_int(row.get("criminal_cases")),
                "assets": assets_value,
                "liabilities": liabilities_value,
                "movable_assets": _parse_int(row.get("movable_assets_rs")),
                "immovable_assets": _parse_int(row.get("immovable_assets_rs")),
                "pan_given": (row.get("pan_given") or "").strip(),
                "serious_ipc": _parse_int(row.get("serious_ipc_counts")),
                "is_winner": str(row.get("is_winner", "")).strip() == "1",
                "election_expenditure": _parse_int(row.get("election_expenditure_rs")),
                # post-election 2026: the winner is the sitting MLA
                "sitting": (str(row.get("sitting_MLA", "")).strip() == "1"
                            or str(row.get("is_winner", "")).strip() == "1"),
                "myneta_url": (row.get("myneta_url") or "").strip(),
                "self_profession": (row.get("self_profession") or "").strip(),
                "self_profession_ta": (row.get("self_profession_ta") or "").strip(),
                "spouse_profession": (row.get("spouse_profession") or "").strip(),
                "spouse_profession_ta": (row.get("spouse_profession_ta") or "").strip(),
                "education_details_clean": education_details_raw,
                "education_details_ta": (row.get("education_details_ta") or "").strip(),
                "criminal_cases_summary": (row.get("legal_summary_short") or row.get("criminal_cases_summary") or "").strip(),
                "key_promises": key_promises,
                "all_promises": all_promises,
                "state_delivery": state_delivery,
                "constituency_delivery": constituency_delivery,
                "photo_url": (row.get("photo_url") or "").strip(),
                "affidavit_url": (row.get("affidavit_url") or "").strip(),
                "affidavit_pdf_url": (row.get("affidavit_pdf_url") or "").strip(),
                "gender": (row.get("gender") or "").strip(),
                "phone": (row.get("phone") or "").strip(),
                "email": (row.get("email") or "").strip(),
                "facebook": (row.get("facebook") or "").strip(),
                "twitter": (row.get("twitter") or "").strip(),
                "instagram": (row.get("instagram") or "").strip(),
                "youtube": (row.get("youtube") or "").strip(),
            }
        )
    candidate_cards.sort(key=lambda c: (not c["sitting"],))
    return render(
        request,
        "core/constituency_detail.html",
        {
            "year": year,
            "constituency": constituency,
            "district_name": district_name,
            "summary_cards": summary_cards,
            "candidate_cards": candidate_cards,
            "region_display": region_display,
        },
    )


def candidate_detail(request, candidate_id: int):
    election = Election.objects.filter(year=2021).first()
    candidate = get_object_or_404(
        Candidate.objects.select_related("party", "constituency")
        .prefetch_related(
            "affidavits",
            "legal_cases",
            Prefetch(
                "results",
                queryset=CandidateResult.objects.filter(election=election)
                if election
                else CandidateResult.objects.none(),
                to_attr="results_2021",
            ),
        ),
        pk=candidate_id,
    )
    party_symbol = None
    if candidate.party:
        party_symbol = _party_symbol_url(candidate.party.abbreviation or candidate.party.name)
    return render(
        request,
        "core/candidate_detail.html",
        {
            "candidate": candidate,
            "election": election,
            "missing_affidavit": not candidate.affidavits.all(),
            "missing_legal": not candidate.legal_cases.all(),
            "missing_results": not candidate.results_2021,
            "party_symbol": party_symbol,
            "status_ta": STATUS_TA.get(candidate.status or "", ""),
            "gender_ta": GENDER_TA.get(candidate.gender or "", ""),
        },
    )


def search(request):
    query = request.GET.get("q", "").strip()
    constituencies = []
    candidates = []
    parties = []
    if query:
        constituencies = Constituency.objects.filter(
            Q(name__icontains=query)
            | Q(name_ta__icontains=query)
            | Q(district__icontains=query)
            | Q(district_ta__icontains=query)
        )[:25]
        candidates = Candidate.objects.select_related("party", "constituency").filter(
            Q(name__icontains=query)
            | Q(name_ta__icontains=query)
            | Q(party__name__icontains=query)
            | Q(party__name_ta__icontains=query)
            | Q(constituency__name__icontains=query)
            | Q(constituency__name_ta__icontains=query)
        )[:25]
        parties = Party.objects.filter(Q(name__icontains=query) | Q(name_ta__icontains=query))[:25]

    return render(
        request,
        "core/search.html",
        {
            "query": query,
            "constituencies": constituencies,
            "candidates": candidates,
            "parties": parties,
        },
    )


def data_quality_dashboard(request):
    total_constituencies = Constituency.objects.count()
    total_candidates = Candidate.objects.count()
    constituencies_missing_candidates = Constituency.objects.filter(candidates__isnull=True).count()
    candidates_missing_affidavit = Candidate.objects.filter(affidavits__isnull=True).count()
    candidates_missing_legal = Candidate.objects.filter(legal_cases__isnull=True).count()
    constituencies_missing_manifestos = Constituency.objects.filter(manifestos__isnull=True).count()
    parties_missing_manifestos = Party.objects.filter(manifestos__isnull=True).count()

    return render(
        request,
        "core/dashboard.html",
        {
            "total_constituencies": total_constituencies,
            "total_candidates": total_candidates,
            "constituencies_missing_candidates": constituencies_missing_candidates,
            "candidates_missing_affidavit": candidates_missing_affidavit,
            "candidates_missing_legal": candidates_missing_legal,
            "constituencies_missing_manifestos": constituencies_missing_manifestos,
            "parties_missing_manifestos": parties_missing_manifestos,
        },
    )


def _parse_int(value: Optional[str]) -> Optional[int]:
    if not value:
        return None
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def _row_value(row: dict, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = (row.get(key) or "").strip()
        if value:
            return value
    return ""


def _numeric_bounds(rows: list[dict], key: str) -> tuple[int, int]:
    values = [_parse_int(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return 0, 0
    return min(values), max(values)


@lru_cache(maxsize=4)
def _load_party_rows(csv_path: str) -> list[dict]:
    path = Path(csv_path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _compute_overview_stats(rows: list[dict]) -> dict:
    """Compute overview statistics from candidate rows."""
    if not rows:
        return {
            "total_parties": 0,
            "total_candidates": 0,
            "overall_avg_cases": None,
            "overall_avg_age": None,
            "overall_avg_assets": None,
            "overall_avg_expenditure": None,
        }

    party_set = set()
    cases_total = 0
    cases_count = 0
    age_total = 0
    age_count = 0
    assets_total = 0
    assets_count = 0
    expenditure_total = 0
    expenditure_count = 0

    for row in rows:
        party_name = (row.get("party") or "").strip() or "Independent / Unknown"
        party_set.add(party_name)

        cases_value = _parse_int(row.get("criminal_cases"))
        if cases_value is not None and cases_value > 500:
            cases_value = None  # discard obviously corrupt data
        age_value = _parse_int(row.get("age"))
        assets_value = _parse_int(row.get("total_assets_rs"))
        expenditure_value = _parse_int(row.get("election_expenditure_rs"))

        if cases_value is not None:
            cases_total += cases_value
            cases_count += 1
        if age_value is not None:
            age_total += age_value
            age_count += 1
        if assets_value is not None:
            assets_total += assets_value
            assets_count += 1
        if expenditure_value is not None:
            expenditure_total += expenditure_value
            expenditure_count += 1

    return {
        "total_parties": len(party_set),
        "total_candidates": len(rows),
        "overall_avg_cases": round(cases_total / cases_count, 2) if cases_count else None,
        "overall_avg_age": round(age_total / age_count, 1) if age_count else None,
        "overall_avg_assets": round(assets_total / assets_count, 0) if assets_count else None,
        "overall_avg_expenditure": round(expenditure_total / expenditure_count, 0) if expenditure_count else None,
    }


# ---------- Categorical bucket definitions ----------
CASES_BUCKETS = {"0": (0, 0), "1-5": (1, 5), "6+": (6, None)}
AGE_BUCKETS = {"under35": (None, 34), "35-44": (35, 44), "45-54": (45, 54), "55+": (55, None)}
ASSETS_BUCKETS = {
    "under10l": (None, 999999),
    "10l-1cr": (1000000, 9999999),
    "1cr-10cr": (10000000, 99999999),
    "10cr+": (100000000, None),
}


def _bucket_range(bucket_value: str, buckets: dict) -> tuple:
    """Return (min_val, max_val) for a categorical bucket value, or (None, None) if not found."""
    return buckets.get(bucket_value, (None, None))


def _passes_bucket_filter(value, bucket_min, bucket_max) -> bool:
    """Check if a numeric value passes a bucket filter range."""
    if value is None:
        return False
    if bucket_min is not None and value < bucket_min:
        return False
    if bucket_max is not None and value > bucket_max:
        return False
    return True


def party_dashboard(request):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"
    cases_filter = request.GET.get("cases", "").strip()
    age_group_filter = request.GET.get("age_group", "").strip()
    assets_range_filter = request.GET.get("assets_range", "").strip()
    sitting_filter = request.GET.get("sitting_mla", "").strip()
    gender_filter = request.GET.get("gender", "").strip()
    district_filter = request.GET.get("district", "").strip()
    constituency_filter = request.GET.get("constituency", "").strip()
    selected_party = request.GET.get("party", "").strip()
    sort_key = request.GET.get("sort", "seats" if year == "2026" else "candidate_count")  # kept for backwards-compat URLs
    sort_order = request.GET.get("order", "desc")

    cases_min, cases_max = _bucket_range(cases_filter, CASES_BUCKETS)
    age_min, age_max = _bucket_range(age_group_filter, AGE_BUCKETS)
    assets_min, assets_max = _bucket_range(assets_range_filter, ASSETS_BUCKETS)
    cases_filter_active = cases_filter in CASES_BUCKETS
    age_filter_active = age_group_filter in AGE_BUCKETS
    assets_filter_active = assets_range_filter in ASSETS_BUCKETS

    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("fct_candidates_26.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(str(csv_path))
    has_winner = bool(rows and "is_winner" in rows[0])
    # post-election 2026: winners are the sitting MLAs
    has_sitting = bool(rows and "sitting_MLA" in rows[0]) or has_winner
    has_gender = bool(rows and "gender" in rows[0])

    def _row_sitting(row: dict):
        if "sitting_MLA" in row:
            return _parse_int(row.get("sitting_MLA"))
        return 1 if str(row.get("is_winner", "")).strip() == "1" else 0

    filtered_rows: list[dict] = []
    party_set = set()
    district_set = set()
    district_map: dict[str, set[str]] = defaultdict(set)
    district_key = ("2021_district", "district")
    constituency_key = ("2021_constituency", "constituency")
    for row in rows:
        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        assets_value = _parse_int(row.get("total_assets_rs"))
        sitting_value = _row_sitting(row)
        party_name = (row.get("party") or "").strip() or "Independent / Unknown"
        district_name = _get_district_for_row(row, year)
        constituency_name = _row_value(row, constituency_key)
        party_set.add(party_name)
        if district_name:
            district_set.add(district_name)
            if constituency_name:
                district_map[district_name].add(constituency_name)
        if cases_filter_active and not _passes_bucket_filter(cases_value, cases_min, cases_max):
            continue
        if age_filter_active and not _passes_bucket_filter(age_value, age_min, age_max):
            continue
        if assets_filter_active and not _passes_bucket_filter(assets_value, assets_min, assets_max):
            continue
        if has_sitting and sitting_filter in {"0", "1"}:
            if sitting_value is None or str(sitting_value) != sitting_filter:
                continue
        if has_gender and gender_filter in {"male", "female"}:
            row_gender = (row.get("gender") or "").strip().lower()
            if row_gender != gender_filter:
                continue
        if selected_party and party_name != selected_party:
            continue
        if district_filter and district_name != district_filter:
            continue
        if constituency_filter and constituency_name != constituency_filter:
            continue
        filtered_rows.append(row)

    party_data: dict[str, dict] = defaultdict(lambda: {
        "count": 0,
        "cases_total": 0,
        "cases_count": 0,
        "cases_positive": 0,
        "age_total": 0,
        "age_count": 0,
        "assets_total": 0,
        "assets_count": 0,
        "expenditure_total": 0,
        "expenditure_count": 0,
        "sitting_total": 0,
        "seats_total": 0,
        "women_count": 0,
        "education_counts": Counter(),
    })

    parties_with_sitting: set[str] = set()
    for row in filtered_rows:
        party = (row.get("party") or "").strip() or "Independent / Unknown"
        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        education_value = (row.get("education_category") or row.get("education") or "").strip()
        assets_value = _parse_int(row.get("total_assets_rs"))
        expenditure_value = _parse_int(row.get("election_expenditure_rs"))
        sitting_value = _row_sitting(row)
        winner_value = 1 if str(row.get("is_winner", "")).strip() == "1" else 0

        gender_value = (row.get("gender") or "").strip().lower()

        bucket = party_data[party]
        bucket["count"] += 1
        if gender_value == "female":
            bucket["women_count"] += 1
        if cases_value is not None:
            bucket["cases_total"] += cases_value
            bucket["cases_count"] += 1
            if cases_value > 0:
                bucket["cases_positive"] += 1
        if age_value is not None:
            bucket["age_total"] += age_value
            bucket["age_count"] += 1
        if assets_value is not None:
            bucket["assets_total"] += assets_value
            bucket["assets_count"] += 1
        if expenditure_value is not None:
            bucket["expenditure_total"] += expenditure_value
            bucket["expenditure_count"] += 1
        if sitting_value is not None and sitting_value > 0:
            bucket["sitting_total"] += 1
            parties_with_sitting.add(party)
        bucket["seats_total"] += winner_value
        if education_value:
            bucket["education_counts"][education_value] += 1

    # Build manifesto lookup for party dashboard badges
    _party_manifesto_info: dict[str, dict] = {}
    _dash_party_objs = {
        p.name.strip().lower(): p
        for p in Party.objects.all().only("id", "name", "abbreviation")
        if p.name
    }
    for abbr_p in Party.objects.all().only("id", "abbreviation"):
        if abbr_p.abbreviation:
            _dash_party_objs.setdefault(abbr_p.abbreviation.strip().lower(), abbr_p)
    _dash_manifesto_ids = set()
    for m in Manifesto.objects.filter(
        constituency__isnull=True, candidate__isnull=True, party__isnull=False
    ).select_related("party").order_by("-last_updated"):
        if m.party and m.party.name:
            key = m.party.name.strip().lower()
            if key not in _party_manifesto_info:
                _party_manifesto_info[key] = {"manifesto_id": m.id, "has_manifesto": True, "key_promise_count": 0}
                _dash_manifesto_ids.add(m.id)
            if m.party.abbreviation:
                abbr_key = m.party.abbreviation.strip().lower()
                if abbr_key not in _party_manifesto_info:
                    _party_manifesto_info[abbr_key] = _party_manifesto_info[key]
    if _dash_manifesto_ids:
        from django.db.models import Count
        _counts = (
            ManifestoPromise.objects.filter(manifesto_id__in=_dash_manifesto_ids, is_key=True)
            .values("manifesto_id")
            .annotate(cnt=Count("id"))
        )
        _count_map = {row["manifesto_id"]: row["cnt"] for row in _counts}
        for info in _party_manifesto_info.values():
            info["key_promise_count"] = _count_map.get(info["manifesto_id"], 0)

    party_stats = []
    for party, stats in party_data.items():
        avg_cases = (
            round(stats["cases_total"] / stats["cases_count"], 2)
            if stats["cases_count"]
            else None
        )
        avg_age = round(stats["age_total"] / stats["age_count"], 1) if stats["age_count"] else None
        avg_assets = (
            round(stats["assets_total"] / stats["assets_count"], 0)
            if stats["assets_count"]
            else None
        )
        cases_pct = round((stats["cases_positive"] / stats["count"]) * 100, 1) if stats["count"] else 0.0
        edu_ranking = stats["education_counts"].most_common()
        top_edu_en = ""
        for edu_name, _ in edu_ranking:
            if edu_name and edu_name != "Others":
                top_edu_en = edu_name
                break
        if not top_edu_en and edu_ranking:
            top_edu_en = edu_ranking[0][0]
        avg_expenditure = (
            round(stats["expenditure_total"] / stats["expenditure_count"], 0)
            if stats["expenditure_count"]
            else None
        )
        total_expenditure = stats["expenditure_total"]
        _EDU_RANK = {
            "Doctorate": 1, "Post Graduate": 2, "Graduate Professional": 3,
            "Graduate": 4, "Diploma": 5, "12th Pass": 6, "10th Pass": 7,
            "8th Pass": 8, "5th Pass": 9, "Literate": 10, "Illiterate": 11,
            "Others": 12,
        }
        edu_rank = _EDU_RANK.get(top_edu_en, 13)
        women_pct = round((stats["women_count"] / stats["count"]) * 100, 1) if stats["count"] else 0.0
        party_stats.append(
            {
                "party": party,
                "party_display": _display_party_name(party),
                "party_display_ta": _display_party_name_ta(party),
                "party_symbol": _party_symbol_url(party),
                "candidate_count": stats["count"],
                "seats": stats["seats_total"],
                "women_pct": women_pct,
                "avg_cases": avg_cases,
                "avg_age": avg_age,
                "avg_assets": avg_assets,
                "avg_expenditure": avg_expenditure,
                "total_expenditure": total_expenditure,
                "cases_pct": cases_pct,
                "top_education": top_edu_en,
                "top_education_rank": edu_rank,
                "top_education_ta": EDUCATION_TA.get(top_edu_en, top_edu_en),
                "has_manifesto": _party_manifesto_info.get(party.strip().lower(), {}).get("has_manifesto", False),
                "key_promise_count": _party_manifesto_info.get(party.strip().lower(), {}).get("key_promise_count", 0),
            }
        )

    sort_map = {
        "party": "party",
        "seats": "seats",
        "candidate_count": "candidate_count",
        "avg_cases": "avg_cases",
        "cases_pct": "cases_pct",
        "avg_age": "avg_age",
        "avg_assets": "avg_assets",
        "top_education": "top_education",
    }
    sort_field = sort_map.get(sort_key, "candidate_count")
    reverse_sort = sort_order != "asc"

    def _sort_value(item: dict):
        value = item.get(sort_field)
        if isinstance(value, str):
            return value.lower()
        return value if value is not None else -1

    party_stats.sort(key=_sort_value, reverse=reverse_sort)

    overview = _compute_overview_stats(filtered_rows)
    total_candidates = overview["total_candidates"]
    total_parties = overview["total_parties"]
    overall_avg_cases = overview["overall_avg_cases"]
    overall_avg_age = overview["overall_avg_age"]
    overall_avg_assets = overview["overall_avg_assets"]
    overall_avg_expenditure = overview["overall_avg_expenditure"]

    available_constituencies = sorted(district_map.get(district_filter, set())) if district_filter else sorted(
        {const for consts in district_map.values() for const in consts}
    )

    base_query = {
        "year": year,
        "cases": cases_filter,
        "age_group": age_group_filter,
        "assets_range": assets_range_filter,
        "sitting_mla": sitting_filter,
        "gender": gender_filter,
        "party": selected_party,
        "district": district_filter,
        "constituency": constituency_filter,
    }
    base_query_no_party = dict(base_query)
    base_query_no_party.pop("party", None)
    prominent_set = parties_with_sitting | PROMINENT_PARTIES
    party_options = sorted(
        [
            {
                "value": party,
                "label": _display_party_name(party),
                "label_ta": _display_party_name_ta(party),
                "is_prominent": party in prominent_set,
            }
            for party in party_set
        ],
        key=lambda item: item["label"].lower(),
    )

    # Build Tamil district/constituency lookups from the DB
    district_ta_map = dict(
        Constituency.objects.exclude(district_ta="")
        .values_list("district", "district_ta")
        .distinct()
    )
    constituency_ta_map = dict(
        Constituency.objects.exclude(name_ta="")
        .values_list("name", "name_ta")
    )
    districts_list = [
        {"value": d, "label_ta": district_ta_map.get(d, "")}
        for d in sorted(district_set)
    ]
    constituencies_list = [
        {"value": c, "label_ta": constituency_ta_map.get(c, "")}
        for c in available_constituencies
    ]

    return render(
        request,
        "core/party_dashboard.html",
        {
            "year": year,
            "csv_path": csv_path,
            "total_candidates": total_candidates,
            "total_parties": total_parties,
            "overall_avg_cases": overall_avg_cases,
            "overall_avg_age": overall_avg_age,
            "overall_avg_assets": overall_avg_assets,
            "overall_avg_expenditure": overall_avg_expenditure,
            "party_stats": party_stats,
            "selected_cases": cases_filter,
            "selected_age_group": age_group_filter,
            "selected_assets_range": assets_range_filter,
            "sitting_mla": sitting_filter if has_sitting else "",
            "has_sitting": has_sitting,
            "has_gender": has_gender,
            "selected_gender": gender_filter,
            "rows_count": len(filtered_rows),
            "party_options": party_options,
            "selected_party": selected_party,
            "districts": districts_list,
            "selected_district": district_filter,
            "constituencies": constituencies_list,
            "selected_constituency": constituency_filter,
            "base_query": urlencode(base_query, doseq=True),
            "base_query_no_party": urlencode(base_query_no_party, doseq=True),
        },
    )


def party_detail(request, party_name: str):
    year = request.GET.get("year", "2026").strip()
    if year not in {"2021", "2026"}:
        year = "2026"
    party_display_name = _display_party_name(party_name)
    party_display_name_ta = _display_party_name_ta(party_name)
    party_symbol = _party_symbol_url(party_name)
    cases_filter = request.GET.get("cases", "").strip()
    age_group_filter = request.GET.get("age_group", "").strip()
    assets_range_filter = request.GET.get("assets_range", "").strip()
    sitting_filter = request.GET.get("sitting_mla", "").strip()
    gender_filter = request.GET.get("gender", "").strip()
    district_filter = request.GET.get("district", "").strip()
    constituency_filter = request.GET.get("constituency", "").strip()

    cases_min, cases_max = _bucket_range(cases_filter, CASES_BUCKETS)
    age_min, age_max = _bucket_range(age_group_filter, AGE_BUCKETS)
    assets_min, assets_max = _bucket_range(assets_range_filter, ASSETS_BUCKETS)
    cases_filter_active = cases_filter in CASES_BUCKETS
    age_filter_active = age_group_filter in AGE_BUCKETS
    assets_filter_active = assets_range_filter in ASSETS_BUCKETS

    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("fct_candidates_26.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(str(csv_path))

    has_sitting = bool(rows and ("sitting_MLA" in rows[0] or "is_winner" in rows[0]))
    has_gender = bool(rows and "gender" in rows[0])
    district_key = ("2021_district", "district")
    constituency_key = ("2021_constituency", "constituency")
    district_set = set()
    district_map: dict[str, set[str]] = defaultdict(set)
    all_party_rows = []

    for row in rows:
        if (row.get("party") or "").strip() != party_name:
            continue
        all_party_rows.append(row)
        district_name = _get_district_for_row(row, year)
        constituency_name = _row_value(row, constituency_key)
        if district_name:
            district_set.add(district_name)
            if constituency_name:
                district_map[district_name].add(constituency_name)

    party_rows = []
    for row in all_party_rows:
        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        assets_value = _parse_int(row.get("total_assets_rs"))
        sitting_value = (_parse_int(row.get("sitting_MLA")) if "sitting_MLA" in row
                         else (1 if str(row.get("is_winner", "")).strip() == "1" else 0))
        district_name = _get_district_for_row(row, year)
        constituency_name = _row_value(row, constituency_key)

        if cases_filter_active and not _passes_bucket_filter(cases_value, cases_min, cases_max):
            continue
        if age_filter_active and not _passes_bucket_filter(age_value, age_min, age_max):
            continue
        if assets_filter_active and not _passes_bucket_filter(assets_value, assets_min, assets_max):
            continue
        if has_sitting and sitting_filter in {"0", "1"}:
            if sitting_value is None or str(sitting_value) != sitting_filter:
                continue
        if has_gender and gender_filter in {"male", "female"}:
            row_gender = (row.get("gender") or "").strip().lower()
            if row_gender != gender_filter:
                continue
        if district_filter and district_name != district_filter:
            continue
        if constituency_filter and constituency_name != constituency_filter:
            continue
        party_rows.append(row)

    headers = list(rows[0].keys()) if rows else []
    excluded_headers = {
        "party", "sitting_MLA", "bye_election", "total_assets", "liabilities", "const_off",
        "liabilities_rs", "myneta_url",
        "education", "education_details", "criminal_cases_details", "election_expenditure",
        "education_details_clean", "criminal_cases_summary",
        "self_profession", "spouse_profession",
        "photo_url", "affidavit_url", "affidavit_pdf_url", "pdf_path", "current_status",
        "phone", "email", "facebook", "twitter", "instagram", "youtube",
        "address", "fathers_name", "gender",
        "self_profession_ta", "spouse_profession_ta", "education_details_ta",
        "pan_given", "serious_ipc_counts", "movable_assets_rs", "immovable_assets_rs",
        "is_winner",
    }
    # 2026 has no election-expenditure data (not filed yet); hide that column only.
    if year == "2026":
        excluded_headers.add("election_expenditure_rs")
    allowed_headers = [header for header in headers if header not in excluded_headers]
    # Ensure election_expenditure_rs is included (2021 only)
    if year != "2026" and "election_expenditure_rs" not in allowed_headers and "election_expenditure_rs" in (headers or []):
        allowed_headers.append("election_expenditure_rs")
    label_overrides = {
        "total_assets_rs": "Family Assets (₹)",
        "election_expenditure_rs": "Election Expenditure (₹)",
        "criminal_cases": "Criminal cases",
        "2021_constituency": "Constituency",
        "2021_district": "District",
        "constituency": "Constituency",
        "education_category": "Education",
        "education_formatted": "Education Details",
        "legal_summary_short": "Legal History",
    }
    non_sortable = {"candidate", "2021_constituency", "2021_district", "constituency", "education_formatted", "legal_summary_short"}
    columns = []
    for header in allowed_headers:
        label_en = label_overrides.get(header, header.replace("_", " ").title())
        columns.append({
            "key": header,
            "label": label_en,
            "label_ta": COLUMN_LABEL_TA.get(label_en, ""),
            "is_currency": header in {"total_assets_rs", "election_expenditure_rs"},
            "is_number": header in {"criminal_cases", "age", "total_assets_rs", "election_expenditure_rs"},
            "is_sortable": header not in non_sortable,
        })
    # Card view: reorder so Education, Age, Criminal Cases come first
    card_order = ["candidate", "education_category", "education", "age", "criminal_cases"]
    card_exclude = {"education_formatted", "legal_summary_short"}
    col_by_key = {c["key"]: c for c in columns}
    card_columns = [col_by_key[k] for k in card_order if k in col_by_key]
    card_columns += [c for c in columns if c["key"] not in card_order and c["key"] not in card_exclude]
    myneta_key = "myneta_url"
    constituency_header = None
    if "2021_constituency" in allowed_headers:
        constituency_header = "2021_constituency"
    elif "constituency" in allowed_headers:
        constituency_header = "constituency"
    current_language = request.session.get("language", "en")
    _constituency_ta_map = dict(
        Constituency.objects.exclude(name_ta="").values_list("name", "name_ta")
    ) if current_language == "ta" else {}
    rows_table = []
    candidate_modal_data = []
    for row in party_rows:
        row_data = {header: row.get(header, "") for header in allowed_headers}
        row_data["photo_url"] = (row.get("photo_url") or "").strip()
        row_data["affidavit_url"] = (row.get("affidavit_url") or "").strip()
        row_data["affidavit_pdf_url"] = (row.get("affidavit_pdf_url") or "").strip()
        const_off = (row.get("const_off") or "").strip()
        if constituency_header and const_off:
            row_data[constituency_header] = const_off
        for district_header in ("2021_district", "district"):
            if district_header in row_data:
                val = row_data[district_header]
                if not val and year == "2026":
                    val = _get_district_for_row(row, year)
                if val:
                    row_data[district_header] = str(val).strip().title()
        # Translate gender value for Tamil
        if current_language == "ta" and "gender" in row_data:
            row_data["gender"] = GENDER_TA.get(row_data["gender"], row_data["gender"])
        # Translate constituency name for Tamil
        if current_language == "ta" and constituency_header and row_data.get(constituency_header):
            row_data[constituency_header] = _constituency_ta_map.get(row_data[constituency_header], row_data[constituency_header])
        # Translate candidate name for Tamil
        if current_language == "ta" and "candidate" in row_data:
            row_data["candidate"] = _get_tamil_name(row_data["candidate"])
        # Translate education category for Tamil
        if current_language == "ta" and "education_category" in row_data:
            edu_cat = row_data["education_category"]
            row_data["education_category"] = EDUCATION_TA.get(edu_cat, edu_cat)
        row_data["is_2016"] = _is_2016_row(row)
        rows_table.append(row_data)
        # Modal extended data
        cand_name_en = (row.get("candidate") or "").strip()
        edu_en = (row.get("education_category") or row.get("education") or "").strip()
        candidate_modal_data.append({
            "name": cand_name_en,
            "name_ta": _get_tamil_name(cand_name_en),
            "education": edu_en,
            "education_ta": EDUCATION_TA.get(edu_en, ""),
            "age": _parse_int(row.get("age")),
            "criminal_cases": _parse_int(row.get("criminal_cases")),
            "assets": _parse_int(row.get("total_assets_rs")),
            "liabilities": _parse_int(row.get("liabilities_rs")),
            "movable_assets": _parse_int(row.get("movable_assets_rs")),
            "immovable_assets": _parse_int(row.get("immovable_assets_rs")),
            "pan_given": (row.get("pan_given") or "").strip(),
            "serious_ipc": _parse_int(row.get("serious_ipc_counts")),
            "is_winner": str(row.get("is_winner", "")).strip() == "1",
            "election_expenditure": _parse_int(row.get("election_expenditure_rs")),
            "self_profession": (row.get("self_profession") or "").strip(),
            "self_profession_ta": (row.get("self_profession_ta") or "").strip(),
            "spouse_profession": (row.get("spouse_profession") or "").strip(),
            "spouse_profession_ta": (row.get("spouse_profession_ta") or "").strip(),
            "education_details_clean": (row.get("education_details") or row.get("education_formatted") or row.get("education_details_clean") or "").strip(),
            "education_details_ta": (row.get("education_details_ta") or "").strip(),
            "criminal_cases_summary": (row.get("legal_summary_short") or row.get("criminal_cases_summary") or "").strip(),
            "myneta_url": (row.get("myneta_url") or "").strip(),
            "affidavit_url": (row.get("affidavit_url") or "").strip(),
            "affidavit_pdf_url": (row.get("affidavit_pdf_url") or "").strip(),
            "photo_url": (row.get("photo_url") or "").strip(),
            "phone": (row.get("phone") or "").strip(),
            "email": (row.get("email") or "").strip(),
            "facebook": (row.get("facebook") or "").strip(),
            "twitter": (row.get("twitter") or "").strip(),
            "instagram": (row.get("instagram") or "").strip(),
            "youtube": (row.get("youtube") or "").strip(),
        })
    available_constituencies = sorted(district_map.get(district_filter, set())) if district_filter else sorted(
        {const for consts in district_map.values() for const in consts}
    )

    party_obj = Party.objects.filter(name=party_name).first() or Party.objects.filter(abbreviation=party_name).first()

    # Load manifesto and promises for this party
    party_manifesto = None
    party_manifesto_promises = []
    party_key_promises = []
    if party_obj:
        party_manifesto = (
            Manifesto.objects.filter(party=party_obj, constituency__isnull=True, candidate__isnull=True)
            .order_by("-last_updated", "-id")
            .first()
        )
        if party_manifesto:
            party_manifesto_promises = list(
                ManifestoPromise.objects.filter(manifesto=party_manifesto)
                .only("id", "slug", "text", "text_ta", "category", "position", "is_key")
                .order_by("position", "id")[:30]
            )
            party_key_promises = [p for p in party_manifesto_promises if p.is_key]

    # Tamil lookups for districts/constituencies
    district_ta_map = dict(
        Constituency.objects.exclude(district_ta="")
        .values_list("district", "district_ta")
        .distinct()
    )
    constituency_ta_map = dict(
        Constituency.objects.exclude(name_ta="")
        .values_list("name", "name_ta")
    )
    districts_list = [
        {"value": d, "label_ta": district_ta_map.get(d, "")}
        for d in sorted(district_set)
    ]
    constituencies_list = [
        {"value": c, "label_ta": constituency_ta_map.get(c, "")}
        for c in available_constituencies
    ]

    return render(
        request,
        "core/party_detail.html",
        {
            "party_name": party_name,
            "party_display_name": party_display_name,
            "party_display_name_ta": party_display_name_ta,
            "party_symbol": party_symbol,
            "party_obj": party_obj,
            "year": year,
            "rows": rows_table,
            "columns": columns,
            "card_columns": card_columns,
            "myneta_key": myneta_key,
            "row_count": len(party_rows),
            "candidate_modal_data_json": json.dumps(candidate_modal_data, ensure_ascii=False),
            "selected_cases": cases_filter,
            "selected_age_group": age_group_filter,
            "selected_assets_range": assets_range_filter,
            "sitting_mla": sitting_filter if has_sitting else "",
            "has_gender": has_gender,
            "selected_gender": gender_filter,
            "districts": districts_list,
            "selected_district": district_filter,
            "constituencies": constituencies_list,
            "selected_constituency": constituency_filter,
            "manifesto": party_manifesto,
            "manifesto_promises": party_manifesto_promises,
            "key_promises": party_key_promises,
        },
    )


class ConstituencyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Constituency.objects.all()
    serializer_class = ConstituencySerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "district"]


class PartyViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Party.objects.all()
    serializer_class = PartySerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "abbreviation"]


class CandidateViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Candidate.objects.select_related("party", "constituency")
    serializer_class = CandidateSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "party__name", "constituency__name"]


class ManifestoViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Manifesto.objects.select_related("party", "constituency", "candidate")
    serializer_class = ManifestoSerializer
    filter_backends = [filters.SearchFilter]
    search_fields = ["party__name", "constituency__name", "candidate__name"]
