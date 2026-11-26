"""Unit tests for 2-Axis Step Auditor, Cross-Platform Portability Scanner, and 3-OS Parity Gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from core.step_auditor import _check_cross_platform_portability, run_step_telemetry
from scripts.verify_cross_os import cmd_compare, run_multiprocess_lock_check


def test_portability_scanner_clean_repo_and_defect_detection(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    # Current VieForge repo must have ZERO static portability violations
    assert _check_cross_platform_portability(repo_root) == []

    # Synthetic repo containing all 3 Windows/POSIX portability bugs must be caught
    bad_core = tmp_path / "core"
    bad_core.mkdir()
    (bad_core / "leaky.py").write_text(
        'import msvcrt\n'
        'from pathlib import Path\n'
        'LEAK = "C:\\\\Users\\\\Admin\\\\secret.txt"\n'
        'def load(p: Path) -> str:\n'
        '    return p.read_text()\n',
        encoding="utf-8",
    )
    issues = _check_cross_platform_portability(tmp_path)
    assert any("hardcoded personal Windows path" in i for i in issues)
    assert any("unguarded OS-specific import 'msvcrt'" in i for i in issues)
    assert any("missing encoding='utf-8'" in i for i in issues)


def test_two_axis_step_telemetry_and_core_gate_cap(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parent.parent
    tel = run_step_telemetry(repo_root, "two_axis_unit", run_tests=False)
    assert tel.passed is True
    assert tel.core_gate_passed is True
    assert all(tel.core_contracts.values())
    assert tel.perf_metrics["session_overhead_tokens"] <= 1500
    assert tel.perf_metrics["statedb_1000_ms"] > 0
    assert tel.perf_metrics["trimmer_10k_saved_pct"] >= 90.0
    assert tel.improvement_score >= 90.0

    # Synthetic repo with an unguarded msvcrt import MUST fail Core Gate and cap local_score <= 60
    bad_core = tmp_path / "core"
    bad_core.mkdir()
    (bad_core / "bad_os.py").write_text("import msvcrt\n", encoding="utf-8")
    bad_tel = run_step_telemetry(tmp_path, "two_axis_fail", run_tests=False)
    assert bad_tel.passed is False
    assert bad_tel.core_gate_passed is False
    assert bad_tel.core_contracts["C4_cross_os_portability"] is False
    assert bad_tel.local_score <= 60.0


def test_multiprocess_os_lock_and_cross_os_parity_comparator(tmp_path: Path) -> None:
    mp = run_multiprocess_lock_check()
    assert mp["passed"] is True
    assert mp["actual_count"] == 30

    def _make_report(label: str, net_mult: float = 16.9, passed: bool = True) -> Path:
        p = tmp_path / f"os_report_{label}.json"
        data = {
            "os_label": label,
            "python_version": "3.12.0",
            "overall_passed": passed,
            "telemetry": {
                "core_gate_passed": passed,
                "improvement_score": 100.0,
                "perf_metrics": {"statedb_1000_ms": 42.0, "trimmer_10k_lines_ms": 12.0},
            },
            "multiprocess_lock": {"passed": True, "lock_backend": "fcntl" if "ubuntu" in label else "msvcrt"},
            "clean_wheel_e2e": {
                "passed": passed,
                "benchmark": {
                    "overhead_tokens": 1459,
                    "without_tokens": 52732,
                    "with_tokens": 3114,
                    "net_multiplier": net_mult,
                    "net_reduction_pct": 94.1,
                },
            },
        }
        p.write_text(json.dumps(data), encoding="utf-8")
        return p

    r_lin = _make_report("ubuntu-py3.12")
    r_mac = _make_report("macos-py3.12")
    r_win = _make_report("windows-py3.12")
    md_out = tmp_path / "scorecard.md"

    rc_ok = cmd_compare(argparse.Namespace(reports=[str(r_lin), str(r_mac), str(r_win)], markdown_out=str(md_out)))
    assert rc_ok == 0
    assert "Parity Gate: PASSED" in md_out.read_text(encoding="utf-8")

    # Divergent OS benchmark MUST fail parity gate!
    r_bad = _make_report("macos-divergent", net_mult=12.1)
    rc_bad = cmd_compare(argparse.Namespace(reports=[str(r_lin), str(r_bad)], markdown_out=""))
    assert rc_bad == 1
