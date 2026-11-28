"""Unit & integration tests for Stage 4 (v0.8.0 -> v1.0) Self-Learning Eval & Lens UI.

Covers:
  - core/repair_eval.py (15-case offline $0 repair eval suite + anonymized recurring lesson harvester)
  - scripts/eval_repair.py (`vieforge eval`, `vieforge eval --markdown`, `vieforge eval harvest`)
  - plugins/vieforge-lens/ui/panel.html + tool_open_vieforge_panel() repair_health telemetry
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.error_memory import ErrorMemory
from core.hooks_engine import HooksEngine
from core.repair_eval import (
    export_self_learning_playbook,
    harvest_recurring_lessons,
    run_repair_eval_suite,
)
from core.repair_loop import RepairLoopManager
from core.safe_patcher import PatchEdit
from scripts import cli
from scripts.mcp_server import _load_panel_html, tool_open_vieforge_panel


def test_repair_eval_suite_passes_all_15_cases() -> None:
    report = run_repair_eval_suite()
    assert report.total_cases == 15
    assert report.passed_cases == 15
    assert report.pass_rate_pct == 100.0
    assert report.avg_localization_tokens > 0
    assert report.avg_localization_tokens < 400
    md = report.to_markdown()
    assert "15/15 PASS" in md
    assert "E15_hooks_and_sandbox_isolation" in md


def test_harvest_recurring_lessons_anonymizes_and_aggregates(tmp_path: Path) -> None:
    proj = tmp_path / "demo_proj"
    proj.mkdir()
    with ErrorMemory(proj / ".vieforge" / "error_memory.db") as mem:
        for _ in range(3):
            mem.record_incident(
                fingerprint="deadbeef1234",
                error_type="UnicodeDecodeError",
                symptom="cp1252 failure",
                root_cause="Missing utf-8 with leaked sk-proj-secret1234567890abcdef",
                verified_fix="Set encoding='utf-8'",
                prevention="Check AST portability",
            )

    qa_dir = proj / "data"
    qa_dir.mkdir()
    qa_dir.joinpath("qa_feedback.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"code": "SCHEMA_INVALID"}),
                json.dumps({"code": "SCHEMA_INVALID"}),
                json.dumps({"code": "OK"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    harv = harvest_recurring_lessons([proj], min_hits=2)
    assert harv["scanned_projects"] == 1
    assert len(harv["recurring_hard_errors"]) == 1
    hard = harv["recurring_hard_errors"][0]
    assert hard["hit_count"] == 3
    assert "sk-proj-" not in hard["root_cause"]
    assert "[REDACTED_API_KEY]" in hard["root_cause"]
    assert harv["recurring_soft_errors"] == [{"code": "SCHEMA_INVALID", "count": 2}]
    assert len(harv["suggested_next_steps"]) >= 2


def test_cli_eval_and_harvest_commands(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["eval", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["passed_cases"] == 15

    assert cli.main(["eval", "--json", "harvest", "--project", str(tmp_path)]) == 0
    harv = json.loads(capsys.readouterr().out)
    assert harv["scanned_projects"] == 1


def test_vieforge_lens_panel_includes_repair_health_in_all_5_languages(tmp_path: Path) -> None:
    data = tool_open_vieforge_panel(project_dir=str(tmp_path))
    assert "repair_health" in data
    rh = data["repair_health"]
    assert set(rh.keys()) == {"memory_incidents", "memory_hits", "snapshots_count", "pending_approvals"}

    html = _load_panel_html()
    assert "innerHTML" not in html
    assert "http://" not in html and "https://" not in html
    for phrase in (
        "Debugging & Safety Health",
        "Sức khỏe & Sửa lỗi",
        "デバッグ・安全性ヘルス",
        "Fehlerbehebung & Sicherheit",
        "디버깅 및 안전 상태",
    ):
        assert phrase in html


def test_repair_proposal_includes_impact_risk_and_self_learning_playbook(tmp_path: Path) -> None:
    proj = tmp_path / "app_repo"
    (proj / "tests").mkdir(parents=True)
    pg = proj / "policy_guard.py"
    pg.write_text("SAFE = True\n", encoding="utf-8")
    (proj / "tests" / "test_pg.py").write_text("import policy_guard\n", encoding="utf-8")

    mgr = RepairLoopManager(proj)
    prop = mgr.propose_repair(
        session_id="imp_s1",
        log_text=f'  File "{pg}", line 1\nValueError: bad\n',
        hypothesis="Relax guard temporarily",
        edits=[PatchEdit("policy_guard.py", "SAFE = True", "SAFE = False")],
        include_git=False,
    )
    assert prop.impact_risk == "RED"
    assert "tests/test_pg.py" in prop.impact_related_tests

    # Rejecting records a disproven hypothesis that the self-learning playbook surfaces
    mgr.reject_proposal(session_id="imp_s1", proposal_id=prop.proposal_id, reason="Never disable guard")
    out_file = proj / ".vieforge" / "learned_rules.md"
    pb = export_self_learning_playbook([proj], out_path=out_file, min_hits=1)
    assert out_file.is_file()
    assert "Relax guard temporarily" in pb["markdown"]
    assert "Never disable guard" in pb["markdown"]

    # Pre-commit hook on repo with no staged files must not false-block
    engine = HooksEngine(proj)
    pre = engine.run_pre_commit()
    assert pre.passed is True
    assert pre.checks[0].name == "staged_scope"
