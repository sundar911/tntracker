# Data Sources and Licensing Notes

This project prioritizes official Election Commission sources. Secondary sources are used
only where official data is unavailable or incomplete.

## Primary (official)
- Election Commission of India (ECI) affidavit portal for candidate affidavits, including
  criminal cases, assets, liabilities, and education: https://affidavit.eci.gov.in/
- Tamil Nadu elections portal for official forms, notices, and state-specific material:
  https://www.elections.tn.gov.in/
- ECI statistical reports for historical election results:
  https://www.eci.gov.in/statistical-reports
  - These reports can be exported to CSV and imported via `import_results_csv`.

## Secondary (credible civic)
- ADR/MyNeta for structured affidavit data and summaries when official data is incomplete:
  https://www.myneta.info/

## 2026 candidate announcements (credible media, provisional)
- Times of India: NTK candidate announcements (2026)
  https://timesofindia.indiatimes.com/city/chennai/ntk-marches-ahead-by-declaring-candidates-commences-campaign/articleshow/125599015.cms
- Times Now Tamil: NTK candidate announcements (2026)
  https://tamil.timesnownews.com/news/tamil-nadu-election-2026-ntk-seeman-releases-first-100-candidates-list-check-star-faces-here-article-153251924
- News Today: NTK candidate announcements (2026)
  https://newstodaynet.com/2025/12/06/2026-polls-ntk-releases-first-list-of-100-candidates/

## Tertiary (reputable media, manual only)
- Nationally reputed news media may be cited if both official and ADR/MyNeta data are
  missing for a candidate's legal history. These entries must include a published date
  and source URL.

## GIS / Constituency boundaries
- Open Government Data (OGD) platform dataset for TN assembly constituencies:
  https://www.data.gov.in/resource/general-election-tamil-nadu-legislative-assembly-constituencies-shb-2019
- Community-maintained GeoJSON (to be validated against official sources):
  https://github.com/baskicanvas/tamilnadu-assembly-constituency-maps

## 2026 candidate data status
- As of now, official 2026 candidate nominations and affidavits are not yet published.
  Once the nomination window opens and ECI publishes affidavits, the ingestion
  pipeline can be run against the official affidavit portal.

## Licensing reminders
- Verify terms of use for each source before automated scraping.
- Where automated access is restricted, download data manually and store it as source
  documents with explicit provenance in the database.
