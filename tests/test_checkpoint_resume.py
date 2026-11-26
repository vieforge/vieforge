"""Unit tests for SQLite checkpoint/resume, deduplication, LLMRouter JSON retry & QA logger."""

from __future__ import annotations

import sys
from pathlib import Path

from core.llm_router import LLMRouter
from core.qa_logger import QALogger
from core.state_db import StateDB, compute_input_hash
from core.subprocess_runner import run_isolated


def test_sqlite_checkpoint_interrupt_resume_and_dedup(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    items = [
        ("doc_01", compute_input_hash("Alpha document")),
        ("doc_02", compute_input_hash("Beta document")),
        ("doc_03", compute_input_hash("Gamma document")),
    ]

    # Run 1: process only first item, then simulate mid-run interrupt
    with StateDB(db_path) as db:
        counts = db.register_items("job_resume", items)
        assert counts["pending"] == 3
        pending = db.get_pending_items("job_resume", max_retries=2)
        assert [p.item_id for p in pending] == ["doc_01", "doc_02", "doc_03"]
        db.mark_done("job_resume", "doc_01", output={"ok": True}, cost_usd=0.0)

    # Run 2: resume with identical inputs -> doc_01 is skipped at $0 extra cost
    with StateDB(db_path) as db:
        counts2 = db.register_items("job_resume", items)
        assert counts2["done"] == 1
        assert counts2["pending"] == 2
        pending2 = db.get_pending_items("job_resume", max_retries=2)
        assert [p.item_id for p in pending2] == ["doc_02", "doc_03"]

        # Simulate retry cap on doc_02 (max 2 retries)
        assert db.mark_failed("job_resume", "doc_02", "transient error") == 1
        assert db.mark_failed("job_resume", "doc_02", "still failing") == 2
        db.mark_done("job_resume", "doc_03", output={"ok": True}, cost_usd=0.0)

        # doc_02 reached retry_count=2, so it is no longer returned in pending
        assert db.get_pending_items("job_resume", max_retries=2) == []

        summary = db.get_job_summary("job_resume")
        assert summary["total"] == 3
        assert summary["done"] == 2
        assert summary["failed"] == 1
        assert summary["total_cost_usd"] == 0.0

        # Changing input_hash for doc_01 resets it to pending
        new_status = db.register_item("job_resume", "doc_01", compute_input_hash("Alpha v2"))
        assert new_status == "pending"


def test_llm_router_batch_session_and_json_self_repair() -> None:
    calls = {"n": 0}

    def _flaky_small_model(_prompt: str, _system: str) -> str:
        calls["n"] += 1
        if calls["n"] == 1:
            return "not valid json at all"
        if calls["n"] == 2:
            return '```json\n{"title": "only title"}\n```'
        return '```json\n{"title": "ok", "tags": ["a", "b"]}\n```'

    router = LLMRouter(model="qwen2.5:3b", mock=True, mock_handler=_flaky_small_model)
    with router.batch_session(keep_alive="2m"):
        assert router._effective_keep_alive(None) == "2m"
        resp = router.generate_json(
            "Extract title and tags",
            required_keys=["title", "tags"],
            max_retries=2,
        )
    # After batch_session exits, single calls revert to keep_alive=0
    assert router._effective_keep_alive(None) == 0
    assert resp.attempts == 3
    assert resp.parsed_json == {"title": "ok", "tags": ["a", "b"]}


def test_subprocess_runner_and_qa_logger(tmp_path: Path) -> None:
    res = run_isolated(
        [sys.executable, "-c", "for i in range(40): print('repeat line')"],
        timeout_sec=10.0,
    )
    assert res.returncode == 0
    assert "(repeated 40x)" in res.stdout_trimmed
    assert res.saved_pct > 50.0

    qa = QALogger(log_path=tmp_path / "qa_feedback.jsonl")
    qa.log("job_1", "item_1", "y", stage="extract")
    qa.log("job_1", "item_2", "s", stage="extract", note="missing field")
    qa.log("job_1", "item_3", "OK", stage="extract")

    summary = qa.summarize(job_id="job_1")
    assert summary["total"] == 3
    assert summary["ok"] == 2
    assert summary["by_code"]["SCHEMA_INVALID"] == 1
