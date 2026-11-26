from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPT_PATH = Path(__file__).resolve().parent.parent / "examples" / "03_ci_log_triage" / "run_ci_triage.py"
_SPEC = importlib.util.spec_from_file_location("run_ci_triage", _SCRIPT_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_ci_triage_dry_run(tmp_path: Path, capsys) -> None:
    db_file = tmp_path / "dry.db"
    rc = _MOD.main(["--dry-run", "--db", str(db_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Example 03 --dry-run" in out
    assert not db_file.exists()


def test_ci_triage_mock_and_resume(tmp_path: Path, capsys) -> None:
    db_file = tmp_path / "triage.db"
    rc1 = _MOD.main(["--mock-llm", "--reset", "--db", str(db_file)])
    assert rc1 == 0
    out1 = capsys.readouterr().out
    assert "[TRIAGED] ci_ubuntu_py312" in out1
    assert "secrets_redacted=" in out1

    rc2 = _MOD.main(["--mock-llm", "--db", str(db_file)])
    assert rc2 == 0
    out2 = capsys.readouterr().out
    assert "already_done=3 pending=0" in out2


def test_ci_triage_custom_log_dir(tmp_path: Path, capsys) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "build.log").write_text(
        "[INFO] step 1 ok\nFAILED tests/test_x.py - AssertionError: boom\n",
        encoding="utf-8",
    )
    db_file = tmp_path / "custom.db"
    rc = _MOD.main(["--log-dir", str(log_dir), "--mock-llm", "--reset", "--db", str(db_file)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[TRIAGED] build" in out
