from __future__ import annotations

import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)

from .config import GitHubConfig
from .schemas import PaperRecord, RepoInspection
from .utils import ensure_dir, list_root_files, repo_name_from_url, safe_read_text, slugify, truncate

GITHUB_API = "https://api.github.com"
MAX_CLONE_BYTES = 10 * 1024 * 1024 * 1024  # 10 GB


class GitHubClient:
    def __init__(self, cfg: GitHubConfig, session: requests.Session | None = None):
        self.cfg = cfg
        self.session = session or requests.Session()
        token = os.getenv(cfg.token_env)
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.session.headers.update(headers)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def search_repo(self, paper: PaperRecord) -> str | None:
        if paper.repo_url:
            return paper.repo_url
        query = f'"{paper.title}" in:name,description,readme'
        resp = self.session.get(
            f"{GITHUB_API}/search/repositories",
            params={"q": query, "per_page": 5, "sort": "stars", "order": "desc"},
            timeout=30,
        )
        time.sleep(1)  # GitHub API: stay under search rate limit
        if resp.status_code >= 400:
            return None
        items = resp.json().get("items", [])
        if not items:
            return None
        top = items[0]
        return top.get("html_url")

    def clone_and_inspect(self, repo_url: str, workdir: str | Path) -> RepoInspection:
        workdir = ensure_dir(workdir)
        repo_slug = slugify(repo_name_from_url(repo_url).replace("/", "-"))
        local_path = workdir / repo_slug
        if not local_path.exists():
            subprocess.run(
                ["git", "clone", "--depth", "1", repo_url, str(local_path)],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=120,
            )
            # Reject oversized repos
            clone_size = sum(f.stat().st_size for f in local_path.rglob("*") if f.is_file())
            if clone_size > MAX_CLONE_BYTES:
                shutil.rmtree(local_path)
                raise ValueError(
                    f"Cloned repo exceeds size limit: {clone_size / (1024 * 1024):.0f}MB > 10GB"
                )
        root_files = list_root_files(local_path)
        dependency_files = [
            name
            for name in root_files
            if name in {"requirements.txt", "pyproject.toml", "setup.py", "environment.yml", "Dockerfile"}
        ]
        candidate_entrypoints = [
            name
            for name in root_files
            if name in {"train.py", "main.py", "run.py", "app.py", "demo.py", "inference.py"}
        ]

        readme_excerpt = ""
        for candidate in ["README.md", "README.rst", "readme.md", "Readme.md"]:
            readme_excerpt = safe_read_text(local_path / candidate, max_chars=14000)
            if readme_excerpt:
                break

        return RepoInspection(
            repo_url=repo_url,
            repo_name=repo_name_from_url(repo_url),
            local_path=str(local_path),
            readme_excerpt=truncate(readme_excerpt, 12000),
            root_files=root_files,
            candidate_entrypoints=candidate_entrypoints,
            dependency_files=dependency_files,
        )
