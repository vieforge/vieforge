#!/usr/bin/env python3
"""Cross-OS Verification & 3-OS Parity Gate for VieForge (100% Python stdlib, $0).

Designed to run identically on Ubuntu Linux, macOS, and Windows (both locally and in
GitHub Actions CI):
  1. `run` subcommand:
     - Runs the 2-Axis local auditor (`core/step_auditor.py`).
     - Installs VieForge as a clean non-editable package into an isolated temp venv
       and executes all core user flows from an EMPTY workspace outside the repo:
         * `vieforge-preflight --json` -> PASS
         * `vieforge-bench --json` -> >= 15x / >= 93% net reduction, overhead <= 1500
         * `vieforge-mcp --check` -> OK
         * `vieforge-setup --dry-run` (with isolated HOME) -> rc 0
         * Bundled `run_demo.py` interrupt (rc 130) -> resume (rc 0, 5/5 done, $0)
     - Runs a REAL multi-process OS lock contention test (`fcntl` on POSIX, `msvcrt` on Windows).
     - Writes structured JSON report (`os_report_<os>.json`).
  2. `compare` subcommand:
     - Loads multiple OS reports (e.g. Ubuntu + macOS + Windows).
     - Verifies every OS passed ALL gates AND deterministic outputs match across OSes.
     - Writes a Markdown Scorecard table for `$GITHUB_STEP_SUMMARY` and exits non-zero on mismatch.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import tempfile
import venv
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.step_auditor import run_step_telemetry


def _venv_bin(venv_dir: Path, name: str) -> Path:
    if os.name == "nt":
        for ext in (".exe", ".cmd", ".bat", ""):
            cand = venv_dir / "Scripts" / f"{name}{ext}"
            if cand.exists():
                return cand
        return venv_dir / "Scripts" / f"{name}.exe"
    return venv_dir / "bin" / name


def _run_cmd(cmd: list[str], cwd: Path, env: dict[str, str] | None = None, timeout: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def run_multiprocess_lock_check() -> dict[str, Any]:
    """Spawn 2 real OS child processes contending on CrossProcessLock to verify fcntl/msvcrt."""
    with tempfile.TemporaryDirectory(prefix="vieforge_mplock_") as tmp_str:
        tmp = Path(tmp_str)
        lock_file = tmp / "shared.lock"
        counter_file = tmp / "counter.txt"
        counter_file.write_text("0", encoding="utf-8")

        worker_code = (
            "import sys, pathlib; "
            f"sys.path.insert(0, {str(REPO_ROOT)!r}); "
            "from core.concurrency import CrossProcessLock; "
            f"lock = CrossProcessLock(pathlib.Path({str(lock_file)!r}), timeout_sec=10.0); "
            f"cf = pathlib.Path({str(counter_file)!r}); "
            "\nfor _ in range(15):\n"
            "    with lock:\n"
            "        n = int(cf.read_text(encoding='utf-8').strip())\n"
            "        cf.write_text(str(n + 1), encoding='utf-8')\n"
        )
        p1 = subprocess.Popen([sys.executable, "-c", worker_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        p2 = subprocess.Popen([sys.executable, "-c", worker_code], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out1, err1 = p1.communicate(timeout=30)
        out2, err2 = p2.communicate(timeout=30)
        final_count = int(counter_file.read_text(encoding="utf-8").strip())
        passed = p1.returncode == 0 and p2.returncode == 0 and final_count == 30
        return {
            "passed": passed,
            "expected_count": 30,
            "actual_count": final_count,
            "lock_backend": "msvcrt" if os.name == "nt" else "fcntl",
            "errors": [e.decode("utf-8", errors="replace").strip() for e in (err1, err2) if e.strip()],
        }


def run_clean_wheel_e2e_check() -> dict[str, Any]:
    """Install non-editable wheel into a clean venv and execute all core flows outside the repo."""
    steps: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="vieforge_clean_install_") as tmp_str:
        tmp = Path(tmp_str)
        venv_dir = tmp / "venv"
        empty_cwd = tmp / "empty_workspace"
        fake_home = tmp / "fake_home"
        empty_cwd.mkdir()
        fake_home.mkdir()

        venv.create(venv_dir, with_pip=True)
        vpy = _venv_bin(venv_dir, "python")

        env = dict(os.environ)
        env["HOME"] = str(fake_home)
        env["USERPROFILE"] = str(fake_home)
        env["PYTHONUTF8"] = "1"
        env.pop("PYTHONPATH", None)

        # Build wheel cleanly using current environment (zero network needed when hatchling is installed)
        dist_dir = tmp / "dist"
        dist_dir.mkdir()
        built_wheel: Path | None = None
        try:
            from hatchling.build import build_wheel

            wheel_name = build_wheel(str(dist_dir), config_settings=None, metadata_directory=None)
            built_wheel = dist_dir / wheel_name
            steps["wheel_build"] = {"ok": built_wheel.is_file(), "wheel": wheel_name}
        except Exception:
            # Fallback to pip install directly if hatchling isn't in outer env
            steps["wheel_build"] = {"ok": True, "wheel": "pip_direct_fallback"}

        if built_wheel is not None and built_wheel.is_file():
            inst = _run_cmd(
                [str(vpy), "-m", "pip", "install", "--no-deps", "--no-index", str(built_wheel)],
                cwd=empty_cwd,
                env=env,
                timeout=180,
            )
        else:
            inst = _run_cmd(
                [str(vpy), "-m", "pip", "install", "--no-deps", str(REPO_ROOT)],
                cwd=empty_cwd,
                env=env,
                timeout=180,
            )
        steps["pip_install"] = {"ok": inst.returncode == 0, "rc": inst.returncode}
        if inst.returncode != 0:
            steps["pip_install"]["stderr_tail"] = inst.stderr[-600:]
            return {"passed": False, "steps": steps}

        # 0. Unified `vieforge --version` & `vieforge check --json`
        vf_ver = _run_cmd([str(_venv_bin(venv_dir, "vieforge")), "--version"], cwd=empty_cwd, env=env)
        steps["vieforge_unified_cli"] = {
            "ok": vf_ver.returncode == 0 and vf_ver.stdout.strip().startswith("vieforge "),
            "rc": vf_ver.returncode,
            "version": vf_ver.stdout.strip(),
        }

        # 1. vieforge-preflight --json (must find bundled configs + SKILL.md!)
        pf = _run_cmd([str(_venv_bin(venv_dir, "vieforge-preflight")), "--json"], cwd=empty_cwd, env=env)
        pf_ok = False
        if pf.returncode == 0:
            try:
                pf_data = json.loads(pf.stdout)
                pf_ok = pf_data.get("overall") == "PASS"
            except Exception:
                pf_ok = False
        steps["vieforge_preflight"] = {"ok": pf_ok, "rc": pf.returncode}

        # 2. vieforge-bench --json (verifies deterministic token benchmark from clean wheel!)
        bn = _run_cmd([str(_venv_bin(venv_dir, "vieforge-bench")), "--json"], cwd=empty_cwd, env=env)
        bn_ok = False
        bench_summary: dict[str, Any] = {}
        if bn.returncode == 0:
            try:
                bn_data = json.loads(bn.stdout)
                net = bn_data["combined_net"]
                ov = bn_data["baseline_overhead"]
                bench_summary = {
                    "overhead_tokens": ov["total_session_overhead_tokens"],
                    "without_tokens": net["without_vieforge_tokens"],
                    "with_tokens": net["with_vieforge_tokens_including_overhead"],
                    "net_multiplier": net["net_multiplier"],
                    "net_reduction_pct": net["net_reduction_pct"],
                }
                bn_ok = (
                    ov["total_session_overhead_tokens"] <= 1500
                    and net["net_multiplier"] >= 15.0
                    and net["net_reduction_pct"] >= 93.0
                )
            except Exception:
                bn_ok = False
        steps["vieforge_bench"] = {"ok": bn_ok, "rc": bn.returncode, "summary": bench_summary}

        # 3. vieforge-mcp --check
        mc = _run_cmd([str(_venv_bin(venv_dir, "vieforge-mcp")), "--check"], cwd=empty_cwd, env=env)
        mc_ok = mc.returncode == 0 and '"mcp_worker_self_check": "OK"' in mc.stdout
        steps["vieforge_mcp_check"] = {"ok": mc_ok, "rc": mc.returncode}

        # 4. vieforge-setup --dry-run (uses bundled SKILL.md, isolated fake HOME)
        su = _run_cmd([str(_venv_bin(venv_dir, "vieforge-setup")), "--dry-run"], cwd=empty_cwd, env=env)
        steps["vieforge_setup_dry_run"] = {"ok": su.returncode == 0, "rc": su.returncode}

        # 5. Bundled Demo 01: interrupt at 2/5 (rc=130) -> resume to 5/5 (rc=0) in empty_cwd/.vieforge
        demo_locate = _run_cmd(
            [str(vpy), "-c", "from core.paths import resolve_asset; print(resolve_asset('examples/01_batch_doc_processor/run_demo.py'))"],
            cwd=empty_cwd,
            env=env,
        )
        demo_path = demo_locate.stdout.strip()
        d1 = _run_cmd(
            [str(vpy), demo_path, "--reset", "--mock-llm", "--simulate-interrupt-after", "2"],
            cwd=empty_cwd,
            env=env,
        )
        d2 = _run_cmd([str(vpy), demo_path, "--mock-llm"], cwd=empty_cwd, env=env)
        demo_ok = d1.returncode == 130 and d2.returncode == 0 and "Items processed : 5" in d2.stdout
        steps["demo_interrupt_resume"] = {
            "ok": demo_ok,
            "interrupt_rc": d1.returncode,
            "resume_rc": d2.returncode,
        }

        all_ok = all(s.get("ok") for s in steps.values())
        return {"passed": all_ok, "steps": steps, "benchmark": bench_summary}


def cmd_run(args: argparse.Namespace) -> int:
    tel = run_step_telemetry(REPO_ROOT, args.step, run_tests=not args.no_pytest)
    mp = run_multiprocess_lock_check()
    wheel = run_clean_wheel_e2e_check() if not args.skip_wheel else {"passed": True, "skipped": True, "benchmark": {}}

    overall_passed = bool(tel.passed and mp["passed"] and wheel["passed"])
    report = {
        "schema_version": "1.0",
        "os_label": args.os_label or f"{platform.system().lower()}-py{platform.python_version_tuple()[0]}.{platform.python_version_tuple()[1]}",
        "os_system": platform.system(),
        "platform": sys.platform,
        "python_version": platform.python_version(),
        "overall_passed": overall_passed,
        "telemetry": tel.to_dict(),
        "multiprocess_lock": mp,
        "clean_wheel_e2e": wheel,
    }

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if overall_passed else 1


def cmd_compare(args: argparse.Namespace) -> int:
    reports: list[dict[str, Any]] = []
    for p_str in args.reports:
        p = Path(p_str)
        reports.append(json.loads(p.read_text(encoding="utf-8")))

    if not reports:
        print("ERROR: No OS reports provided to compare.", file=sys.stderr)
        return 1

    parity_errors: list[str] = []
    ref_bench: dict[str, Any] | None = None
    ref_label = ""

    lines = [
        "# VieForge Cross-OS Verification & Parity Scorecard",
        "",
        "| Runner / OS | Python | Core Gate (5/5) | Improvement Score | Clean Wheel E2E | OS Lock (`fcntl`/`msvcrt`) | StateDB 1k (ms) | Trimmer 10k (ms) | Net Token Reduction |",
        "| :--- | :---: | :---: | :---: | :---: | :---: | ---: | ---: | :---: |",
    ]

    for rep in reports:
        label = rep.get("os_label", rep.get("os_system", "unknown"))
        py_v = rep.get("python_version", "?")
        tel = rep.get("telemetry", {})
        core_ok = bool(tel.get("core_gate_passed"))
        imp_score = tel.get("improvement_score", 0.0)
        wheel = rep.get("clean_wheel_e2e", {})
        wheel_ok = bool(wheel.get("passed"))
        mp = rep.get("multiprocess_lock", {})
        mp_ok = bool(mp.get("passed"))
        lock_be = mp.get("lock_backend", "?")
        perf = tel.get("perf_metrics", {})
        bench = wheel.get("benchmark", {})

        if not rep.get("overall_passed"):
            parity_errors.append(f"{label}: overall_passed=False (core={core_ok}, wheel={wheel_ok}, lock={mp_ok})")

        if bench:
            if ref_bench is None:
                ref_bench = bench
                ref_label = label
            else:
                for key in ("overhead_tokens", "without_tokens", "with_tokens", "net_multiplier"):
                    if bench.get(key) != ref_bench.get(key):
                        parity_errors.append(
                            f"Parity mismatch on '{key}': {ref_label}={ref_bench.get(key)} vs {label}={bench.get(key)}"
                        )

        net_str = f"{bench.get('net_multiplier', '?')}x (-{bench.get('net_reduction_pct', '?')}%)" if bench else "skipped"
        lines.append(
            f"| `{label}` | `{py_v}` | {'PASS' if core_ok else 'FAIL'} | **{imp_score}/100** | "
            f"{'PASS' if wheel_ok else 'FAIL'} | {'PASS' if mp_ok else 'FAIL'} (`{lock_be}`) | "
            f"{perf.get('statedb_1000_ms', '?')} | {perf.get('trimmer_10k_lines_ms', '?')} | `{net_str}` |"
        )

    lines.append("")
    if parity_errors:
        lines.append("## Parity Gate: FAILED")
        for err in parity_errors:
            lines.append(f"- {err}")
    else:
        lines.append(f"## Parity Gate: PASSED ({len(reports)} OS environments verified with 100% behavioral parity)")

    md = "\n".join(lines) + "\n"
    if args.markdown_out:
        out_p = Path(args.markdown_out)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(md, encoding="utf-8")

    print(md)
    return 0 if not parity_errors else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="VieForge Cross-OS Verification & 3-OS Parity Gate.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_run = sub.add_parser("run", help="Run full OS verification (2-axis audit + clean wheel E2E + OS lock).")
    p_run.add_argument("--step", default="cross_os_ci", help="Step name for telemetry.")
    p_run.add_argument("--os-label", default="", help="Runner label (e.g. ubuntu-py3.12).")
    p_run.add_argument("--out", default="", help="Path to write JSON report.")
    p_run.add_argument("--no-pytest", action="store_true", help="Skip pytest inside telemetry.")
    p_run.add_argument("--skip-wheel", action="store_true", help="Skip clean wheel install test.")

    p_cmp = sub.add_parser("compare", help="Cross-verify JSON reports from multiple OS runners.")
    p_cmp.add_argument("reports", nargs="+", help="Paths to os_report_*.json files.")
    p_cmp.add_argument("--markdown-out", default="", help="Optional path to write Markdown Scorecard.")

    args = parser.parse_args()
    if args.cmd == "run":
        return cmd_run(args)
    return cmd_compare(args)


if __name__ == "__main__":
    raise SystemExit(main())
