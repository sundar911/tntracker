"""
Digitize ECI 2026 affidavit PDFs using the Sarvam Document Digitization API.

Reads PDFs from data/eci_2026/pdfs/, splits each into ≤10-page chunks (Sarvam's
per-job limit), submits each chunk as a job, and saves the concatenated markdown
to data/eci_2026/markdown/{stem}.md.

Requires:
    SARVAM_API_KEY env var

Usage:
    export SARVAM_API_KEY=your_key_here
    python scripts/parse_eci_affidavits_sarvam.py --limit 2   # test first
    python scripts/parse_eci_affidavits_sarvam.py              # full run (all 500)
    python scripts/parse_eci_affidavits_sarvam.py --dry-run    # list what would be processed

Resumable: progress tracked in data/.parse_eci_affidavits_progress.json.

Sarvam API reference: https://docs.sarvam.ai/api-reference-docs/document-intelligence/
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import pypdf
import requests

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SARVAM_BASE = "https://api.sarvam.ai"
PDF_DIR = Path("data/eci_2026/pdfs")
MARKDOWN_DIR = Path("data/eci_2026/markdown")
PROGRESS_JSON = Path("data/.parse_eci_affidavits_progress.json")

MAX_PAGES_PER_JOB = 10
POLL_INTERVAL = 5      # seconds between status checks
POLL_MAX = 120          # max polls per job (~10 min)
SAVE_EVERY = 5          # checkpoint every N PDFs
MIN_REQUEST_GAP = 3.0   # seconds between API calls (stay under 10 req/min provisioned limit)

_last_request_time = 0.0


def _rate_limit() -> None:
    """Enforce minimum gap between API requests."""
    global _last_request_time
    elapsed = time.time() - _last_request_time
    if elapsed < MIN_REQUEST_GAP:
        time.sleep(MIN_REQUEST_GAP - elapsed)
    _last_request_time = time.time()


def _api_request(method: str, url: str, retries: int = 5, **kwargs) -> requests.Response:
    """Make an API request with automatic retry on 429 rate limits and 400 errors."""
    for attempt in range(retries):
        _rate_limit()
        resp = requests.request(method, url, **kwargs)
        if resp.status_code == 429:
            wait = min(60, 10 * (attempt + 1))
            print(f"[sarvam] rate limited (429), waiting {wait}s... (attempt {attempt + 1}/{retries})")
            time.sleep(wait)
            continue
        if resp.status_code == 400 and attempt < retries - 1:
            # Upload may not have registered yet — retry after delay
            wait = 3 * (attempt + 1)
            print(f"[sarvam] 400 error, retrying in {wait}s... (attempt {attempt + 1}/{retries})")
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# PDF splitting
# ---------------------------------------------------------------------------


def split_pdf(pdf_path: Path, max_pages: int = MAX_PAGES_PER_JOB) -> list[Path]:
    """
    Split a PDF into ≤max_pages chunks. Returns list of temp file paths.
    Caller is responsible for cleaning up temp files.
    """
    reader = pypdf.PdfReader(str(pdf_path))
    total = len(reader.pages)

    if total <= max_pages:
        return [pdf_path]  # no split needed

    chunks: list[Path] = []
    for start in range(0, total, max_pages):
        end = min(start + max_pages, total)
        writer = pypdf.PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])

        tmp = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
        writer.write(tmp)
        tmp.close()
        chunks.append(Path(tmp.name))

    return chunks


# ---------------------------------------------------------------------------
# Sarvam API
# ---------------------------------------------------------------------------


def _headers(api_key: str) -> dict:
    return {"api-subscription-key": api_key, "Content-Type": "application/json"}


def _create_job(api_key: str) -> str:
    """Create a new doc digitization job. Returns job_id."""
    resp = _api_request(
        "POST",
        f"{SARVAM_BASE}/doc-digitization/job/v1",
        json={"job_parameters": {"language": "en-IN", "output_format": "md"}},
        headers=_headers(api_key),
        timeout=30,
    )
    return resp.json()["job_id"]


def _get_upload_url(api_key: str, job_id: str) -> str:
    """Get pre-signed upload URL for the job."""
    resp = _api_request(
        "POST",
        f"{SARVAM_BASE}/doc-digitization/job/v1/upload-files",
        json={"job_id": job_id, "files": ["affidavit.pdf"]},
        headers=_headers(api_key),
        timeout=30,
    )
    data = resp.json()
    upload_urls = data.get("upload_urls", {})
    if upload_urls:
        first = list(upload_urls.values())[0]
        if isinstance(first, dict):
            return first.get("file_url", "")
        return first
    return data.get("upload_url", "")


def _upload_pdf(upload_url: str, pdf_path: Path) -> None:
    """Upload PDF bytes to the pre-signed Azure Blob URL."""
    _api_request(
        "PUT",
        upload_url,
        data=pdf_path.read_bytes(),
        headers={
            "Content-Type": "application/pdf",
            "x-ms-blob-type": "BlockBlob",
        },
        timeout=120,
    )


def _start_job(api_key: str, job_id: str) -> None:
    """Start processing the job."""
    _api_request(
        "POST",
        f"{SARVAM_BASE}/doc-digitization/job/v1/{job_id}/start",
        json={},
        headers=_headers(api_key),
        timeout=30,
    )


def _poll_until_done(api_key: str, job_id: str) -> None:
    """Poll until job completes or fails. Raises RuntimeError on failure."""
    for attempt in range(POLL_MAX):
        time.sleep(POLL_INTERVAL)
        resp = _api_request(
            "GET",
            f"{SARVAM_BASE}/doc-digitization/job/v1/{job_id}/status",
            headers=_headers(api_key),
            timeout=30,
        )
        data = resp.json()
        state = data.get("job_state", "Unknown")
        if state in ("Completed", "Success", "Done"):
            return
        if state in ("Failed", "Error", "Cancelled"):
            raise RuntimeError(f"Job {job_id} failed: {data.get('error_message', state)}")
    raise RuntimeError(f"Job {job_id} timed out after {POLL_MAX} polls")


def _download_result(api_key: str, job_id: str) -> str:
    """Download and extract markdown from the completed job's result ZIP."""
    resp = _api_request(
        "POST",
        f"{SARVAM_BASE}/doc-digitization/job/v1/{job_id}/download-files",
        json={"job_id": job_id},
        headers=_headers(api_key),
        timeout=30,
    )
    data = resp.json()

    download_urls = data.get("download_urls", {})
    download_url = ""
    if download_urls:
        first = list(download_urls.values())[0]
        if isinstance(first, dict):
            download_url = first.get("file_url", "")
        else:
            download_url = first
    if not download_url:
        download_url = data.get("download_url", "")
    if not download_url:
        raise RuntimeError(f"No download URL in response: {data}")

    zip_resp = _api_request("GET", download_url, timeout=120)

    with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
        md_files = [n for n in zf.namelist() if n.endswith(".md")]
        if md_files:
            return zf.read(md_files[0]).decode("utf-8", errors="replace")
        txt_files = [n for n in zf.namelist() if n.endswith(".txt")]
        if txt_files:
            return zf.read(txt_files[0]).decode("utf-8", errors="replace")
        raise ValueError(f"No markdown/text in ZIP. Files: {zf.namelist()}")


def digitize_chunk(pdf_path: Path, api_key: str) -> str:
    """
    Submit a single PDF chunk (≤10 pages) to Sarvam and return the markdown.
    """
    job_id = _create_job(api_key)
    upload_url = _get_upload_url(api_key, job_id)
    _upload_pdf(upload_url, pdf_path)

    # Wait for Azure blob to register — poll status until total_files > 0
    for wait_attempt in range(6):
        time.sleep(5)
        try:
            resp = _api_request(
                "GET",
                f"{SARVAM_BASE}/doc-digitization/job/v1/{job_id}/status",
                headers=_headers(api_key),
                timeout=30,
            )
            if resp.json().get("total_files", 0) > 0:
                break
        except Exception:
            pass

    _start_job(api_key, job_id)
    _poll_until_done(api_key, job_id)
    return _download_result(api_key, job_id)


def digitize_pdf(pdf_path: Path, api_key: str) -> str:
    """
    Digitize a full PDF, splitting into ≤10-page chunks if needed.
    Returns concatenated markdown for the entire document.
    """
    chunks = split_pdf(pdf_path)
    is_split = chunks[0] != pdf_path
    print(f"[sarvam] {pdf_path.name}: {len(chunks)} chunk(s)")

    parts: list[str] = []
    for i, chunk_path in enumerate(chunks):
        try:
            print(f"[sarvam]   chunk {i + 1}/{len(chunks)}: submitting...")
            md = digitize_chunk(chunk_path, api_key)
            parts.append(md)
            print(f"[sarvam]   chunk {i + 1}/{len(chunks)}: {len(md)} chars")
        except Exception as exc:
            print(f"[sarvam]   chunk {i + 1}/{len(chunks)} ERROR: {exc}", file=sys.stderr)
            parts.append(f"\n\n<!-- ERROR: chunk {i + 1} failed: {exc} -->\n\n")
        finally:
            if is_split:
                chunk_path.unlink(missing_ok=True)

    return "\n\n---\n\n".join(parts)


# ---------------------------------------------------------------------------
# Progress tracking
# ---------------------------------------------------------------------------


def _load_progress() -> set[str]:
    if PROGRESS_JSON.exists():
        data = json.loads(PROGRESS_JSON.read_text())
        return set(data.get("done", []))
    return set()


def _save_progress(done: set[str]) -> None:
    PROGRESS_JSON.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS_JSON.write_text(json.dumps({"done": sorted(done)}, indent=2))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Digitize ECI affidavit PDFs via Sarvam Vision")
    parser.add_argument("--api-key", default=os.environ.get("SARVAM_API_KEY", ""))
    parser.add_argument("--pdf-dir", default=str(PDF_DIR))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true", help="List PDFs to process without calling API")
    args = parser.parse_args()

    api_key = args.api_key
    if not api_key and not args.dry_run:
        print("[sarvam] ERROR: SARVAM_API_KEY not set. Export it or pass --api-key.", file=sys.stderr)
        sys.exit(1)

    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.exists():
        print(f"[sarvam] ERROR: PDF dir not found: {pdf_dir}", file=sys.stderr)
        sys.exit(1)

    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if args.limit:
        pdfs = pdfs[: args.limit]

    done = _load_progress()
    remaining = [p for p in pdfs if p.stem not in done]
    print(f"[sarvam] {len(pdfs)} PDFs total, {len(done)} already done, {len(remaining)} to process")

    if args.dry_run:
        for p in remaining[:20]:
            pages = len(pypdf.PdfReader(str(p)).pages)
            chunks = (pages + MAX_PAGES_PER_JOB - 1) // MAX_PAGES_PER_JOB
            print(f"  {p.name}: {pages} pages → {chunks} chunk(s)")
        if len(remaining) > 20:
            print(f"  ... and {len(remaining) - 20} more")
        return

    for idx, pdf_path in enumerate(remaining, start=1):
        stem = pdf_path.stem
        md_path = MARKDOWN_DIR / f"{stem}.md"

        # Skip if markdown already exists (from a previous partial run)
        if md_path.exists() and md_path.stat().st_size > 100:
            print(f"[sarvam] [{idx}/{len(remaining)}] {stem} — markdown exists, marking done")
            done.add(stem)
            continue

        print(f"[sarvam] [{idx}/{len(remaining)}] {pdf_path.name}")

        try:
            markdown = digitize_pdf(pdf_path, api_key)
            md_path.write_text(markdown, encoding="utf-8")
            done.add(stem)
            print(f"[sarvam] [{idx}/{len(remaining)}] saved {md_path.name} ({len(markdown)} chars)")
        except Exception as exc:
            print(f"[sarvam] [{idx}/{len(remaining)}] FAILED: {exc}", file=sys.stderr)

        if idx % SAVE_EVERY == 0:
            _save_progress(done)
            print(f"[sarvam] checkpoint: {len(done)} done")

    _save_progress(done)
    print(f"[sarvam] complete. {len(done)} PDFs digitized → {MARKDOWN_DIR}")


if __name__ == "__main__":
    main()
