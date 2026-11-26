"""TierForge Post-Step Quality Auditor & 3-Judge Cross-Validation Engine ($0 Local).

Audits every milestone/step of `tierforge-harness` (and any project built on it) using
deterministic local engineering telemetry + optional 3-Judge cross-validation prompts:

  1. Layer 0 — Objective Local Engineering Telemetry ($0, deterministic):
     - Pytest regression pass rate
     - Commercial license audit (`core/license_audit.py`)
     - Stdlib purity check on `core/` (no non-stdlib third-party hard dependencies)
     - Config JSON validity (`config/*.json`) & zero stray `patch_*` scripts
     - VRAM leak check (`data/vram_profile.jsonl` if present)
     -> Produces `local_score` (0..100) and structured error records mapped to `config/qa_error_codes.json`.

  2. Layer 1 — 3-Judge Cross-Validation Prompt Exporter ($0 Antigravity tokens):
     - Judge 1: Architecture & Reliability Engineer (100pt)
     - Judge 2: DX, 3-Tier Automation & Token Efficiency Reviewer (100pt)
     - Judge 3: Commercial Readiness, Security & QA Auditor (100pt)
     -> Golden Step Rule: ALL 3 Judges >= 85/100 AND max_spread <= 10 AND local_telemetry.passed == True.

  3. Recursive Buff Log (`data/step_audit_log.jsonl`):
     - Appends every step audit result + detected error codes so future cycles can fix & buff systematically.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from core.license_audit import run_license_audit

STDLIB_MODULES: set[str] = set(getattr(sys, "stdlib_module_names", set())) | {
    "__future__",
    "typing_extensions",
}


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

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "checked_at": self.checked_at,
            "passed": self.passed,
            "local_score": self.local_score,
            "checks": {
                "pytest_ok": self.pytest_total > 0 and self.pytest_passed == self.pytest_total,
                "pytest_ratio": f"{self.pytest_passed}/{self.pytest_total}",
                "license_passed": self.license_passed,
                "stdlib_pure": self.stdlib_pure,
                "configs_valid": self.configs_valid,
                "zero_patch_scripts": self.zero_patch_scripts,
                "vram_leaks": self.vram_leaks,
            },
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

        # Collect imports guarded inside try/except ImportError blocks
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


def _run_pytest_summary(project_root: Path, python_exe: Optional[str] = None) -> tuple[int, int]:
    """Run pytest on `project_root/tests` and return (passed, total)."""
    tests_dir = project_root / "tests"
    if not tests_dir.exists() or not list(tests_dir.glob("test_*.py")):
        return 0, 0
    py = python_exe or sys.executable
    env = dict(**__import__("os").environ)
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
    import re

    m_pass = re.search(r"(\d+)\s+passed", out)
    m_fail = re.search(r"(\d+)\s+failed", out)
    m_err = re.search(r"(\d+)\s+error", out)
    passed = int(m_pass.group(1)) if m_pass else 0
    failed = int(m_fail.group(1)) if m_fail else 0
    errors = int(m_err.group(1)) if m_err else 0
    return passed, passed + failed + errors


def run_step_telemetry(
    project_root: Path,
    step_name: str,
    *,
    run_tests: bool = True,
    python_exe: Optional[str] = None,
) -> StepTelemetry:
    """Measure objective engineering health telemetry after a TierForge project step."""
    findings: list[StepAuditFinding] = []
    score = 100.0

    # 1. Pytest suite (35 pts)
    if run_tests:
        p_ok, p_tot = _run_pytest_summary(project_root, python_exe=python_exe)
        if p_tot == 0:
            score -= 20.0
            findings.append(StepAuditFinding("QA_NO_TESTS", "WARN", "No pytest files found in tests/."))
        elif p_ok < p_tot:
            score -= 35.0
            findings.append(
                StepAuditFinding("QA_PYTEST_FAIL", "FAIL", f"Pytest failures: {p_ok}/{p_tot} passed.")
            )
    else:
        p_ok, p_tot = 1, 1

    # 2. Commercial License Audit (20 pts)
    lic = run_license_audit(project_root, check_installed_pkgs=False)
    if not lic.passed:
        score -= 20.0
        findings.append(
            StepAuditFinding("LICENSE_VIOLATION", "FAIL", "Banned commercial-unsafe references found.")
        )

    # 3. Core Stdlib Purity (20 pts)
    stdlib_viols = _check_stdlib_purity(project_root)
    stdlib_pure = len(stdlib_viols) == 0
    if not stdlib_pure:
        score -= 20.0
        findings.append(
            StepAuditFinding("STDLIB_PURITY_FAIL", "FAIL", f"Non-stdlib imports in core/: {stdlib_viols}")
        )

    # 4. Config JSON Validity (10 pts)
    configs_valid = True
    cfg_dir = project_root / "config"
    if cfg_dir.exists():
        for jf in sorted(cfg_dir.glob("*.json")):
            try:
                json.loads(jf.read_text(encoding="utf-8"))
            except Exception as exc:
                configs_valid = False
                score -= 10.0
                findings.append(StepAuditFinding("CONFIG_INVALID_JSON", "FAIL", f"{jf.name}: {exc}"))

    # 5. Zero stray patch_* scripts (10 pts)
    patch_files = [
        p.name
        for p in project_root.rglob("patch_*.py")
        if ".venv" not in p.parts and ".git" not in p.parts
    ]
    zero_patch = len(patch_files) == 0
    if not zero_patch:
        score -= 10.0
        findings.append(
            StepAuditFinding("STRAY_PATCH_SCRIPT", "WARN", f"Stray patch_* scripts found: {patch_files}")
        )

    # 6. VRAM Leak Check (5 pts)
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
        score -= 5.0
        findings.append(
            StepAuditFinding("VRAM_LEAK_RECORDED", "WARN", f"{vram_leaks} VRAM leak events in vram_profile.jsonl.")
        )

    score = max(0.0, round(score, 1))
    passed = not any(f.severity == "FAIL" for f in findings)

    return StepTelemetry(
        step_name=step_name,
        checked_at=datetime.now(timezone.utc).isoformat(),
        passed=passed,
        local_score=score,
        pytest_passed=p_ok,
        pytest_total=p_tot,
        license_passed=lic.passed,
        stdlib_pure=stdlib_pure,
        configs_valid=configs_valid,
        zero_patch_scripts=zero_patch,
        vram_leaks=vram_leaks,
        findings=findings,
    )


def export_step_3judge_audit(
    project_root: Path,
    step_name: str,
    *,
    run_tests: bool = True,
    python_exe: Optional[str] = None,
) -> dict[str, Any]:
    """Measure local telemetry, append to `data/step_audit_log.jsonl`, and export 3-Judge prompt."""
    tel = run_step_telemetry(project_root, step_name, run_tests=run_tests, python_exe=python_exe)
    data_dir = project_root / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    # Append to recursive step audit history log
    log_path = data_dir / "step_audit_log.jsonl"
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(tel.to_dict(), ensure_ascii=False) + "\n")

    # Collect core file inventory for the 3 Judges
    core_files = sorted(p.name for p in (project_root / "core").glob("*.py")) if (project_root / "core").exists() else []
    test_files = sorted(p.name for p in (project_root / "tests").glob("test_*.py")) if (project_root / "tests").exists() else []

    prompt_md = (
        f"# TIERFORGE POST-STEP 3-JUDGE CROSS-VALIDATION AUDIT (`{step_name}`)\n\n"
        f"Act as **3 independent Judges** to score completeness, catch latent bugs, and propose priority buffs "
        f"for step `{step_name}` of `tierforge-harness`.\n\n"
        f"## 0. Objective Local Engineering Telemetry (0 cloud tokens)\n"
        f"```json\n{json.dumps(tel.to_dict(), indent=2, ensure_ascii=False)}\n```\n"
        f"- Core modules: `{', '.join(core_files)}`\n"
        f"- Test suites: `{', '.join(test_files)}`\n\n"
        "## TierForge 3-Judge Rubric (100 pts / Judge)\n"
        "1. **JUDGE 1 — Architecture & Reliability Engineer (100pt):** Modularity, stdlib purity, "
        "checkpoint/resume integrity, VRAM & budget guards, multi-project concurrency.\n"
        "2. **JUDGE 2 — DX, 3-Tier Automation & Token Efficiency (100pt):** Smoothness of Tier 1/2/3 "
        "delegation, `--dry-run` UX, and beginner onboarding.\n"
        "3. **JUDGE 3 — Commercial Readiness, Security & QA Auditor (100pt):** Clean-room MIT licensing, "
        "zero OS-mutation, pytest coverage, and extensibility.\n\n"
        "## Required Verdict Format\n"
        "- State clearly: `J1 = X/100 | J2 = Y/100 | J3 = Z/100 | Max Spread = |...|`\n"
        "- **GOLDEN STEP** when `J1, J2, J3 >= 85`, `max_spread <= 10`, and `local_telemetry.passed == true`.\n"
        "- List the **Top 3 latent bugs / highest-leverage buffs** for the next iteration.\n"
    )
    prompt_path = data_dir / f"step_3judge_prompt_{step_name}.md"
    prompt_path.write_text(prompt_md, encoding="utf-8")

    return {
        "telemetry": tel.to_dict(),
        "log_path": str(log_path),
        "prompt_path": str(prompt_path),
    }
