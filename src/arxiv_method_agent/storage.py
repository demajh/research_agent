from __future__ import annotations

import logging
from pathlib import Path

import boto3
from tenacity import retry, stop_after_attempt, wait_exponential, before_sleep_log

logger = logging.getLogger(__name__)

from .config import StorageConfig
from .utils import slugify


class StorageClient:
    def __init__(self, cfg: StorageConfig):
        self.cfg = cfg
        self.s3 = boto3.client("s3", region_name=cfg.region, endpoint_url=cfg.endpoint_url)

    def upload_tree(self, local_dir: str | Path, run_id: str, interest_name: str, paper_id: str) -> str:
        local_dir = Path(local_dir)
        prefix = (
            f"{self.cfg.prefix.rstrip('/')}/{run_id}/{slugify(interest_name)}/{slugify(paper_id)}"
        )
        for path in local_dir.rglob("*"):
            if path.is_dir():
                continue
            rel = path.relative_to(local_dir).as_posix()
            key = f"{prefix}/{rel}"
            self._upload_one(str(path), key)
        logger.info("Uploaded artifacts to s3://%s/%s", self.cfg.bucket, prefix)
        return f"s3://{self.cfg.bucket}/{prefix}"

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=1, max=30),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
    def _upload_one(self, local_path: str, key: str) -> None:
        self.s3.upload_file(local_path, self.cfg.bucket, key)
