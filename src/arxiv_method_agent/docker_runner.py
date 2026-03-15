from __future__ import annotations

import json
import logging
import os
import shutil
import tarfile
import threading
from pathlib import Path
from textwrap import dedent
from typing import Iterable

import docker

logger = logging.getLogger(__name__)

from .schemas import BenchmarkPlan, BenchmarkResult
from .utils import ensure_dir, write_json


DENY_PATTERNS = [
    "sudo ",
    " rm -rf /",
    "rm -rf /",
    ":(){",
    "shutdown",
    "reboot",
    "systemctl",
    "service ",
    "docker ",
    "podman ",
    "kubectl ",
    "mount ",
    "umount ",
    "iptables",
    "ssh ",
    "scp ",
    "nc ",
    # Pipe-to-shell attacks
    "curl|bash",
    "curl|sh",
    "wget|bash",
    "wget|sh",
    "curl | bash",
    "curl | sh",
    "wget | bash",
    "wget | sh",
    # Destructive / escape patterns
    "dd if=",
    "dd of=",
    "ld_preload",
    "mkfs",
    "/dev/tcp/",
    "/dev/udp/",
    "bash -i >& /dev/",
    "exec 5<>/dev/tcp/",
    "python -c 'import socket",
    "ncat ",
    "socat ",
    "telnet ",
]

ALLOWED_BASE_IMAGES = [
    "python:",
    "nvidia/cuda:",
    "ubuntu:",
    "debian:",
    "continuumio/miniconda",
    "pytorch/pytorch:",
]


def _check_symlinks(src: Path) -> None:
    """Raise if any symlink under src resolves outside src."""
    resolved_root = src.resolve()
    for dirpath, dirnames, filenames in os.walk(src):
        for name in filenames + dirnames:
            full = Path(dirpath) / name
            if full.is_symlink():
                target = full.resolve()
                if not str(target).startswith(str(resolved_root)):
                    raise ValueError(
                        f"Symlink escape detected: {full} -> {target}"
                    )


class DockerRunner:
    def __init__(self):
        self.client = docker.from_env()
        self._active_containers: set[str] = set()
        self._lock = threading.Lock()

    def validate_plan(self, plan: BenchmarkPlan) -> None:
        for cmd in [*plan.setup_commands, *plan.run_commands]:
            lowered = f" {cmd.lower()}"
            for pattern in DENY_PATTERNS:
                if pattern in lowered:
                    raise ValueError(f"Unsafe command rejected by policy: {cmd}")

        if not any(plan.base_image.startswith(prefix) for prefix in ALLOWED_BASE_IMAGES):
            raise ValueError(
                f"Base image '{plan.base_image}' is not in the allowlist. "
                f"Allowed prefixes: {ALLOWED_BASE_IMAGES}"
            )

    def cleanup(self) -> None:
        """Kill and remove all tracked containers. Called on shutdown."""
        with self._lock:
            ids = list(self._active_containers)
        for cid in ids:
            try:
                c = self.client.containers.get(cid)
                c.kill()
                c.remove(force=True)
                logger.info("Cleaned up container %s", cid[:12])
            except Exception:
                pass
        with self._lock:
            self._active_containers.clear()

    def run_plan(
        self,
        plan: BenchmarkPlan,
        repo_path: str | Path,
        benchmark_assets_dir: str | Path,
        artifact_dir: str | Path,
        image_tag: str,
        timeout_seconds: int,
        context_dir: str | Path | None = None,
    ) -> BenchmarkResult:
        self.validate_plan(plan)

        repo_path = Path(repo_path)
        benchmark_assets_dir = Path(benchmark_assets_dir)
        artifact_dir = ensure_dir(artifact_dir)

        # Docker build context goes to a separate workspace directory (cleaned up
        # after the run) so it doesn't pollute the paper's output directory.
        if context_dir is not None:
            context_dir = ensure_dir(Path(context_dir))
        else:
            context_dir = ensure_dir(artifact_dir / "_docker_context")

        repo_copy = context_dir / "repo"
        benchmark_copy = context_dir / "benchmark_assets"

        if repo_copy.exists():
            shutil.rmtree(repo_copy)
        if benchmark_copy.exists():
            shutil.rmtree(benchmark_copy)

        _check_symlinks(repo_path)
        _check_symlinks(benchmark_assets_dir)
        shutil.copytree(repo_path, repo_copy)
        shutil.copytree(benchmark_assets_dir, benchmark_copy)

        dockerfile = dedent(
            f"""
            FROM {plan.base_image}
            WORKDIR /workspace/repo
            ENV PIP_DISABLE_PIP_VERSION_CHECK=1
            ENV PYTHONDONTWRITEBYTECODE=1
            ENV PYTHONUNBUFFERED=1
            {self._apt_block(plan)}
            COPY repo /workspace/repo
            COPY benchmark_assets /workspace/benchmark_assets
            RUN mkdir -p /workspace/out
            {self._python_packages_block(plan)}
            {self._setup_block(plan)}
            COPY run.sh /workspace/run.sh
            RUN chmod +x /workspace/run.sh
            CMD ["bash", "/workspace/run.sh"]
            """
        ).strip() + "\n"
        (context_dir / "Dockerfile").write_text(dockerfile, encoding="utf-8")

        run_script = dedent(
            f"""
            #!/usr/bin/env bash
            set -euo pipefail
            mkdir -p /workspace/out
            cd /workspace/repo
            {self._commands_block(plan.run_commands)}
            """
        ).strip() + "\n"
        (context_dir / "run.sh").write_text(run_script, encoding="utf-8")

        build_log_path = artifact_dir / "build.log"
        run_log_path = artifact_dir / "run.log"
        metrics_local_path = artifact_dir / "metrics.json"
        image_tar_path = artifact_dir / "image.tar"

        # Build
        try:
            logger.info("Building Docker image %s", image_tag)
            image, build_logs = self.client.images.build(
                path=str(context_dir),
                tag=image_tag,
                rm=True,
                forcerm=True,
                pull=True,
            )
            with open(build_log_path, "w", encoding="utf-8") as f:
                for entry in build_logs:
                    f.write(json.dumps(entry, default=str) + "\n")
        except Exception as exc:
            logger.error("Docker build failed for %s: %s", image_tag, exc)
            return BenchmarkResult(
                status="error",
                build_log_path=str(build_log_path),
                run_log_path=None,
                image_tar_path=None,
                local_artifact_dir=str(artifact_dir),
                reason=f"Docker build failed: {exc}",
            )

        # Run (network disabled, resource-limited)
        try:
            logger.info("Running container %s (timeout=%ds)", image_tag, timeout_seconds)
            container = self.client.containers.run(
                image_tag,
                detach=True,
                network_disabled=True,
                mem_limit="8g",
                nano_cpus=4_000_000_000,
                working_dir="/workspace/repo",
                auto_remove=False,
            )
            with self._lock:
                self._active_containers.add(container.id)
            try:
                result = container.wait(timeout=timeout_seconds)
                logs = container.logs(stdout=True, stderr=True)
                run_log_path.write_bytes(logs)
                status_code = int(result.get("StatusCode", 1))
                try:
                    stream, _ = container.get_archive(plan.metrics_output_path)
                    tmp_tar = artifact_dir / "metrics.tar"
                    with open(tmp_tar, "wb") as f:
                        for chunk in stream:
                            f.write(chunk)
                    with tarfile.open(tmp_tar) as tf:
                        member = tf.getmembers()[0]
                        extracted = tf.extractfile(member)
                        if extracted is None:
                            raise FileNotFoundError("metrics output missing")
                        metrics_local_path.write_bytes(extracted.read())
                    tmp_tar.unlink(missing_ok=True)
                except Exception:
                    write_json(metrics_local_path, {"success": float(status_code == 0)})
            finally:
                with self._lock:
                    self._active_containers.discard(container.id)
                try:
                    container.remove(force=True)
                except Exception:
                    pass
        except Exception as exc:
            logger.error("Container run failed for %s: %s", image_tag, exc)
            return BenchmarkResult(
                status="error",
                build_log_path=str(build_log_path),
                run_log_path=str(run_log_path),
                image_tar_path=None,
                local_artifact_dir=str(artifact_dir),
                reason=f"Container run failed: {exc}",
            )

        # Save Docker image as tar
        try:
            img = self.client.images.get(image_tag)
            with open(image_tar_path, "wb") as f:
                for chunk in img.save(named=True):
                    f.write(chunk)
        except Exception:
            image_tar_path = None

        # Parse metrics
        metrics = {}
        metric_name = None
        metric_value = None
        if metrics_local_path.exists():
            try:
                metrics = json.loads(metrics_local_path.read_text(encoding="utf-8"))
                if isinstance(metrics, dict) and metrics:
                    for k, v in metrics.items():
                        if isinstance(v, (int, float)):
                            metric_name = str(k)
                            metric_value = float(v)
                            break
            except Exception:
                metrics = {}

        return BenchmarkResult(
            status="passed" if metric_value is not None or metrics_local_path.exists() else "failed",
            metric_name=metric_name,
            metric_value=metric_value,
            metrics=metrics,
            build_log_path=str(build_log_path),
            run_log_path=str(run_log_path),
            image_tar_path=str(image_tar_path) if image_tar_path else None,
            local_artifact_dir=str(artifact_dir),
        )

    @staticmethod
    def _apt_block(plan: BenchmarkPlan) -> str:
        if not plan.apt_packages:
            return ""
        pkgs = " ".join(plan.apt_packages)
        return f"RUN apt-get update && apt-get install -y --no-install-recommends {pkgs} && rm -rf /var/lib/apt/lists/*"

    @staticmethod
    def _python_packages_block(plan: BenchmarkPlan) -> str:
        if not plan.python_packages:
            return ""
        pkgs = " ".join(plan.python_packages)
        return f"RUN python -m pip install --upgrade pip && python -m pip install {pkgs}"

    @staticmethod
    def _setup_block(plan: BenchmarkPlan) -> str:
        if not plan.setup_commands:
            return ""
        lines = " && \\\n    ".join(plan.setup_commands)
        return f"RUN bash -lc \"{lines}\""

    @staticmethod
    def _commands_block(cmds: list[str]) -> str:
        if not cmds:
            return 'python - <<"PY"\nimport json\nfrom pathlib import Path\nPath("/workspace/out/metrics.json").write_text(json.dumps({"success": 1.0}))\nPY'
        return "\n".join(cmds)
