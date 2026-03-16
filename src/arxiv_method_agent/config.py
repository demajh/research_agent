from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class BenchmarkConfig(BaseModel):
    id: str
    metric_name: str
    run_timeout_seconds: int = 1800


class InterestConfig(BaseModel):
    name: str
    description: str
    keywords: List[str] = Field(default_factory=list)
    benchmark: BenchmarkConfig


class PipelineConfig(BaseModel):
    lookback_hours: int = 24
    max_papers_per_interest: int = 10
    require_human_approval_for_execution: bool = False
    dry_run: bool = False
    retention_days: int = 30
    dedup: bool = True
    categories: List[str] = Field(
        default_factory=lambda: ["cs.LG", "cs.AI", "cs.CV", "cs.CL", "stat.ML"],
        description="arXiv categories to search",
    )


class LLMConfig(BaseModel):
    model_name: str
    temperature: float = 0.0


class GitHubConfig(BaseModel):
    token_env: str = "GITHUB_TOKEN"


class StorageConfig(BaseModel):
    enabled: bool = True
    bucket: str = ""
    prefix: str = "arxiv-method-agent"
    region: Optional[str] = None
    endpoint_url_env: Optional[str] = None

    @property
    def endpoint_url(self) -> Optional[str]:
        if self.endpoint_url_env:
            return os.getenv(self.endpoint_url_env)
        return None


class EmailConfig(BaseModel):
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    username_env: str = "GMAIL_USER"
    password_env: str = "GMAIL_PW"
    from_email: str
    to: List[str]


class AppConfig(BaseModel):
    pipeline: PipelineConfig
    llm: LLMConfig
    github: GitHubConfig
    storage: StorageConfig = StorageConfig()
    email: Optional[EmailConfig] = None
    interests: List[InterestConfig]


def load_config(path: str | Path) -> AppConfig:
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return AppConfig.model_validate(data)
