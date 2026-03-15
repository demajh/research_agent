#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

source .venv/bin/activate
export PYTHONPATH=src
python -m arxiv_method_agent.main run --config configs/interests.yaml
