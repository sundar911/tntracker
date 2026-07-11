# TN Election Tracker — Development Notes

## Design Philosophy
- Design for the least privileged smartphone user in Tamil Nadu. Every single person with a smartphone, no matter their background, should be able to easily and intuitively navigate the website.
- Always optimise for the best phone experience (mobile-first design).
- Keep things as simple as possible — provide users with as much information as necessary to inform their vote, nothing more.
- **Bilingual accessibility is non-negotiable.** Every piece of user-facing data — candidate names, party names, constituency names, education, profession, and all UI labels — must be available in both Tamil and English. When new data is ingested (e.g. from ECI CSVs or affidavit parsing), always ensure Tamil translations are generated before considering the data ready for production. Use the Sarvam Translate API (`SARVAM_API_KEY` in `.env`) for transliteration and translation of candidate data.

## Demographic Context — Tamil Nadu Electorate

Key statistics grounding our design decisions:

- **Population:** ~80M; ~50% urban (one of India's most urbanized states)
- **Electorate:** ~62M registered voters (18–29: 23%, 30–49: 41%, 50–69: 27%, 70+: 7%)
- **Smartphone penetration:** 85–90% urban, 60–70% rural
- **Internet gender gap:** ~60–65% male, 35–40% female users
- **Literacy:** 80.3% overall (male 86.8%, female 73.9%); urban ~88–90%, rural ~73–75%
- **Language:** 90%+ Tamil mother tongue; only 15–20% comfortable in English
- **Digital literacy tiers:** basic (WhatsApp/YouTube) ~90% of smartphone owners; transactional (UPI, forms) ~50–60%; advanced (web browsing, govt portals) ~25–35%
- **Disability:** ~1.65% of population (visual ~28%, locomotor ~24%, hearing ~19%)
- **Below poverty line:** ~5–8%; large informal sector workforce in rural areas
- **Election info sources:** TV dominant; WhatsApp is the biggest digital channel; YouTube for younger voters
- **Transgender community:** ~40K registered; TN has India's first transgender welfare board

These numbers directly inform our choices around language defaults, font sizes, page weight budgets, and accessibility requirements.

## User Personas

Ten fictional personas grounded in real TN demographic data guide every design and development decision. **Before adding a feature or changing UI, ask: "Does this work for all ten?"**

| # | Name | File | Represents |
|---|------|------|------------|
| 1 | Kavitha | [docs/personas/01-kavitha.md](docs/personas/01-kavitha.md) | Young urban woman, bilingual, digitally savvy (Chennai) |
| 2 | Murugan | [docs/personas/02-murugan.md](docs/personas/02-murugan.md) | Middle-aged rural daily-wage worker, Tamil-only, basic digital literacy (Thanjavur) |
| 3 | Lakshmi | [docs/personas/03-lakshmi.md](docs/personas/03-lakshmi.md) | Older semi-urban homemaker, Tamil-only, moderate phone skills (Madurai) |
| 4 | Rajesh | [docs/personas/04-rajesh.md](docs/personas/04-rajesh.md) | Urban small business owner, bilingual, power user (Coimbatore) |
| 5 | Selvi | [docs/personas/05-selvi.md](docs/personas/05-selvi.md) | Young rural woman with visual impairment, screen reader user (Tenkasi) |
| 6 | Priya | [docs/personas/06-priya.md](docs/personas/06-priya.md) | Transgender community worker, Tamil-dominant (Tiruchirappalli) |
| 7 | Aravind | [docs/personas/07-aravind.md](docs/personas/07-aravind.md) | First-time voter, college student, displaced voter (Karur/Trichy) |
| 8 | Meenakshi | [docs/personas/08-meenakshi.md](docs/personas/08-meenakshi.md) | Government school teacher, small-town, moderate digital literacy (Dharmapuri) |
| 9 | Thatha | [docs/personas/09-thatha.md](docs/personas/09-thatha.md) | Elderly retired farmer, shared device, minimal digital literacy (Sivaganga) |
| 10 | Karthik | [docs/personas/10-karthik.md](docs/personas/10-karthik.md) | Young urban gig worker, app-literate but website-unfamiliar (Chennai) |

### Diversity coverage

| Dimension | Coverage |
|---|---|
| Gender | Female (Kavitha, Lakshmi, Selvi, Meenakshi), Male (Murugan, Rajesh, Aravind, Thatha, Karthik), Transgender (Priya) |
| Age | 19, 22, 24, 26, 31, 38, 44, 47, 61, 74 |
| Geography | Chennai, Coimbatore, Trichy (urban); Madurai (semi-urban); Dharmapuri, Karur (small town); Thanjavur, Tenkasi, Sivaganga (rural) |
| Language | Tamil-only (4), Tamil-dominant (3), Bilingual (3) |
| Digital literacy | Minimal → Basic → Moderate → App-specific → High → Power user |
| Income | BPL/low → Lower-middle → Middle → Upper-middle |
| Disability | Visual impairment (Selvi) |
| Device | Shared phone (Thatha), Budget Android (3), Mid-range Android (3), Older hand-me-down (1), iPhone (1) |
| Voter type | First-time (Aravind), Displaced/outstation (Aravind), Veteran (Thatha) |

## Accessibility Principles (Persona-Driven)

These are derived directly from our personas. The persona name in parentheses indicates who drives each requirement.

- **Semantic HTML first.** Use proper heading hierarchy (h1–h6), landmark regions (`<nav>`, `<main>`, `<footer>`), and associated form labels. No div-soup. *(Selvi)*
- **WCAG 2.1 AA minimum.** Colour contrast ratios ≥ 4.5:1, visible focus indicators, alt text on all images. *(Selvi)*
- **Large touch targets.** Minimum 48×48px for all interactive elements; 56px+ for primary actions. *(Murugan, Thatha, Lakshmi)*
- **Tamil-first content.** Default to Tamil; English toggle always visible but never required to use the app. *(Murugan, Lakshmi, Selvi, Thatha)*
- **Minimal page weight.** Target < 100KB initial page load. Compress images, lazy-load non-critical content. *(Murugan, Karthik — patchy rural coverage / always on mobile data)*
- **No information by colour alone.** Always pair colour with text, icons, or patterns. *(Selvi)*
- **Single-scroll layouts over nested navigation.** Avoid hamburger menus, modals, and multi-step flows where possible. *(Lakshmi, Thatha)*
- **Readable Tamil typography.** Minimum 16px for Tamil body text; line-height ≥ 1.5. *(Lakshmi, Murugan, Thatha)*
- **Keyboard and swipe navigable.** Every interactive element must be reachable without a mouse or precise visual targeting. *(Selvi)*
- **No auto-playing media.** *(Selvi)*
- **Stateless design.** No login, no account, no saved state — the app must work on shared devices with zero setup. *(Thatha, Priya)*
- **App-like simplicity.** Card-based, vertical-scroll layouts familiar to users of Swiggy, WhatsApp, and YouTube. *(Karthik, Aravind)*
- **Gender-neutral UI language.** No gendered assumptions in labels, greetings, or form fields. *(Priya)*
- **Offline resilience.** Content that has loaded should remain visible if the connection drops mid-page. *(Karthik, Murugan)*
