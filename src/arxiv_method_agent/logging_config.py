from __future__ import annotations

import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """Configure structured logging for the agent.

    Call once at startup (in main.py) before any other work.
    """
    root = logging.getLogger("arxiv_method_agent")
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    if not root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        handler.setFormatter(formatter)
        root.addHandler(handler)

    # Quiet noisy third-party loggers
    for name in ("urllib3", "httpx", "docker", "botocore", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
