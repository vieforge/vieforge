"""Unit tests for MCP worker tool functions and install_skill.py absolute-path generator."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from mcp.worker_mcp_server import (
    tool_estimate_batch_dry_run,
    tool_get_job_summary,
    tool_run_local_batch,
    tool_run_trimmed_command,
)
from scripts.install_skill import build_mcp_snippet, install_skill


def test_mcp_worker_tools_batch_resume_and_trim(tmp_path: Path) -> None:
    db_path = tmp_path / "mcp_state.db"
    items = [
        {"id": "a1", "text": "Server latency spiked"},
        {"id": "a2", "text": "Database backup succeeded"},
    ]

    # Run 1 (mock local batch)
    res1 = json.loads(
        tool_run_local_batch(
            job_id="mcp_job",
            items_json=json.dumps(items),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res1["summary"]["done"] == 2
    assert res1["processed_this_run"] == 2
    assert res1["savings_receipt"]["actual_usd"] == 0.0

    # Run 2 (same items -> 100% skipped from checkpoint at $0)
    res2 = json.loads(
        tool_run_local_batch(
            job_id="mcp_job",
            items_json=json.dumps(items),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res2["processed_this_run"] == 0
    assert res2["skipped_from_checkpoint"] == 2

    summary = json.loads(tool_get_job_summary("mcp_job", db_path=str(db_path)))
    assert summary["done"] == 2

    est = json.loads(tool_estimate_batch_dry_run("external/media-render-unit", item_count=10))
    assert est["estimated_base_usd"] == 0.8

    cmd_res = json.loads(tool_run_trimmed_command(f'"{sys.executable}" -c "print(123)"'))
    assert cmd_res["returncode"] == 0
    assert "123" in cmd_res["stdout_trimmed"]


def test_install_skill_and_absolute_mcp_snippet(tmp_path: Path) -> None:
    snippet = build_mcp_snippet()
    srv = snippet["mcpServers"]["tierforge-worker"]
    assert Path(srv["command"]).is_absolute()
    assert Path(srv["args"][0]).is_absolute()
    assert srv["args"][0].endswith("worker_mcp_server.py")

    dest = tmp_path / "three-tier-orchestrator"
    copied = install_skill(dest, dry_run=False)
    assert len(copied) >= 3
    assert (dest / "SKILL.md").is_file()
