import csv
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

from .models import Candidate, CandidateResult, Constituency, Election, Manifesto, Party
from .serializers import CandidateSerializer, ConstituencySerializer, ManifestoSerializer, PartySerializer


def home(request):
    return render(request, "core/home.html")


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


def _match_constituency_key(key: str, candidates: set[str]) -> str:
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
    matches = difflib.get_close_matches(key, candidates, n=1, cutoff=0.9)
    return matches[0] if matches else key


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


def _party_color(party_name: Optional[str]) -> Optional[str]:
    if not party_name:
        return None
    return PARTY_COLORS.get(party_name.strip())


def map_data(request):
    rows = _load_smla_rows()
    sitting_lookup: dict[str, dict] = {}
    constituency_seen: set[str] = set()
    official_lookup = _load_official_constituencies()
    for row in rows:
        constituency_key = _normalize_constituency_name(row.get("2021_constituency"))
        if constituency_key:
            constituency_seen.add(constituency_key)
        if str(row.get("sitting_MLA", "")).strip() != "1":
            continue
        party_name = (row.get("party") or "").strip()
        sitting_lookup[constituency_key] = {
            "party": party_name,
            "party_color": _party_color(party_name),
        }

    features = []
    for constituency in Constituency.objects.exclude(boundary_geojson__isnull=True):
        raw_key = _normalize_constituency_name(constituency.name)
        constituency_key = _match_constituency_key(raw_key, constituency_seen)
        lookup = sitting_lookup.get(constituency_key, {})
        is_vacant = constituency_key in constituency_seen and not lookup
        is_unknown = constituency_key not in constituency_seen
        official_name = official_lookup.get(constituency_key) or official_lookup.get(raw_key) or constituency.name
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": constituency.id,
                    "name": official_name,
                    "district": constituency.district,
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
    rows = _load_smla_rows()
    constituency_key = _normalize_constituency_name(constituency.name)
    candidates = [
        row for row in rows
        if _normalize_constituency_name(row.get("2021_constituency")) == constituency_key
    ]
    district_name = (candidates[0].get("2021_district") if candidates else None) or constituency.district

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
        candidate_cards.append(
            {
                "name": (row.get("candidate") or "").strip() or "Unknown",
                "party": (row.get("party") or "").strip() or "Independent / Unknown",
                "education": (row.get("education") or "").strip(),
                "age": _parse_int(row.get("age")),
                "criminal_cases": _parse_int(row.get("criminal_cases")),
                "assets": assets_value,
                "liabilities": liabilities_value,
                "sitting": str(row.get("sitting_MLA", "")).strip() == "1",
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
    return render(
        request,
        "core/candidate_detail.html",
        {
            "candidate": candidate,
            "election": election,
            "missing_affidavit": not candidate.affidavits.all(),
            "missing_legal": not candidate.legal_cases.all(),
            "missing_results": not candidate.results_2021,
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
        sitting_pct = round((stats["sitting_total"] / stats["count"]) * 100, 1) if stats["count"] else 0.0
        top_education = stats["education_counts"].most_common(1)
        party_stats.append(
            {
                "party": party,
                "candidate_count": stats["count"],
                "avg_cases": avg_cases,
                "avg_age": avg_age,
                "avg_assets": avg_assets,
                "avg_liabilities": avg_liabilities,
                "cases_pct": cases_pct,
                "sitting_pct": sitting_pct,
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
        "sitting_pct": "sitting_pct",
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
            "all_parties": sorted(party_set),
            "selected_party": selected_party,
            "districts": sorted(district_set),
            "selected_district": district_filter,
            "constituencies": available_constituencies,
            "selected_constituency": constituency_filter,
            "sort_key": sort_key,
            "sort_order": sort_order,
            "base_query": urlencode(base_query, doseq=True),
        },
    )


def party_detail(request, party_name: str):
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
    excluded_headers = {"party", "sitting_MLA", "bye_election", "total_assets", "liabilities"}
    allowed_headers = [header for header in headers if header not in excluded_headers]
    label_overrides = {
        "total_assets_rs": "Total Assets (₹)",
        "liabilities_rs": "Total Liabilities (₹)",
        "criminal_cases": "Criminal cases",
        "2021_constituency": "Constituency",
        "2021_district": "District",
        "myneta_url": "Myneta",
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
    rows_table = [
        {header: row.get(header, "") for header in allowed_headers}
        for row in party_rows
    ]
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
