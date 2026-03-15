from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS processed_papers (
    arxiv_id     TEXT NOT NULL,
    interest     TEXT NOT NULL,
    run_id       TEXT NOT NULL,
    processed_at TEXT NOT NULL,
    PRIMARY KEY (arxiv_id, interest)
);
"""


class DeduplicationTracker:
    """Track which papers have been processed to avoid re-processing across runs."""

    def __init__(self, db_path: str | Path):
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def is_processed(self, arxiv_id: str, interest_name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM processed_papers WHERE arxiv_id = ? AND interest = ?",
            (arxiv_id, interest_name),
        ).fetchone()
        return row is not None

    def mark_processed(self, arxiv_id: str, interest_name: str, run_id: str) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO processed_papers (arxiv_id, interest, run_id, processed_at) "
            "VALUES (?, ?, ?, ?)",
            (arxiv_id, interest_name, run_id, datetime.now(timezone.utc).isoformat()),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
