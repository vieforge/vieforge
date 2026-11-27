"""Bidirectional (Forward + Reverse) tests for VieForge mechanical discipline gates."""

from __future__ import annotations

import datetime as dt
import subprocess
from pathlib import Path

from scripts import doc_drift_gate, third_patch_gate


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_third_patch_forward_reverse_and_diagnosis_unblock(tmp_path: Path) -> None:
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "ci@vieforge.dev")
    _git(tmp_path, "config", "user.name", "VieForge CI")

    target = tmp_path / "worker.py"
    target.write_text("v = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "worker.py")
    _git(tmp_path, "commit", "-m", "feat: initial worker")

    # Forward test: 1 fix commit -> PASS
    target.write_text("v = 2\n", encoding="utf-8")
    _git(tmp_path, "add", "worker.py")
    _git(tmp_path, "commit", "-m", "fix: handle timeout")
    res_pass = third_patch_gate.evaluate_third_patch(tmp_path, ["worker.py"], hours=72, threshold=2)
    assert res_pass["status"] == "PASS"

    # Reverse test: 2nd fix commit -> BLOCKED before 3rd patch
    target.write_text("v = 3\n", encoding="utf-8")
    _git(tmp_path, "add", "worker.py")
    _git(tmp_path, "commit", "-m", "fix: retry timeout again")
    res_block = third_patch_gate.evaluate_third_patch(tmp_path, ["worker.py"], hours=72, threshold=2)
    assert res_block["status"] == "BLOCKED"
    assert res_block["blocked"][0]["file"] == "worker.py"

    # Unblock with a valid 3-section diagnosis note
    diag_dir = tmp_path / "docs" / "diagnosis"
    diag_dir.mkdir(parents=True, exist_ok=True)
    (diag_dir / "worker.md").write_text(
        "# worker.py diagnosis\n\n## Symptom\nTimeout\n\n## Hypothesis\nPool leak\n\n## Observation\nVerified\n",
        encoding="utf-8",
    )
    res_unblock = third_patch_gate.evaluate_third_patch(tmp_path, ["worker.py"], hours=72, threshold=2)
    assert res_unblock["status"] == "PASS"


def test_doc_drift_forward_and_reverse(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("<!--measure: token_savings=94.1% @2026-11-25-->\n", encoding="utf-8")
    res_ok = doc_drift_gate.evaluate_doc_drift(tmp_path, max_age_days=30, today=dt.date(2026, 11, 27))
    assert res_ok["status"] == "PASS"
    assert res_ok["total_markers"] == 1

    # Reverse test: expired marker -> STALE
    (tmp_path / "OLD.md").write_text("<!--measure: routes=76 @2026-08-01-->\n", encoding="utf-8")
    res_stale = doc_drift_gate.evaluate_doc_drift(tmp_path, max_age_days=30, today=dt.date(2026, 11, 27))
    assert res_stale["status"] == "STALE"
    assert len(res_stale["stale"]) == 1
    assert res_stale["stale"][0]["name"] == "routes"
