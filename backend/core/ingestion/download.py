from __future__ import annotations

import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen


def download_file(
    url: str,
    dest_path: str | Path,
    *,
    timeout: int = 30,
    retries: int = 2,
    backoff: float = 1.5,
    user_agent: str = "tntracker/1.0",
    skip_existing: bool = False,
) -> Path:
    dest = Path(dest_path)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if skip_existing and dest.exists() and dest.stat().st_size > 0:
        return dest

    attempt = 0
    while True:
        try:
            request = Request(url, headers={"User-Agent": user_agent})
            with urlopen(request, timeout=timeout) as response, dest.open("wb") as handle:
                handle.write(response.read())
            return dest
        except (TimeoutError, URLError) as exc:
            attempt += 1
            if attempt > retries:
                raise exc
            time.sleep(backoff * attempt)
    return dest
