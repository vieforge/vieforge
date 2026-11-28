"""Unit & integration tests for Stage 2 (v0.6.0) Approval-Gated Repair Brain.

Covers:
  - core/error_memory.py (SQLite WAL postmortem KB, secret redaction, exact & similarity lookup)
  - core/patch_applier.py (SEARCH/REPLACE parsing, uniqueness, syntax pre-check, all-or-nothing, path guard)
  - core/repair_loop.py (2-phase approval gate, auto-rollback on verify fail, anti-loop, ErrorMemory recall)
  - scripts/diagnose.py CLI & scripts/mcp_server.py::tool_diagnose_and_locate
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from core.error_memory import ErrorMemory
from core.repair_loop import ApprovalRequiredError, RepairLoopManager
from core.safe_patcher import PatchApplier, PatchEdit, parse_search_replace_blocks
from scripts import cli
from scripts.mcp_server import tool_diagnose_and_locate

# ---------------------------------------------------------------------------
# 1. Local SQLite Error Memory
# ---------------------------------------------------------------------------


def test_error_memory_record_redacts_secrets_and_matches_exact_and_similar(tmp_path: Path) -> None:
    db_path = tmp_path / ".vieforge" / "error_memory.db"
    with ErrorMemory(db_path) as mem:
        inc1 = mem.record_incident(
            fingerprint="aa11bb22cc33",
            error_type="UnicodeDecodeError",
            symptom="cp1252 codec error while reading token sk-proj-secret1234567890abcdef",
            root_cause="Subprocess missing PYTHONUTF8=1 and explicit utf-8 encoding",
            verified_fix="Pass encoding='utf-8' and errors='replace' to subprocess.run",
            files=["core/subprocess_runner.py"],
            tags=["windows", "utf8"],
        )
        assert "[REDACTED_API_KEY]" in inc1.symptom
        assert "sk-proj-" not in inc1.symptom
        assert inc1.hit_count == 1

        # Second record on same fingerprint increments hit_count
        inc2 = mem.record_incident(
            fingerprint="aa11bb22cc33",
            error_type="UnicodeDecodeError",
            symptom="",
            root_cause="",
            verified_fix="",
            files=["scripts/preflight_check.py"],
        )
        assert inc2.hit_count == 2
        assert "scripts/preflight_check.py" in inc2.files

        # Exact fingerprint match
        exact = mem.lookup(fingerprint="aa11bb22cc33")
        assert len(exact) == 1
        assert exact[0].match_type == "exact_fingerprint"
        assert exact[0].score == 1.0

        # Similarity fallback without fingerprint
        sim = mem.lookup(error_type="UnicodeDecodeError", query_text="windows cp1252 subprocess utf8")
        assert len(sim) >= 1
        assert sim[0].match_type == "similarity"
        assert sim[0].incident.fingerprint == "aa11bb22cc33"


# ---------------------------------------------------------------------------
# 2. Narrow Patch Applier (SEARCH/REPLACE + Pre-Write Syntax Verification)
# ---------------------------------------------------------------------------


def test_patch_applier_parses_dry_runs_and_enforces_syntax_all_or_nothing(tmp_path: Path) -> None:
    good = tmp_path / "good.py"
    bad = tmp_path / "bad.py"
    good.write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
    bad.write_text("def mul(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")

    patch_text = (
        "FILE: good.py\n"
        "<<<<<<< SEARCH\n"
        "    return a - b\n"
        "=======\n"
        "    return a + b\n"
        ">>>>>>> REPLACE\n"
    )
    edits = parse_search_replace_blocks(patch_text)
    assert len(edits) == 1

    applier = PatchApplier(tmp_path)
    preview = applier.apply(edits, dry_run=True)
    assert preview.ok is True
    assert "+    return a + b" in preview.results[0].unified_diff
    # Dry-run must leave file untouched
    assert "return a - b" in good.read_text(encoding="utf-8")

    # All-or-nothing syntax guard: valid edit on good.py + syntax-breaking edit on bad.py -> ZERO writes
    broken_batch = [
        edits[0],
        PatchEdit(file_path="bad.py", search="return a * b", replace="return a * ("),
    ]
    failed = applier.apply(broken_batch, dry_run=False)
    assert failed.ok is False
    assert "SyntaxError" in failed.error
    assert "return a - b" in good.read_text(encoding="utf-8")
    assert "return a * b" in bad.read_text(encoding="utf-8")


def test_patch_applier_rejects_ambiguous_and_outside_repo(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    dup = repo / "dup.py"
    dup.write_text("x = 1\ny = 2\nx = 1\n", encoding="utf-8")
    outside = tmp_path / "outside.py"
    outside.write_text("z = 1\n", encoding="utf-8")

    applier = PatchApplier(repo)
    amb = applier.apply([PatchEdit("dup.py", "x = 1", "x = 2")], dry_run=True)
    assert amb.ok is False
    assert "ambiguous" in amb.error

    esc = applier.apply([PatchEdit(str(outside), "z = 1", "z = 2")], dry_run=True)
    assert esc.ok is False
    assert "escapes repo root" in esc.error


# ---------------------------------------------------------------------------
# 3. Approval-Gated Repair Loop (Propose -> Approve -> Verify / Auto-Rollback)
# ---------------------------------------------------------------------------


def test_repair_loop_enforces_approval_and_records_verified_fix(tmp_path: Path) -> None:
    calc = tmp_path / "calc.py"
    calc.write_text("def divide(a: int, b: int) -> float:\n    return a / b\n", encoding="utf-8")
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{calc}", line 2, in divide\n'
        "    return a / b\n"
        "ZeroDivisionError: division by zero\n"
    )
    mgr = RepairLoopManager(tmp_path)
    edit = PatchEdit("calc.py", "    return a / b", "    return 0.0 if b == 0 else a / b")

    # Phase 1: Propose NEVER modifies workspace files
    prop = mgr.propose_repair(
        session_id="fix_div",
        log_text=tb,
        hypothesis="Guard zero denominator before dividing",
        edits=[edit],
        include_git=False,
    )
    assert prop.status == "AWAITING_APPROVAL"
    assert "return a / b" in calc.read_text(encoding="utf-8")

    # Missing approver identity must be rejected
    with pytest.raises(ApprovalRequiredError):
        mgr.approve_and_apply(session_id="fix_div", proposal_id=prop.proposal_id, approved_by="")

    # Phase 2: Explicit approval applies patch, snapshots, verifies, and saves to ErrorMemory
    verify_cmd = f'"{sys.executable}" -c "import calc; assert calc.divide(10, 0) == 0.0"'
    done = mgr.approve_and_apply(
        session_id="fix_div",
        proposal_id=prop.proposal_id,
        approved_by="owner",
        verify_cmd=verify_cmd,
    )
    assert done.status == "APPROVED_AND_FIXED"
    assert done.verification_rc == 0
    assert done.snapshot_id
    assert "0.0 if b == 0" in calc.read_text(encoding="utf-8")

    # Next session hitting same error immediately recalls the verified fix from ErrorMemory
    prop2 = mgr.propose_repair(session_id="fix_div_again", log_text=tb, include_git=False)
    assert len(prop2.known_fixes) == 1
    assert prop2.known_fixes[0]["match_type"] == "exact_fingerprint"


def test_repair_loop_auto_rollbacks_on_failed_verify_and_detects_loop(tmp_path: Path) -> None:
    mod = tmp_path / "mod.py"
    mod.write_text("def compute(x: int) -> int:\n    return x - 1\n", encoding="utf-8")
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{mod}", line 2, in compute\n'
        "AssertionError: expected 10\n"
    )
    mgr = RepairLoopManager(tmp_path)
    wrong_edit = PatchEdit("mod.py", "    return x - 1", "    return x - 2")

    prop = mgr.propose_repair(
        session_id="loop_test",
        log_text=tb,
        hypothesis="Wrong guess: subtract 2 instead of 1",
        edits=[wrong_edit],
        include_git=False,
    )
    assert prop.status == "AWAITING_APPROVAL"

    verify_cmd = f'"{sys.executable}" -c "import mod; assert mod.compute(9) == 10"'
    res = mgr.approve_and_apply(
        session_id="loop_test",
        proposal_id=prop.proposal_id,
        approved_by="owner",
        verify_cmd=verify_cmd,
        auto_rollback_on_fail=True,
    )
    assert res.status == "ROLLED_BACK_ON_FAIL"
    # File must be automatically restored to original state
    assert mod.read_text(encoding="utf-8") == "def compute(x: int) -> int:\n    return x - 1\n"

    # Re-proposing on the exact same fingerprint triggers Anti-Loop protection
    prop_loop = mgr.propose_repair(
        session_id="loop_test",
        log_text=tb,
        hypothesis="Trying again on identical error",
        edits=[wrong_edit],
        include_git=False,
    )
    assert prop_loop.loop_detected is True
    assert prop_loop.status == "LOOP_DETECTED"


def test_repair_loop_reject_blocks_subsequent_approve(tmp_path: Path) -> None:
    f = tmp_path / "x.py"
    f.write_text("A = 1\n", encoding="utf-8")
    mgr = RepairLoopManager(tmp_path)
    prop = mgr.propose_repair(
        session_id="rej_sess",
        log_text=f'  File "{f}", line 1\nValueError: bad\n',
        edits=[PatchEdit("x.py", "A = 1", "A = 2")],
        include_git=False,
    )
    rej = mgr.reject_proposal(session_id="rej_sess", proposal_id=prop.proposal_id, reason="Not safe")
    assert rej.status == "REJECTED"
    with pytest.raises(ApprovalRequiredError):
        mgr.approve_and_apply(session_id="rej_sess", proposal_id=prop.proposal_id, approved_by="owner")


# ---------------------------------------------------------------------------
# 4. CLI `vieforge diagnose` & Read-Only MCP `tool_diagnose_and_locate`
# ---------------------------------------------------------------------------


def test_cli_diagnose_and_mcp_diagnose_and_locate(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    app = tmp_path / "app.py"
    app.write_text("def greet() -> str:\n    return 'old'\n", encoding="utf-8")
    log_file = tmp_path / "fail.log"
    log_file.write_text(
        f'Traceback (most recent call last):\n  File "{app}", line 2, in greet\nAssertionError: expected new\n',
        encoding="utf-8",
    )
    patch_file = tmp_path / "fix.patch"
    patch_file.write_text(
        "FILE: app.py\n<<<<<<< SEARCH\n    return 'old'\n=======\n    return 'new'\n>>>>>>> REPLACE\n",
        encoding="utf-8",
    )

    rc1 = cli.main(
        [
            "diagnose",
            "--repo",
            str(tmp_path),
            "--json",
            "propose",
            "--session",
            "cli_s1",
            "--log",
            str(log_file),
            "--patch",
            str(patch_file),
            "--hypothesis",
            "Return 'new' from greet",
            "--no-git",
        ]
    )
    assert rc1 == 0
    prop = json.loads(capsys.readouterr().out)
    assert prop["status"] == "AWAITING_APPROVAL"

    verify_cmd = f'"{sys.executable}" -c "import app; assert app.greet() == \'new\'"'
    rc2 = cli.main(
        [
            "diagnose",
            "--repo",
            str(tmp_path),
            "--json",
            "approve",
            "--session",
            "cli_s1",
            "--proposal",
            prop["proposal_id"],
            "--by",
            "owner",
            "--verify-cmd",
            verify_cmd,
        ]
    )
    assert rc2 == 0
    done = json.loads(capsys.readouterr().out)
    assert done["status"] == "APPROVED_AND_FIXED"

    # Read-only MCP tool recalls the recorded fix and slices the failing function
    mcp_payload = json.loads(
        tool_diagnose_and_locate(log_file.read_text(encoding="utf-8"), repo_root=str(tmp_path))
    )
    assert mcp_payload["error_type"] == "AssertionError"
    assert len(mcp_payload["known_fixes"]) == 1
    assert mcp_payload["located_symbols"][0]["symbol_name"] == "greet"
