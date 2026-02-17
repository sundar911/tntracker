"""Enrich data/dim_parties.csv with curated data for significant parties.

Fills in ideology, coalition, leader, manifesto themes, etc. for the
~20 most significant parties in Tamil Nadu. Leaves minor/fringe parties
with minimal data.

Usage:
    python scripts/curate_dim_parties.py
"""
from __future__ import annotations

import csv
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DIM_PARTIES_PATH = DATA_DIR / "dim_parties.csv"

PARTY_DATA: dict[str, dict[str, str]] = {
    "DMK": {
        "abbreviation": "DMK",
        "party_name_ta": "திராவிட முன்னேற்றக் கழகம்",
        "abbreviation_ta": "தி.மு.க",
        "founded_year": "1949",
        "founder": "C. N. Annadurai",
        "current_leader": "M. K. Stalin",
        "headquarters": "Chennai",
        "website": "https://dmk.in",
        "political_ideology": "Dravidian politics, Social democracy, Secularism",
        "political_position": "Centre-Left",
        "eci_recognition": "State (TN)",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "Women empowerment (free bus, Kalaignar Magalir Urimai Thogai), Social justice, Industrial growth, Education reform, Healthcare access",
        "governance_record_note": "Governing TN since 2021; launched Makkalai Thedi Maruthuvam, Kalaignar Magalir Urimai Thogai (Rs 1000/month for women), free breakfast scheme in govt schools",
    },
    "AIADMK": {
        "abbreviation": "AIADMK",
        "party_name_ta": "அனைத்திந்திய அண்ணா திராவிட முன்னேற்றக் கழகம்",
        "abbreviation_ta": "அ.இ.அ.தி.மு.க",
        "founded_year": "1972",
        "founder": "M. G. Ramachandran",
        "current_leader": "Edappadi K. Palaniswami",
        "headquarters": "Chennai",
        "website": "https://aiadmk.com",
        "political_ideology": "Dravidian politics, Populism, Social conservatism",
        "political_position": "Centre-Right",
        "eci_recognition": "State (TN)",
        "coalition_2021": "AIADMK+ (NDA alliance)",
        "coalition_2026": "AIADMK+ (independent front)",
        "key_manifesto_themes": "Amma canteens, Free laptops, Farm loan waivers, Social welfare schemes, Women safety",
        "governance_record_note": "Governed TN 2011-2021; known for Amma canteens, Amma water, free mixers/grinders; criticism over handling of Jayalalithaa's death and succession crisis",
    },
    "BJP": {
        "abbreviation": "BJP",
        "party_name_ta": "பாரதிய ஜனதா கட்சி",
        "abbreviation_ta": "பா.ஜ.க",
        "founded_year": "1980",
        "founder": "Syama Prasad Mukherjee (predecessor Jan Sangh)",
        "current_leader": "K. Annamalai (TN state president)",
        "headquarters": "New Delhi",
        "website": "https://bjp.org",
        "political_ideology": "Hindu nationalism, Integral humanism, Economic liberalism",
        "political_position": "Right",
        "eci_recognition": "National",
        "coalition_2021": "AIADMK+ (NDA alliance)",
        "coalition_2026": "NDA (alliance TBD)",
        "key_manifesto_themes": "Ram temple, National security, Make in India, Hindutva, Anti-corruption",
        "governance_record_note": "National ruling party since 2014; limited presence in TN state governance; allied with AIADMK in 2021",
    },
    "INC": {
        "abbreviation": "INC",
        "party_name_ta": "இந்திய தேசிய காங்கிரஸ்",
        "abbreviation_ta": "காங்.",
        "founded_year": "1885",
        "founder": "Allan Octavian Hume",
        "current_leader": "Mallikarjun Kharge (national president)",
        "headquarters": "New Delhi",
        "website": "https://inc.in",
        "political_ideology": "Social liberalism, Secularism, Centre-left economics",
        "political_position": "Centre-Left",
        "eci_recognition": "National",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "NYAY income scheme, Rural employment, Secular governance, Social welfare",
        "governance_record_note": "National ruling party multiple times; junior ally of DMK in TN since 2004; won 25 seats in TN in 2021 as part of DMK alliance",
    },
    "Naam Tamilar Katchi": {
        "abbreviation": "NTK",
        "party_name_ta": "நாம் தமிழர் கட்சி",
        "abbreviation_ta": "ந.த.க",
        "founded_year": "2010",
        "founder": "Seeman",
        "current_leader": "Seeman",
        "headquarters": "Chennai",
        "website": "https://naamtamilar.org",
        "political_ideology": "Tamil nationalism, Anti-caste, Pan-Tamil identity",
        "political_position": "Left",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Contested alone",
        "coalition_2026": "Contested alone (expected)",
        "key_manifesto_themes": "Tamil Eelam solidarity, Prohibition, Anti-Hindutva, Caste annihilation, Environmental protection",
        "governance_record_note": "Never held state power; contested all 234 seats in 2021; growing vote share as third force",
    },
    "BSP": {
        "abbreviation": "BSP",
        "party_name_ta": "பகுஜன் சமாஜ் கட்சி",
        "abbreviation_ta": "ப.ச.க",
        "founded_year": "1984",
        "founder": "Kanshi Ram",
        "current_leader": "Mayawati",
        "headquarters": "New Delhi",
        "website": "https://bsp.org",
        "political_ideology": "Ambedkarism, Social justice for Dalits/Bahujans",
        "political_position": "Centre-Left",
        "eci_recognition": "National",
        "coalition_2021": "Contested alone",
        "coalition_2026": "",
        "key_manifesto_themes": "Dalit empowerment, Reservation expansion, Anti-caste discrimination",
        "governance_record_note": "Governed Uttar Pradesh multiple times; limited impact in TN",
    },
    "Makkal Needhi Maiam": {
        "abbreviation": "MNM",
        "party_name_ta": "மக்கள் நீதி மய்யம்",
        "abbreviation_ta": "ம.நீ.ம",
        "founded_year": "2018",
        "founder": "Kamal Haasan",
        "current_leader": "Kamal Haasan",
        "headquarters": "Chennai",
        "website": "https://makkalneedhimaiam.in",
        "political_ideology": "Centrism, Good governance, Anti-corruption",
        "political_position": "Centre",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Contested alone",
        "coalition_2026": "",
        "key_manifesto_themes": "Corruption-free governance, Education reform, Women safety, Digital governance",
        "governance_record_note": "Founded in 2018 by actor Kamal Haasan; contested 154 seats in 2019 LS and 175 in 2021 assembly; no seats won",
    },
    "Amma Makkal Munnettra Kazagam": {
        "abbreviation": "AMMK",
        "party_name_ta": "அம்மா மக்கள் முன்னேற்றக் கழகம்",
        "abbreviation_ta": "அ.ம.மு.க",
        "founded_year": "2018",
        "founder": "T. T. V. Dhinakaran",
        "current_leader": "T. T. V. Dhinakaran",
        "headquarters": "Chennai",
        "website": "",
        "political_ideology": "Dravidian politics, Populism (AIADMK splinter)",
        "political_position": "Centre-Right",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Contested alone",
        "coalition_2026": "",
        "key_manifesto_themes": "Continuation of Jayalalithaa welfare legacy, Women empowerment, Farm support",
        "governance_record_note": "Splinter from AIADMK after Jayalalithaa's death; TTV Dhinakaran won RK Nagar bypoll 2017; no seats in 2021",
    },
    "DMDK": {
        "abbreviation": "DMDK",
        "party_name_ta": "தேசிய முற்போக்கு திராவிட கழகம்",
        "abbreviation_ta": "தே.மு.தி.க",
        "founded_year": "2005",
        "founder": "Vijayakanth",
        "current_leader": "Premalatha Vijayakanth",
        "headquarters": "Chennai",
        "website": "",
        "political_ideology": "Dravidian politics, Anti-corruption, Populism",
        "political_position": "Centre",
        "eci_recognition": "State (TN)",
        "coalition_2021": "AIADMK+ (NDA alliance)",
        "coalition_2026": "",
        "key_manifesto_themes": "Anti-corruption, Prohibition, Rural development",
        "governance_record_note": "Won 29 seats in 2011 as AIADMK ally; actor Vijayakanth passed away in 2023; party influence declining",
    },
    "CPI": {
        "abbreviation": "CPI",
        "party_name_ta": "இந்தியக் கம்யூனிஸ்ட் கட்சி",
        "abbreviation_ta": "இ.க.க",
        "founded_year": "1925",
        "founder": "M. N. Roy and others",
        "current_leader": "D. Raja (national general secretary)",
        "headquarters": "New Delhi",
        "website": "https://communistparty.in",
        "political_ideology": "Marxism-Leninism, Communism",
        "political_position": "Left",
        "eci_recognition": "National",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "Workers rights, Land reform, Anti-privatisation, Secularism",
        "governance_record_note": "Long-standing DMK ally; won 2 seats in 2021 TN assembly",
    },
    "CPI(M)": {
        "abbreviation": "CPI(M)",
        "party_name_ta": "இந்தியக் கம்யூனிஸ்ட் கட்சி (மார்க்சிஸ்ட்)",
        "abbreviation_ta": "இ.க.க(ம)",
        "founded_year": "1964",
        "founder": "E. M. S. Namboodiripad and others",
        "current_leader": "Sitaram Yechury (national general secretary)",
        "headquarters": "New Delhi",
        "website": "https://cpim.org",
        "political_ideology": "Marxism-Leninism, Communism",
        "political_position": "Left",
        "eci_recognition": "National",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "Workers rights, Public sector, Anti-imperialism, Secularism",
        "governance_record_note": "Long-standing DMK ally; won 2 seats in 2021 TN assembly",
    },
    "Viduthalai Chiruthaigal Katchi": {
        "abbreviation": "VCK",
        "party_name_ta": "விடுதலைச் சிறுத்தைகள் கட்சி",
        "abbreviation_ta": "வி.சி.க",
        "founded_year": "1999",
        "founder": "Thol. Thirumavalavan",
        "current_leader": "Thol. Thirumavalavan",
        "headquarters": "Chennai",
        "website": "",
        "political_ideology": "Ambedkarism, Dalit rights, Social justice, Anti-caste",
        "political_position": "Left",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "Dalit empowerment, Anti-caste violence, Land rights for landless, Education access",
        "governance_record_note": "Key DMK ally; Thirumavalavan is a Lok Sabha MP; won 4 seats in 2021 TN assembly",
    },
    "Pattali Makkal Katchi": {
        "abbreviation": "PMK",
        "party_name_ta": "பாட்டாளி மக்கள் கட்சி",
        "abbreviation_ta": "பா.ம.க",
        "founded_year": "1989",
        "founder": "S. Ramadoss",
        "current_leader": "Anbumani Ramadoss",
        "headquarters": "Chennai",
        "website": "https://pmk.org.in",
        "political_ideology": "Vanniyar community politics, Social justice, Prohibition",
        "political_position": "Centre",
        "eci_recognition": "State (TN)",
        "coalition_2021": "AIADMK+ (NDA alliance)",
        "coalition_2026": "",
        "key_manifesto_themes": "Vanniyar reservation (internal), Prohibition, Agricultural support, Caste-based reservation",
        "governance_record_note": "Known for Vanniyar community politics; allied with NDA in 2021; won 5 seats; Anbumani Ramadoss was Union Health Minister 2004-09",
    },
    "IUML": {
        "abbreviation": "IUML",
        "party_name_ta": "இந்திய யூனியன் முஸ்லிம் லீக்",
        "abbreviation_ta": "இ.யூ.மு.லீ",
        "founded_year": "1948",
        "founder": "Muhammad Ismail",
        "current_leader": "K. M. Kader Mohideen (TN president)",
        "headquarters": "Chennai (TN unit)",
        "website": "",
        "political_ideology": "Muslim minority rights, Secularism, Social welfare",
        "political_position": "Centre-Left",
        "eci_recognition": "State (Kerala)",
        "coalition_2021": "Secular Progressive Alliance (DMK+)",
        "coalition_2026": "Secular Progressive Alliance (DMK+)",
        "key_manifesto_themes": "Minority welfare, Education, Anti-communalism",
        "governance_record_note": "Long-standing DMK ally; representation in TN Muslim-majority constituencies",
    },
    "Puthiya Tamilagam": {
        "abbreviation": "PT",
        "party_name_ta": "புதிய தமிழகம்",
        "abbreviation_ta": "பு.த",
        "founded_year": "1996",
        "founder": "K. Krishnasamy",
        "current_leader": "K. Krishnasamy",
        "headquarters": "Thoothukudi",
        "website": "",
        "political_ideology": "Devendrakula Vellalar community rights, Social justice",
        "political_position": "Centre",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "AIADMK+ (NDA alliance)",
        "coalition_2026": "",
        "key_manifesto_themes": "Devendrakula Vellalar rights, Education, Anti-caste discrimination",
        "governance_record_note": "Caste-based party for Devendrakula Vellalar community; has shifted alliances between DMK and AIADMK",
    },
    "SDPI": {
        "abbreviation": "SDPI",
        "party_name_ta": "சோசியல் டெமாக்ரடிக் பார்ட்டி ஆஃப் இந்தியா",
        "abbreviation_ta": "எஸ்.டி.பி.ஐ",
        "founded_year": "2009",
        "founder": "E. Abubacker",
        "current_leader": "M. K. Faizy (national president)",
        "headquarters": "New Delhi",
        "website": "",
        "political_ideology": "Muslim minority rights, Social democracy",
        "political_position": "Centre-Left",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Contested alone",
        "coalition_2026": "",
        "key_manifesto_themes": "Minority rights, Anti-NRC/CAA, Social welfare",
        "governance_record_note": "",
    },
    "My India Party": {
        "abbreviation": "MIP",
        "party_name_ta": "மை இந்தியா பார்ட்டி",
        "abbreviation_ta": "",
        "founded_year": "2020",
        "founder": "",
        "current_leader": "",
        "headquarters": "",
        "website": "",
        "political_ideology": "",
        "political_position": "",
        "eci_recognition": "Registered-Unrecognised",
        "coalition_2021": "Contested alone",
        "coalition_2026": "",
        "key_manifesto_themes": "",
        "governance_record_note": "",
    },
    "IND": {
        "abbreviation": "IND",
        "party_name_ta": "சுயேச்சை",
        "abbreviation_ta": "சுயே.",
        "eci_recognition": "Independent",
    },
}


def main() -> None:
    if not DIM_PARTIES_PATH.exists():
        raise SystemExit(f"Missing: {DIM_PARTIES_PATH}. Run build_dim_parties.py first.")

    with DIM_PARTIES_PATH.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows = list(reader)

    updated = 0
    for row in rows:
        party_name = row.get("party_name", "")
        curated = PARTY_DATA.get(party_name)
        if curated:
            for key, value in curated.items():
                if key in row and value:
                    row[key] = value
            updated += 1
        else:
            if not row.get("eci_recognition"):
                row["eci_recognition"] = "Registered-Unrecognised"

    with DIM_PARTIES_PATH.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Updated {updated} parties with curated data in {DIM_PARTIES_PATH}")


if __name__ == "__main__":
    main()
