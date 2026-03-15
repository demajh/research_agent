# Architecture

This document describes the internal architecture of arXiv Method Scout: how the LangGraph workflow is structured, how state flows between nodes, and how checkpointing and parallelism work.

## Overview

The pipeline is a directed acyclic graph (DAG) built with [LangGraph](https://github.com/langchain-ai/langgraph). It has five node types connected in sequence, with one fan-out stage that processes interests in parallel.

```
START
  │
  ▼
fetch_candidates ──── Fetch papers from arXiv API
  │
  ▼
fanout_interests ──── Conditional edge: one Send() per interest
  │
  ├──▶ process_interest (Interest A) ──┐
  ├──▶ process_interest (Interest B) ──┤  (parallel)
  └──▶ process_interest (Interest C) ──┘
                                       │
                                       ▼
                              compose_email ──── Merge reports, build HTML
                                       │
                                       ▼
                                 send_email ──── SMTP delivery (or dry-run skip)
                                       │
                                       ▼
                                      END
```

## Source files

| File | Role |
|------|------|
| `graph.py` | Defines `AgentState`, wires nodes and edges, compiles the graph |
| `nodes.py` | `WorkflowContext` class with all business logic |
| `main.py` | Entry point: config loading, graph invocation, signal handling |

## State schema

The graph operates on `AgentState`, a `TypedDict`:

```python
class AgentState(TypedDict, total=False):
    run_id: str                                          # Unique ID for this pipeline run
    candidate_papers: list[dict]                         # Raw arXiv papers (serialized PaperRecord)
    interest_reports: Annotated[list[dict], operator.add] # Accumulated per-interest reports
    email_subject: str
    email_html: str
    email_text: str
```

The `Annotated[list[dict], operator.add]` on `interest_reports` is the key LangGraph pattern: when multiple parallel `process_interest` nodes each return `{"interest_reports": [...]}`, LangGraph concatenates all the lists automatically using `operator.add`.

## Fan-out with `Send()`

The `fanout_interests` function is a conditional edge that returns a list of `Send()` objects—one per interest defined in the config:

```python
def fanout_interests(state: AgentState):
    sends = []
    for interest in ctx.cfg.interests:
        sends.append(
            Send(
                "process_interest",
                {
                    "run_id": state["run_id"],
                    "interest": interest.model_dump(mode="json"),
                    "candidate_papers": state.get("candidate_papers", []),
                },
            )
        )
    return sends
```

Each `Send()` creates an independent invocation of the `process_interest` node with its own state slice (`InterestTaskState`). Results are merged back into the main `AgentState` via the `operator.add` reducer.

## Checkpointing

The graph uses `SqliteSaver` from `langgraph-checkpoint-sqlite` for durable checkpointing:

```python
conn = sqlite3.connect(db_path, check_same_thread=False)
checkpointer = SqliteSaver(conn)
graph = builder.compile(checkpointer=checkpointer)
```

The checkpoint database is stored at `.runs/agent.db`. This means:

- If the pipeline crashes mid-run, it can be resumed from the last checkpoint.
- Each run gets a unique `thread_id` (the `run_id`), so checkpoints don't collide across runs.

## Node details

### `fetch_candidates`

Calls `ArxivClient.fetch_recent_papers()` to get the last day of papers from `cs.LG`, `cs.AI`, and `stat.ML`. Returns serialized `PaperRecord` objects in the `candidate_papers` state field.

The arXiv client has a two-strategy approach:
1. Try a date-range query (`submittedDate:[start TO end]`) first.
2. If that fails (arXiv's date filter is unreliable), fall back to a category-only query and filter by date on the client side.

### `process_interest`

The core of the pipeline. For each interest, this node:

1. **Keyword prefilter** — Fast string matching against title, abstract, and comments. Papers that don't match any keyword are skipped without an LLM call.
2. **Deduplication check** — Queries the SQLite dedup tracker. Papers already processed for this interest in a previous run are skipped.
3. **LLM triage** — Sends the paper to Claude with a structured output schema (`PaperTriage`). The LLM decides relevance, implementation status, benchmark family, and writes a value summary.
4. **Repo resolution** — If the paper didn't link to a repo, searches GitHub by paper title.
5. **Clone and inspect** — Shallow-clones the repo, reads the README, lists files, identifies entrypoints and dependency files.
6. **Benchmark planning** — Sends repo metadata to Claude, which generates a `BenchmarkPlan` (Docker image, packages, commands).
7. **Optional human approval** — If `require_human_approval_for_execution: true`, the pipeline pauses with a LangGraph `interrupt()` and waits for approval.
8. **Docker execution** — Builds and runs the container with network disabled, 8GB memory limit, and 4-CPU limit.
9. **Artifact upload** — If storage is enabled, uploads logs, metrics, and the Docker image tar to S3.

### `compose_email`

Merges all `InterestReport` objects from the parallel fan-in, builds HTML and plain-text email bodies with paper details, triage summaries, and benchmark results.

### `send_email`

Sends the email via SMTP, or skips if `dry_run: true`.

## WorkflowContext

`WorkflowContext` (in `nodes.py`) is the dependency injection container for the pipeline. It holds all clients:

| Client | Purpose |
|--------|---------|
| `LLMClient` | Claude API calls with structured output |
| `ArxivClient` | arXiv API queries |
| `GitHubClient` | GitHub search + repo cloning |
| `BenchmarkRegistry` | Generates synthetic benchmark datasets |
| `DockerRunner` | Docker image build + container execution |
| `StorageClient` | S3-compatible uploads |
| `EmailClient` | SMTP email sending |
| `DeduplicationTracker` | SQLite-backed paper dedup |

The context is created once in `main.py` and passed to `build_graph()`, which closes over it in the node functions.

## Error handling

- **Transient failures** (network, rate limits) are retried with exponential backoff via [tenacity](https://tenacity.readthedocs.io/).
- **Auth/billing errors** from the Anthropic API are not retried (they won't succeed on retry).
- **Benchmark failures** are caught and recorded as `BenchmarkResult(status="error")` — they don't crash the pipeline.
- **Signal handling** — `SIGTERM` and `SIGINT` trigger graceful shutdown: active Docker containers are killed, the dedup database is closed, and the process exits cleanly.

## Adding a new node

To add a new processing step:

1. Add a method to `WorkflowContext` in `nodes.py`.
2. Add any new state fields to `AgentState` in `graph.py`.
3. Define a node function in `build_graph()` that calls your new method.
4. Wire it into the graph with `builder.add_node()` and `builder.add_edge()`.
