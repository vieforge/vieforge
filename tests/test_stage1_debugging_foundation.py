"""Unit & integration tests for Stage 1 (v0.5.0) Debugging Foundation.

Covers:
  - core/output_trimmer.py v2 (pytest, linter, js_stack, go_rust modes + secret safety)
  - core/fault_locator.py (AST slicing, callers, fingerprint stability, syntax-error fallback, CLI)
  - core/workspace_snapshot.py (create/diff/dry-run/rollback/prune, path-traversal & SHA-256 guards, CLI)
  - scripts/third_patch_gate.py (--init-diagnosis auto-scaffolding)
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from core.fault_locator import compute_error_fingerprint, locate_fault, parse_error_frames
from core.output_trimmer import trim_output
from core.workspace_snapshot import WorkspaceSnapshotManager
from scripts import cli, third_patch_gate

# ---------------------------------------------------------------------------
# 1. Output Trimmer v2 (Tool-aware multi-language log compression)
# ---------------------------------------------------------------------------


def test_output_trimmer_pytest_drops_passed_keeps_failures() -> None:
    lines = ["============================= test session starts ============================="]
    lines.extend([f"tests/test_sample.py::test_case_{i} PASSED [ {i}%]" for i in range(1, 35)])
    lines.extend(
        [
            "=================================== FAILURES ===================================",
            "_______________________________ test_broken_math _______________________________",
            "    def test_broken_math():",
            ">       assert 1 + 1 == 3",
            "E       assert 2 == 3",
            "tests/test_sample.py:42: AssertionError",
            "=========================== short test summary info ============================",
            "FAILED tests/test_sample.py::test_broken_math - assert 2 == 3",
            "========================= 1 failed, 34 passed in 0.21s =========================",
        ]
    )
    res = trim_output("\n".join(lines), max_lines=40)
    assert res.detected_mode == "pytest"
    assert "34 PASSED pytest lines omitted" in res.text
    assert "FAILED tests/test_sample.py::test_broken_math" in res.text
    assert "E       assert 2 == 3" in res.text
    assert res.saved_pct > 40.0


def test_output_trimmer_linter_groups_repeated_codes() -> None:
    diag = [f"src/mod_{i}.py:{10 + i}:1: F401 `os` imported but unused" for i in range(10)]
    diag.extend([f"src/web_{i}.ts({i + 1},4): error TS2304: Cannot find name 'foo'" for i in range(5)])
    res = trim_output("\n".join(diag))
    assert res.detected_mode == "linter"
    assert "F401 (10x)" in res.text
    assert "TS2304 (5x)" in res.text
    assert "(+6 more)" in res.text


def test_output_trimmer_js_stack_collapses_node_modules() -> None:
    js_log = "\n".join(
        [
            "TypeError: Cannot read properties of undefined (reading 'id')",
            "    at renderUser (src/app.ts:18:12)",
            "    at Layer.handle (node_modules/express/lib/router/layer.js:95:5)",
            "    at next (node_modules/express/lib/router/route.js:144:13)",
            "    at processTicksAndRejections (node:internal/process/task_queues:95:5)",
            "    at main (src/server.ts:44:3)",
        ]
    )
    res = trim_output(js_log)
    assert res.detected_mode == "js_stack"
    assert res.traceback_trimmed is True
    assert "renderUser (src/app.ts:18:12)" in res.text
    assert "main (src/server.ts:44:3)" in res.text
    assert "3 node_modules/internal frames collapsed" in res.text


def test_output_trimmer_go_rust_panic_trimmed() -> None:
    go_log = "\n".join(
        [
            "panic: runtime error: index out of range [5] with length 2",
            "goroutine 1 [running]:",
            *[f"main.step{i}(0x1234)\n\t/app/main.go:{10 + i} +0x42" for i in range(25)],
        ]
    )
    res = trim_output(go_log, max_traceback_lines=12)
    assert res.detected_mode == "go_rust"
    assert res.traceback_trimmed is True
    assert "Go/Rust stack frames trimmed" in res.text


# ---------------------------------------------------------------------------
# 2. Hierarchical Fault Locator (AST slicing, callers, fingerprint, CLI)
# ---------------------------------------------------------------------------


def test_fault_locator_slices_python_function_and_finds_callers(tmp_path: Path) -> None:
    (tmp_path / "calc.py").write_text(
        "import math\n\n"
        "def safe_helper(x: int) -> int:\n"
        "    return x + 1\n\n"
        "def divide_items(total: int, count: int) -> float:\n"
        "    # risky division\n"
        "    return total / count\n",
        encoding="utf-8",
    )
    (tmp_path / "runner.py").write_text(
        "from calc import divide_items\n\n"
        "def run() -> float:\n"
        "    return divide_items(10, 0)\n",
        encoding="utf-8",
    )
    tb = (
        "Traceback (most recent call last):\n"
        f'  File "{tmp_path / "runner.py"}", line 4, in run\n'
        "    return divide_items(10, 0)\n"
        f'  File "{tmp_path / "calc.py"}", line 8, in divide_items\n'
        "    return total / count\n"
        "ZeroDivisionError: division by zero\n"
    )
    report = locate_fault(tb, repo_root=tmp_path, include_git=False)
    assert report.error_type == "ZeroDivisionError"
    assert len(report.located_symbols) >= 1
    top = report.located_symbols[0]
    assert top.file_path == "calc.py"
    assert top.symbol_name == "divide_items"
    assert top.symbol_kind == "function"
    assert ">>>    8 |     return total / count" in top.snippet
    assert "import math" in top.imports
    assert any(c.startswith("runner.py:4") for c in top.callers)
    assert report.estimated_tokens < 400


def test_fault_locator_fingerprint_stable_across_memory_addresses(tmp_path: Path) -> None:
    (tmp_path / "mod.py").write_text("def boom():\n    raise ValueError('bad')\n", encoding="utf-8")
    f1 = parse_error_frames(f'  File "{tmp_path / "mod.py"}", line 2, in boom', repo_root=tmp_path)
    fp1 = compute_error_fingerprint("ValueError", "object at 0x7f112233 invalid", f1)
    fp2 = compute_error_fingerprint("ValueError", "object at 0x99aabbcc invalid", f1)
    assert fp1 == fp2


def test_fault_locator_handles_syntax_error_and_multilang_frames(tmp_path: Path) -> None:
    broken = tmp_path / "broken.py"
    broken.write_text("def bad(:\n    pass\n", encoding="utf-8")
    tb = f'  File "{broken}", line 1\nSyntaxError: invalid syntax\n'
    rep = locate_fault(tb, repo_root=tmp_path, include_git=False)
    assert rep.located_symbols[0].symbol_kind == "syntax_error_window"

    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "main.rs").write_text("fn main() {\n    panic!(\"oops\");\n}\n", encoding="utf-8")
    rust_log = "thread 'main' panicked at 'oops'\n  --> src/main.rs:2:5\n"
    rust_rep = locate_fault(rust_log, repo_root=tmp_path, include_git=False)
    assert any(s.file_path == "src/main.rs" for s in rust_rep.located_symbols)


def test_cli_locate_json_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "app.py").write_text("def fail():\n    return 1 / 0\n", encoding="utf-8")
    log_path = tmp_path / "err.log"
    log_path.write_text(
        f'Traceback (most recent call last):\n  File "{tmp_path / "app.py"}", line 2, in fail\nZeroDivisionError: division by zero\n',
        encoding="utf-8",
    )
    rc = cli.main(["locate", "--repo", str(tmp_path), "--log", str(log_path), "--no-git", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["error_type"] == "ZeroDivisionError"
    assert payload["located_symbols"][0]["symbol_name"] == "fail"


# ---------------------------------------------------------------------------
# 3. Workspace Snapshot & Rollback (Create, Diff, Dry-Run, Restore, Security)
# ---------------------------------------------------------------------------


def test_workspace_snapshot_create_diff_rollback_and_cleanup(tmp_path: Path) -> None:
    target = tmp_path / "module.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    new_file = tmp_path / "generated.py"

    mgr = WorkspaceSnapshotManager(tmp_path)
    snap = mgr.create_snapshot(["module.py", "generated.py"], label="pre_fix", note="Before repair")
    assert (tmp_path / ".vieforge" / "snapshots" / ".gitignore").is_file()

    # Simulate an unsafe edit that mutates an existing file and creates a stray file
    target.write_text("VALUE = 999\n", encoding="utf-8")
    new_file.write_text("# stray file\n", encoding="utf-8")

    diff = mgr.diff_snapshot(snap.snapshot_id)
    statuses = {f["rel_path"]: f["status"] for f in diff["files"]}
    assert statuses["module.py"] == "modified"
    assert statuses["generated.py"] == "created_since_snapshot"

    # Dry-run must not mutate workspace
    preview = mgr.rollback(snap.snapshot_id, dry_run=True)
    assert preview["restored"] == ["module.py"]
    assert preview["removed"] == ["generated.py"]
    assert target.read_text(encoding="utf-8") == "VALUE = 999\n"
    assert new_file.exists()

    # Real rollback restores original content and removes stray file
    restored = mgr.rollback(snap.snapshot_id, dry_run=False)
    assert restored["restored"] == ["module.py"]
    assert restored["removed"] == ["generated.py"]
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not new_file.exists()


def test_workspace_snapshot_security_and_integrity_guards(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top secret", encoding="utf-8")

    mgr = WorkspaceSnapshotManager(repo)
    with pytest.raises(ValueError, match="escapes repo root"):
        mgr.create_snapshot([str(outside)])

    # Integrity mismatch detection if backup file is tampered with
    inside = repo / "ok.py"
    inside.write_text("x = 1\n", encoding="utf-8")
    snap = mgr.create_snapshot(["ok.py"], label="check")
    backup = repo / ".vieforge" / "snapshots" / snap.snapshot_id / "files" / "ok.py"
    backup.write_text("corrupted!\n", encoding="utf-8")
    with pytest.raises(ValueError, match="integrity mismatch"):
        mgr.rollback(snap.snapshot_id)


def test_cli_rollback_end_to_end(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "demo.py"
    f.write_text("a = 1\n", encoding="utf-8")
    assert cli.main(["rollback", "--repo", str(tmp_path), "create", "demo.py", "--label", "cli_test"]) == 0
    _ = capsys.readouterr()
    f.write_text("a = 2\n", encoding="utf-8")
    assert cli.main(["rollback", "--repo", str(tmp_path), "restore"]) == 0
    assert f.read_text(encoding="utf-8") == "a = 1\n"


# ---------------------------------------------------------------------------
# 4. Third-Patch Gate --init-diagnosis Auto-Scaffolding
# ---------------------------------------------------------------------------


def test_third_patch_init_diagnosis_scaffolds_valid_note(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@vieforge.dev"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "VieForge Test"], cwd=tmp_path, check=True)

    app = tmp_path / "app.py"
    app.write_text("v = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fix(app): first patch"], cwd=tmp_path, check=True, capture_output=True)

    app.write_text("v = 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "fix(app): second patch"], cwd=tmp_path, check=True, capture_output=True)

    # First run with --init-diagnosis blocks AND scaffolds docs/diagnosis/app.py.md
    rc1 = third_patch_gate.main(["--repo", str(tmp_path), "--init-diagnosis", "--json"])
    out1 = json.loads(capsys.readouterr().out)
    assert rc1 == 1
    assert out1["status"] == "BLOCKED"
    assert out1["scaffolded_diagnosis"] == ["docs/diagnosis/app.py.md"]
    diag_file = tmp_path / "docs" / "diagnosis" / "app.py.md"
    assert diag_file.is_file()
    content = diag_file.read_text(encoding="utf-8")
    assert "## Symptom" in content and "## Hypothesis" in content and "## Observation" in content

    # Second run now passes because a valid structured diagnosis note exists
    rc2 = third_patch_gate.main(["--repo", str(tmp_path), "--json"])
    out2 = json.loads(capsys.readouterr().out)
    assert rc2 == 0
    assert out2["status"] == "PASS"
