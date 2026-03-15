# Configuration

All configuration is defined in a single YAML file. Copy the example and edit it:

```bash
cp configs/interests.example.yaml configs/interests.yaml
```

## Full config reference

```yaml
pipeline:
  lookback_hours: 24                          # How many hours back to search arXiv
  max_papers_per_interest: 10                 # Cap on papers processed per interest per run
  require_human_approval_for_execution: false # Pause before Docker execution
  dry_run: true                               # Skip email sending; SMTP config not required
  retention_days: 30                          # Auto-delete run artifacts older than this

llm:
  model_name: claude-sonnet-4-20250514       # Any Anthropic model ID
  temperature: 0.0                            # 0.0 for deterministic triage

github:
  token_env: GITHUB_TOKEN                     # Env var name holding your GitHub token

storage:
  enabled: false                              # false = local-only, no S3 upload
  bucket: your-datalake-bucket                # S3 bucket name (ignored when disabled)
  prefix: arxiv-method-agent                  # Key prefix for uploaded artifacts
  region: us-west-2                           # AWS region (ignored when disabled)
  endpoint_url_env: S3_ENDPOINT_URL           # Env var for custom S3 endpoint (MinIO, R2)

email:                                        # Omit entire section when dry_run: true
  smtp_host: smtp.example.com
  smtp_port: 587
  username_env: SMTP_USERNAME                 # Env var name for SMTP username
  password_env: SMTP_PASSWORD                 # Env var name for SMTP password
  from_email: research-bot@example.com
  to:
    - alice@example.com
    - bob@example.com

interests:
  - name: graph machine learning
    description: >
      New methods for graph representation learning, graph transformers,
      graph foundation models, node classification, link prediction,
      and molecular graphs.
    keywords:
      - graph neural network
      - graph transformer
      - node classification
    benchmark:
      id: generic_python_demo
      metric_name: success
      run_timeout_seconds: 1800
```

## Section details

### `pipeline`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `lookback_hours` | int | 24 | Time window for arXiv queries. Papers published outside this window are filtered out. |
| `max_papers_per_interest` | int | 10 | Maximum papers to fully process (triage + benchmark) per interest per run. Prevents runaway LLM/Docker costs. |
| `require_human_approval_for_execution` | bool | false | When true, the pipeline pauses before each Docker benchmark execution and waits for approval via LangGraph's interrupt mechanism. |
| `dry_run` | bool | false | When true, email sending is skipped and SMTP config is not required. Useful for testing. |
| `retention_days` | int | 30 | Run artifact directories older than this are deleted at the end of each run. Set to 0 to disable cleanup. |

### `llm`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `model_name` | str | *(required)* | Anthropic model ID. Examples: `claude-sonnet-4-20250514`, `claude-haiku-4-5-20241022`. |
| `temperature` | float | 0.0 | LLM temperature. 0.0 gives deterministic, consistent triage results. |

### `github`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `token_env` | str | `GITHUB_TOKEN` | Name of the environment variable holding your GitHub personal access token. The token is optional but recommended—without it, GitHub API rate limits are very low (60 requests/hour). |

### `storage`

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | true | Set to `false` to keep all artifacts local (no S3 upload). When disabled, `bucket`, `region`, and `endpoint_url_env` are ignored. |
| `bucket` | str | `""` | S3 bucket name. Required when `enabled: true`. |
| `prefix` | str | `arxiv-method-agent` | Key prefix for all uploaded artifacts. |
| `region` | str | *(optional)* | AWS region for the bucket. |
| `endpoint_url_env` | str | *(optional)* | Name of env var holding a custom S3 endpoint URL (for MinIO, Cloudflare R2, etc.). |

### `email`

The entire `email` section can be omitted when `dry_run: true`. When `dry_run: false`, it is required.

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `smtp_host` | str | *(required)* | SMTP server hostname. |
| `smtp_port` | int | 587 | SMTP port. 587 is standard for STARTTLS. |
| `username_env` | str | `SMTP_USERNAME` | Env var name for SMTP login username. |
| `password_env` | str | `SMTP_PASSWORD` | Env var name for SMTP login password. |
| `from_email` | str | *(required)* | Sender address for the daily email. |
| `to` | list[str] | *(required)* | List of recipient email addresses. |

### `interests`

Each interest defines a research topic to monitor:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | str | *(required)* | Human-readable name (used in email headings and logs). |
| `description` | str | *(required)* | Natural-language description of what you're looking for. This is sent to the LLM for triage. Be specific—good descriptions produce better filtering. |
| `keywords` | list[str] | `[]` | Fast prefilter keywords. A paper must match at least one keyword (case-insensitive, checked against title + abstract + comments) before the LLM is called. Empty list means all papers go to LLM triage. |
| `benchmark.id` | str | *(required)* | Which benchmark family to use. See [Benchmarking](benchmarking.md) for available IDs. |
| `benchmark.metric_name` | str | *(required)* | Primary metric name (for display in email). |
| `benchmark.run_timeout_seconds` | int | 1800 | Maximum container runtime in seconds before timeout. |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `ANTHROPIC_API_KEY` | Always | Your Anthropic API key (starts with `sk-ant-`). |
| `GITHUB_TOKEN` | Recommended | GitHub personal access token for higher API rate limits. Without it, you're limited to 60 search requests/hour. |
| `SMTP_USERNAME` | When `dry_run: false` | SMTP login username. |
| `SMTP_PASSWORD` | When `dry_run: false` | SMTP login password. |
| `AWS_ACCESS_KEY_ID` | When `storage.enabled: true` | AWS credentials for S3. Can also use instance profiles or `~/.aws/credentials`. |
| `AWS_SECRET_ACCESS_KEY` | When `storage.enabled: true` | AWS credentials for S3. |
| `S3_ENDPOINT_URL` | When using non-AWS S3 | Custom endpoint for MinIO, R2, etc. |

The agent validates required environment variables at startup and fails fast with a clear error message if any are missing.

## Modes of operation

### Minimal dry-run (testing)

```yaml
pipeline:
  dry_run: true
storage:
  enabled: false
# email section omitted entirely
```

Required env vars: `ANTHROPIC_API_KEY` only (plus `GITHUB_TOKEN` recommended).

### Local-only with email

```yaml
pipeline:
  dry_run: false
storage:
  enabled: false
email:
  smtp_host: smtp.gmail.com
  # ...
```

Required env vars: `ANTHROPIC_API_KEY`, `SMTP_USERNAME`, `SMTP_PASSWORD`.

### Full production

```yaml
pipeline:
  dry_run: false
storage:
  enabled: true
  bucket: my-research-bucket
email:
  smtp_host: smtp.example.com
  # ...
```

Required env vars: all of the above plus `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`.
