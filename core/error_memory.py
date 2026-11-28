"""Project-Isolated Error Memory & Postmortem KB (SQLite WAL, 100% Python stdlib, MIT).

Stores structured Hard-Error postmortems locally inside `<repo>/.vieforge/error_memory.db`
so repeated errors are matched in < 5 ms at $0 before spending Cloud Agent tokens:
  1. Exact match on 12-char `fingerprint` from `core/fault_locator.py` (score = 1.0).
  2. Lightweight normalized-token similarity fallback for paraphrased/shifted errors.
  3. Automatic secret redaction before anything is written to SQLite.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.output_trimmer import redact_secrets_text

_TOKEN_RE = re.compile(r"[a-z0-9_]{3,}")
_STOPWORDS = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "that",
    "this",
    "line",
    "file",
    "error",
    "exception",
    "failed",
    "traceback",
    "most",
    "recent",
    "call",
    "last",
}


@dataclass
class ErrorIncident:
    incident_id: int
    fingerprint: str
    error_type: str
    symptom: str
    root_cause: str
    verified_fix: str
    prevention: str = ""
    files: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    hit_count: int = 1
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ErrorLookupMatch:
    incident: ErrorIncident
    score: float
    match_type: str  # "exact_fingerprint" | "similarity"

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 3),
            "match_type": self.match_type,
            "incident": self.incident.to_dict(),
        }


def _tokenize(text: str) -> set[str]:
    return {
        tok
        for tok in _TOKEN_RE.findall(text.lower())
        if tok not in _STOPWORDS
    }


def _clean_text(text: str, max_len: int = 1000) -> str:
    safe, _ = redact_secrets_text((text or "").strip())
    return safe[:max_len]


class ErrorMemory:
    """SQLite WAL-backed local postmortem knowledge base (project-isolated)."""

    def __init__(self, db_path: Path | str) -> None:
        self.db_path = Path(db_path).resolve()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS error_kb (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                error_type TEXT NOT NULL,
                symptom TEXT NOT NULL,
                root_cause TEXT NOT NULL,
                verified_fix TEXT NOT NULL,
                prevention TEXT NOT NULL DEFAULT '',
                files_json TEXT NOT NULL DEFAULT '[]',
                tags_json TEXT NOT NULL DEFAULT '[]',
                hit_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_error_kb_type ON error_kb(error_type);"
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> ErrorMemory:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    @staticmethod
    def _row_to_incident(row: sqlite3.Row) -> ErrorIncident:
        return ErrorIncident(
            incident_id=int(row["id"]),
            fingerprint=str(row["fingerprint"]),
            error_type=str(row["error_type"]),
            symptom=str(row["symptom"]),
            root_cause=str(row["root_cause"]),
            verified_fix=str(row["verified_fix"]),
            prevention=str(row["prevention"]),
            files=list(json.loads(row["files_json"] or "[]")),
            tags=list(json.loads(row["tags_json"] or "[]")),
            hit_count=int(row["hit_count"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def record_incident(
        self,
        *,
        fingerprint: str,
        error_type: str,
        symptom: str,
        root_cause: str,
        verified_fix: str,
        prevention: str = "",
        files: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> ErrorIncident:
        """Insert a new verified postmortem or update hit count/fix on matching fingerprint."""
        fp = fingerprint.strip()
        if not fp:
            raise ValueError("fingerprint is required")
        now = datetime.now(timezone.utc).isoformat()
        clean_type = _clean_text(error_type or "UnknownError", 120)
        clean_symptom = _clean_text(symptom, 800)
        clean_cause = _clean_text(root_cause, 800)
        clean_fix = _clean_text(verified_fix, 1200)
        clean_prev = _clean_text(prevention, 600)
        norm_files = sorted({f.replace("\\", "/").strip() for f in (files or []) if f.strip()})
        norm_tags = sorted({t.strip().lower() for t in (tags or []) if t.strip()})

        existing = self._conn.execute(
            "SELECT * FROM error_kb WHERE fingerprint = ?",
            (fp,),
        ).fetchone()

        if existing is not None:
            merged_files = sorted(set(json.loads(existing["files_json"] or "[]")) | set(norm_files))
            merged_tags = sorted(set(json.loads(existing["tags_json"] or "[]")) | set(norm_tags))
            self._conn.execute(
                """
                UPDATE error_kb
                SET error_type = ?, symptom = ?, root_cause = ?, verified_fix = ?,
                    prevention = ?, files_json = ?, tags_json = ?,
                    hit_count = hit_count + 1, updated_at = ?
                WHERE fingerprint = ?
                """,
                (
                    clean_type,
                    clean_symptom or existing["symptom"],
                    clean_cause or existing["root_cause"],
                    clean_fix or existing["verified_fix"],
                    clean_prev or existing["prevention"],
                    json.dumps(merged_files, ensure_ascii=False),
                    json.dumps(merged_tags, ensure_ascii=False),
                    now,
                    fp,
                ),
            )
        else:
            self._conn.execute(
                """
                INSERT INTO error_kb (
                    fingerprint, error_type, symptom, root_cause, verified_fix,
                    prevention, files_json, tags_json, hit_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (
                    fp,
                    clean_type,
                    clean_symptom,
                    clean_cause,
                    clean_fix,
                    clean_prev,
                    json.dumps(norm_files, ensure_ascii=False),
                    json.dumps(norm_tags, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        self._conn.commit()
        row = self._conn.execute(
            "SELECT * FROM error_kb WHERE fingerprint = ?",
            (fp,),
        ).fetchone()
        assert row is not None
        return self._row_to_incident(row)

    def lookup(
        self,
        *,
        fingerprint: str = "",
        error_type: str = "",
        query_text: str = "",
        limit: int = 3,
        min_score: float = 0.25,
    ) -> list[ErrorLookupMatch]:
        """Lookup exact fingerprint first, then score remaining incidents by token overlap."""
        matches: list[ErrorLookupMatch] = []
        seen_ids: set[int] = set()

        fp = fingerprint.strip()
        if fp:
            row = self._conn.execute(
                "SELECT * FROM error_kb WHERE fingerprint = ?",
                (fp,),
            ).fetchone()
            if row is not None:
                inc = self._row_to_incident(row)
                seen_ids.add(inc.incident_id)
                matches.append(ErrorLookupMatch(incident=inc, score=1.0, match_type="exact_fingerprint"))

        if len(matches) >= limit:
            return matches[:limit]

        q_tokens = _tokenize(f"{error_type} {query_text}")
        norm_type = (error_type or "").strip().lower()
        if not q_tokens and not norm_type:
            return matches[:limit]

        rows = self._conn.execute("SELECT * FROM error_kb ORDER BY hit_count DESC, updated_at DESC").fetchall()
        scored: list[ErrorLookupMatch] = []
        for row in rows:
            inc = self._row_to_incident(row)
            if inc.incident_id in seen_ids:
                continue
            doc_tokens = _tokenize(
                f"{inc.error_type} {inc.symptom} {inc.root_cause} {' '.join(inc.files)} {' '.join(inc.tags)}"
            )
            overlap = len(q_tokens & doc_tokens)
            union = len(q_tokens | doc_tokens)
            jaccard = (overlap / union) if union > 0 else 0.0
            type_bonus = 0.22 if norm_type and inc.error_type.lower() == norm_type else 0.0
            score = min(0.99, jaccard + type_bonus)
            if score >= min_score:
                scored.append(ErrorLookupMatch(incident=inc, score=score, match_type="similarity"))

        scored.sort(key=lambda m: (m.score, m.incident.hit_count), reverse=True)
        matches.extend(scored)
        return matches[: max(1, limit)]

    def list_incidents(self, limit: int = 20) -> list[ErrorIncident]:
        """List stored error postmortems ordered by recency."""
        rows = self._conn.execute(
            "SELECT * FROM error_kb ORDER BY updated_at DESC, id DESC LIMIT ?",
            (max(1, limit),),
        ).fetchall()
        return [self._row_to_incident(r) for r in rows]

    def stats(self) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT COUNT(*) AS cnt, COALESCE(SUM(hit_count), 0) AS hits FROM error_kb"
        ).fetchone()
        return {
            "total_incidents": int(row["cnt"]) if row else 0,
            "total_hits": int(row["hits"]) if row else 0,
            "db_path": str(self.db_path),
        }
