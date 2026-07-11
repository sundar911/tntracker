"""Sarvam Document Intelligence API client for OCR of scanned affidavit PDFs.

Sarvam's async job-based workflow:
  1. Create job  POST /doc-digitization/job/v1           — JSON body, returns job_id
  2. Get URL     POST /doc-digitization/job/v1/upload-files — returns presigned PUT URL
  3. Upload file PUT  <presigned_url>                    — binary upload (x-ms-blob-type)
  4. Start job   POST /doc-digitization/job/v1/{job_id}/start
  5. Poll status GET  /doc-digitization/job/v1/{job_id}  until Completed
  6. Download    GET  /doc-digitization/job/v1/{job_id}/download — returns ZIP of .md files
"""
from __future__ import annotations

import io
import time
import zipfile
from pathlib import Path

import requests

SARVAM_BASE = "https://api.sarvam.ai"
_JOB_URL = f"{SARVAM_BASE}/doc-digitization/job/v1"
_POLL_INTERVAL = 5   # seconds between status checks
_TIMEOUT_SECS = 300  # give up after 5 minutes per batch


def _headers(api_key: str) -> dict:
    return {"api-subscription-key": api_key}


def _create_job(api_key: str, language: str = "ta-IN") -> str:
    """Create a doc-digitization job and return the job_id."""
    resp = requests.post(
        _JOB_URL,
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"job_parameters": {"language": language, "output_format": "md"}},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Create job failed {resp.status_code}: {resp.text}")
    return resp.json()["job_id"]


def _get_upload_url(job_id: str, filename: str, api_key: str) -> str:
    """Request a presigned upload URL for the given file."""
    resp = requests.post(
        f"{_JOB_URL}/upload-files",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"job_id": job_id, "files": [filename]},
        timeout=30,
    )
    if not resp.ok:
        raise RuntimeError(f"Get upload URL failed {resp.status_code}: {resp.text}")
    data = resp.json()
    # Response: {"upload_urls": {"<filename>": {"file_url": "https://..."}}, ...}
    return data["upload_urls"][filename]["file_url"]


def _upload_file(presigned_url: str, file_path: Path) -> None:
    """Binary-PUT the file to the presigned Azure Blob Storage URL."""
    with open(file_path, "rb") as fh:
        resp = requests.put(
            presigned_url,
            data=fh,
            headers={
                "Content-Type": "application/zip",
                "x-ms-blob-type": "BlockBlob",
            },
            timeout=120,
        )
    if not resp.ok:
        raise RuntimeError(f"File upload failed {resp.status_code}: {resp.text[:200]}")


def _start_job(job_id: str, api_key: str) -> None:
    resp = requests.post(
        f"{_JOB_URL}/{job_id}/start",
        headers=_headers(api_key),
        timeout=30,
    )
    resp.raise_for_status()


def _poll_until_done(job_id: str, api_key: str) -> None:
    """Block until the job reaches Completed (or raises on failure/timeout)."""
    deadline = time.time() + _TIMEOUT_SECS
    while time.time() < deadline:
        resp = requests.get(f"{_JOB_URL}/{job_id}/status", headers=_headers(api_key), timeout=30)
        resp.raise_for_status()
        state = resp.json().get("job_state", "")
        if state == "Completed":
            return
        if state in ("Failed", "PartiallyCompleted"):
            raise RuntimeError(f"Sarvam job {job_id} ended with state: {state}")
        time.sleep(_POLL_INTERVAL)
    raise TimeoutError(f"Sarvam job {job_id} did not complete within {_TIMEOUT_SECS}s")


def _download_results(job_id: str, api_key: str) -> str:
    """Download the results ZIP and return concatenated markdown text.

    New Sarvam API flow: POST /{job_id}/download-files returns presigned URLs,
    then GET each URL to fetch the actual ZIP content.
    """
    resp = requests.post(
        f"{_JOB_URL}/{job_id}/download-files",
        headers={**_headers(api_key), "Content-Type": "application/json"},
        json={"job_id": job_id},
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    download_urls = data.get("download_urls", {})

    parts: list[str] = []
    for filename, info in download_urls.items():
        file_url = info.get("file_url", "")
        if not file_url:
            continue
        zip_resp = requests.get(file_url, timeout=120)
        zip_resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(zip_resp.content)) as zf:
            for name in sorted(zf.namelist()):
                if name.endswith(".md"):
                    parts.append(zf.read(name).decode("utf-8"))
    return "\n\n".join(parts)


def ocr_zip_batch(zip_path: Path, api_key: str, language: str = "ta-IN") -> str:
    """Submit a ZIP of JPEG pages to Sarvam OCR and return extracted markdown text.

    Args:
        zip_path: Path to a ZIP file containing ≤10 JPEG images (flat structure).
        api_key:  Sarvam API key (from SARVAM_API_KEY env var).
        language: BCP-47 language code; default ta-IN (Tamil).

    Returns:
        Concatenated markdown text extracted from all pages in the ZIP.
    """
    job_id = _create_job(api_key, language)
    presigned_url = _get_upload_url(job_id, zip_path.name, api_key)
    _upload_file(presigned_url, zip_path)
    _start_job(job_id, api_key)
    _poll_until_done(job_id, api_key)
    return _download_results(job_id, api_key)
