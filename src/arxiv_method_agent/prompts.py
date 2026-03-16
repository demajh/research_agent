from __future__ import annotations

import json

from .schemas import BenchmarkPlan, PaperRecord, RepoInspection


def triage_prompt(interest_name: str, interest_description: str, paper: PaperRecord) -> str:
    return f"""
You are writing a detailed technical briefing on a fresh arXiv paper for a senior research engineer.
They are an expert — do not dumb things down. Be specific, quantitative, and technical.

Interest name:
{interest_name}

Interest description:
{interest_description}

Paper title:
{paper.title}

Authors:
{", ".join(paper.authors)}

Categories:
{", ".join(paper.categories)}

Author comment:
{paper.comment or '(none)'}

Abstract:
{paper.summary}

Produce the following fields:

**relevant** — Is this paper relevant to the stated interest?

**relevance_score** — 0.0 to 1.0.

**relevance_reason** — One sentence explaining why it is or isn't relevant.

**value_summary** — 3-5 sentences. Explain what concrete problem this paper solves and
why an ML practitioner should care. Mention specific capabilities enabled (e.g. "enables
processing 1K-frame 4K videos on a single A100" not "enables longer videos"). Include
the most impressive quantitative claims from the abstract. If there's a new dataset or
benchmark introduced, describe it and say why it matters.

**how_it_works** — 4-8 sentences giving a technical explanation of the method. Describe
the architecture, training procedure, loss functions, or algorithmic innovations in
enough detail that a researcher could understand the core idea without reading the paper.
Use precise terminology. Mention what existing components it builds on or replaces.
If the method has multiple stages, describe each stage.

**expected_vs_sota** — 2-4 sentences with SPECIFIC NUMBERS extracted from the abstract or
author comment. Quote the actual metrics reported (e.g. "67.0% on VideoMME", "19x speedup
over baseline ViT"). Compare to any baselines or prior work mentioned. If the abstract
reports improvements over a named prior method, include the delta. If no quantitative
results appear in the abstract, say exactly that: "No quantitative results reported in abstract."

**likely_benchmark_family** — Which benchmark family best fits this paper.

Guidelines:
- Be skeptical of hype — separate what's claimed from what's demonstrated.
- NEVER say vague things like "likely competitive" or "promising results". Always use numbers.
- Extract every quantitative result mentioned in the abstract.
""".strip()


def benchmark_plan_prompt(
    interest_name: str,
    paper_title: str,
    paper_summary: str,
    benchmark_description: str,
    inspection: RepoInspection,
) -> str:
    return f"""
You are producing a SAFE, non-interactive Docker benchmark plan for a fresh ML research repository.

Interest:
{interest_name}

Paper title:
{paper_title}

Paper abstract:
{paper_summary}

Benchmark description:
{benchmark_description}

Repo:
{inspection.repo_url}

Top-level files:
{inspection.root_files}

Candidate entrypoints:
{inspection.candidate_entrypoints}

Dependency files:
{inspection.dependency_files}

README excerpt:
{inspection.readme_excerpt}

Constraints:
- The repo is cloned into /workspace/repo
- Benchmark assets are under /workspace/benchmark_assets
- Write metrics JSON to /workspace/out/metrics.json
- Use only non-interactive shell commands
- No sudo
- No background services
- Prefer pip install -r requirements.txt or pip install -e . when appropriate
- If no safe or credible plan exists, mark benchmarkable=false

Goal: Run the model on a TINY input (10-20 samples max) to verify the code actually works.
This is NOT a full benchmark — we just need proof the code runs and produces output.

IMPORTANT: Keep it small and fast. Use only 10-20 samples/images/inputs. If the script has
a --num_samples, --max_steps, --limit, or similar flag, set it to a small number. If it reads
a dataset, truncate or subset it. The run should finish in under 5 minutes. We are NOT trying
to reproduce the paper's results — just confirming the code executes end-to-end.

Look for the repo's own demo, example, inference, or evaluation scripts (demo.py, inference.py,
evaluate.py, test.py, or README quick-start commands). Run it on the smallest possible input
and capture whatever numeric output it produces: inference time, output shape, loss value,
parameter count, or any number the script prints.

Do NOT produce a plan that just runs "python -c 'import package_name'" or checks that pip
install succeeds. That is not a benchmark.

If the repo has no runnable script or demo, mark benchmarkable=false with a clear reason.

For benchmarkable repos, provide:
- base_image (use GPU images if the model needs CUDA, e.g. tensorflow/tensorflow:latest-gpu, pytorch/pytorch:latest, nvidia/cuda:12.1.0-devel-ubuntu22.04)
- apt_packages
- python_packages
- setup_commands
- run_commands (must actually execute the model on 10-20 samples and write metrics to /workspace/out/metrics.json)
- metrics_output_path

The final run command must create the metrics file with real output from the model run.
""".strip()


def benchmark_summary_prompt(
    paper_title: str,
    plan: BenchmarkPlan,
    run_log: str,
    metrics: dict,
    status: str,
) -> str:
    return f"""
You are summarizing the results of running a benchmark on a research paper's code repository.
Write a concise 2-4 sentence summary for a senior research engineer.

Paper title: {paper_title}

What was run:
- Base image: {plan.base_image}
- Setup commands: {plan.setup_commands}
- Run commands: {plan.run_commands}

Exit status: {status}

Metrics output:
{json.dumps(metrics, indent=2, default=str) if metrics else "(no metrics file produced)"}

Last 100 lines of run log:
{run_log}

Write a summary that covers:
1. What was actually executed (e.g. "Ran inference on 10 sample images using the pretrained ResNet-50 model")
2. Whether it completed successfully or failed (and why if it failed)
3. Key numeric results from the output (e.g. "Average inference time: 23ms/image, output shape: [10, 1000]")

Be specific. Do NOT say generic things like "the benchmark passed successfully."
""".strip()
