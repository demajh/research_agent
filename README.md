# arXiv Method Scout

An automated pipeline that monitors arXiv daily for new AI/ML papers, triages them against your research interests using an LLM, and—when a paper ships code—builds a Docker container and runs a small benchmark to verify the method actually works. Results are delivered as a daily email digest.

## Why this exists

Keeping up with arXiv is a full-time job. Hundreds of papers land every day across cs.LG, cs.AI, and stat.ML alone. Most researchers cope by skimming titles, relying on Twitter, or checking a handful of curated feeds. All of these miss papers that matter to *your* specific interests.

This project automates the grunt work:

- **You define your interests** in plain English (plus optional keyword filters).
- **The agent fetches the last day of arXiv papers**, filters them by keyword, then uses Claude to triage each paper for relevance, novelty, and implementability.
- **For papers with public code**, the agent clones the repo, generates a safe Docker benchmark plan, builds the container, and runs a small smoke test—all in an isolated, network-disabled environment.
- **Artifacts** (build logs, run logs, metrics, Docker images) are saved locally or uploaded to any S3-compatible object store.
- **A daily email** summarizes everything: what was found, why it matters, whether the code runs, and what metrics came out.

The result is a system that surfaces the 5–10 papers per day that are most relevant to your work, with reproducible evidence for the ones that ship code.

## How it works

```
                    ┌─────────────────┐
                    │  YAML config    │
                    │  (interests,    │
                    │   credentials)  │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │  Fetch arXiv    │
                    │  papers (API)   │
                    └────────┬────────┘
                             │
              ┌──────────────┼──────────────┐
              │              │              │
     ┌────────▼──────┐ ┌────▼───────┐ ┌────▼───────┐
     │  Interest A   │ │ Interest B │ │ Interest C │   (parallel)
     │               │ │            │ │            │
     │ 1. Keyword    │ │  (same)    │ │  (same)    │
     │    prefilter  │ │            │ │            │
     │ 2. LLM triage│ │            │ │            │
     │ 3. Find repo  │ │            │ │            │
     │ 4. Clone +    │ │            │ │            │
     │    inspect    │ │            │ │            │
     │ 5. Plan       │ │            │ │            │
     │    benchmark  │ │            │ │            │
     │ 6. Docker     │ │            │ │            │
     │    build+run  │ │            │ │            │
     │ 7. Collect    │ │            │ │            │
     │    metrics    │ │            │ │            │
     └──────┬───────┘ └─────┬──────┘ └─────┬──────┘
            │               │              │
            └───────────────┼──────────────┘
                            │
                   ┌────────▼────────┐
                   │  Compose email  │
                   │  (HTML + text)  │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │   Send email    │
                   └─────────────────┘
```

The pipeline is orchestrated by [LangGraph](https://github.com/langchain-ai/langgraph), which handles fan-out across interests, durable checkpointing (SQLite-backed), and optional human-in-the-loop approval gates. Each interest is processed in parallel, and results are merged back into a single email.

### Three-tier benchmarking policy

Not every paper can be benchmarked automatically. The agent uses a conservative policy:

| Tier | Condition | Action |
|------|-----------|--------|
| 1 | Repo available + safe benchmark plan | Benchmark automatically in Docker |
| 2 | Repo available, no safe plan | Include in email as interesting but unverified |
| 3 | No repo, but implementable from paper | Include in email as candidate for manual work |

## Quick start

### Prerequisites

- Python 3.11+
- Docker (daemon running)
- Git
- An [Anthropic API key](https://console.anthropic.com/settings/keys)
- A [GitHub personal access token](https://github.com/settings/tokens) (optional, but recommended for higher rate limits)

### 1. Install

```bash
git clone <repo-url> && cd research_agent
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### 2. Configure

```bash
cp configs/interests.example.yaml configs/interests.yaml
```

Edit `configs/interests.yaml` to define your research interests. The example config runs in `dry_run: true` mode (no email sent, no S3 upload) so you can test safely.

### 3. Set environment variables

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export GITHUB_TOKEN="ghp_..."           # optional
```

### 4. Run

```bash
PYTHONPATH=src python -m arxiv_method_agent.main run --config configs/interests.yaml
```

On the first run with `dry_run: true`, you'll see structured log output showing papers fetched, triaged, and (if repos are found) benchmarked. No email is sent and no artifacts are uploaded.

### 5. Schedule daily runs

```bash
# crontab -e
20 1 * * * cd /path/to/research_agent && ./scripts/run_daily.sh >> /var/log/arxiv-scout.log 2>&1
```

arXiv updates daily around midnight US Eastern. Running at 1:20 AM gives the feed time to populate.

## Documentation

| Guide | What it covers |
|-------|---------------|
| [Architecture](docs/architecture.md) | LangGraph workflow, state management, checkpointing, fan-out pattern |
| [Configuration](docs/configuration.md) | All YAML options, environment variables, dry-run vs production mode |
| [Benchmarking](docs/benchmarking.md) | Docker isolation, security model, benchmark families, adding custom benchmarks |
| [LLM Integration](docs/llm-integration.md) | Triage prompts, structured output, retry logic, model selection |
| [Storage and Email](docs/storage-and-email.md) | S3 upload, local-only mode, SMTP setup, email format |
| [Production Deployment](docs/production-deployment.md) | Scheduling, deduplication, graceful shutdown, log management, monitoring |

## License

Licensed under the [Apache License, Version 2.0](https://www.apache.org/licenses/LICENSE-2.0).

```
Copyright 2025 Contributors to the arXiv Method Scout project

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
```
