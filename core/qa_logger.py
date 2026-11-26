"""Single-Key QA Gate Feedback Logger (`qa_feedback.jsonl`).

Maps concise single-key review codes from `config/qa_error_codes.json` into structured
JSONL records and produces compact failure summaries for Tier 1 root-cause patching.
100% Python stdlib.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.paths import default_data_dir, resolve_asset

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_QA_CODES_PATH = resolve_asset("config/qa_error_codes.json")
DEFAULT_QA_LOG_PATH = default_data_dir() / "qa_feedback.jsonl"


def load_qa_codes(codes_path: Path | None = None) -> dict[str, dict[str, Any]]:
    path = codes_path or DEFAULT_QA_CODES_PATH
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return dict(data.get("codes", {}))
    return {
        "OK": {"key": "y", "severity": "info", "description": "Passed quality gate."},
        "REJECT_OTHER": {"key": "x", "severity": "error", "description": "Rejected."},
    }


class QALogger:
    """Append-only JSONL logger for human/automated QA gate decisions."""

    def __init__(
        self,
        log_path: Path | str | None = None,
        codes_path: Path | str | None = None,
    ) -> None:
        self.log_path = Path(log_path) if log_path is not None else DEFAULT_QA_LOG_PATH
        self.codes = load_qa_codes(Path(codes_path) if codes_path is not None else None)
        self._key_to_code = {
            str(meta.get("key", "")).lower(): code
            for code, meta in self.codes.items()
            if meta.get("key")
        }

    def resolve_code(self, code_or_key: str) -> str:
        token = code_or_key.strip()
        upper = token.upper()
        if upper in self.codes:
            return upper
        lower = token.lower()
        if lower in self._key_to_code:
            return self._key_to_code[lower]
        return "REJECT_OTHER"

    def log(
        self,
        job_id: str,
        item_id: str | int,
        code_or_key: str,
        *,
        stage: str = "",
        note: str = "",
    ) -> dict[str, Any]:
        code = self.resolve_code(code_or_key)
        meta = self.codes.get(code, {})
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "job_id": job_id,
            "item_id": str(item_id),
            "stage": stage,
            "code": code,
            "severity": meta.get("severity", "error"),
            "note": note.strip(),
        }
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        return record

    def summarize(self, job_id: str | None = None) -> dict[str, Any]:
        if not self.log_path.exists():
            return {"total": 0, "ok": 0, "pass_rate_pct": 0.0, "by_code": {}}
        counts: Counter[str] = Counter()
        for line in self.log_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if job_id is not None and rec.get("job_id") != job_id:
                continue
            counts[str(rec.get("code", "REJECT_OTHER"))] += 1
        total = sum(counts.values())
        ok = counts.get("OK", 0)
        return {
            "total": total,
            "ok": ok,
            "pass_rate_pct": round((ok / total) * 100.0, 1) if total > 0 else 0.0,
            "by_code": dict(counts),
        }
