# Production Deployment

This document covers everything you need to run arXiv Method Scout reliably in production: scheduling, deduplication, graceful shutdown, logging, and monitoring.

## Scheduling

### cron

arXiv publishes new papers daily around midnight US Eastern (except weekends). A good schedule is 1–2 hours after that:

```bash
# crontab -e
20 1 * * 1-5 cd /path/to/research_agent && ./scripts/run_daily.sh >> /var/log/arxiv-scout.log 2>&1
```

The `1-5` restricts to weekdays. The agent handles weekends automatically (shifts the lookback window to the most recent Friday), but there's no point running on Saturday/Sunday since arXiv won't have new papers.

### The run script

`scripts/run_daily.sh` activates the virtualenv and runs the pipeline:

```bash
#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
source .venv/bin/activate
export PYTHONPATH=src
python -m arxiv_method_agent.main run --config configs/interests.yaml
```

Make sure your environment variables (`ANTHROPIC_API_KEY`, etc.) are available to the cron environment. You can either:
- Source an env file in `run_daily.sh`
- Set them in the crontab with `ANTHROPIC_API_KEY=... crontab` entries
- Use a secrets manager

### systemd timer (alternative)

For more control over logging and restart behavior:

```ini
# /etc/systemd/system/arxiv-scout.service
[Unit]
Description=arXiv Method Scout daily run
After=docker.service

[Service]
Type=oneshot
WorkingDirectory=/opt/research_agent
ExecStart=/opt/research_agent/scripts/run_daily.sh
EnvironmentFile=/opt/research_agent/.env
StandardOutput=journal
StandardError=journal

# /etc/systemd/system/arxiv-scout.timer
[Unit]
Description=Run arXiv Method Scout daily

[Timer]
OnCalendar=Mon..Fri 01:20
Persistent=true

[Install]
WantedBy=timers.target
```

## Deduplication

The agent tracks processed papers in a SQLite database (`.runs/agent.db`, table `processed_papers`) to avoid re-processing the same paper across runs.

### How it works

- Before calling the LLM for triage, the agent checks `DeduplicationTracker.is_processed(arxiv_id, interest_name)`.
- After processing a paper (regardless of outcome), it calls `mark_processed(arxiv_id, interest_name, run_id)`.
- The primary key is `(arxiv_id, interest)`, so the same paper can be processed for different interests but won't be re-triaged for the same interest.

### Implications

- **First run**: All papers in the lookback window are processed.
- **Second run** (same day): Previously processed papers are skipped. Only genuinely new papers are triaged.
- **Next day**: New papers from arXiv are processed; yesterday's papers are skipped.

### Resetting dedup

To force re-processing of all papers, delete the database:

```bash
rm .runs/agent.db
```

Or to reset just one interest:

```bash
sqlite3 .runs/agent.db "DELETE FROM processed_papers WHERE interest = 'graph machine learning';"
```

## Graceful shutdown

The agent registers signal handlers for `SIGTERM` and `SIGINT`:

```python
def _shutdown_handler(signum, frame):
    logger.warning("Received signal %s, cleaning up...", signum)
    ctx.docker.cleanup()
    raise SystemExit(1)
```

On shutdown:
1. All active Docker containers are killed and removed.
2. The dedup database connection is closed (via the `finally` block).
3. Old run directories are cleaned up.

This prevents orphaned Docker containers from accumulating if the pipeline is interrupted.

## Logging

### Format

All logs go to stderr in structured format:

```
2026-03-14T22:39:34 INFO     arxiv_method_agent.arxiv_client  Fetched 158 candidate papers
2026-03-14T22:39:46 WARNING  arxiv_method_agent.llm           Retrying structured in 2 seconds...
```

Format: `<timestamp> <level> <logger-name> <message>`

### Log levels

- **INFO** — Normal operation: papers fetched, triage results, benchmark outcomes, timing.
- **WARNING** — Retry attempts, fallback strategies, non-fatal issues.
- **ERROR** — Docker build/run failures, unrecoverable errors.

### Noisy loggers

These third-party loggers are suppressed to WARNING level:
- `urllib3`, `httpx`, `httpcore` — HTTP client noise
- `docker` — Docker SDK noise
- `botocore` — AWS SDK noise

### Redirecting logs

For production, redirect stderr to a log file:

```bash
python -m arxiv_method_agent.main run --config configs/interests.yaml 2>> /var/log/arxiv-scout.log
```

Or use the run script which captures both stdout and stderr:

```bash
./scripts/run_daily.sh >> /var/log/arxiv-scout.log 2>&1
```

## Timing and observability

Key operations are wrapped with `log_duration()`, which logs wall-clock time:

```
arXiv candidate fetch completed in 12.2s
process_interest(graph machine learning) completed in 45.3s
Full pipeline run completed in 58.1s
```

At the end of each interest, summary stats are logged:

```
Interest 'graph machine learning': 8 papers triaged, 3 benchmarked, 2 passed
```

## Run output structure

Each run creates a date-stamped directory under `.runs/`, and a `latest` symlink always points to the most recent completed run:

```
.runs/
├── latest -> 2026-03-14_224800/
├── 2026-03-14_224800/
│   ├── run.json          # Summary stats
│   ├── papers.json       # Index of all triaged papers
│   ├── email.html        # Saved email
│   └── papers/           # Per-paper details + benchmark artifacts
└── 2026-03-13_013000/
    └── ...
```

To quickly check the last run: `cat .runs/latest/run.json` or open `.runs/latest/email.html` in a browser.

Temporary files (cloned repos, Docker build contexts) are stored in `_workspace/` during the run and cleaned up automatically on success.

## Checkpointing

The LangGraph workflow uses `SqliteSaver` for durable checkpointing. The checkpoint database is at `.runs/agent.db` (shared with the dedup tracker).

If the pipeline crashes mid-run, the checkpoint enables potential recovery. Each run uses a unique `thread_id` (the date-stamped run ID), so checkpoints don't collide.

## Artifact retention

Old run directories are automatically deleted based on `pipeline.retention_days`:

```yaml
pipeline:
  retention_days: 30  # Delete run dirs older than 30 days
```

Cleanup happens at the end of each pipeline run (in the `finally` block). Directories are identified by modification time, not by name parsing. The `latest` symlink is never deleted.

Set `retention_days: 0` to disable automatic cleanup.

## Docker prerequisites

The Docker daemon must be running on the machine where the agent executes. The agent uses the Docker SDK (`docker` Python package) which connects via the Docker socket.

Verify Docker is available:

```bash
docker info
```

If running in a VM or container, make sure the Docker socket is mounted or the Docker daemon is accessible.

## Disk space

Temporary files (cloned repos, Docker build contexts) are cleaned up after each successful run, which dramatically reduces disk usage compared to keeping everything.

Persisted artifacts per benchmarked paper include `build.log`, `run.log`, `metrics.json`, and `image.tar`. The image tar is typically the largest file (~100–500 MB).

For a daily run processing ~5 papers with repos:

- **Per run (after workspace cleanup)**: ~500 MB–2 GB
- **30-day retention**: ~15–60 GB

Plan disk space accordingly, or set `retention_days` to a lower value.

## Failure modes and recovery

| Failure | Behavior | Recovery |
|---------|----------|----------|
| arXiv API down | Retries 3x, then fails | Re-run later; dedup prevents re-processing |
| Anthropic API error | Retries 3x (except auth errors) | Fix API key/billing, re-run |
| Docker build fails | Recorded as `status: "error"`, pipeline continues | Check `build.log` for details |
| Container timeout | Container killed after `run_timeout_seconds` | Increase timeout or simplify benchmark |
| S3 upload fails | Retries 3x per file, then logs error | Artifacts remain local in `.runs/` |
| SMTP fails | Retries 3x, then fails | Check SMTP credentials; re-run to get email |
| Pipeline crash | Containers cleaned up via signal handler | Re-run; dedup skips already-processed papers |
