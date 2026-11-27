from __future__ import annotations

import json
from pathlib import Path

from core.policy_guard import PolicyGuard


def test_policy_guard_rbac_and_dangerous_command_block(tmp_path: Path) -> None:
    audit_log = tmp_path / "policy_audit.jsonl"
    guard = PolicyGuard(audit_log_path=audit_log)

    d_view_ok = guard.authorize(role="viewer", action="get_job_summary", actor="alice")
    assert d_view_ok.allowed is True

    d_view_deny = guard.authorize(role="viewer", action="run_local_batch", actor="alice")
    assert d_view_deny.allowed is False

    d_dev_danger = guard.authorize(
        role="developer",
        action="run_trimmed_command",
        actor="bob",
        command="rm -rf /tmp/something && rm -rf /",
    )
    assert d_dev_danger.allowed is False
    assert "dangerous" in d_dev_danger.reason.lower()

    d_admin = guard.authorize(role="admin", action="custom_enterprise_tool", actor="root")
    assert d_admin.allowed is True

    lines = [json.loads(ln) for ln in audit_log.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 4
    assert lines[1]["allowed"] is False


def test_policy_guard_custom_enterprise_hook(tmp_path: Path) -> None:
    guard = PolicyGuard(
        audit_log_path=tmp_path / "custom.jsonl",
        custom_authorizer=lambda role, action, meta: meta.get("tenant") == "acme",
    )
    assert guard.authorize(role="developer", action="run_local_batch", metadata={"tenant": "acme"}).allowed is True
    assert guard.authorize(role="developer", action="run_local_batch", metadata={"tenant": "other"}).allowed is False
