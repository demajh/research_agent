# LLM Integration

This document covers how arXiv Method Scout uses Claude for paper triage and benchmark planning: the prompts, structured output schemas, retry logic, and model selection.

## Overview

The agent makes two types of LLM calls:

1. **Triage** — Given a paper and an interest description, decide if the paper is relevant, assess implementability, and write a brief summary.
2. **Benchmark planning** — Given a repo's structure and a benchmark description, generate a safe Docker execution plan.

Both calls use Claude's structured output feature (via `langchain-anthropic`'s `with_structured_output()`) to guarantee the response matches a Pydantic schema.

## LLM client

The `LLMClient` class (`src/arxiv_method_agent/llm.py`) wraps `ChatAnthropic`:

```python
class LLMClient:
    def __init__(self, cfg: LLMConfig):
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        self._model = ChatAnthropic(
            model=cfg.model_name,
            temperature=cfg.temperature,
            api_key=api_key,
        )

    def structured(self, prompt: str, schema: Type[T]) -> T:
        """Returns a Pydantic model instance matching the schema."""
        runnable = self._model.with_structured_output(schema)
        return runnable.invoke(prompt)

    def text(self, prompt: str) -> str:
        """Returns a plain text response."""
        return self._model.invoke(prompt).content
```

Both methods include a 0.5-second delay after each call for rate limiting.

## Triage prompt and schema

### Prompt

The triage prompt (`src/arxiv_method_agent/prompts.py`) sends the LLM:

- The interest name and description
- The paper title, authors, categories, comments, and abstract

It asks the LLM to decide:

1. Is the paper relevant to the interest?
2. Is it repo-backed, implementable from the paper, or not implementable?
3. Which benchmark family does it resemble?
4. A crisp value summary, method explanation, and expected-vs-SOTA statement.

Guidelines instruct the LLM to be skeptical of hype and not hallucinate repo URLs.

### Schema

The `PaperTriage` Pydantic model (`src/arxiv_method_agent/schemas.py`):

```python
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
```

Using `Literal` types constrains the LLM to valid enum values. The `Field(ge=0.0, le=1.0)` on `relevance_score` ensures it stays in range.

## Benchmark plan prompt and schema

### Prompt

The benchmark plan prompt sends the LLM:

- Interest name, paper title, and abstract
- Benchmark description (from the registry)
- Repo inspection data: URL, top-level files, candidate entrypoints, dependency files, README excerpt

It asks for a Docker execution plan with constraints:
- Non-interactive shell commands only
- No sudo, no background services
- Prefer standard install patterns (`pip install -r requirements.txt`)
- If no safe plan exists, set `benchmarkable: false`

### Schema

```python
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
```

## Retry logic

Both `structured()` and `text()` are wrapped with [tenacity](https://tenacity.readthedocs.io/) retry decorators:

```python
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=20),
    retry=retry_if_not_exception_type((
        AuthenticationError,
        BadRequestError,
        PermissionDeniedError,
    )),
    before_sleep=before_sleep_log(logger, logging.WARNING),
    reraise=True,
)
```

Key behavior:
- **3 attempts** with exponential backoff (1s, 2s, 4s, up to 20s max).
- **Auth, billing, and permission errors are NOT retried** — they won't succeed on retry and would just waste time.
- **Transient errors** (rate limits, server errors, network issues) are retried.
- **Warnings are logged** before each retry sleep.

## Model selection

Set the model in your config:

```yaml
llm:
  model_name: claude-sonnet-4-20250514   # Best balance of quality and cost
  temperature: 0.0
```

Recommendations:
- **claude-sonnet-4-20250514** — Best balance of triage quality and cost for daily runs.
- **claude-haiku-4-5-20241022** — Cheaper and faster, adequate for simple keyword-heavy interests.
- **temperature 0.0** — Recommended for consistent, reproducible triage results.

## Cost estimation

Each paper that passes the keyword prefilter costs one triage LLM call (~1,000–2,000 input tokens). Papers with repos that pass triage cost one additional benchmark planning call (~2,000–4,000 input tokens).

For a typical run with 150 candidate papers and 2 interests:
- ~50 papers pass keyword filters → ~50 triage calls
- ~5 papers have repos → ~5 benchmark plan calls
- Total: ~55 calls, roughly $0.10–$0.30 with Sonnet pricing

## Customizing prompts

The prompt templates are in `src/arxiv_method_agent/prompts.py`. You can modify them to:

- Change the triage criteria (e.g., weight novelty differently)
- Add domain-specific instructions (e.g., "ignore papers that only test on ImageNet")
- Change the benchmark planning constraints
- Add few-shot examples for better structured output quality
