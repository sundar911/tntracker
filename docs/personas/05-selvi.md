# Selvi — Young Rural Woman with Visual Impairment

## Profile

| Field | Detail |
|---|---|
| Name | Selvi Marimuthu |
| Age | 22 |
| Location | Village near Tenkasi, Tirunelveli district (rural, southern TN) |
| Occupation | Studying BA Tamil through TNOU (Tamil Nadu Open University) distance education |
| Language | Tamil only — proficient Tamil reader via screen reader |
| Device | Android phone (Samsung Galaxy M14) with TalkBack enabled; uses wired earphones |
| Connectivity | 4G (Airtel) — decent coverage in her area |
| Digital literacy | Moderate-to-good within accessible apps (WhatsApp, YouTube via voice); struggles severely with inaccessible websites |
| Income bracket | Lower-middle (father is a temple priest; family income Rs. 15,000–20,000/month) |
| Disability | Low vision (retinitis pigmentosa) — relies on TalkBack screen reader and high magnification |

## How Selvi consumes election information

Selvi listens to Tamil news on YouTube — she subscribes to audio-friendly channels and uses YouTube's accessibility features. She receives voice notes from friends and community members on WhatsApp. Her local community radio station covers election news. When she needs detailed information, she asks her younger brother to read it to her from a website, because most websites don't work with her screen reader.

## What Selvi needs from the app

- Fully screen-reader compatible interface — TalkBack must be able to read every element in a logical order
- Semantic HTML with proper heading hierarchy (h1 → h2 → h3) so she can navigate by headings
- Meaningful alt text on all images — especially candidate photos and party symbols
- Keyboard and swipe-navigable — every interactive element must be reachable without visual targeting
- Skip-to-content link so she doesn't have to swipe through the header and nav on every page

## Key accessibility considerations

- **WCAG 2.1 AA compliance minimum** — this is non-negotiable for Selvi to use the app at all
- Proper ARIA labels on all interactive elements (buttons, links, filters, toggles)
- No information conveyed by colour alone — always pair with text, icons, or patterns
- Focus indicators must be clearly visible (not just a subtle outline change)
- No auto-playing media — unexpected audio interrupts her screen reader
- Form labels must be programmatically associated with inputs (not just visually adjacent)
- Avoid image-based text — all text must be actual HTML text that screen readers can parse
- Candidate information must flow in logical reading order (name → party → constituency → details), not in a visual grid that reads nonsensically when linearized

## Quote

> "பெரும்பாலான websites என் screen reader-ல வேலை செய்யாது — buttons-க்கு பேரே இருக்காது, படங்களுக்கு description இருக்காது. இந்த app வேற மாதிரி இருக்கணும்."
>
> *"Most websites don't work with my screen reader — buttons have no names, images have no descriptions. I hope this app is different."*
