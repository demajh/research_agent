from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


class PaperRecord(BaseModel):
    arxiv_id: str
    title: str
    summary: str
    authors: List[str] = Field(default_factory=list)
    published: datetime
    updated: datetime
    categories: List[str] = Field(default_factory=list)
    comment: Optional[str] = None
    pdf_url: Optional[str] = None
    abs_url: Optional[str] = None
    candidate_urls: List[str] = Field(default_factory=list)
    repo_url: Optional[str] = None


class PaperTriage(BaseModel):
    relevant: bool
    relevance_score: float = Field(ge=0.0, le=1.0)
    relevance_reason: str
    implementation_status: Literal[
        "repo_available",
        "implementable_from_paper",
        "not_implementable",
    ]
    implementation_reason: str
    likely_benchmark_family: Literal[
        "generic_python_demo",
        "tabular_binary_classification",
        "time_series_forecasting",
        "text_classification",
        "unknown",
    ]
    value_summary: str
    how_it_works: str
    expected_vs_sota: str


class RepoInspection(BaseModel):
    repo_url: str
    repo_name: str
    local_path: str
    readme_excerpt: str
    root_files: List[str]
    candidate_entrypoints: List[str]
    dependency_files: List[str]


class BenchmarkPlan(BaseModel):
    benchmarkable: bool
    reason: str
    base_image: str = "python:3.11-slim"
    apt_packages: List[str] = Field(default_factory=list)
    python_packages: List[str] = Field(default_factory=list)
    setup_commands: List[str] = Field(default_factory=list)
    run_commands: List[str] = Field(default_factory=list)
    metrics_output_path: str = "/workspace/out/metrics.json"
    result_notes: str = ""


class BenchmarkResult(BaseModel):
    status: Literal["passed", "failed", "error", "skipped"]
    metric_name: Optional[str] = None
    metric_value: Optional[float] = None
    metrics: Dict[str, Any] = Field(default_factory=dict)
    build_log_path: Optional[str] = None
    run_log_path: Optional[str] = None
    image_tar_path: Optional[str] = None
    local_artifact_dir: Optional[str] = None
    bucket_uri: Optional[str] = None
    reason: Optional[str] = None


class PaperOutcome(BaseModel):
    paper: PaperRecord
    triage: PaperTriage
    interest_name: str
    repo_url: Optional[str] = None
    repo_inspection: Optional[RepoInspection] = None
    benchmark_plan: Optional[BenchmarkPlan] = None
    benchmark_result: Optional[BenchmarkResult] = None


class InterestReport(BaseModel):
    interest_name: str
    papers: List[PaperOutcome] = Field(default_factory=list)


class EmailPayload(BaseModel):
    subject: str
    html_body: str
    text_body: str
