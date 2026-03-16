from __future__ import annotations

import argparse
import json
import logging
import shutil
import signal
import time as _time
from datetime import datetime, timezone
from pathlib import Path

from .config import load_config
from .dedup import DeduplicationTracker
from .graph import build_graph
from .logging_config import setup_logging
from .nodes import WorkflowContext
from .schemas import InterestReport
from .utils import log_duration

logger = logging.getLogger(__name__)


def _cleanup_old_runs(work_root: Path, retention_days: int) -> None:
    """Delete run directories older than retention_days."""
    if retention_days <= 0:
        return
    cutoff = _time.time() - (retention_days * 86400)
    if not work_root.exists():
        return
    for child in work_root.iterdir():
        if child.is_symlink():
            continue
        if child.is_dir() and child.stat().st_mtime < cutoff:
            logger.info("Removing old run directory: %s", child)
            shutil.rmtree(child, ignore_errors=True)


def _write_run_outputs(run_dir: Path, result: dict, cfg, started_at: datetime) -> None:
    """Write run.json, papers.json, email.html, and email.txt to the run directory."""
    run_dir.mkdir(parents=True, exist_ok=True)
    completed_at = datetime.now(timezone.utc)

    # --- Save email so you can always see what was (or would have been) sent ---
    email_html = result.get("email_html", "")
    email_text = result.get("email_text", "")
    if email_html:
        (run_dir / "email.html").write_text(email_html, encoding="utf-8")
    if email_text:
        (run_dir / "email.txt").write_text(email_text, encoding="utf-8")

    # --- Build papers.json: the single index of everything the run found ---
    interest_reports = result.get("interest_reports", [])
    reports = [InterestReport.model_validate(r) for r in interest_reports]

    papers_index = []
    total_triaged = 0
    total_benchmarked = 0
    total_passed = 0

    for report in reports:
        for outcome in report.papers:
            total_triaged += 1
            entry = {
                "arxiv_id": outcome.paper.arxiv_id,
                "title": outcome.paper.title,
                "authors": outcome.paper.authors,
                "interest": outcome.interest_name,
                "arxiv_url": outcome.paper.abs_url,
                "relevance_score": outcome.triage.relevance_score,
                "value_summary": outcome.triage.value_summary,
                "how_it_works": outcome.triage.how_it_works,
                "expected_vs_sota": outcome.triage.expected_vs_sota,
                "repo_url": outcome.repo_url,
                "benchmark_status": None,
                "metric_name": None,
                "metric_value": None,
            }
            if outcome.benchmark_result:
                total_benchmarked += 1
                entry["benchmark_status"] = outcome.benchmark_result.status
                entry["metric_name"] = outcome.benchmark_result.metric_name
                entry["metric_value"] = outcome.benchmark_result.metric_value
                if outcome.benchmark_result.status == "passed":
                    total_passed += 1
            papers_index.append(entry)

    (run_dir / "papers.json").write_text(
        json.dumps({"run_id": result.get("run_id", ""), "papers": papers_index},
                   indent=2, default=str),
        encoding="utf-8",
    )

    # --- Write run.json: top-level summary ---
    candidate_count = len(result.get("candidate_papers", []))
    run_data = {
        "run_id": result.get("run_id", ""),
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "duration_seconds": round((completed_at - started_at).total_seconds(), 1),
        "dry_run": cfg.pipeline.dry_run,
        "interests": [i.name for i in cfg.interests],
        "papers_fetched": candidate_count,
        "papers_triaged": total_triaged,
        "papers_benchmarked": total_benchmarked,
        "papers_passed": total_passed,
    }
    (run_dir / "run.json").write_text(json.dumps(run_data, indent=2), encoding="utf-8")


def run_pipeline(config_path: str) -> None:
    setup_logging()

    config_file = Path(config_path)
    if not config_file.exists():
        raise FileNotFoundError(f"Config file not found: {config_file}")

    cfg = load_config(config_path)
    work_root = Path(".runs")
    work_root.mkdir(parents=True, exist_ok=True)
    db_path = str(work_root / "agent.db")
    if cfg.pipeline.dedup:
        dedup = DeduplicationTracker(db_path)
    else:
        dedup = None
        logger.info("Deduplication disabled")
    ctx = WorkflowContext(cfg, work_root=work_root, dedup=dedup)
    graph = build_graph(ctx, db_path=db_path)

    # Date-stamped run ID so you can tell runs apart at a glance
    started_at = datetime.now(timezone.utc)
    run_id = started_at.strftime("%Y-%m-%d_%H%M%S")
    run_dir = work_root / run_id

    config = {"configurable": {"thread_id": run_id}}

    def _shutdown_handler(signum, frame):
        logger.warning("Received signal %s, cleaning up...", signum)
        ctx.docker.cleanup()
        raise SystemExit(1)

    signal.signal(signal.SIGTERM, _shutdown_handler)
    signal.signal(signal.SIGINT, _shutdown_handler)

    try:
        with log_duration("Full pipeline run", logger):
            result = graph.invoke({"run_id": run_id}, config=config)

        # Write summary files
        _write_run_outputs(run_dir, result, cfg, started_at)

        # Update `latest` symlink
        latest = work_root / "latest"
        if latest.is_symlink() or latest.exists():
            latest.unlink()
        latest.symlink_to(run_id)

        # Clean up workspace (repos, docker contexts, benchmark assets)
        workspace = run_dir / "_workspace"
        if workspace.exists():
            shutil.rmtree(workspace, ignore_errors=True)
            logger.info("Cleaned up workspace for run %s", run_id)

        logger.info("Completed run %s", run_id)
        logger.info("Results saved to %s", run_dir)
    finally:
        ctx.docker.cleanup()
        if dedup:
            dedup.close()
        _cleanup_old_runs(work_root, cfg.pipeline.retention_days)


def main() -> None:
    parser = argparse.ArgumentParser(description="arXiv method scout agent")
    sub = parser.add_subparsers(dest="command", required=True)

    run_parser = sub.add_parser("run", help="run the daily workflow")
    run_parser.add_argument("--config", required=True, help="Path to YAML config")

    args = parser.parse_args()

    if args.command == "run":
        run_pipeline(args.config)


if __name__ == "__main__":
    main()
