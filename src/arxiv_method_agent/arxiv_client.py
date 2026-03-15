from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import List
from urllib.parse import urlencode

import feedparser
import requests
from dateutil import parser as dt_parser
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)

from .schemas import PaperRecord
from .utils import extract_urls, first_github_url, format_arxiv_timestamp

ARXIV_API = "https://export.arxiv.org/api/query"
DEFAULT_CATEGORIES = ["cs.LG", "cs.AI", "stat.ML"]


class ArxivClient:
    def __init__(self, session: requests.Session | None = None):
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "arxiv-method-agent/0.1"})

    def fetch_recent_papers(
        self,
        start_dt: datetime,
        end_dt: datetime,
        categories: list[str] | None = None,
        max_results: int = 200,
    ) -> list[PaperRecord]:
        """Fetch recent papers, trying date-range query first, then falling
        back to category-only query with client-side date filtering."""
        categories = categories or DEFAULT_CATEGORIES

        # Try date-range query first
        try:
            papers = self._query_with_date_range(start_dt, end_dt, categories, max_results)
            if papers:
                return papers
            logger.info("Date-range query returned 0 papers, trying category-only fallback")
        except Exception as exc:
            logger.warning("Date-range query failed (%s), trying category-only fallback", exc)

        # Fallback: query by category, filter by date client-side
        return self._query_category_only(start_dt, end_dt, categories, max_results)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _query_with_date_range(
        self,
        start_dt: datetime,
        end_dt: datetime,
        categories: list[str],
        max_results: int,
    ) -> list[PaperRecord]:
        category_q = " OR ".join(f"cat:{cat}" for cat in categories)
        date_q = (
            f"submittedDate:[{format_arxiv_timestamp(start_dt)}"
            f"+TO+{format_arxiv_timestamp(end_dt)}]"
        )
        search_query = f"({category_q}) AND {date_q}"
        return self._do_query(search_query, max_results)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _query_category_only(
        self,
        start_dt: datetime,
        end_dt: datetime,
        categories: list[str],
        max_results: int,
    ) -> list[PaperRecord]:
        category_q = " OR ".join(f"cat:{cat}" for cat in categories)
        search_query = f"({category_q})"
        papers = self._do_query(search_query, max_results)

        # Filter client-side by date
        start_utc = start_dt.astimezone(timezone.utc)
        end_utc = end_dt.astimezone(timezone.utc)
        filtered = [p for p in papers if start_utc <= p.published <= end_utc]
        logger.info(
            "Category-only query: %d total, %d in date range", len(papers), len(filtered)
        )
        return filtered

    def _do_query(self, search_query: str, max_results: int) -> list[PaperRecord]:
        params = {
            "search_query": search_query,
            "start": 0,
            "max_results": max_results,
            "sortBy": "submittedDate",
            "sortOrder": "descending",
        }
        resp = self.session.get(ARXIV_API, params=params, timeout=60)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        papers: list[PaperRecord] = []
        for entry in feed.entries:
            urls = extract_urls(entry.get("summary", ""), entry.get("arxiv_comment", ""))
            pdf_url = None
            abs_url = entry.get("id")
            for link in entry.get("links", []):
                if getattr(link, "type", None) == "application/pdf":
                    pdf_url = link.get("href")
            papers.append(
                PaperRecord(
                    arxiv_id=entry.id.rsplit("/", 1)[-1],
                    title=" ".join(entry.title.split()),
                    summary=" ".join(entry.summary.split()),
                    authors=[a.name for a in entry.get("authors", [])],
                    published=dt_parser.isoparse(entry.published),
                    updated=dt_parser.isoparse(entry.updated),
                    categories=[t["term"] for t in entry.get("tags", [])],
                    comment=entry.get("arxiv_comment"),
                    pdf_url=pdf_url,
                    abs_url=abs_url,
                    candidate_urls=urls,
                    repo_url=first_github_url(urls),
                )
            )
        time.sleep(3)  # arXiv API rate limit: 1 request per 3 seconds
        return papers
