# Tamil Nadu Assembly Elections Tracker

Public transparency site for Tamil Nadu Assembly Elections 2026. Built with Django,
DRF, and open-source GIS data for constituency maps.

## Quick start (local)
1. Create a virtualenv and install dependencies:
   - `python -m venv .venv && source .venv/bin/activate`
   - `pip install -r requirements.txt`
2. Run migrations:
   - `python backend/manage.py migrate`
3. Start the server:
   - `python backend/manage.py runserver`

## Docker (VPS or local)
From `infra/`:
- `docker compose up --build`

Then run migrations in the web container:
- `docker compose exec web python manage.py migrate`
- `docker compose exec web python manage.py collectstatic --noinput`

## Ingestion commands
- Import affidavit CSV:
  - `python backend/manage.py import_affidavit_csv path/to/affidavits.csv --source-url=https://affidavit.eci.gov.in/`
- Import constituency GeoJSON:
  - `python backend/manage.py import_constituency_geojson path/to/tn.geojson --source-url=https://www.data.gov.in/resource/general-election-tamil-nadu-legislative-assembly-constituencies-shb-2019`
- Download and import constituency GeoJSON from URL:
  - `python backend/manage.py sync_constituencies_geojson --url=https://raw.githubusercontent.com/baskicanvas/tamilnadu-assembly-constituency-maps/main/tn_ac_2021.geojson --source-url=https://github.com/baskicanvas/tamilnadu-assembly-constituency-maps`
- Download and import TNLA 2021 Form 21E PDFs (official results):
  - `python backend/manage.py sync_tnla2021_form21e --continue-on-error --skip-existing`
- Import official results from a CSV export (alternative to Form 21E):
  - `python backend/manage.py import_results_csv --csv-path=path/to/results.csv --source-url=https://www.eci.gov.in/statistical-reports`
- Import manifestos from a JSON index (URL or local path):
  - `python backend/manage.py sync_manifestos_index --index-path=docs/manifestos_index.sample.json`
- Pull 2026 NTK announcements from credible news sources:
  - `python backend/manage.py sync_ntk_2026_announcements`
- Import ADR/MyNeta legal history for a candidate:
  - `python backend/manage.py sync_myneta_candidate --url=https://www.myneta.info/<election>/candidate.php?candidate_id=123`
- Import ADR/MyNeta legal history for all Tamil Nadu 2021 candidates:
  - `python backend/manage.py sync_myneta_tn2021`
- One-shot sync (2021 official + 2026 announcements + MyNeta legal history):
  - `python backend/manage.py sync_election_data --with-geojson`
  - Optional (enable Form21E PDFs): 
    `python backend/manage.py sync_election_data --with-geojson --with-form21e --form21e-continue-on-error --form21e-skip-existing`
  - Alternative (skip Form21E, use results CSV): 
    `python backend/manage.py sync_election_data --with-geojson --skip-form21e --results-csv-path=path/to/results.csv --results-csv-source-url=https://www.eci.gov.in/statistical-reports`

Notes:
- All sync steps are best-effort; if a source is unreachable, the command logs a warning and continues.
- The UI flags missing data on constituency and candidate pages.

## Data sources
See `docs/sources.md` for official and secondary sources with licensing notes.

## Bilingual content
Each core model includes Tamil fields (e.g., `name_ta`, `summary_ta`). Populate these
for full Tamil coverage and use the language toggle at the top right of each page.

## Frontend design map (for UI work)
Primary templates and styling live under `backend/core/templates/core/` and
`backend/core/static/core/site.css`. Use this section to navigate UI updates.

### Key templates
- `home.html`: Homepage hero, navigation, primary CTAs, and feature cards.
- `map.html`: Constituency map page (Leaflet) with tooltip styling hooks.
- `party_dashboard.html`: Party-level dashboard, filters/slicers, and summary table.
- `party_detail.html`: Party drill-through table (candidate rows + filters).
- `constituency_detail.html`: Constituency summary stats + candidate cards.
- `candidate_detail.html`: Candidate profile + affidavit and legal history.
- `dashboard.html`: Data quality dashboard counts.
- `search.html`: Search page results layout.

### Shared styling
- `backend/core/static/core/site.css`: Global styles, nav, buttons, cards, filters,
  slider UI, and typography. Most UI tweaks should start here.

### UI data formatting (frontend)
- Indian numeral grouping is applied via the Django template filter
  `backend/core/templatetags/indian_numbers.py` (`|indian` in templates).

### Common links
- Language toggle uses `/set-lang/<en|ta>/`.
- Map data is fetched from `/map/data/`.