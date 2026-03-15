from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np

from .utils import ensure_dir, write_json


@dataclass
class BenchmarkAssets:
    benchmark_id: str
    metric_name: str
    dataset_dir: Path
    description: str


class BenchmarkRegistry:
    def build_assets(self, benchmark_id: str, base_dir: str | Path) -> BenchmarkAssets:
        base_dir = ensure_dir(base_dir)
        if benchmark_id == "generic_python_demo":
            return self._generic_python_demo(base_dir)
        if benchmark_id == "tabular_binary_classification":
            return self._tabular_binary_classification(base_dir)
        if benchmark_id == "time_series_forecasting":
            return self._time_series_forecasting(base_dir)
        if benchmark_id == "text_classification":
            return self._text_classification(base_dir)
        raise ValueError(f"Unknown benchmark id: {benchmark_id}")

    def _generic_python_demo(self, base_dir: Path) -> BenchmarkAssets:
        out = ensure_dir(base_dir / "generic_python_demo")
        write_json(
            out / "instructions.json",
            {
                "goal": "Verify that the repo installs and a basic command can run.",
                "output_contract": "Write JSON metrics to /workspace/out/metrics.json",
                "example_metric": {"success": 1.0},
            },
        )
        return BenchmarkAssets(
            benchmark_id="generic_python_demo",
            metric_name="success",
            dataset_dir=out,
            description=(
                "Smoke benchmark only. It is acceptable to validate installability, importability, or a tiny example run. "
                "The plan must still create /workspace/out/metrics.json."
            ),
        )

    def _tabular_binary_classification(self, base_dir: Path) -> BenchmarkAssets:
        out = ensure_dir(base_dir / "tabular_binary_classification")
        rng = np.random.default_rng(7)
        x_pos = rng.normal(loc=1.0, scale=1.0, size=(80, 8))
        x_neg = rng.normal(loc=-1.0, scale=1.0, size=(80, 8))
        x = np.vstack([x_pos, x_neg])
        y = np.array([1] * 80 + [0] * 80)
        idx = rng.permutation(len(y))
        x = x[idx]
        y = y[idx]
        train_x, test_x = x[:120], x[120:]
        train_y, test_y = y[:120], y[120:]
        self._write_csv(out / "train.csv", train_x, train_y)
        self._write_csv(out / "test.csv", test_x, test_y)
        write_json(
            out / "instructions.json",
            {
                "task": "binary classification",
                "metric": "accuracy",
                "train_path": "/workspace/benchmark_assets/train.csv",
                "test_path": "/workspace/benchmark_assets/test.csv",
                "label_column": "label",
            },
        )
        return BenchmarkAssets(
            benchmark_id="tabular_binary_classification",
            metric_name="accuracy",
            dataset_dir=out,
            description=(
                "A tiny synthetic binary classification CSV benchmark. Prefer a repo CLI that can train or infer from CSV files. "
                "If the repo is not compatible with CSV-based data, mark benchmarkable=false."
            ),
        )

    def _time_series_forecasting(self, base_dir: Path) -> BenchmarkAssets:
        out = ensure_dir(base_dir / "time_series_forecasting")
        rng = np.random.default_rng(11)
        t = np.arange(0, 240)
        y = np.sin(t / 8.0) + 0.2 * rng.normal(size=len(t))
        train_rows = [{"t": int(i), "value": float(v)} for i, v in zip(t[:180], y[:180])]
        test_rows = [{"t": int(i), "value": float(v)} for i, v in zip(t[180:], y[180:])]
        self._write_dict_csv(out / "train.csv", train_rows)
        self._write_dict_csv(out / "test.csv", test_rows)
        write_json(
            out / "instructions.json",
            {
                "task": "univariate forecasting",
                "metric": "mae",
                "train_path": "/workspace/benchmark_assets/train.csv",
                "test_path": "/workspace/benchmark_assets/test.csv",
            },
        )
        return BenchmarkAssets(
            benchmark_id="time_series_forecasting",
            metric_name="mae",
            dataset_dir=out,
            description=(
                "A tiny synthetic forecasting benchmark. Prefer a repo CLI that can train on a CSV time series and evaluate on a small holdout."
            ),
        )

    def _text_classification(self, base_dir: Path) -> BenchmarkAssets:
        out = ensure_dir(base_dir / "text_classification")
        train = [
            {"text": "great product and excellent battery life", "label": 1},
            {"text": "terrible customer support and broken screen", "label": 0},
            {"text": "fast delivery and good quality", "label": 1},
            {"text": "poor packaging and bad fit", "label": 0},
            {"text": "works exactly as expected", "label": 1},
            {"text": "returned it after one day", "label": 0},
        ]
        test = [
            {"text": "excellent quality and fast service", "label": 1},
            {"text": "bad battery and disappointing build", "label": 0},
        ]
        (out / "train.jsonl").write_text("\n".join(json.dumps(x) for x in train), encoding="utf-8")
        (out / "test.jsonl").write_text("\n".join(json.dumps(x) for x in test), encoding="utf-8")
        write_json(
            out / "instructions.json",
            {
                "task": "text classification",
                "metric": "accuracy",
                "train_path": "/workspace/benchmark_assets/train.jsonl",
                "test_path": "/workspace/benchmark_assets/test.jsonl",
            },
        )
        return BenchmarkAssets(
            benchmark_id="text_classification",
            metric_name="accuracy",
            dataset_dir=out,
            description=(
                "A tiny JSONL text classification benchmark. Prefer a repo CLI that can train or infer from JSONL inputs."
            ),
        )

    @staticmethod
    def _write_csv(path: Path, x: np.ndarray, y: np.ndarray) -> None:
        fieldnames = [f"f{i}" for i in range(x.shape[1])] + ["label"]
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row_x, row_y in zip(x, y):
                row = {f"f{i}": float(v) for i, v in enumerate(row_x)}
                row["label"] = int(row_y)
                writer.writerow(row)

    @staticmethod
    def _write_dict_csv(path: Path, rows: list[dict]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
