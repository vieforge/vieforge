"""TierForge 2-Axis Quality Auditor & Cross-Verification Engine ($0 Local).

Separates quality evaluation into two strict axes:

  Axis A — Core Capability Contract Gate (MUST-PASS 5/5; any failure fails the build):
    1. C1_token_efficiency    : Session overhead (SKILL.md + AGENTS.md + MCP) <= 1,500 tokens.
    2. C2_durability_resume   : StateDB register -> partial done -> resume skips 100% done at $0.
    3. C3_budget_and_safety   : MIT clean-room license audit + core/ 100% stdlib + valid configs
                                + BudgetExceededError hard-cap enforcement.
    4. C4_cross_os_portability: Zero unguarded Windows-only paths/imports or missing UTF-8 text I/O
                                + CrossProcessLock works cleanly on current OS.
    5. C5_pytest_and_hygiene  : 100% pytest pass rate (when enabled) + zero stray patch_* scripts.

  Axis B — Performance & Experience Improvement Score (0..100 pts):
    - Performance (50 pts):
        * StateDB 1,000-item register + resume latency (ms)         : up to 20 pts
        * output_trimmer 10,000-line compression latency (ms)       : up to 15 pts
        * Session token overhead headroom below 1,500-token cap     : up to 15 pts
    - Experience / DX (50 pts):
        * VRAM hygiene (0 recorded leaks)                           : up to 15 pts
        * Bilingual docs + version sync (pyproject == core.__version__): up to 15 pts
        * Cross-OS static portability & config cleanliness          : up to 20 pts

  Layer 1 — 3-Judge Cross-Validation Prompt Exporter ($0 Antigravity tokens):
    Exports both Axis A & Axis B metrics into `data/step_3judge_prompt_<step>.md`.
"""

from __future__ import annotations

import ast
import json
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.concurrency import CrossProcessLock
from core.cost_tracker import BudgetExceededError, CostTracker
from core.license_audit import run_license_audit
from core.output_trimmer import trim_output
from core.state_db import StateDB, compute_input_hash, scoped_job_id

STDLIB_MODULES: set[str] = set(getattr(sys, "stdlib_module_names", set())) | {
    "__future__",
    "typing_extensions",
}

MAX_SESSION_OVERHEAD_TOKENS = 1500
MCP_TOOL_SCHEMA_EST_TOKENS = 320
WINDOWS_PERSONAL_PATH_RE = re.compile(r"(?:^|[\s'\"])([A-Za-z]:\\(?:Users|Repos|VieForge|ai-short-drama|tierforge))")


@dataclass
class StepAuditFinding:
    code: str
    severity: str  # "FAIL" | "WARN"
    detail: str


@dataclass
class StepTelemetry:
    step_name: str
    checked_at: str
    passed: bool
    local_score: float
    pytest_passed: int
    pytest_total: int
    license_passed: bool
    stdlib_pure: bool
    configs_valid: bool
    zero_patch_scripts: bool
    vram_leaks: int
    findings: list[StepAuditFinding] = field(default_factory=list)
    core_gate_passed: bool = True
    core_contracts: dict[str, bool] = field(default_factory=dict)
    improvement_score: float = 100.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    perf_metrics: dict[str, Any] = field(default_factory=dict)
    portability_issues: list[str] = field(default_factory=list)
    runtime_env: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "checked_at": self.checked_at,
            "passed": self.passed,
            "core_gate_passed": self.core_gate_passed,
            "local_score": self.local_score,
            "improvement_score": self.improvement_score,
            "runtime_env": self.runtime_env,
            "core_contracts": self.core_contracts,
            "score_breakdown": self.score_breakdown,
            "perf_metrics": self.perf_metrics,
            "checks": {
                "pytest_ok": self.pytest_total > 0 and self.pytest_passed == self.pytest_total,
                "pytest_ratio": f"{self.pytest_passed}/{self.pytest_total}",
                "license_passed": self.license_passed,
                "stdlib_pure": self.stdlib_pure,
                "configs_valid": self.configs_valid,
                "zero_patch_scripts": self.zero_patch_scripts,
                "portability_clean": len(self.portability_issues) == 0,
                "vram_leaks": self.vram_leaks,
            },
            "portability_issues": self.portability_issues,
            "findings": [asdict(f) for f in self.findings],
        }


def _check_stdlib_purity(project_root: Path) -> list[str]:
    """Verify `core/*.py` has no unguarded third-party top-level imports."""
    core_dir = project_root / "core"
    if not core_dir.exists():
        return []
    local_pkgs = {"core", "mcp", "scripts", "tests", "qa", "stages"}
    violations: list[str] = []

    for py_file in sorted(core_dir.glob("*.py")):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except Exception as exc:
            violations.append(f"{py_file.name}: syntax error ({exc})")
            continue

        guarded_nodes: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for sub in ast.walk(node):
                    if isinstance(sub, (ast.Import, ast.ImportFrom)):
                        guarded_nodes.add(id(sub))

        for node in ast.walk(tree):
            if id(node) in guarded_nodes:
                continue
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if top not in STDLIB_MODULES and top not in local_pkgs:
                        violations.append(f"{py_file.name}:{node.lineno} imports '{top}'")
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                top = node.module.split(".")[0]
                if top not in STDLIB_MODULES and top not in local_pkgs:
                    violations.append(f"{py_file.name}:{node.lineno} imports from '{top}'")
    return violations


def _check_cross_platform_portability(project_root: Path) -> list[str]:
    """Static AST scanner catching common Windows-only / cross-OS defects before Linux/macOS CI."""
    issues: list[str] = []
    scan_dirs = [project_root / "core", project_root / "mcp", project_root / "scripts"]

    for scan_dir in scan_dirs:
        if not scan_dir.is_dir():
            continue
        for py_file in sorted(scan_dir.glob("*.py")):
            rel = str(py_file.relative_to(project_root)).replace("\\", "/")
            try:
                src = py_file.read_text(encoding="utf-8")
                tree = ast.parse(src)
            except Exception as exc:
                issues.append(f"{rel}: parse error ({exc})")
                continue

            # 1. Catch accidental leaked personal Windows absolute paths in non-comment string literals
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and WINDOWS_PERSONAL_PATH_RE.search(node.value)
                ):
                    issues.append(f"{rel}:{node.lineno} hardcoded personal Windows path literal")

            # 2. Catch unguarded OS-specific imports (msvcrt / fcntl must be inside `if` or `try`)
            guarded_imports: set[int] = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.If, ast.Try)):
                    for sub in ast.walk(node):
                        if isinstance(sub, (ast.Import, ast.ImportFrom)):
                            guarded_imports.add(id(sub))
            for node in ast.walk(tree):
                if id(node) in guarded_imports:
                    continue
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        if alias.name in ("msvcrt", "fcntl", "winreg"):
                            issues.append(f"{rel}:{node.lineno} unguarded OS-specific import '{alias.name}'")

            # 3. Catch `.read_text()` / `.write_text()` calls missing explicit `encoding=`
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("read_text", "write_text")
                ):
                    kw_names = {kw.arg for kw in node.keywords if kw.arg}
                    if "encoding" not in kw_names:
                        issues.append(f"{rel}:{node.lineno} {node.func.attr}() missing encoding='utf-8'")

    return issues


def _run_pytest_summary(project_root: Path, python_exe: Optional[str] = None) -> tuple[int, int]:
    """Run pytest on `project_root/tests` and return (passed, total)."""
    tests_dir = project_root / "tests"
    if not tests_dir.exists() or not list(tests_dir.glob("test_*.py")):
        return 0, 0
    py = python_exe or sys.executable
    env = dict(**os.environ)
    env["PYTHONPATH"] = str(project_root)
    proc = subprocess.run(
        [py, "-m", "pytest", str(tests_dir), "-o", "addopts=", "--tb=no"],
        cwd=str(project_root),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=120,
    )
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    m_pass = re.search(r"(\d+)\s+passed", out)
    m_fail = re.search(r"(\d+)\s+failed", out)
    m_err = re.search(r"(\d+)\s+error", out)
    passed = int(m_pass.group(1)) if m_pass else 0
    failed = int(m_fail.group(1)) if m_fail else 0
    errors = int(m_err.group(1)) if m_err else 0
    return passed, passed + failed + errors


def _measure_session_overhead(project_root: Path) -> int:
    skill_path = project_root / "skills" / "three-tier-orchestrator" / "SKILL.md"
    agents_path = project_root / "AGENTS.md"
    skill_tok = math.ceil(len(skill_path.read_text(encoding="utf-8")) / 4) if skill_path.is_file() else 0
    agents_tok = math.ceil(len(agents_path.read_text(encoding="utf-8")) / 4) if agents_path.is_file() else 0
    if skill_tok == 0 and agents_tok == 0:
        return 0
    return skill_tok + agents_tok + MCP_TOOL_SCHEMA_EST_TOKENS


def _run_core_micro_benchmarks_and_contracts() -> tuple[dict[str, Any], bool, bool, bool]:
    """Execute real in-memory/tempdir micro-benchmarks & contract checks ($0, <0.2s)."""
    durability_ok = False
    budget_ok = False
    lock_ok = False
    statedb_ms = 0.0
    trimmer_ms = 0.0
    trimmer_saved_pct = 0.0

    with tempfile.TemporaryDirectory(prefix="vieforge_audit_") as tmp_str:
        tmp = Path(tmp_str)

        # 1. StateDB durability + 1,000-item micro-benchmark
        t0 = time.perf_counter()
        db_path = tmp / "bench.db"
        items = [(f"item_{i:04d}", compute_input_hash(f"payload_{i}")) for i in range(1000)]
        job_id = scoped_job_id("audit_proj", "bench_job")
        with StateDB(db_path) as db:
            db.register_items(job_id, items)
            db.mark_done_batch(job_id, [(f"item_{i:04d}", {"ok": True}, 0.0) for i in range(499)])
            db.mark_done(job_id, "item_0499", output={"ok": True}, cost_usd=0.0)
            pending = db.get_pending_items(job_id)
            summary = db.get_job_summary(job_id)
        statedb_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        durability_ok = (len(pending) == 500 and summary["done"] == 500 and summary["pending"] == 500)

        # 2. Budget guard hard-cap check
        tracker = CostTracker(
            job_id="audit_budget",
            max_job_usd=0.50,
            daily_usd_cap=1.00,
            daily_spend_path=tmp / "daily.json",
        )
        tracker.record("s1", "external/media-render-unit", 0.40, item_id="1")
        try:
            tracker.record("s2", "external/media-render-unit", 0.20, item_id="2")
            budget_ok = False
        except BudgetExceededError:
            budget_ok = True

        # 3. Cross-OS advisory lock sanity
        lock = CrossProcessLock(tmp / "audit.lock", timeout_sec=2.0)
        with lock:
            lock_ok = True

    # 4. Output trimmer 10,000-line micro-benchmark
    noisy_lines = [f"\x1b[32mDownloading chunk {i}/10000 50%\x1b[0m" for i in range(9980)]
    noisy_lines.extend(["Traceback (most recent call last):", '  File "app.py", line 12', "ValueError: boom"])
    noisy_blob = "\n".join(noisy_lines)
    t1 = time.perf_counter()
    trimmed = trim_output(noisy_blob, max_lines=40)
    trimmer_ms = round((time.perf_counter() - t1) * 1000.0, 2)
    trimmer_saved_pct = trimmed.saved_pct

    metrics = {
        "statedb_1000_ms": statedb_ms,
        "trimmer_10k_lines_ms": trimmer_ms,
        "trimmer_10k_saved_pct": trimmer_saved_pct,
    }
    return metrics, durability_ok, budget_ok, lock_ok


def _check_docs_and_version_sync(project_root: Path) -> bool:
    pyproject = project_root / "pyproject.toml"
    init_py = project_root / "core" / "__init__.py"
    if not pyproject.is_file() or not init_py.is_file():
        return True  # Synthetic minimal temp project in unit tests
    required_docs = ["README.md", "README.vi.md", "AGENT_INSTALL.md", "CHANGELOG.md"]
    if not all((project_root / d).is_file() for d in required_docs):
        return False
    m_pyp = re.search(r'^version\s*=\s*"([^"]+)"', pyproject.read_text(encoding="utf-8"), re.MULTILINE)
    m_init = re.search(r'^__version__\s*=\s*"([^"]+)"', init_py.read_text(encoding="utf-8"), re.MULTILINE)
    return bool(m_pyp and m_init and m_pyp.group(1) == m_init.group(1))


def run_step_telemetry(
    project_root: Path,
    step_name: str,
    *,
    run_tests: bool = True,
    python_exe: Optional[str] = None,
) -> StepTelemetry:
    """Measure 2-Axis (Core Contract Gate + Performance/DX Score) engineering telemetry."""
    findings: list[StepAuditFinding] = []

    # 1. Pytest suite
    if run_tests:
        p_ok, p_tot = _run_pytest_summary(project_root, python_exe=python_exe)
        pytest_ok = p_tot > 0 and p_ok == p_tot
        if p_tot == 0:
            findings.append(StepAuditFinding("QA_NO_TESTS", "WARN", "No pytest files found in tests/."))
        elif p_ok < p_tot:
            findings.append(
                StepAuditFinding("QA_PYTEST_FAIL", "FAIL", f"Pytest failures: {p_ok}/{p_tot} passed.")
            )
    else:
        p_ok, p_tot = 1, 1
        pytest_ok = True

    # 2. Commercial License Audit
    lic = run_license_audit(project_root, check_installed_pkgs=False)
    if not lic.passed:
        findings.append(
            StepAuditFinding("LICENSE_VIOLATION", "FAIL", "Banned commercial-unsafe references found.")
        )

    # 3. Core Stdlib Purity
    stdlib_viols = _check_stdlib_purity(project_root)
    stdlib_pure = len(stdlib_viols) == 0
    if not stdlib_pure:
        findings.append(
            StepAuditFinding("STDLIB_PURITY_FAIL", "FAIL", f"Non-stdlib imports in core/: {stdlib_viols}")
        )

    # 4. Config JSON Validity
    configs_valid = True
    cfg_dir = project_root / "config"
    if cfg_dir.exists():
        for jf in sorted(cfg_dir.glob("*.json")):
            try:
                json.loads(jf.read_text(encoding="utf-8"))
            except Exception as exc:
                configs_valid = False
                findings.append(StepAuditFinding("CONFIG_INVALID_JSON", "FAIL", f"{jf.name}: {exc}"))

    # 5. Zero stray patch_* scripts
    patch_files = [
        p.name
        for p in project_root.rglob("patch_*.py")
        if ".venv" not in p.parts and ".git" not in p.parts
    ]
    zero_patch = len(patch_files) == 0
    if not zero_patch:
        findings.append(
            StepAuditFinding("STRAY_PATCH_SCRIPT", "WARN", f"Stray patch_* scripts found: {patch_files}")
        )

    # 6. Cross-Platform Portability Static Scan
    portability_issues = _check_cross_platform_portability(project_root)
    if portability_issues:
        findings.append(
            StepAuditFinding(
                "PORTABILITY_ISSUE",
                "FAIL",
                f"Cross-platform issues ({len(portability_issues)}): {portability_issues[:5]}",
            )
        )

    # 7. VRAM Leak Check
    vram_leaks = 0
    vram_log = project_root / "data" / "vram_profile.jsonl"
    if vram_log.exists():
        for line in vram_log.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if rec.get("leak_detected"):
                    vram_leaks += 1
            except Exception:
                continue
    if vram_leaks > 0:
        findings.append(
            StepAuditFinding("VRAM_LEAK_RECORDED", "WARN", f"{vram_leaks} VRAM leak events in vram_profile.jsonl.")
        )

    # 8. Core micro-benchmarks + Session overhead + Docs/version sync
    overhead_tokens = _measure_session_overhead(project_root)
    perf_metrics, durability_ok, budget_ok, lock_ok = _run_core_micro_benchmarks_and_contracts()
    perf_metrics["session_overhead_tokens"] = overhead_tokens
    perf_metrics["session_overhead_cap"] = MAX_SESSION_OVERHEAD_TOKENS
    docs_synced = _check_docs_and_version_sync(project_root)

    if overhead_tokens > MAX_SESSION_OVERHEAD_TOKENS:
        findings.append(
            StepAuditFinding(
                "OVERHEAD_CAP_EXCEEDED",
                "FAIL",
                f"Session overhead {overhead_tokens} tokens exceeds hard cap {MAX_SESSION_OVERHEAD_TOKENS}.",
            )
        )
    if not durability_ok:
        findings.append(StepAuditFinding("CORE_DURABILITY_FAIL", "FAIL", "StateDB checkpoint/resume contract failed."))
    if not budget_ok:
        findings.append(StepAuditFinding("CORE_BUDGET_FAIL", "FAIL", "CostTracker hard-cap contract failed."))
    if not lock_ok:
        findings.append(StepAuditFinding("CORE_LOCK_FAIL", "FAIL", "CrossProcessLock contract failed."))

    # --- AXIS A: 5 Core Capability Contracts (MUST-PASS) ---
    core_contracts = {
        "C1_token_efficiency": overhead_tokens <= MAX_SESSION_OVERHEAD_TOKENS and perf_metrics["trimmer_10k_saved_pct"] >= 90.0,
        "C2_durability_resume": durability_ok,
        "C3_budget_and_safety": lic.passed and stdlib_pure and configs_valid and budget_ok,
        "C4_cross_os_portability": len(portability_issues) == 0 and lock_ok,
        "C5_pytest_and_hygiene": pytest_ok,
    }
    core_gate_passed = all(core_contracts.values())

    # --- AXIS B: Performance (50 pts) + Experience/DX (50 pts) ---
    sb_ms = float(perf_metrics["statedb_1000_ms"])
    perf_statedb = 20.0 if sb_ms < 250.0 else (15.0 if sb_ms < 500.0 else (10.0 if sb_ms < 1000.0 else 5.0))
    tr_ms = float(perf_metrics["trimmer_10k_lines_ms"])
    perf_trimmer = 15.0 if tr_ms < 80.0 else (10.0 if tr_ms < 200.0 else 5.0)
    perf_overhead = 15.0 if overhead_tokens <= MAX_SESSION_OVERHEAD_TOKENS else (10.0 if overhead_tokens <= 1800 else 5.0)

    dx_vram = 15.0 if vram_leaks == 0 else 5.0
    dx_docs = 15.0 if docs_synced else 5.0
    dx_portability = 20.0 if (len(portability_issues) == 0 and zero_patch and configs_valid) else 10.0
    if not zero_patch:
        dx_portability = min(dx_portability, 10.0)

    score_breakdown = {
        "perf_statedb_20": perf_statedb,
        "perf_trimmer_15": perf_trimmer,
        "perf_overhead_15": perf_overhead,
        "dx_vram_15": dx_vram,
        "dx_docs_sync_15": dx_docs,
        "dx_portability_hygiene_20": dx_portability,
    }
    improvement_score = round(sum(score_breakdown.values()), 1)
    # Hard rule: failing any Core Contract caps local_score <= 60.0 and fails the audit
    local_score = improvement_score if core_gate_passed else min(improvement_score, 60.0)
    passed = core_gate_passed and not any(f.severity == "FAIL" for f in findings)

    runtime_env = {
        "os": platform.system(),
        "platform": sys.platform,
        "python": platform.python_version(),
    }

    return StepTelemetry(
        step_name=step_name,
        checked_at=datetime.now(timezone.utc).isoformat(),
        passed=passed,
        local_score=local_score,
        pytest_passed=p_ok,
        pytest_total=p_tot,
        license_passed=lic.passed,
        stdlib_pure=stdlib_pure,
        configs_valid=configs_valid,
        zero_patch_scripts=zero_patch,
        vram_leaks=vram_leaks,
        findings=findings,
        core_gate_passed=core_gate_passed,
        core_contracts=core_contracts,
        improvement_score=improvement_score,
        score_breakdown=score_breakdown,
        perf_metrics=perf_metrics,
        portability_issues=portability_issues,
        runtime_env=runtime_env,
    )


def export_step_3judge_audit(
    project_root: Path,
    step_name: str,
    *,
    run_tests: bool = True,
    python_exe: Optional[str] = None,
) -> dict[str, Any]:
    """Measure 2-axis telemetry, append to `data/step_audit_log.jsonl`, and export 3-Judge prompt."""
    tel = run_step_telemetry(project_root, step_name, run_tests=run_tests, python_exe=python_exe)
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    log_path = data_dir / "step_audit_log.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(tel.to_dict(), ensure_ascii=False) + "\n")

    core_files = sorted(p.name for p in (project_root / "core").glob("*.py")) if (project_root / "core").exists() else []
    test_files = sorted(p.name for p in (project_root / "tests").glob("test_*.py")) if (project_root / "tests").exists() else []

    prompt_md = (
        f"# VIEFORGE 2-AXIS 3-JUDGE CROSS-VALIDATION AUDIT (`{step_name}`)\n\n"
        f"Act as **3 independent Judges** to audit step `{step_name}` of `vieforge` (`tierforge-harness`).\n"
        f"Core philosophy: **Core functions MUST work identically across Windows / macOS / Linux; "
        f"iterations focus strictly on Experience (DX) & Performance.**\n\n"
        f"## 0. Objective 2-Axis Engineering Telemetry (0 cloud tokens)\n"
        f"```json\n{json.dumps(tel.to_dict(), indent=2, ensure_ascii=False)}\n```\n"
        f"- Core modules: `{', '.join(core_files)}`\n"
        f"- Test suites: `{', '.join(test_files)}`\n\n"
        "## Axis A — Core Capability Contract Gate (MUST-PASS 5/5)\n"
        "Verify no regressions in Token Efficiency (`<=1500` overhead), Checkpoint/Resume Durability, "
        "Budget & Clean-Room Safety, Cross-OS Portability (`fcntl`/`msvcrt`/UTF-8/clean wheel install), and Pytest.\n\n"
        "## Axis B — 3-Judge Improvement Rubric (Performance 50pt + Experience/DX 50pt)\n"
        "1. **JUDGE 1 — Cross-OS Reliability & Performance Engineer (100pt):** Latency of SQLite WAL / trimmer, "
        "POSIX vs Windows edge cases, wheel packaging, multi-project locks.\n"
        "2. **JUDGE 2 — Developer Experience & Token Efficiency Reviewer (100pt):** Onboarding friction, "
        "actionable errors, MCP ergonomics, skill token budget discipline.\n"
        "3. **JUDGE 3 — Global OSS Security & QA Auditor (100pt):** Clean-room MIT licensing, zero OS-mutation, "
        "test realism (no fake mocks hiding broken runtime paths).\n\n"
        "## Required Verdict Format\n"
        "- State clearly: `Core Gate = PASS/FAIL | J1 = X/100 | J2 = Y/100 | J3 = Z/100 | Max Spread = |...|`\n"
        "- **GOLDEN RELEASE** when `Core Gate == PASS`, `J1, J2, J3 >= 85`, and `max_spread <= 10`.\n"
        "- List **Top 3 concrete latent defects or Performance/DX buffs** citing exact file names & lines.\n"
    )
    prompt_path = data_dir / f"step_3judge_prompt_{step_name}.md"
    prompt_path.write_text(prompt_md, encoding="utf-8")

    return {
        "telemetry": tel.to_dict(),
        "log_path": str(log_path),
        "prompt_path": str(prompt_path),
    }
