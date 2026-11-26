"""SQLite WAL Job/Item Checkpointing & Input-Hash Deduplication.

Persists per-item state (`pending` | `done` | `failed`, `retry_count`, `actual_cost_usd`)
so interrupted batch jobs resume cleanly at $0 extra cost. 100% Python stdlib.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def compute_input_hash(payload: Any) -> str:
    """Deterministic SHA-256 hash for item deduplication."""
    if isinstance(payload, bytes):
        raw = payload
    elif isinstance(payload, str):
        raw = payload.encode("utf-8")
    else:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def scoped_job_id(project_id: str, job_id: str) -> str:
    """Namespace a job_id by project_id so concurrent workspaces never collide in a shared DB."""
    clean_proj = project_id.strip().replace(":", "_")
    clean_job = job_id.strip()
    if not clean_proj or clean_job.startswith(f"{clean_proj}::"):
        return clean_job
    return f"{clean_proj}::{clean_job}"


@dataclass
class ItemRecord:
    job_id: str
    item_id: str
    input_hash: str
    status: str
    retry_count: int
    actual_cost_usd: float
    output_json: str | None
    last_error: str | None
    updated_at: str


class StateDB:
    """SQLite WAL state store for checkpointed Tier 2/3 batch execution."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.db_path, timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA busy_timeout=15000;")
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
                job_id TEXT PRIMARY KEY,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS items (
                job_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                input_hash TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                retry_count INTEGER NOT NULL DEFAULT 0,
                actual_cost_usd REAL NOT NULL DEFAULT 0.0,
                output_json TEXT,
                last_error TEXT,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (job_id, item_id),
                FOREIGN KEY (job_id) REFERENCES jobs(job_id)
            );

            CREATE INDEX IF NOT EXISTS idx_items_job_status
                ON items (job_id, status);
            """
        )
        self._conn.commit()

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def create_job(self, job_id: str, metadata: dict[str, Any] | None = None) -> None:
        now = self._now()
        meta_str = json.dumps(metadata or {}, ensure_ascii=False)
        self._conn.execute(
            """
            INSERT INTO jobs (job_id, metadata_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (job_id, meta_str, now, now),
        )
        self._conn.commit()

    def register_item(self, job_id: str, item_id: str, input_hash: str) -> str:
        """Register an item with deduplication.

        - New item -> inserted as 'pending'.
        - Existing item with SAME input_hash and status=='done' -> kept 'done' (skipped on resume).
        - Existing item with CHANGED input_hash -> reset to 'pending' and retry_count=0.
        Returns the resulting item status.
        """
        now = self._now()
        row = self._conn.execute(
            "SELECT input_hash, status FROM items WHERE job_id = ? AND item_id = ?",
            (job_id, item_id),
        ).fetchone()
        if row is None:
            self._conn.execute(
                """
                INSERT INTO items (job_id, item_id, input_hash, status, retry_count, actual_cost_usd, updated_at)
                VALUES (?, ?, ?, 'pending', 0, 0.0, ?)
                """,
                (job_id, item_id, input_hash, now),
            )
            self._conn.commit()
            return "pending"

        if row["input_hash"] != input_hash:
            self._conn.execute(
                """
                UPDATE items
                SET input_hash = ?, status = 'pending', retry_count = 0,
                    output_json = NULL, last_error = NULL, updated_at = ?
                WHERE job_id = ? AND item_id = ?
                """,
                (input_hash, now, job_id, item_id),
            )
            self._conn.commit()
            return "pending"

        return str(row["status"])

    def register_items(self, job_id: str, items: list[tuple[str, str]]) -> dict[str, int]:
        """Bulk register `(item_id, input_hash)` pairs. Returns counts of new/skipped/reset."""
        self.create_job(job_id)
        counts = {"pending": 0, "done": 0, "failed": 0}
        for item_id, input_hash in items:
            st = self.register_item(job_id, item_id, input_hash)
            counts[st] = counts.get(st, 0) + 1
        return counts

    def get_pending_items(self, job_id: str, max_retries: int = 2) -> list[ItemRecord]:
        """Return items that are not 'done' and have retry_count < max_retries."""
        rows = self._conn.execute(
            """
            SELECT * FROM items
            WHERE job_id = ?
              AND status != 'done'
              AND retry_count < ?
            ORDER BY item_id ASC
            """,
            (job_id, max_retries),
        ).fetchall()
        return [ItemRecord(**dict(r)) for r in rows]

    def get_item(self, job_id: str, item_id: str) -> ItemRecord | None:
        row = self._conn.execute(
            "SELECT * FROM items WHERE job_id = ? AND item_id = ?",
            (job_id, item_id),
        ).fetchone()
        return ItemRecord(**dict(row)) if row else None

    def mark_done(
        self,
        job_id: str,
        item_id: str,
        output: Any = None,
        cost_usd: float = 0.0,
    ) -> None:
        now = self._now()
        out_str = json.dumps(output, ensure_ascii=False) if output is not None else None
        self._conn.execute(
            """
            UPDATE items
            SET status = 'done',
                output_json = ?,
                actual_cost_usd = ROUND(actual_cost_usd + ?, 6),
                last_error = NULL,
                updated_at = ?
            WHERE job_id = ? AND item_id = ?
            """,
            (out_str, float(cost_usd), now, job_id, item_id),
        )
        self._conn.commit()

    def mark_failed(
        self,
        job_id: str,
        item_id: str,
        error: str,
        cost_usd: float = 0.0,
    ) -> int:
        """Mark an item failed, increment retry_count, and return the new retry_count."""
        now = self._now()
        self._conn.execute(
            """
            UPDATE items
            SET status = 'failed',
                retry_count = retry_count + 1,
                actual_cost_usd = ROUND(actual_cost_usd + ?, 6),
                last_error = ?,
                updated_at = ?
            WHERE job_id = ? AND item_id = ?
            """,
            (float(cost_usd), error[:1000], now, job_id, item_id),
        )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT retry_count FROM items WHERE job_id = ? AND item_id = ?",
            (job_id, item_id),
        ).fetchone()
        return int(row["retry_count"]) if row else 0

    def get_job_summary(self, job_id: str, max_failed_sample: int = 5) -> dict[str, Any]:
        """Return a compact, Tier-1-friendly JSON summary of job progress and spend."""
        rows = self._conn.execute(
            """
            SELECT status, COUNT(*) AS cnt, COALESCE(SUM(actual_cost_usd), 0.0) AS cost
            FROM items
            WHERE job_id = ?
            GROUP BY status
            """,
            (job_id,),
        ).fetchall()
        counts = {"pending": 0, "done": 0, "failed": 0}
        total_cost = 0.0
        for r in rows:
            counts[str(r["status"])] = int(r["cnt"])
            total_cost += float(r["cost"])

        failed_rows = self._conn.execute(
            """
            SELECT item_id, retry_count, last_error
            FROM items
            WHERE job_id = ? AND status = 'failed'
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (job_id, max_failed_sample),
        ).fetchall()

        total = sum(counts.values())
        return {
            "job_id": job_id,
            "total": total,
            "done": counts["done"],
            "pending": counts["pending"],
            "failed": counts["failed"],
            "total_cost_usd": round(total_cost, 6),
            "failed_sample": [
                {
                    "item_id": str(r["item_id"]),
                    "retry_count": int(r["retry_count"]),
                    "error": (r["last_error"] or "")[:200],
                }
                for r in failed_rows
            ],
        }

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> StateDB:
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.close()
