"""Unit tests for multi-project concurrency safety & multi-agent installer."""

from __future__ import annotations

import threading
from pathlib import Path

from core.concurrency import OllamaLeaseManager, exclusive_gpu_lock
from core.cost_tracker import CostTracker
from core.state_db import StateDB, compute_input_hash, scoped_job_id
from scripts.install_skill import (
    ALL_TARGETS,
    build_codex_toml_snippet,
    resolve_target_dir,
)


def test_ollama_lease_manager_prevents_premature_unload(tmp_path: Path) -> None:
    mgr = OllamaLeaseManager(lock_dir=tmp_path)
    # Project A and Project B both acquire leases on shared Ollama VRAM
    assert mgr.acquire("projA-1", model="qwen2.5:7b") == 1
    assert mgr.acquire("projB-1", model="qwen2.5:7b") == 2
    assert mgr.active_count() == 2

    # Project B finishes first -> MUST NOT unload VRAM while Project A is still active
    assert mgr.release("projB-1") is False
    assert mgr.active_count() == 1

    # Project A finishes last -> NOW safe to unload VRAM (keep_alive=0)
    assert mgr.release("projA-1") is True
    assert mgr.active_count() == 0


def test_concurrent_daily_spend_atomic_updates(tmp_path: Path) -> None:
    daily_file = tmp_path / "daily_spend.json"
    errors: list[Exception] = []

    def _worker(idx: int) -> None:
        try:
            tracker = CostTracker(
                job_id=f"proj_{idx}",
                max_job_usd=10.0,
                daily_usd_cap=100.0,
                daily_spend_path=daily_file,
            )
            for step in range(5):
                tracker.record("api", "external/media-render-unit", 0.10, item_id=f"{idx}_{step}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=_worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    check = CostTracker(job_id="verify", daily_spend_path=daily_file)
    # 4 projects * 5 calls * $0.10 = $2.00 exact (zero lost updates)
    assert round(check.get_today_spend_usd(), 2) == 2.00


def test_scoped_job_ids_isolate_concurrent_projects(tmp_path: Path) -> None:
    db_path = tmp_path / "shared_state.db"
    j_a = scoped_job_id("project-alpha", "batch_01")
    j_b = scoped_job_id("project-beta", "batch_01")
    assert j_a != j_b

    with StateDB(db_path) as db:
        db.register_items(j_a, [("doc1", compute_input_hash("alpha"))])
        db.register_items(j_b, [("doc1", compute_input_hash("beta"))])
        db.mark_done(j_a, "doc1", output={"src": "alpha"})

        assert db.get_job_summary(j_a)["done"] == 1
        assert db.get_job_summary(j_b)["done"] == 0
        assert db.get_job_summary(j_b)["pending"] == 1


def test_exclusive_gpu_lock_and_multi_agent_targets(tmp_path: Path) -> None:
    with exclusive_gpu_lock(lock_dir=tmp_path, timeout_sec=5.0):
        pass

    for target in ALL_TARGETS:
        dest = resolve_target_dir(target, home=tmp_path)
        assert dest.name == "three-tier-orchestrator"

    toml_str = build_codex_toml_snippet()
    assert "[mcp_servers.tierforge-worker]" in toml_str
    assert "mcp_server.py" in toml_str
