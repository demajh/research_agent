# Storage and Email

This document covers artifact storage (local and S3) and email delivery configuration.

## Artifact storage

### Local artifacts

Run directories use date-stamped names so you can tell them apart at a glance. A `latest` symlink always points to the most recent completed run.

```
.runs/
├── agent.db                                    # Checkpoint + dedup database
├── latest -> 2026-03-14_224800/                # Symlink to most recent run
└── 2026-03-14_224800/
    ├── run.json                                # Summary: timing, stats, interests
    ├── papers.json                             # Index of ALL triaged papers
    ├── email.html                              # The email (or what would have been sent)
    ├── email.txt                               # Plain-text version
    └── papers/
        └── <interest-slug>/
            └── <arxiv-id>/
                ├── paper.json                  # Paper metadata + triage + benchmark details
                ├── build.log                   # Docker build output (if benchmarked)
                ├── run.log                     # Container stdout/stderr (if benchmarked)
                ├── metrics.json                # Extracted metrics (if benchmarked)
                └── image.tar                   # Docker image snapshot (if benchmarked)
```

**Key files to look at first:**

- **`run.json`** — One-glance summary: how many papers were fetched, triaged, benchmarked, and passed.
- **`papers.json`** — Flat index of every paper the agent found interesting, with triage scores and benchmark results. Open this one file to see everything.
- **`email.html`** — Open in a browser to see exactly what the email looks like.
- **`papers/<interest>/<arxiv-id>/paper.json`** — Full detail for one paper: abstract, triage reasoning, benchmark metrics.

Temporary files (cloned repos, Docker build contexts, generated datasets) are stored in a `_workspace/` directory during the run and automatically cleaned up on successful completion.

### Artifact cleanup

Old run directories are automatically deleted based on `pipeline.retention_days` (default: 30 days). Cleanup runs at the end of each pipeline execution. Set `retention_days: 0` to disable automatic cleanup. The `latest` symlink is never deleted by cleanup.

### S3 upload

When `storage.enabled: true`, per-paper artifacts are uploaded to your S3-compatible bucket after each benchmark completes:

```
s3://<bucket>/<prefix>/<run-id>/<interest-slug>/<arxiv-id>/
├── paper.json
├── build.log
├── run.log
├── metrics.json
└── image.tar
```

#### Configuration

```yaml
storage:
  enabled: true
  bucket: my-research-bucket
  prefix: arxiv-method-agent
  region: us-west-2
```

#### Environment variables

For AWS S3:

```bash
export AWS_ACCESS_KEY_ID="..."
export AWS_SECRET_ACCESS_KEY="..."
```

You can also use IAM instance profiles or `~/.aws/credentials` — the agent uses boto3, which supports the standard AWS credential chain.

#### Non-AWS S3-compatible stores

For MinIO, Cloudflare R2, DigitalOcean Spaces, etc., set the endpoint URL:

```yaml
storage:
  enabled: true
  bucket: my-bucket
  endpoint_url_env: S3_ENDPOINT_URL
```

```bash
export S3_ENDPOINT_URL="https://minio.example.com:9000"
```

#### Upload retry logic

Each file upload is retried up to 3 times with exponential backoff. If an individual file fails after 3 attempts, the error is logged but doesn't crash the pipeline.

### Local-only mode

To keep everything local with no S3 upload:

```yaml
storage:
  enabled: false
```

When disabled, the `bucket`, `region`, and `endpoint_url_env` fields are ignored, and AWS credential environment variables are not required.

## Email

### Email format

The daily email includes:

- **Subject**: `Daily arXiv method scout — <run-id>`
- **Per interest section** with each matching paper:
  - Paper title and arXiv link
  - Value summary (why this paper matters)
  - Method explanation (how it works)
  - Expected vs. SOTA assessment
  - Implementation status and reasoning
  - GitHub repo link (if found)
  - Benchmark result: status, metric name/value
  - Artifact location (S3 URI, if uploaded)
  - Collapsible abstract

The email is sent in both HTML and plain-text format.

### SMTP configuration

```yaml
email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  username_env: SMTP_USERNAME
  password_env: SMTP_PASSWORD
  from_email: research-bot@example.com
  to:
    - alice@example.com
    - bob@example.com
```

```bash
export SMTP_USERNAME="research-bot@example.com"
export SMTP_PASSWORD="your-app-password"
```

The agent uses STARTTLS on port 587 (standard for most SMTP providers).

### Provider-specific notes

#### Gmail

1. Enable 2-factor authentication on your Google account.
2. Generate an [App Password](https://myaccount.google.com/apppasswords).
3. Use the app password as `SMTP_PASSWORD`.

```yaml
email:
  smtp_host: smtp.gmail.com
  smtp_port: 587
  from_email: your.email@gmail.com
```

#### Amazon SES

```yaml
email:
  smtp_host: email-smtp.us-west-2.amazonaws.com
  smtp_port: 587
```

Use your SES SMTP credentials (not your IAM credentials).

#### Self-hosted / Postfix

```yaml
email:
  smtp_host: mail.example.com
  smtp_port: 587
```

### Dry-run mode

When `pipeline.dry_run: true`:

- The `email` config section can be omitted entirely.
- No SMTP environment variables are required.
- The email payload is still composed (so you can inspect `email_subject`, `email_html`, `email_text` in the graph state) but not sent.
- A log message confirms the skip: `"Dry run — skipping email send"`.

### Email retry logic

Email sending is retried up to 3 times with exponential backoff (2s, 4s, 8s). This handles transient SMTP connection issues.
