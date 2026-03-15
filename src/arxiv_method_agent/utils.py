from __future__ import annotations

import json
import logging
import os
import re
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, Iterator, Sequence
from urllib.parse import urlparse


URL_RE = re.compile(r"https?://[^\s)>,]+")
GITHUB_RE = re.compile(r"https?://(?:www\.)?github\.com/[^\s)>,]+")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def format_arxiv_timestamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y%m%d%H%M")


def lookback_window(hours: int) -> tuple[datetime, datetime]:
    end = utc_now()
    # arXiv doesn't publish on weekends. If today is Saturday or Sunday,
    # shift the window back to cover the most recent weekday.
    weekday = end.weekday()  # 0=Mon ... 4=Fri, 5=Sat, 6=Sun
    if weekday == 5:  # Saturday -> back to Friday
        end = end - timedelta(days=1)
    elif weekday == 6:  # Sunday -> back to Friday
        end = end - timedelta(days=2)
    start = end - timedelta(hours=hours)
    return start, end


def extract_urls(*texts: str | None) -> list[str]:
    urls: list[str] = []
    for text in texts:
        if not text:
            continue
        urls.extend(URL_RE.findall(text))
    deduped = []
    seen = set()
    for url in urls:
        cleaned = url.rstrip(".,;:")
        if cleaned not in seen:
            seen.add(cleaned)
            deduped.append(cleaned)
    return deduped


def first_github_url(urls: Sequence[str]) -> str | None:
    for url in urls:
        if GITHUB_RE.match(url):
            return url
    return None


def slugify(value: str) -> str:
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", "-", value)
    value = re.sub(r"-+", "-", value)
    return value.strip("-")


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_read_text(path: str | Path, max_chars: int = 12000) -> str:
    p = Path(path)
    if not p.exists() or not p.is_file():
        return ""
    try:
        return p.read_text(encoding="utf-8", errors="ignore")[:max_chars]
    except Exception:
        return ""


def list_root_files(path: str | Path) -> list[str]:
    p = Path(path)
    if not p.exists():
        return []
    return sorted([x.name for x in p.iterdir()][:200])


def repo_name_from_url(url: str) -> str:
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        return f"{parts[0]}/{parts[1].removesuffix('.git')}"
    return parsed.path.strip("/")


def write_json(path: str | Path, payload: dict) -> None:
    Path(path).write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


@contextmanager
def log_duration(operation: str, logger_instance: logging.Logger | None = None):
    """Context manager that logs the wall-clock duration of a block."""
    log = logger_instance or logging.getLogger("arxiv_method_agent")
    start = time.monotonic()
    try:
        yield
    finally:
        elapsed = time.monotonic() - start
        log.info("%s completed in %.1fs", operation, elapsed)
