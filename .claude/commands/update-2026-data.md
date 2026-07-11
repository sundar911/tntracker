# Update 2026 Candidate Data

Rebuild `data/fct_candidates_26.csv` from the latest candidates.json and affidavit markdowns.

## What to do

1. **Run the build script** to regenerate the CSV:
   ```
   python scripts/build_fct_candidates_26.py --sarvam-api-key $SARVAM_API_KEY
   ```
   If no Sarvam API key is available, run without LLM fallback:
   ```
   python scripts/build_fct_candidates_26.py
   ```

2. **Check the extraction stats** printed by the script. For candidates with markdowns, aim for:
   - Education ≥ 85%
   - Profession ≥ 85%
   - Assets ≥ 85%
   - Phone ≥ 75%

3. **If extraction rates dropped** compared to previous runs, investigate:
   - Run the parser on failing files to identify new OCR patterns
   - Update regex patterns in `backend/core/ingestion/parse_form26_markdown.py`
   - Re-run the build script
   - Iterate until rates are back up

4. **Review data quality warnings** — fix any corrupted values

5. **Key files:**
   - Input: `data/eci_2026/candidates.json` (identity fields from ECI, 100% reliable)
   - Input: `data/eci_2026/markdown/*.md` (Sarvam Vision OCR of affidavit PDFs)
   - Parser: `backend/core/ingestion/parse_form26_markdown.py`
   - LLM fallback: `backend/core/ingestion/sarvam_llm_extract.py`
   - Build script: `scripts/build_fct_candidates_26.py`
   - Output: `data/fct_candidates_26.csv`

6. **Important context:**
   - Sarvam Vision OCR sometimes misaligns table columns — the parser handles this with flexible regexes
   - The parser handles both Tamil and English affidavit formats
   - Tamil section markers have many variants (with/without parentheses, spaces, colons, dashes)
   - Never store section headers as education values (e.g. "is as under", "வருமாறு")
   - Social media is legitimately empty for most candidates (they list "Nil")
