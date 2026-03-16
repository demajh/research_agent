from __future__ import annotations

import logging
import re
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

PAPERS_WITH_CODE_API = "https://paperswithcode.com/api/v1/papers/"
_GITHUB_REPO_RE = re.compile(r"https?://github\.com/[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+")

ARXIV_API = "https://export.arxiv.org/api/query"
DEFAULT_CATEGORIES = ["cs.LG", "cs.AI", "cs.CV", "cs.CL", "stat.ML"]


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
        # No client-side date filter — the date-range query already failed,
        # so just return the most recent papers and let keyword/triage filter.
        logger.info("Category-only fallback: returning %d most recent papers", len(papers))
        return papers

    def find_repo_url(self, paper: PaperRecord) -> str | None:
        """Find the paper's code repository.

        1. Direct GitHub link in abstract/comment
        2. Follow project page links (.github.io) from abstract/comment
        3. Download the PDF, extract text, search for GitHub URLs
        """
        # 1. Direct GitHub repo link from abstract/comment
        if paper.repo_url:
            return paper.repo_url

        # 2. Follow project page links from abstract/comment
        for url in paper.candidate_urls:
            if ".github.io" in url or "project" in url.lower():
                repo = self._follow_project_page(url)
                if repo:
                    logger.info("Found repo for %s via project page %s: %s",
                                paper.arxiv_id, url, repo)
                    return repo

        # 3. Download PDF and search full paper text for GitHub links
        if paper.pdf_url:
            repo = self._search_pdf(paper.pdf_url, paper.arxiv_id)
            if repo:
                return repo

        return None

    def _follow_project_page(self, url: str) -> str | None:
        """Fetch a project page and look for GitHub repo links."""
        try:
            resp = self.session.get(url, timeout=15)
            if resp.status_code != 200:
                return None
            return self._first_github_repo(resp.text)
        except Exception as exc:
            logger.debug("Failed to follow project page %s: %s", url, exc)
        return None

    def _search_pdf(self, pdf_url: str, arxiv_id: str) -> str | None:
        """Download PDF and extract GitHub URLs from the full paper text."""
        try:
            resp = self.session.get(pdf_url, timeout=60)
            if resp.status_code != 200:
                return None

            import subprocess
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(resp.content)
                tmp_path = tmp.name

            try:
                result = subprocess.run(
                    ["pdftotext", tmp_path, "-"],
                    capture_output=True, text=True, timeout=30,
                )
                if result.returncode != 0:
                    return None
                text = result.stdout
            finally:
                import os
                os.unlink(tmp_path)

            repo = self._first_github_repo(text)
            if repo:
                logger.info("Found repo for %s in PDF: %s", arxiv_id, repo)
                time.sleep(1)
                return repo

        except Exception as exc:
            logger.debug("PDF search failed for %s: %s", arxiv_id, exc)

        time.sleep(1)
        return None

    @staticmethod
    def _first_github_repo(text: str) -> str | None:
        """Return the first github.com/owner/repo URL from text."""
        for match in _GITHUB_REPO_RE.finditer(text):
            cleaned = match.group(0).rstrip(".,;:)/\"'")
            parts = cleaned.rstrip("/").split("/")
            if len(parts) >= 5:  # https://github.com/owner/repo
                return cleaned
        return None

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
