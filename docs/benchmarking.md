# Benchmarking

This document explains how arXiv Method Scout benchmarks research code: the Docker isolation model, the security constraints, the built-in benchmark families, and how to add your own.

## How benchmarking works

When the agent finds a paper that links to a public GitHub repo (or finds one via search), it runs this sequence:

1. **Clone** — Shallow clone (`--depth 1`) of the repo, with a 120-second timeout and a 500MB size limit.
2. **Inspect** — Read the README, list top-level files, identify dependency files (`requirements.txt`, `pyproject.toml`, etc.) and candidate entrypoints (`train.py`, `main.py`, etc.).
3. **Generate benchmark assets** — Create a tiny synthetic dataset appropriate to the benchmark family (CSV, JSONL, or just instructions).
4. **LLM planning** — Send the repo metadata and benchmark description to Claude. The LLM generates a `BenchmarkPlan`: base Docker image, apt/pip packages, setup commands, run commands, and expected metrics output path.
5. **Validate** — Check all generated commands against a security deny-list and the base image against an allow-list.
6. **Build** — Generate a Dockerfile and build the image.
7. **Run** — Start the container with network disabled, 8GB memory limit, and 4-CPU limit. Wait for completion or timeout.
8. **Collect** — Extract `metrics.json` from the container, save build/run logs and the Docker image tar.

## Security model

Running arbitrary code from the internet requires serious guardrails. The agent implements defense in depth:

### Command deny-list

Every command in the generated `setup_commands` and `run_commands` is checked against a deny-list before execution. Denied patterns include:

- `sudo`, `rm -rf /` — Privilege escalation and destructive commands
- `docker`, `podman`, `kubectl` — Container escape
- `ssh`, `scp`, `nc`, `ncat`, `socat`, `telnet` — Network access tools
- `curl|bash`, `wget|sh` — Pipe-to-shell attacks
- `dd if=`, `mkfs` — Disk manipulation
- `/dev/tcp/`, `/dev/udp/` — Bash network redirects
- `LD_PRELOAD` — Library injection
- Reverse shell patterns

### Base image allow-list

Only these image prefixes are allowed:

- `python:` (e.g., `python:3.11-slim`)
- `nvidia/cuda:`
- `ubuntu:`
- `debian:`
- `continuumio/miniconda`
- `pytorch/pytorch:`

Any other base image is rejected.

### Runtime isolation

Containers run with:

- **Network disabled** (`network_disabled=True`) — No internet access at runtime.
- **Memory limit** — 8GB maximum.
- **CPU limit** — 4 cores maximum.
- **No privilege escalation** — Standard Docker unprivileged mode.

### Symlink validation

Before copying repo files into the Docker build context, the agent checks for symlinks that resolve outside the source directory. This prevents path traversal attacks where a malicious repo symlinks to `/etc/passwd` or similar.

### Container tracking

Active containers are tracked in a thread-safe set. On `SIGTERM` or `SIGINT`, all tracked containers are killed and removed to prevent orphaned processes.

## Benchmark families

The `BenchmarkRegistry` provides synthetic datasets and instructions for different task types. Each benchmark family generates a tiny dataset and an `instructions.json` file that guides the LLM's benchmark plan.

### `generic_python_demo`

**Metric:** `success` (1.0 or 0.0)

A smoke test. The LLM is instructed to verify that the repo installs, imports work, and a basic command runs. This is the default for papers where you just want to know "does this code work at all?"

### `tabular_binary_classification`

**Metric:** `accuracy`

Generates a synthetic binary classification dataset:
- 120 training samples, 40 test samples
- 8 numeric features drawn from two Gaussian clusters
- CSV format with columns `f0`–`f7` and `label`

### `time_series_forecasting`

**Metric:** `mae` (mean absolute error)

Generates a synthetic univariate time series:
- 180 training points, 60 test points
- Noisy sine wave
- CSV format with columns `t` and `value`

### `text_classification`

**Metric:** `accuracy`

Generates a tiny sentiment classification dataset:
- 6 training examples, 2 test examples
- JSONL format with `text` and `label` fields

## Adding a custom benchmark

To add a new benchmark family:

1. Open `src/arxiv_method_agent/benchmark_registry.py`.
2. Add a new method:

```python
def _my_custom_benchmark(self, base_dir: Path) -> BenchmarkAssets:
    out = ensure_dir(base_dir / "my_custom_benchmark")

    # Generate your synthetic dataset
    # ...write data files to `out`...

    write_json(
        out / "instructions.json",
        {
            "task": "my task description",
            "metric": "my_metric",
            "train_path": "/workspace/benchmark_assets/train.csv",
            "test_path": "/workspace/benchmark_assets/test.csv",
        },
    )
    return BenchmarkAssets(
        benchmark_id="my_custom_benchmark",
        metric_name="my_metric",
        dataset_dir=out,
        description="Description sent to the LLM when generating the benchmark plan.",
    )
```

3. Register it in `build_assets()`:

```python
if benchmark_id == "my_custom_benchmark":
    return self._my_custom_benchmark(base_dir)
```

4. Reference it in your config:

```yaml
interests:
  - name: my interest
    benchmark:
      id: my_custom_benchmark
      metric_name: my_metric
```

## Metrics output contract

Every benchmark must produce a JSON file at the path specified in `BenchmarkPlan.metrics_output_path` (default: `/workspace/out/metrics.json`). The JSON should be a flat dictionary with numeric values:

```json
{
  "accuracy": 0.85,
  "f1": 0.82
}
```

The first numeric key-value pair is used as the primary metric in the email summary. If the metrics file is missing or invalid, the agent falls back to reporting `success: 1.0` if the container exited cleanly, or `success: 0.0` if it failed.

## Artifacts

Each benchmark run produces these artifacts in `.runs/<run-date>/papers/<interest>/<arxiv-id>/`:

| File | Description |
|------|-------------|
| `paper.json` | Paper metadata, triage reasoning, and benchmark summary |
| `build.log` | Docker build output (JSON lines) |
| `run.log` | Container stdout/stderr |
| `metrics.json` | Extracted metrics from the container |
| `image.tar` | Docker image snapshot (for reproducibility) |

Papers that pass triage but aren't benchmarked (no repo, or plan marked `benchmarkable: false`) will have only `paper.json`.

When `storage.enabled: true`, these are also uploaded to S3 under `s3://<bucket>/<prefix>/<run-id>/<interest>/<arxiv-id>/`.

## Human approval mode

Set `pipeline.require_human_approval_for_execution: true` to pause before each Docker execution. The pipeline uses LangGraph's `interrupt()` mechanism, which halts the graph and waits for external input before continuing.

This is useful for:
- First-time deployment (review what the LLM plans to run)
- High-security environments
- Auditing the agent's judgment
