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
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from rest_framework import viewsets, filters

from .models import (
    Candidate,
    CandidateResult,
    CoalitionMembership,
    Constituency,
    Election,
    Manifesto,
    ManifestoPromise,
    Party,
    PartyFulfilmentClaim,
    PromiseAssessment,
)
from .serializers import CandidateSerializer, ConstituencySerializer, ManifestoSerializer, PartySerializer


def home(request):
    # Load 2021 candidate data for overview stats
    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / "fct_candidates_21.csv"
    rows = _load_party_rows(csv_path)
    stats = _compute_overview_stats(rows)

    return render(request, "core/home.html", {
        "total_parties": stats["total_parties"],
        "total_candidates": stats["total_candidates"],
        "overall_avg_cases": stats["overall_avg_cases"],
        "overall_avg_age": stats["overall_avg_age"],
        "overall_avg_assets": stats["overall_avg_assets"],
        "overall_avg_liabilities": stats["overall_avg_liabilities"],
    })


def resources(request):
    """External resources page with curated links to election data sources."""
    resources_list = [
        {
            "title": "IndiaVotes - Tamil Nadu 2021 Results",
            "url": "https://www.indiavotes.com/vidhan-sabha/2021/tamil-nadu/283/40",
            "description": "Comprehensive assembly-wise election results for Tamil Nadu 2021, including vote counts and margins for all constituencies.",
            "description_ta": "தமிழ்நாடு 2021 தொகுதி வாரியான தேர்தல் முடிவுகள், வாக்கு எண்ணிக்கை மற்றும் வெற்றி வித்தியாசம் உள்ளிட்ட தகவல்கள்.",
            "category": "Results",
        },
        {
            "title": "Tamil Nadu Assembly Elections Visual Analytics",
            "url": "https://data-analytics.github.io/Election_Data/tamil_nadu.html?extra_year=2021#individual_hold",
            "description": "Interactive visualizations of Tamil Nadu assembly elections from 1967 to 2021, with party-wise trends and constituency-level analysis.",
            "description_ta": "1967 முதல் 2021 வரையிலான தமிழ்நாடு சட்டமன்றத் தேர்தல்களின் ஊடாடும் காட்சிப்படுத்தல்கள், கட்சி வாரியான போக்குகள் மற்றும் தொகுதி நிலை பகுப்பாய்வு.",
            "category": "Analytics",
        },
        {
            "title": "DMK's 2021 Manifesto Promise Fulfilment (The Hindu)",
            "url": "https://www.thehindu.com/news/national/tamil-nadu/dmk-says-its-govt-fulfilled-80-of-poll-promises/article70467002.ece",
            "description": "News report on DMK's claim of fulfilling 80% of its 2021 election manifesto promises, with details on implemented and pending commitments.",
            "description_ta": "2021 தேர்தல் அறிக்கை வாக்குறுதிகளில் 80% நிறைவேற்றப்பட்டதாக திமுக அறிவிப்பு, செயல்படுத்தப்பட்ட மற்றும் நிலுவையில் உள்ள உறுதிமொழிகள் குறித்த விவரங்கள்.",
            "category": "News",
        },
        {
            "title": "MyNeta - Tamil Nadu Assembly Elections",
            "url": "https://myneta.info/state_assembly.php?state=Tamil%20Nadu",
            "description": "Candidate affidavit data from Election Commission archives, including criminal records, assets, liabilities, and educational qualifications for all candidates.",
            "description_ta": "தேர்தல் ஆணையக் காப்பகங்களிலிருந்து வேட்பாளர் வாக்குறுதி தரவு, குற்றப் பதிவுகள், சொத்துக்கள், கடன்கள் மற்றும் கல்வித் தகுதிகள் உள்ளிட்டவை.",
            "category": "Affidavits",
        },
        {
            "title": "MLA Election Expenditure Analysis 2021 (ADR)",
            "url": "https://adrindia.org/sites/default/files/Analysis_of_Election_Expenditure_Statements_of_MLA_Tamil_Nadu_Assembly_2021_English.pdf",
            "description": "Association for Democratic Reforms analysis of election expenditure statements filed by MLAs in Tamil Nadu 2021, including party-wise spending patterns.",
            "description_ta": "தமிழ்நாடு 2021-ல் சட்டமன்ற உறுப்பினர்கள் தாக்கல் செய்த தேர்தல் செலவு அறிக்கைகளின் ஜனநாயக சீர்திருத்த சங்கம் பகுப்பாய்வு.",
            "category": "Expenditure",
        },
        {
            "title": "16th Tamil Nadu Legislative Assembly Members",
            "url": "https://assembly.tn.gov.in/16thassembly/members.php",
            "description": "Official list of current MLAs from the Tamil Nadu Legislative Assembly website, with contact details and constituency information.",
            "description_ta": "தமிழ்நாடு சட்டமன்ற இணையதளத்திலிருந்து தற்போதைய சட்டமன்ற உறுப்பினர்களின் அதிகாரப்பூர்வ பட்டியல், தொடர்பு விவரங்கள் மற்றும் தொகுதி தகவல்கள்.",
            "category": "Official",
        },
        {
            "title": "NITI Aayog - Tamil Nadu Fiscal Landscape",
            "url": "https://www.niti.gov.in/sites/default/files/2025-03/Macro-and-Fiscal-Landscape-of-the-State-of-Tamil-Nadu.pdf",
            "description": "NITI Aayog report on Tamil Nadu's macroeconomic and fiscal landscape, providing context on state finances and development indicators.",
            "description_ta": "தமிழ்நாட்டின் பொருளாதார மற்றும் நிதி நிலப்பரப்பு குறித்த நிதி ஆயோக் அறிக்கை, மாநில நிதி மற்றும் வளர்ச்சி குறியீடுகள் பற்றிய சூழல்.",
            "category": "Economy",
        },
    ]
    return render(request, "core/resources.html", {"resources": resources_list})


def map_view(request):
    return render(request, "core/map.html")


@lru_cache(maxsize=1)
def _load_smla_rows() -> list[dict]:
    csv_path = settings.BASE_DIR.parent / "data" / "fct_candidates_21.csv"
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _normalize_constituency_name(name: Optional[str]) -> str:
    normalized = re.sub(r"[^A-Z0-9]+", " ", (name or "").strip().upper())
    return re.sub(r"\s+", " ", normalized).strip()


def _is_2016_row(row: dict) -> bool:
    candidate_name = (row.get("candidate") or "").strip().lower()
    constituency_name = _normalize_constituency_name(
        row.get("2021_constituency") or row.get("constituency")
    )
    return candidate_name == "ambethkumar s" and constituency_name == "VANDAVASI SC"


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


PARTY_COLORS = {
    "DMK": "#D7263D",
    "AIADMK": "#2E8B57",
    "BJP": "#E4572E",
    "INC": "#1D4ED8",
    "Pattali Makkal Katchi": "#D4A017",
    "PMK": "#D4A017",
    "Viduthalai Chiruthaigal Katchi": "#5B2A86",
    "VCK": "#5B2A86",
    "CPI": "#C1121F",
    "CPI(M)": "#8A1C1C",
    "CPI(ML)(L)": "#8A1C1C",
    "DMDK": "#F59E0B",
    "Amma Makkal Munnettra Kazagam": "#059669",
    "Makkal Needhi Maiam": "#0E7490",
    "Naam Tamilar Katchi": "#6D28D9",
    "IND": "#64748B",
}

LOCAL_PARTY_SYMBOLS = {
    "aiadmk": "aiadmk.svg",
    "all india anna dravida munnetra kazhagam": "aiadmk.svg",
    "bjp": "bjp.png",
    "bharatiya janata party": "bjp.png",
    "dmk": "Dravida_Munnetra_Kazhagam_logo.png",
    "dravida munnetra kazhagam": "Dravida_Munnetra_Kazhagam_logo.png",
    "inc": "inc.png",
    "indian national congress": "inc.png",
    "pmk": "Pmk_flag.jpg",
    "pattali makkal katchi": "Pmk_flag.jpg",
    "cpi": "cpi_election_symbol.svg.png",
    "communist party of india": "cpi_election_symbol.svg.png",
    "cpi(m)": "cpim.svg.png",
    "cpi m": "cpim.svg.png",
    "communist party of india (marxist)": "cpim.svg.png",
    "vck": "vck_election_symbol.png",
    "viduthalai chiruthaigal katchi": "vck_election_symbol.png",
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
        return f"/static/party_symbols/{filename}"
    return None


def map_data(request):
    rows = _load_smla_rows()
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

    # Force correct global alias for Tirupathur (overrides any auto-generated alias)
    # GeoJSON has "Tiruppattur" -> normalized "TIRUPPATTUR", CSV has "TIRUPATTUR"
    alias_by_district[""]["TIRUPPATTUR"] = "TIRUPATTUR"

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
                    "district": display_district,
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
    return JsonResponse({"type": "FeatureCollection", "features": features, "legend": legend})


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


def set_language(request, language: str):
    if language not in {"en", "ta"}:
        language = "en"
    request.session["language"] = language
    next_url = request.GET.get("next") or reverse("home")
    return redirect(next_url)

def constituency_detail(request, constituency_id: int):
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
    rows = _load_smla_rows()
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
    # GeoJSON has "Tiruppattur" -> normalized "TIRUPPATTUR", CSV has "TIRUPATTUR"
    alias_by_district[""]["TIRUPPATTUR"] = "TIRUPATTUR"

    constituency_key = _resolve_constituency_key(
        _normalize_constituency_name(constituency.name),
        _normalize_constituency_name(constituency.district),
        constituency_seen,
        district_candidates,
        alias_by_district,
        cutoff=0.85,
    )
    candidates = [
        row for row in rows
        if _normalize_constituency_name(row.get("2021_constituency")) == constituency_key
    ]
    district_name = (candidates[0].get("2021_district") if candidates else None) or constituency.district
    current_language = request.session.get("language", "en")

    # Attach key promises for each party/coalition (best-effort; missing data is OK).
    party_lookup: dict[str, Party] = {}
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

    coalition_by_party_id: dict[int, int] = {}
    if party_ids:
        election_2021 = Election.objects.filter(year=2021).first()
        memberships = CoalitionMembership.objects.select_related("coalition", "coalition__election").filter(
            party_id__in=party_ids
        )
        if election_2021:
            memberships = memberships.filter(coalition__election=election_2021)
        for membership in memberships:
            if membership.party_id and membership.coalition_id:
                coalition_by_party_id.setdefault(membership.party_id, membership.coalition_id)

    manifesto_by_party_id: dict[int, Manifesto] = {}
    manifesto_by_coalition_id: dict[int, Manifesto] = {}
    promise_chips_by_manifesto_id: dict[int, list[dict]] = {}
    state_assessment_by_party_promise: dict[tuple[int, int], PromiseAssessment] = {}
    constituency_assessment_by_promise: dict[int, PromiseAssessment] = {}
    claim_by_party_id: dict[int, PartyFulfilmentClaim] = {}
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

            election_2021 = Election.objects.filter(year=2021).first()
            claim_qs = PartyFulfilmentClaim.objects.filter(party_id__in=party_ids).select_related(
                "source_document", "election"
            )
            if election_2021:
                claim_qs = claim_qs.filter(election=election_2021)
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
    liabilities_values = [_parse_int(row.get("liabilities_rs")) for row in candidates]
    liabilities_values = [value for value in liabilities_values if value is not None]

    candidate_count = len(candidates)
    party_count = len({(row.get("party") or "").strip() for row in candidates if (row.get("party") or "").strip()})
    cases_positive = sum(1 for value in cases_values if value and value > 0)
    avg_cases = (sum(cases_values) / len(cases_values)) if cases_values else None
    avg_age = (sum(age_values) / len(age_values)) if age_values else None
    avg_assets = (sum(assets_values) / len(assets_values)) if assets_values else None
    avg_liabilities = (sum(liabilities_values) / len(liabilities_values)) if liabilities_values else None
    cases_pct = round((cases_positive / candidate_count) * 100, 1) if candidate_count else 0

    summary_cards = [
        {"label": "Candidates", "value": _format_indian_number(candidate_count)},
        {"label": "Parties", "value": _format_indian_number(party_count)},
        {"label": "Avg cases", "value": _format_indian_number(round(avg_cases, 2) if avg_cases is not None else None)},
        {"label": "Cases %", "value": f"{_format_indian_number(cases_pct)}%"},
        {"label": "Avg age", "value": _format_indian_number(round(avg_age, 1) if avg_age is not None else None)},
        {
            "label": "Avg assets",
            "value": f"₹ {_format_indian_number(round(avg_assets, 0) if avg_assets is not None else None)}",
        },
        {
            "label": "Avg liabilities",
            "value": f"₹ {_format_indian_number(round(avg_liabilities, 0) if avg_liabilities is not None else None)}",
        },
    ]

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
        candidate_cards.append(
            {
                "name": (row.get("candidate") or "").strip() or "Unknown",
                "party": (row.get("party") or "").strip() or "Independent / Unknown",
                "party_symbol": _party_symbol_url(raw_party) or party_symbols.get(raw_party.lower()),
                "is_2016": _is_2016_row(row),
                "education": (row.get("education") or "").strip(),
                "age": _parse_int(row.get("age")),
                "criminal_cases": _parse_int(row.get("criminal_cases")),
                "assets": assets_value,
                "liabilities": liabilities_value,
                "sitting": str(row.get("sitting_MLA", "")).strip() == "1",
                "key_promises": key_promises,
                "state_delivery": state_delivery,
                "constituency_delivery": constituency_delivery,
            }
        )
    return render(
        request,
        "core/constituency_detail.html",
        {
            "constituency": constituency,
            "district_name": district_name,
            "summary_cards": summary_cards,
            "candidate_cards": candidate_cards,
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


def _load_party_rows(csv_path: Path) -> list[dict]:
    if not csv_path.exists():
        return []
    with csv_path.open("r", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        return list(reader)


def _compute_overview_stats(rows: list[dict]) -> dict:
    """Compute overview statistics from candidate rows."""
    if not rows:
        return {
            "total_parties": 0,
            "total_candidates": 0,
            "overall_avg_cases": None,
            "overall_avg_age": None,
            "overall_avg_assets": None,
            "overall_avg_liabilities": None,
        }

    party_set = set()
    cases_total = 0
    cases_count = 0
    age_total = 0
    age_count = 0
    assets_total = 0
    assets_count = 0
    liabilities_total = 0
    liabilities_count = 0

    for row in rows:
        party_name = (row.get("party") or "").strip() or "Independent / Unknown"
        party_set.add(party_name)

        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        assets_value = _parse_int(row.get("total_assets_rs"))
        liabilities_value = _parse_int(row.get("liabilities_rs"))

        if cases_value is not None:
            cases_total += cases_value
            cases_count += 1
        if age_value is not None:
            age_total += age_value
            age_count += 1
        if assets_value is not None:
            assets_total += assets_value
            assets_count += 1
        if liabilities_value is not None:
            liabilities_total += liabilities_value
            liabilities_count += 1

    return {
        "total_parties": len(party_set),
        "total_candidates": len(rows),
        "overall_avg_cases": round(cases_total / cases_count, 2) if cases_count else None,
        "overall_avg_age": round(age_total / age_count, 1) if age_count else None,
        "overall_avg_assets": round(assets_total / assets_count, 0) if assets_count else None,
        "overall_avg_liabilities": round(liabilities_total / liabilities_count, 0) if liabilities_count else None,
    }


def party_dashboard(request):
    year = request.GET.get("year", "2021").strip()
    if year not in {"2021", "2026"}:
        year = "2021"
    min_cases_raw = request.GET.get("min_cases", "").strip()
    max_cases_raw = request.GET.get("max_cases", "").strip()
    min_age_raw = request.GET.get("min_age", "").strip()
    max_age_raw = request.GET.get("max_age", "").strip()
    min_assets_raw = request.GET.get("min_assets", "").strip()
    max_assets_raw = request.GET.get("max_assets", "").strip()
    min_liabilities_raw = request.GET.get("min_liabilities", "").strip()
    max_liabilities_raw = request.GET.get("max_liabilities", "").strip()
    sitting_filter = request.GET.get("sitting_mla", "").strip()
    district_filter = request.GET.get("district", "").strip()
    constituency_filter = request.GET.get("constituency", "").strip()
    min_cases = _parse_int(min_cases_raw)
    max_cases = _parse_int(max_cases_raw)
    min_age = _parse_int(min_age_raw)
    max_age = _parse_int(max_age_raw)
    min_assets = _parse_int(min_assets_raw)
    max_assets = _parse_int(max_assets_raw)
    min_liabilities = _parse_int(min_liabilities_raw)
    max_liabilities = _parse_int(max_liabilities_raw)
    selected_party = request.GET.get("party", "").strip()
    sort_key = request.GET.get("sort", "candidate_count")
    sort_order = request.GET.get("order", "desc")

    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("tn_2026_candidates.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(csv_path)
    has_sitting = bool(rows and "sitting_MLA" in rows[0])
    cases_bounds = _numeric_bounds(rows, "criminal_cases")
    age_bounds = _numeric_bounds(rows, "age")
    assets_bounds = _numeric_bounds(rows, "total_assets_rs")
    liabilities_bounds = _numeric_bounds(rows, "liabilities_rs")

    cases_filter_active = False
    if min_cases is not None and min_cases > cases_bounds[0]:
        cases_filter_active = True
    if max_cases is not None and max_cases < cases_bounds[1]:
        cases_filter_active = True
    if not cases_filter_active:
        min_cases = None
        max_cases = None

    age_filter_active = False
    if min_age is not None and min_age > age_bounds[0]:
        age_filter_active = True
    if max_age is not None and max_age < age_bounds[1]:
        age_filter_active = True
    if not age_filter_active:
        min_age = None
        max_age = None

    assets_filter_active = False
    if min_assets is not None and min_assets > assets_bounds[0]:
        assets_filter_active = True
    if max_assets is not None and max_assets < assets_bounds[1]:
        assets_filter_active = True
    if not assets_filter_active:
        min_assets = None
        max_assets = None

    liabilities_filter_active = False
    if min_liabilities is not None and min_liabilities > liabilities_bounds[0]:
        liabilities_filter_active = True
    if max_liabilities is not None and max_liabilities < liabilities_bounds[1]:
        liabilities_filter_active = True
    if not liabilities_filter_active:
        min_liabilities = None
        max_liabilities = None

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
        liabilities_value = _parse_int(row.get("liabilities_rs"))
        sitting_value = _parse_int(row.get("sitting_MLA"))
        party_name = (row.get("party") or "").strip() or "Independent / Unknown"
        district_name = _row_value(row, district_key)
        constituency_name = _row_value(row, constituency_key)
        party_set.add(party_name)
        if district_name:
            district_set.add(district_name)
            if constituency_name:
                district_map[district_name].add(constituency_name)
        if cases_filter_active and cases_value is not None:
            if min_cases is not None and cases_value < min_cases:
                continue
            if max_cases is not None and cases_value > max_cases:
                continue
        if age_filter_active and age_value is not None:
            if min_age is not None and age_value < min_age:
                continue
            if max_age is not None and age_value > max_age:
                continue
        if assets_filter_active and assets_value is not None:
            if min_assets is not None and assets_value < min_assets:
                continue
            if max_assets is not None and assets_value > max_assets:
                continue
        if liabilities_filter_active and liabilities_value is not None:
            if min_liabilities is not None and liabilities_value < min_liabilities:
                continue
            if max_liabilities is not None and liabilities_value > max_liabilities:
                continue
        if has_sitting and sitting_filter in {"0", "1"}:
            if sitting_value is None or str(sitting_value) != sitting_filter:
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
        "liabilities_total": 0,
        "liabilities_count": 0,
        "sitting_total": 0,
        "education_counts": Counter(),
    })

    for row in filtered_rows:
        party = (row.get("party") or "").strip() or "Independent / Unknown"
        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        education_value = (row.get("education") or "").strip()
        assets_value = _parse_int(row.get("total_assets_rs"))
        liabilities_value = _parse_int(row.get("liabilities_rs"))
        sitting_value = _parse_int(row.get("sitting_MLA"))

        bucket = party_data[party]
        bucket["count"] += 1
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
        if liabilities_value is not None:
            bucket["liabilities_total"] += liabilities_value
            bucket["liabilities_count"] += 1
        if sitting_value is not None and sitting_value > 0:
            bucket["sitting_total"] += 1
        if education_value:
            bucket["education_counts"][education_value] += 1

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
        avg_liabilities = (
            round(stats["liabilities_total"] / stats["liabilities_count"], 0)
            if stats["liabilities_count"]
            else None
        )
        cases_pct = round((stats["cases_positive"] / stats["count"]) * 100, 1) if stats["count"] else 0.0
        top_education = stats["education_counts"].most_common(1)
        party_stats.append(
            {
                "party": party,
                "party_display": _display_party_name(party),
                "party_symbol": _party_symbol_url(party),
                "candidate_count": stats["count"],
                "avg_cases": avg_cases,
                "avg_age": avg_age,
                "avg_assets": avg_assets,
                "avg_liabilities": avg_liabilities,
                "cases_pct": cases_pct,
                "top_education": top_education[0][0] if top_education else "",
            }
        )

    sort_map = {
        "party": "party",
        "candidate_count": "candidate_count",
        "avg_cases": "avg_cases",
        "cases_pct": "cases_pct",
        "avg_age": "avg_age",
        "avg_assets": "avg_assets",
        "avg_liabilities": "avg_liabilities",
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

    total_candidates = sum(item["candidate_count"] for item in party_stats)
    total_parties = len(party_stats)
    overall_avg_cases = None
    overall_avg_age = None
    overall_avg_assets = None
    overall_avg_liabilities = None
    cases_values = [item["avg_cases"] for item in party_stats if item["avg_cases"] is not None]
    age_values = [item["avg_age"] for item in party_stats if item["avg_age"] is not None]
    assets_values = [item["avg_assets"] for item in party_stats if item["avg_assets"] is not None]
    liabilities_values = [
        item["avg_liabilities"] for item in party_stats if item["avg_liabilities"] is not None
    ]
    if cases_values:
        overall_avg_cases = round(sum(cases_values) / len(cases_values), 2)
    if age_values:
        overall_avg_age = round(sum(age_values) / len(age_values), 1)
    if assets_values:
        overall_avg_assets = round(sum(assets_values) / len(assets_values), 0)
    if liabilities_values:
        overall_avg_liabilities = round(sum(liabilities_values) / len(liabilities_values), 0)

    available_constituencies = sorted(district_map.get(district_filter, set())) if district_filter else sorted(
        {const for consts in district_map.values() for const in consts}
    )

    def _coerce_slider(raw_value: str, bounds: tuple[int, int], default: int) -> int:
        parsed = _parse_int(raw_value)
        return parsed if parsed is not None else default

    base_query = {
        "year": year,
        "min_cases": min_cases_raw,
        "max_cases": max_cases_raw,
        "min_age": min_age_raw,
        "max_age": max_age_raw,
        "min_assets": min_assets_raw,
        "max_assets": max_assets_raw,
        "min_liabilities": min_liabilities_raw,
        "max_liabilities": max_liabilities_raw,
        "sitting_mla": sitting_filter,
        "party": selected_party,
        "district": district_filter,
        "constituency": constituency_filter,
    }
    base_query_no_party = dict(base_query)
    base_query_no_party.pop("party", None)
    party_options = sorted(
        [{"value": party, "label": _display_party_name(party)} for party in party_set],
        key=lambda item: item["label"].lower(),
    )
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
            "overall_avg_liabilities": overall_avg_liabilities,
            "party_stats": party_stats,
            "min_cases": _coerce_slider(min_cases_raw, cases_bounds, cases_bounds[0]),
            "max_cases": _coerce_slider(max_cases_raw, cases_bounds, cases_bounds[1]),
            "min_age": _coerce_slider(min_age_raw, age_bounds, age_bounds[0]),
            "max_age": _coerce_slider(max_age_raw, age_bounds, age_bounds[1]),
            "min_assets": _coerce_slider(min_assets_raw, assets_bounds, assets_bounds[0]),
            "max_assets": _coerce_slider(max_assets_raw, assets_bounds, assets_bounds[1]),
            "min_liabilities": _coerce_slider(min_liabilities_raw, liabilities_bounds, liabilities_bounds[0]),
            "max_liabilities": _coerce_slider(max_liabilities_raw, liabilities_bounds, liabilities_bounds[1]),
            "cases_bounds": cases_bounds,
            "age_bounds": age_bounds,
            "assets_bounds": assets_bounds,
            "liabilities_bounds": liabilities_bounds,
            "sitting_mla": sitting_filter if has_sitting else "",
            "rows_count": len(filtered_rows),
            "party_options": party_options,
            "selected_party": selected_party,
            "districts": sorted(district_set),
            "selected_district": district_filter,
            "constituencies": available_constituencies,
            "selected_constituency": constituency_filter,
            "sort_key": sort_key,
            "sort_order": sort_order,
            "base_query": urlencode(base_query, doseq=True),
            "base_query_no_party": urlencode(base_query_no_party, doseq=True),
        },
    )


def party_detail(request, party_name: str):
    year = request.GET.get("year", "2021").strip()
    if year not in {"2021", "2026"}:
        year = "2021"
    party_display_name = _display_party_name(party_name)
    party_symbol = _party_symbol_url(party_name)
    min_cases_raw = request.GET.get("min_cases", "").strip()
    max_cases_raw = request.GET.get("max_cases", "").strip()
    min_age_raw = request.GET.get("min_age", "").strip()
    max_age_raw = request.GET.get("max_age", "").strip()
    min_assets_raw = request.GET.get("min_assets", "").strip()
    max_assets_raw = request.GET.get("max_assets", "").strip()
    min_liabilities_raw = request.GET.get("min_liabilities", "").strip()
    max_liabilities_raw = request.GET.get("max_liabilities", "").strip()
    sitting_filter = request.GET.get("sitting_mla", "").strip()
    district_filter = request.GET.get("district", "").strip()
    constituency_filter = request.GET.get("constituency", "").strip()
    min_cases = _parse_int(min_cases_raw)
    max_cases = _parse_int(max_cases_raw)
    min_age = _parse_int(min_age_raw)
    max_age = _parse_int(max_age_raw)
    min_assets = _parse_int(min_assets_raw)
    max_assets = _parse_int(max_assets_raw)
    min_liabilities = _parse_int(min_liabilities_raw)
    max_liabilities = _parse_int(max_liabilities_raw)
    data_dir = settings.BASE_DIR.parent / "data"
    csv_path = data_dir / ("tn_2026_candidates.csv" if year == "2026" else "fct_candidates_21.csv")
    rows = _load_party_rows(csv_path)

    has_sitting = bool(rows and "sitting_MLA" in rows[0])
    district_key = ("2021_district", "district")
    constituency_key = ("2021_constituency", "constituency")
    district_set = set()
    district_map: dict[str, set[str]] = defaultdict(set)
    all_party_rows = []

    for row in rows:
        if (row.get("party") or "").strip() != party_name:
            continue
        all_party_rows.append(row)
        district_name = _row_value(row, district_key)
        constituency_name = _row_value(row, constituency_key)
        if district_name:
            district_set.add(district_name)
            if constituency_name:
                district_map[district_name].add(constituency_name)

    cases_bounds = _numeric_bounds(all_party_rows, "criminal_cases")
    age_bounds = _numeric_bounds(all_party_rows, "age")
    assets_bounds = _numeric_bounds(all_party_rows, "total_assets_rs")
    liabilities_bounds = _numeric_bounds(all_party_rows, "liabilities_rs")
    cases_filter_active = False
    if min_cases is not None and min_cases > cases_bounds[0]:
        cases_filter_active = True
    if max_cases is not None and max_cases < cases_bounds[1]:
        cases_filter_active = True
    if not cases_filter_active:
        min_cases = None
        max_cases = None

    age_filter_active = False
    if min_age is not None and min_age > age_bounds[0]:
        age_filter_active = True
    if max_age is not None and max_age < age_bounds[1]:
        age_filter_active = True
    if not age_filter_active:
        min_age = None
        max_age = None

    assets_filter_active = False
    if min_assets is not None and min_assets > assets_bounds[0]:
        assets_filter_active = True
    if max_assets is not None and max_assets < assets_bounds[1]:
        assets_filter_active = True
    if not assets_filter_active:
        min_assets = None
        max_assets = None

    liabilities_filter_active = False
    if min_liabilities is not None and min_liabilities > liabilities_bounds[0]:
        liabilities_filter_active = True
    if max_liabilities is not None and max_liabilities < liabilities_bounds[1]:
        liabilities_filter_active = True
    if not liabilities_filter_active:
        min_liabilities = None
        max_liabilities = None

    party_rows = []
    for row in all_party_rows:
        cases_value = _parse_int(row.get("criminal_cases"))
        age_value = _parse_int(row.get("age"))
        assets_value = _parse_int(row.get("total_assets_rs"))
        liabilities_value = _parse_int(row.get("liabilities_rs"))
        sitting_value = _parse_int(row.get("sitting_MLA"))
        district_name = _row_value(row, district_key)
        constituency_name = _row_value(row, constituency_key)

        if cases_filter_active and cases_value is not None:
            if min_cases is not None and cases_value < min_cases:
                continue
            if max_cases is not None and cases_value > max_cases:
                continue
        if age_filter_active and age_value is not None:
            if min_age is not None and age_value < min_age:
                continue
            if max_age is not None and age_value > max_age:
                continue
        if assets_filter_active and assets_value is not None:
            if min_assets is not None and assets_value < min_assets:
                continue
            if max_assets is not None and assets_value > max_assets:
                continue
        if liabilities_filter_active and liabilities_value is not None:
            if min_liabilities is not None and liabilities_value < min_liabilities:
                continue
            if max_liabilities is not None and liabilities_value > max_liabilities:
                continue
        if has_sitting and sitting_filter in {"0", "1"}:
            if sitting_value is None or str(sitting_value) != sitting_filter:
                continue
        if district_filter and district_name != district_filter:
            continue
        if constituency_filter and constituency_name != constituency_filter:
            continue
        party_rows.append(row)

    headers = list(rows[0].keys()) if rows else []
    excluded_headers = {"party", "sitting_MLA", "bye_election", "total_assets", "liabilities", "const_off"}
    allowed_headers = [header for header in headers if header not in excluded_headers]
    label_overrides = {
        "total_assets_rs": "Total Assets (₹)",
        "liabilities_rs": "Total Liabilities (₹)",
        "criminal_cases": "Criminal cases",
        "2021_constituency": "Constituency",
        "2021_district": "District",
        "myneta_url": "More Info",
    }
    columns = [
        {
            "key": header,
            "label": label_overrides.get(header, header.replace("_", " ").title()),
            "is_currency": header in {"total_assets_rs", "liabilities_rs"},
            "is_number": header in {"criminal_cases", "age", "total_assets_rs", "liabilities_rs"},
        }
        for header in allowed_headers
    ]
    myneta_key = "myneta_url"
    constituency_header = None
    if "2021_constituency" in allowed_headers:
        constituency_header = "2021_constituency"
    elif "constituency" in allowed_headers:
        constituency_header = "constituency"
    rows_table = []
    for row in party_rows:
        row_data = {header: row.get(header, "") for header in allowed_headers}
        const_off = (row.get("const_off") or "").strip()
        if constituency_header and const_off:
            row_data[constituency_header] = const_off
        for district_header in ("2021_district", "district"):
            if district_header in row_data and row_data[district_header]:
                row_data[district_header] = str(row_data[district_header]).strip().title()
        row_data["is_2016"] = _is_2016_row(row)
        rows_table.append(row_data)
    available_constituencies = sorted(district_map.get(district_filter, set())) if district_filter else sorted(
        {const for consts in district_map.values() for const in consts}
    )

    def _coerce_slider(raw_value: str, bounds: tuple[int, int], default: int) -> int:
        parsed = _parse_int(raw_value)
        return parsed if parsed is not None else default

    return render(
        request,
        "core/party_detail.html",
        {
            "party_name": party_name,
            "party_display_name": party_display_name,
            "party_symbol": party_symbol,
            "year": year,
            "rows": rows_table,
            "columns": columns,
            "myneta_key": myneta_key,
            "row_count": len(party_rows),
            "min_cases": _coerce_slider(min_cases_raw, cases_bounds, cases_bounds[0]),
            "max_cases": _coerce_slider(max_cases_raw, cases_bounds, cases_bounds[1]),
            "min_age": _coerce_slider(min_age_raw, age_bounds, age_bounds[0]),
            "max_age": _coerce_slider(max_age_raw, age_bounds, age_bounds[1]),
            "min_assets": _coerce_slider(min_assets_raw, assets_bounds, assets_bounds[0]),
            "max_assets": _coerce_slider(max_assets_raw, assets_bounds, assets_bounds[1]),
            "min_liabilities": _coerce_slider(min_liabilities_raw, liabilities_bounds, liabilities_bounds[0]),
            "max_liabilities": _coerce_slider(max_liabilities_raw, liabilities_bounds, liabilities_bounds[1]),
            "cases_bounds": cases_bounds,
            "age_bounds": age_bounds,
            "assets_bounds": assets_bounds,
            "liabilities_bounds": liabilities_bounds,
            "sitting_mla": sitting_filter if has_sitting else "",
            "districts": sorted(district_set),
            "selected_district": district_filter,
            "constituencies": available_constituencies,
            "selected_constituency": constituency_filter,
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
