from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from langgraph.types import interrupt

logger = logging.getLogger(__name__)

from .arxiv_client import ArxivClient
from .benchmark_registry import BenchmarkRegistry
from .config import AppConfig, InterestConfig
from .docker_runner import DockerRunner
from .emailer import EmailClient
from .github_client import GitHubClient
from .llm import LLMClient
from .prompts import benchmark_plan_prompt, benchmark_summary_prompt, triage_prompt
from .schemas import BenchmarkPlan, BenchmarkResult, EmailPayload, InterestReport, PaperOutcome, PaperRecord, PaperTriage
from .storage import StorageClient
from .utils import ensure_dir, log_duration, lookback_window, safe_read_text, slugify, truncate


class WorkflowContext:
    def __init__(self, cfg: AppConfig, work_root: str | Path, dedup=None):
        self.cfg = cfg
        self.work_root = ensure_dir(work_root)
        self.dedup = dedup

        # --- Startup validation: fail fast on missing env vars ---
        missing = []
        for env_var in ["ANTHROPIC_API_KEY", cfg.github.token_env]:
            if not os.environ.get(env_var):
                missing.append(env_var)

        if not cfg.pipeline.dry_run:
            if cfg.email is None:
                raise ValueError("email config is required when dry_run is false")
            for env_var in [cfg.email.username_env, cfg.email.password_env]:
                if not os.environ.get(env_var):
                    missing.append(env_var)

        if cfg.storage.enabled:
            aws_vars = ["AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
            aws_missing = [v for v in aws_vars if not os.environ.get(v)]
            if aws_missing:
                logger.warning(
                    "AWS credential env vars not set: %s. "
                    "S3 uploads will rely on instance profile or ~/.aws/credentials.",
                    ", ".join(aws_missing),
                )

        if missing:
            raise EnvironmentError(
                f"Required environment variables are not set: {', '.join(missing)}"
            )

        self.llm = LLMClient(cfg.llm)
        self.arxiv = ArxivClient()
        self.github = GitHubClient(cfg.github)
        self.registry = BenchmarkRegistry()
        self.docker = DockerRunner()
        self.storage = StorageClient(cfg.storage)
        if cfg.pipeline.dry_run or cfg.email is None:
            self.email = None
        else:
            self.email = EmailClient(
                cfg.email,
                username=os.environ[cfg.email.username_env],
                password=os.environ[cfg.email.password_env],
            )

    def fetch_candidates(self) -> list[dict]:
        with log_duration("arXiv candidate fetch", logger):
            start_dt, end_dt = lookback_window(self.cfg.pipeline.lookback_hours)
            papers = self.arxiv.fetch_recent_papers(
                start_dt=start_dt,
                end_dt=end_dt,
                categories=self.cfg.pipeline.categories,
            )
            logger.info("Fetched %d candidate papers", len(papers))
            return [p.model_dump(mode="json") for p in papers]

    def process_interest(self, interest: InterestConfig, candidate_papers: list[dict], run_id: str) -> dict:
        with log_duration(f"process_interest({interest.name})", logger):
            candidate_models = [PaperRecord.model_validate(p) for p in candidate_papers]
            report = InterestReport(interest_name=interest.name)
            max_items = self.cfg.pipeline.max_papers_per_interest

            run_dir = self.work_root / run_id
            workspace = ensure_dir(run_dir / "_workspace")
            interest_slug = slugify(interest.name)

            for paper in candidate_models:
                if not self._keyword_prefilter(interest, paper):
                    continue

                if self.dedup and self.dedup.is_processed(paper.arxiv_id, interest.name):
                    logger.debug("Skipping already-processed paper %s for %s", paper.arxiv_id, interest.name)
                    continue

                triage = self.llm.structured(
                    triage_prompt(interest.name, interest.description, paper),
                    PaperTriage,
                )
                if not triage.relevant:
                    continue

                # Paper is relevant — now search for a GitHub repo
                repo_url = self.arxiv.find_repo_url(paper)
                if not repo_url:
                    logger.info("No repo found for relevant paper %s, skipping", paper.arxiv_id)
                    continue

                outcome = PaperOutcome(paper=paper, triage=triage, interest_name=interest.name)
                outcome.repo_url = repo_url

                # Per-paper output directory: papers/<interest>/<arxiv_id>/
                paper_dir = ensure_dir(run_dir / "papers" / interest_slug / paper.arxiv_id)

                try:
                    repo_inspection = self.github.clone_and_inspect(
                        repo_url,
                        workdir=workspace / "repos",
                    )
                    outcome.repo_inspection = repo_inspection

                    assets = self.registry.build_assets(
                        interest.benchmark.id,
                        base_dir=workspace / "benchmark_assets",
                    )
                    plan = self.llm.structured(
                        benchmark_plan_prompt(
                            interest_name=interest.name,
                            paper_title=paper.title,
                            paper_summary=paper.summary,
                            benchmark_description=assets.description,
                            inspection=repo_inspection,
                        ),
                        BenchmarkPlan,
                    )
                    outcome.benchmark_plan = plan

                    if plan.benchmarkable:
                        if self.cfg.pipeline.require_human_approval_for_execution:
                            approval = interrupt(
                                {
                                    "type": "benchmark_execution_approval",
                                    "interest": interest.name,
                                    "paper": paper.title,
                                    "repo_url": repo_url,
                                    "reason": plan.reason,
                                    "setup_commands": plan.setup_commands,
                                    "run_commands": plan.run_commands,
                                }
                            )
                            if not approval:
                                self._write_paper_json(paper_dir, outcome)
                                report.papers.append(outcome)
                                if len(report.papers) >= max_items:
                                    break
                                continue

                        result = self.docker.run_plan(
                            plan=plan,
                            repo_path=repo_inspection.local_path,
                            benchmark_assets_dir=assets.dataset_dir,
                            artifact_dir=paper_dir,
                            context_dir=workspace / "docker_contexts" / slugify(paper.arxiv_id),
                            image_tag=f"arxiv-method-agent:{slugify(paper.arxiv_id)}",
                            timeout_seconds=interest.benchmark.run_timeout_seconds,
                        )
                        if result.local_artifact_dir and self.cfg.storage.enabled:
                            bucket_uri = self.storage.upload_tree(
                                result.local_artifact_dir,
                                run_id=run_id,
                                interest_name=interest.name,
                                paper_id=paper.arxiv_id,
                            )
                            result.bucket_uri = bucket_uri

                        # Generate a human-readable summary of the benchmark run
                        run_log = ""
                        if result.run_log_path:
                            run_log = safe_read_text(result.run_log_path, max_chars=8000)
                            # Take last 100 lines
                            lines = run_log.splitlines()
                            if len(lines) > 100:
                                run_log = "\n".join(lines[-100:])
                        try:
                            result.summary = self.llm.text(
                                benchmark_summary_prompt(
                                    paper_title=paper.title,
                                    plan=plan,
                                    run_log=run_log,
                                    metrics=result.metrics,
                                    status=result.status,
                                )
                            )
                        except Exception as exc:
                            logger.debug("Failed to generate benchmark summary: %s", exc)

                        outcome.benchmark_result = result
                    else:
                        logger.info("Repo not benchmarkable for %s: %s", paper.arxiv_id, plan.reason)
                        outcome.benchmark_result = BenchmarkResult(
                            status="skipped",
                            reason=plan.reason,
                        )
                except Exception as exc:
                    logger.warning("Benchmark failed for paper %s: %s", paper.arxiv_id, exc)
                    outcome.benchmark_result = BenchmarkResult(
                        status="error",
                        reason=f"benchmark execution failed: {exc}",
                    )

                self._write_paper_json(paper_dir, outcome)
                report.papers.append(outcome)
                if self.dedup:
                    self.dedup.mark_processed(paper.arxiv_id, interest.name, run_id)
                if len(report.papers) >= max_items:
                    break

            # Summary stats
            triaged_count = len(report.papers)
            benchmark_count = sum(1 for p in report.papers if p.benchmark_result is not None)
            passed_count = sum(
                1 for p in report.papers
                if p.benchmark_result and p.benchmark_result.status == "passed"
            )
            logger.info(
                "Interest '%s': %d papers triaged, %d benchmarked, %d passed",
                interest.name, triaged_count, benchmark_count, passed_count,
            )
            return {"interest_reports": [report.model_dump(mode="json")]}

    def build_email_payload(self, interest_reports: list[dict], run_id: str) -> EmailPayload:
        reports = [InterestReport.model_validate(r) for r in interest_reports]
        subject = f"Daily arXiv method scout — {run_id}"
        html_parts = [
            "<html><body>",
            f"<h1>Daily arXiv method scout</h1>",
            f"<p>Run ID: <code>{run_id}</code></p>",
            "<p>This email summarizes fresh arXiv AI / ML papers from the last day, filtered by your areas of interest.</p>",
        ]
        text_parts = [
            "Daily arXiv method scout",
            f"Run ID: {run_id}",
            "",
        ]

        for report in reports:
            html_parts.append(f"<h2>{report.interest_name}</h2>")
            text_parts.append(f"## {report.interest_name}")
            if not report.papers:
                html_parts.append("<p>No papers matched this interest today.</p>")
                text_parts.append("No papers matched this interest today.")
                continue

            for outcome in report.papers:
                triage = outcome.triage
                paper = outcome.paper
                html_parts.append(f"<h3>{paper.title}</h3>")
                html_parts.append(
                    f"<p><strong>arXiv</strong>: <a href=\"{paper.abs_url or '#'}\">{paper.arxiv_id}</a>"
                )
                if outcome.repo_url:
                    html_parts.append(
                        f" &nbsp;|&nbsp; <strong>Repo</strong>: <a href=\"{outcome.repo_url}\">{outcome.repo_url}</a>"
                    )
                html_parts.append("</p>")
                html_parts.append(f"<p><strong>Why it matters</strong>: {triage.value_summary}</p>")
                html_parts.append(f"<p><strong>How it works</strong>: {triage.how_it_works}</p>")
                html_parts.append(
                    f"<p><strong>Reported results</strong>: {triage.expected_vs_sota}</p>"
                )
                # Benchmark execution results
                br = outcome.benchmark_result
                if br:
                    status_color = {"passed": "#2e7d32", "failed": "#c62828", "error": "#e65100", "skipped": "#666"}.get(br.status, "#333")
                    if br.summary:
                        html_parts.append(
                            f"<p><strong>Benchmark</strong> "
                            f"<span style=\"color:{status_color};font-weight:bold\">[{br.status.upper()}]</span>: "
                            f"{br.summary}</p>"
                        )
                    else:
                        reason_text = br.reason or br.status
                        html_parts.append(
                            f"<p><strong>Benchmark</strong>: "
                            f"<span style=\"color:{status_color};font-weight:bold\">{br.status.upper()}</span>"
                            f" &mdash; {reason_text}</p>"
                        )
                    if br.bucket_uri:
                        html_parts.append(
                            f"<p><strong>Artifacts</strong>: <code>{br.bucket_uri}</code></p>"
                        )
                html_parts.append(
                    f"<p><details><summary>Abstract</summary><pre>{paper.summary}</pre></details></p>"
                )

                text_parts.extend(
                    [
                        f"- {paper.title}",
                        f"  arXiv: {paper.arxiv_id}",
                    ]
                )
                if outcome.repo_url:
                    text_parts.append(f"  Repo: {outcome.repo_url}")
                text_parts.extend(
                    [
                        f"",
                        f"  Why it matters: {triage.value_summary}",
                        f"",
                        f"  How it works: {triage.how_it_works}",
                        f"",
                        f"  Reported results: {triage.expected_vs_sota}",
                        f"",
                    ]
                )
                if outcome.benchmark_result:
                    br = outcome.benchmark_result
                    if br.summary:
                        text_parts.append(f"  Benchmark [{br.status.upper()}]: {br.summary}")
                    else:
                        text_parts.append(f"  Benchmark: {br.status.upper()} — {br.reason or br.status}")
                    if br.bucket_uri:
                        text_parts.append(f"  Artifacts: {br.bucket_uri}")
                text_parts.append("")

        html_parts.append("</body></html>")
        return EmailPayload(
            subject=subject,
            html_body="\n".join(html_parts),
            text_body="\n".join(text_parts),
        )

    def send_email(self, payload: EmailPayload) -> None:
        if self.email is None:
            logger.info("Dry run — skipping email send")
            return
        self.email.send(payload)

    @staticmethod
    def _keyword_prefilter(interest: InterestConfig, paper: PaperRecord) -> bool:
        if not interest.keywords:
            return True
        haystack = f"{paper.title} {paper.summary} {paper.comment or ''}".lower()
        return any(keyword.lower() in haystack for keyword in interest.keywords)

    @staticmethod
    def _write_paper_json(paper_dir: Path, outcome: PaperOutcome) -> None:
        """Write a human-readable JSON summary for one processed paper."""
        paper = outcome.paper
        triage = outcome.triage
        data = {
            "arxiv_id": paper.arxiv_id,
            "title": paper.title,
            "authors": paper.authors,
            "published": paper.published.isoformat(),
            "categories": paper.categories,
            "arxiv_url": paper.abs_url,
            "abstract": paper.summary,
            "interest": outcome.interest_name,
            "triage": {
                "relevance_score": triage.relevance_score,
                "relevance_reason": triage.relevance_reason,
                "value_summary": triage.value_summary,
                "how_it_works": triage.how_it_works,
                "expected_vs_sota": triage.expected_vs_sota,
            },
            "repo_url": outcome.repo_url,
        }
        if outcome.benchmark_result:
            br = outcome.benchmark_result
            data["benchmark"] = {
                "status": br.status,
                "metric_name": br.metric_name,
                "metric_value": br.metric_value,
                "metrics": br.metrics,
                "reason": br.reason,
            }
        (paper_dir / "paper.json").write_text(
            json.dumps(data, indent=2, default=str), encoding="utf-8"
        )
