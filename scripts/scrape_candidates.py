from __future__ import annotations

import csv
import re
import sys
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright
from rapidfuzz import process as fuzz_process
from rapidfuzz import fuzz

MYNETA_BASE = "https://www.myneta.info/TamilNadu2021/"
ONEINDIA_URL = "https://www.oneindia.com/ntk-candidates-list-for-tamil-nadu-assembly-election/"

OUTPUT_DIR = Path("data")
OUTPUT_2021 = OUTPUT_DIR / "tn_2021_candidates.csv"
OUTPUT_2021_EXTENDED = OUTPUT_DIR / "tn_2021_candidates_extended.csv"
OUTPUT_2026 = OUTPUT_DIR / "tn_2026_candidates.csv"

PARTIES_2026 = ["NTK", "DMK", "AIADMK", "BJP", "INC", "MDMK", "PMK", "VCK", "TVK"]
DEBUG_LOG_PATH = Path(".cursor/debug.log")


# region agent log
def _debug_log(location: str, message: str, data: dict, hypothesis_id: str, run_id: str = "run1") -> None:
    payload = {
        "sessionId": "debug-session",
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")
# endregion agent log


@dataclass
class MynetaCandidate:
    name: str
    party: str
    criminal_cases: str
    education: str
    age: str
    total_assets: str
    liabilities: str
    constituency: str
    district: str
    myneta_url: str

    @property
    def name_norm(self) -> str:
        return normalize_text(self.name)

    @property
    def constituency_norm(self) -> str:
        return normalize_text(self.constituency)


def normalize_text(value: str) -> str:
    value = value.lower()
    value = re.sub(r"\(.*?\)", " ", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return " ".join(value.split()).strip()


def _get_text(element) -> str:
    return " ".join(element.get_text(" ", strip=True).split())


def _extract_rs_amount(value: str) -> str:
    if not value:
        return ""
    match = re.search(r"Rs\s*([0-9,]+)", value)
    if not match:
        return ""
    return match.group(1).replace(",", "")


def _fetch_html(url: str) -> str:
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_fetch_html:entry",
        "fetch start",
        {"url": url},
        hypothesis_id="H1",
    )
    # endregion agent log
    response = requests.get(url, headers={"User-Agent": "tntracker/1.0"}, timeout=30)
    response.raise_for_status()
    html = response.text
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_fetch_html:response",
        "fetched html",
        {"chars": len(html)},
        hypothesis_id="H2",
    )
    # endregion agent log
    # NOTE: regex extraction is commented out; keeping for comparison/debugging.
    # panel_match = re.search(
    #     r"<div[^>]*class=[\"']w3-panel w3-leftbar w3-light-gray[\"'][^>]*>.*?</div>",
    #     html,
    #     flags=re.IGNORECASE | re.DOTALL,
    # )
    # responsive_match = re.search(
    #     r"<div[^>]*class=[\"']w3-responsive[\"'][^>]*>.*?</div>",
    #     html,
    #     flags=re.IGNORECASE | re.DOTALL,
    # )
    # _debug_log(
    #     "scripts/scrape_candidates.py:_fetch_html:matches",
    #     "panel/responsive match",
    #     {
    #         "panel_found": bool(panel_match),
    #         "responsive_found": bool(responsive_match),
    #         "panel_chars": len(panel_match.group(0)) if panel_match else 0,
    #         "responsive_chars": len(responsive_match.group(0)) if responsive_match else 0,
    #     },
    #     hypothesis_id="H3",
    # )
    # if not panel_match and not responsive_match:
    #     return html

    # Always use a real browser render for candidate pages.
    is_candidate_page = "show_candidates" in url and "constituency_id=" in url
    if is_candidate_page:
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_fetch_html:playwright",
            "candidate page, using playwright",
            {"requested_url": url},
            hypothesis_id="H6",
        )
        # endregion agent log
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            html = page.content()
            browser.close()
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_fetch_html:playwright_done",
            "playwright html fetched",
            {"chars": len(html)},
            hypothesis_id="H7",
        )
        # endregion agent log

    # If this is the base page, keep full HTML so we can extract district links.
    if not is_candidate_page:
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_fetch_html:base_page",
            "returning full base HTML",
            {"url": url, "chars": len(html)},
            hypothesis_id="H8",
        )
        # endregion agent log
        return html

    soup = BeautifulSoup(html, "lxml")
    if is_candidate_page:
        responsive_probe = soup.find("div", class_=lambda value: value and "w3-responsive" in value)
        table_probe = responsive_probe.find("table") if responsive_probe else None
        row_probe = table_probe.find_all("tr") if table_probe else []
        sno_values = []
        for row in row_probe:
            cells = row.find_all("td")
            if not cells:
                continue
            sno_text = _get_text(cells[0])
            if sno_text.isdigit():
                sno_values.append(int(sno_text))
        expected_ok = bool(sno_values) and min(sno_values) == 1 and max(sno_values) == len(sno_values)
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_fetch_html:playwright_rows",
            "row count and SNo check",
            {"url": url, "rows": len(row_probe), "sno_count": len(sno_values), "sno_ok": expected_ok},
            hypothesis_id="H13",
        )
        # endregion agent log
        if not expected_ok:
            # region agent log
            _debug_log(
                "scripts/scrape_candidates.py:_fetch_html:playwright_retry",
                "SNo check failed, retrying with networkidle",
                {"url": url, "rows": len(row_probe)},
                hypothesis_id="H14",
            )
            # endregion agent log
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page()
                page.goto(url, wait_until="networkidle", timeout=60000)
                html = page.content()
                browser.close()
            soup = BeautifulSoup(html, "lxml")
            responsive_probe = soup.find("div", class_=lambda value: value and "w3-responsive" in value)
            table_probe = responsive_probe.find("table") if responsive_probe else None
            row_probe = table_probe.find_all("tr") if table_probe else []
            sno_values = []
            for row in row_probe:
                cells = row.find_all("td")
                if not cells:
                    continue
                sno_text = _get_text(cells[0])
                if sno_text.isdigit():
                    sno_values.append(int(sno_text))
            expected_ok = bool(sno_values) and min(sno_values) == 1 and max(sno_values) == len(sno_values)
            # region agent log
            _debug_log(
                "scripts/scrape_candidates.py:_fetch_html:playwright_retry_rows",
                "row count after retry",
                {"url": url, "rows": len(row_probe), "sno_count": len(sno_values), "sno_ok": expected_ok},
                hypothesis_id="H15",
            )
            # endregion agent log
    panel_div = soup.find(
        "div",
        class_=lambda value: value
        and "w3-panel" in value
        and "w3-leftbar" in value
        and "w3-light-gray" in value,
    )
    responsive_div = soup.find("div", class_=lambda value: value and "w3-responsive" in value)
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_fetch_html:matches",
        "panel/responsive match (bs4)",
        {
            "panel_found": panel_div is not None,
            "responsive_found": responsive_div is not None,
        },
        hypothesis_id="H3",
    )
    # endregion agent log
    if not panel_div and not responsive_div:
        return html

    parts: list[str] = []
    if panel_div:
        parts.append(str(panel_div))
    if responsive_div:
        table = responsive_div.find("table")
        rows = table.find_all("tr") if table else responsive_div.find_all("tr")
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_fetch_html:rows",
            "responsive rows extracted (bs4)",
            {"rows": len(rows), "table_found": table is not None},
            hypothesis_id="H4",
        )
        # endregion agent log
        table_html = (
            '<div class="w3-responsive"><table class="w3-table w3-bordered"><tbody>'
            + "".join(str(row) for row in rows)
            + "</tbody></table></div>"
        )
        parts.append(table_html)
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_fetch_html:return",
        "slim html built",
        {"parts": len(parts), "chars": len("\n".join(parts))},
        hypothesis_id="H5",
    )
    # endregion agent log
    return "\n".join(parts)


def _extract_constituency_links() -> list[str]:
    html = _fetch_html(MYNETA_BASE)
    soup = BeautifulSoup(html, "html.parser")
    links = []
    for link in soup.find_all("a", href=True):
        href = link["href"]
        if "show_candidates" in href and "constituency_id=" in href:
            links.append(urljoin(MYNETA_BASE, href))
    return sorted(set(links))


def _parse_myneta_candidates(url: str) -> list[MynetaCandidate]:
    html = _fetch_html(url)
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_parse_myneta_candidates:html",
        "received html",
        {"chars": len(html)},
        hypothesis_id="H2",
    )
    # endregion agent log
    soup = BeautifulSoup(html, "lxml")
    constituency = "Unknown"
    district = "Unknown"
    header_panel = soup.find("div", class_=lambda value: value and "w3-panel" in value)
    if header_panel:
        bold = header_panel.find("b")
        if bold:
            constituency = _get_text(bold)
        links = header_panel.find_all("a")
        if links:
            district_text = _get_text(links[-1])
            if district_text:
                district = district_text
    if constituency == "Unknown":
        header = soup.find("h3")
        if header:
            header_text = _get_text(header)
            match = re.search(r"List of Candidates\s*-\s*([^:]+)", header_text, re.IGNORECASE)
            if match:
                constituency = match.group(1).strip()
            district_match = re.search(r":\s*([^:]+)$", header_text)
            if district_match:
                district = district_match.group(1).strip()

    # NOTE: regex extraction is commented out; keeping for comparison/debugging.
    # table_match = re.search(
    #     r"<table[^>]*class=[\"']w3-table w3-bordered[\"'][^>]*>.*?</table>",
    #     html,
    #     flags=re.IGNORECASE | re.DOTALL,
    # )
    # if not table_match:
    #     _debug_log(
    #         "scripts/scrape_candidates.py:_parse_myneta_candidates:table_match",
    #         "no table match",
    #         {"has_table": False},
    #         hypothesis_id="H3",
    #     )
    #     return []
    # print("\n\n\n\n\n[myneta-debug] table_match:", table_match)
    # table_html = re.sub(r"<script[^>]*>.*?</script>", "", table_match.group(0), flags=re.DOTALL)
    # print("\n\n\n\n\n[myneta-debug] table_html snippet:", table_html[:1000])
    # table = BeautifulSoup(table_html, "lxml").find("table")
    # print("\n\n\n\n\n[myneta-debug] table found:", table is not None)
    # if not table:
    #     return []

    responsive_div = soup.find("div", class_=lambda value: value and "w3-responsive" in value)
    table = responsive_div.find("table") if responsive_div else None
    if not table:
        # region agent log
        _debug_log(
            "scripts/scrape_candidates.py:_parse_myneta_candidates:table_match",
            "no table found (bs4)",
            {"has_table": False, "responsive_found": responsive_div is not None},
            hypothesis_id="H3",
        )
        # endregion agent log
        return []
    # region agent log
    _debug_log(
        "scripts/scrape_candidates.py:_parse_myneta_candidates:table",
        "table parsed",
        {"table_found": True, "tr_count": len(table.find_all("tr"))},
        hypothesis_id="H4",
    )
    # endregion agent log
    rows_debug = table.find_all("tr")
    print("[myneta-debug] tr count:", len(rows_debug))
    for idx, row in enumerate(rows_debug[:5]):
        print(f"[myneta-debug] tr[{idx}] cells:", [ _get_text(td) for td in row.find_all("td") ])
    # region agent log
    if "constituency_id=1" in url or "constituency_id=112" in url:
        td_counts = [len(row.find_all("td")) for row in rows_debug]
        _debug_log(
            "scripts/scrape_candidates.py:_parse_myneta_candidates:rows_debug",
            "row td counts",
            {"url": url, "tr_count": len(rows_debug), "td_counts": td_counts[:40]},
            hypothesis_id="H9",
        )
    # endregion agent log

    headers = [normalize_text(_get_text(th)) for th in table.find_all("th")]
    header_map = {name: idx for idx, name in enumerate(headers)}

    def _col_index(*names: str) -> int | None:
        for name in names:
            key = normalize_text(name)
            if key in header_map:
                return header_map[key]
        return None

    name_idx = _col_index("candidate")
    party_idx = _col_index("party")
    crime_idx = _col_index("criminal cases", "criminal case")
    edu_idx = _col_index("education")
    age_idx = _col_index("age")
    assets_idx = _col_index("total assets", "assets")
    liabilities_idx = _col_index("liabilities")
    if name_idx is None:
        # Fallback to fixed column layout when headers are missing
        name_idx = 1
        party_idx = 2
        crime_idx = 3
        edu_idx = 4
        age_idx = 5
        assets_idx = 6
        liabilities_idx = 7

    candidates: list[MynetaCandidate] = []
    skipped_empty = 0
    skipped_short = 0
    emitted = 0
    for row in table.find_all("tr"):
        cells = row.find_all("td")
        if not cells:
            skipped_empty += 1
            continue
        if name_idx >= len(cells):
            skipped_short += 1
            continue
        name_cell = cells[name_idx]
        name = _get_text(name_cell).replace("Winner", "").strip()
        if not name:
            continue
        link = name_cell.find("a", href=True)
        myneta_url = urljoin(MYNETA_BASE, link["href"]) if link else ""
        party = _get_text(cells[party_idx]) if party_idx is not None and party_idx < len(cells) else ""
        criminal_cases = _get_text(cells[crime_idx]) if crime_idx is not None and crime_idx < len(cells) else ""
        education = _get_text(cells[edu_idx]) if edu_idx is not None and edu_idx < len(cells) else ""
        age = _get_text(cells[age_idx]) if age_idx is not None and age_idx < len(cells) else ""
        total_assets = _get_text(cells[assets_idx]) if assets_idx is not None and assets_idx < len(cells) else ""
        liabilities = (
            _get_text(cells[liabilities_idx]) if liabilities_idx is not None and liabilities_idx < len(cells) else ""
        )
        candidates.append(
            MynetaCandidate(
                name=name,
                party=party,
                criminal_cases=criminal_cases,
                education=education,
                age=age,
                total_assets=total_assets,
                liabilities=liabilities,
                constituency=constituency,
                district=district,
                myneta_url=myneta_url,
            )
        )
        emitted += 1
    # region agent log
    if "constituency_id=1" in url or "constituency_id=112" in url:
        _debug_log(
            "scripts/scrape_candidates.py:_parse_myneta_candidates:emit_stats",
            "row processing stats",
            {
                "url": url,
                "tr_total": len(rows_debug),
                "skipped_empty": skipped_empty,
                "skipped_short": skipped_short,
                "emitted": emitted,
            },
            hypothesis_id="H10",
        )
    # endregion agent log
    return candidates


def scrape_myneta_2021() -> list[MynetaCandidate]:
    candidates: list[MynetaCandidate] = []
    links = _extract_constituency_links()
    for idx, url in enumerate(links, start=1):
        try:
            constituency_candidates = _parse_myneta_candidates(url)
            print(f"[myneta] {idx}/{len(links)} {url} -> {len(constituency_candidates)} candidates")
            candidates.extend(constituency_candidates)
        except Exception as exc:
            print(f"[myneta] failed {url}: {exc}", file=sys.stderr)
    return candidates


def scrape_oneindia_2026() -> list[dict]:
    rows: list[dict] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(ONEINDIA_URL, wait_until="domcontentloaded", timeout=60000)

        for button_label in ("Not Now", "OK"):
            button = page.locator(f"button:has-text('{button_label}')")
            if button.count() > 0:
                try:
                    button.first.click(timeout=2000)
                except Exception:
                    pass

        for party in PARTIES_2026:
            tab = page.locator(f"a:has-text('{party}')")
            if tab.count() == 0:
                continue
            tab.first.click()
            page.wait_for_timeout(1500)
            table = page.locator("table#Table-old tbody tr")
            row_count = table.count()
            for idx in range(row_count):
                cells = table.nth(idx).locator("td")
                if cells.count() < 2:
                    continue
                name = cells.nth(0).inner_text().strip()
                constituency = cells.nth(1).inner_text().strip()
                if not name or name.lower().startswith("candidate"):
                    continue
                rows.append({"candidate": name, "constituency": constituency, "party": party})
            print(f"[oneindia] {party}: {row_count} rows")
        browser.close()
    return rows


def build_myneta_index(candidates: Iterable[MynetaCandidate]):
    by_constituency: dict[str, list[MynetaCandidate]] = defaultdict(list)
    for candidate in candidates:
        by_constituency[candidate.constituency_norm].append(candidate)
    return by_constituency


def match_myneta(
    candidate_name: str, constituency: str, by_constituency: dict[str, list[MynetaCandidate]]
) -> tuple[MynetaCandidate | None, float, str]:
    key = normalize_text(constituency)
    pool = by_constituency.get(key, [])
    if not pool:
        return None, 0.0, "no constituency match"

    name_norm = normalize_text(candidate_name)
    for candidate in pool:
        if candidate.name_norm == name_norm:
            return candidate, 100.0, "exact"

    names = [candidate.name for candidate in pool]
    result = fuzz_process.extractOne(candidate_name, names, scorer=fuzz.token_sort_ratio)
    if not result:
        return None, 0.0, "no fuzzy match"
    match_name, score, _ = result
    match = next((candidate for candidate in pool if candidate.name == match_name), None)
    return match, float(score), "fuzzy"


def write_2021_csv(candidates: list[MynetaCandidate], output_path: Path = OUTPUT_2021):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "candidate",
                "party",
                "criminal_cases",
                "education",
                "age",
                "total_assets",
                "total_assets_rs",
                "liabilities",
                "liabilities_rs",
                "2021_constituency",
                "2021_district",
                "sitting_MLA",
                "myneta_url",
            ]
        )
        last_constituency = None
        for candidate in candidates:
            sitting_mla = 1 if candidate.constituency != last_constituency else 0
            last_constituency = candidate.constituency
            writer.writerow(
                [
                    candidate.name,
                    candidate.party,
                    candidate.criminal_cases,
                    candidate.education,
                    candidate.age,
                    candidate.total_assets,
                    _extract_rs_amount(candidate.total_assets),
                    candidate.liabilities,
                    _extract_rs_amount(candidate.liabilities),
                    candidate.constituency,
                    candidate.district,
                    sitting_mla,
                    candidate.myneta_url,
                ]
            )


def write_2026_csv(rows: list[dict], myneta_index: dict[str, list[MynetaCandidate]]):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with OUTPUT_2026.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "candidate",
                "party",
                "criminal_cases",
                "education",
                "age",
                "myneta_url",
                "needs_review",
                "review_note",
            ]
        )
        for row in rows:
            match, score, reason = match_myneta(row["candidate"], row["constituency"], myneta_index)
            if match and score >= 90:
                needs_review = "no" if match.name_norm == normalize_text(row["candidate"]) else "yes"
                review_note = "" if needs_review == "no" else f"name mismatch ({reason}, score {score:.1f})"
                writer.writerow(
                    [
                        row["candidate"],
                        row["party"],
                        match.criminal_cases,
                        match.education,
                        match.age,
                        match.myneta_url,
                        needs_review,
                        review_note,
                    ]
                )
            else:
                review_note = f"no myneta match ({reason}, score {score:.1f})"
                writer.writerow(
                    [
                        row["candidate"],
                        row["party"],
                        "",
                        "",
                        "",
                        "",
                        "yes",
                        review_note,
                    ]
                )


def main():
    print("Scraping MyNeta 2021...")
    myneta_candidates = scrape_myneta_2021()
    print(f"Total MyNeta candidates: {len(myneta_candidates)}")
    write_2021_csv(myneta_candidates, OUTPUT_2021_EXTENDED)

    print("Scraping OneIndia 2026...")
    oneindia_rows = scrape_oneindia_2026()
    print(f"Total OneIndia rows: {len(oneindia_rows)}")

    myneta_index = build_myneta_index(myneta_candidates)
    write_2026_csv(oneindia_rows, myneta_index)
    print(f"Wrote {OUTPUT_2021_EXTENDED} and {OUTPUT_2026}")


if __name__ == "__main__":
    main()
