from __future__ import annotations

from .schemas import PaperRecord, RepoInspection


def triage_prompt(interest_name: str, interest_description: str, paper: PaperRecord) -> str:
    return f"""
You are triaging fresh arXiv machine learning / AI papers for a research engineer.

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

Decide:
1. whether the paper is relevant to the interest
2. whether it is already repo-backed, implementable from the paper, or not realistically implementable from the available information
3. which benchmark family it most resembles
4. a crisp value summary, method explanation, and expected relative-to-SOTA statement

Guidelines:
- Be skeptical of hype.
- "expected_vs_sota" should be a grounded expectation like "likely competitive", "incremental", "unclear", or "probably below specialized SOTA".
- If the repo is not explicit, do not hallucinate one.
- "implementable_from_paper" should only be chosen if the abstract / comment suggests a reasonably specified method.
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
- The goal is a *small smoke benchmark*, not full reproduction
- If no safe or credible plan exists, mark benchmarkable=false

For generic Python demos, it is acceptable to validate installation + a tiny forward pass or example command.
For benchmarkable repos, provide:
- base_image
- apt_packages
- python_packages
- setup_commands
- run_commands
- metrics_output_path

The final run command must create the metrics file.
""".strip()
