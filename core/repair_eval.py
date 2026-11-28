"""Offline 15-Case Self-Healing Eval Suite & Recurring Lesson Harvester ($0, 100% stdlib, MIT).

Measures VieForge's real debugging, fault localization, patch safety, approval-gate,
rollback, impact-guard, and sandbox capabilities in < 1 second at $0:
  - `run_repair_eval_suite()` : Executes 15 deterministic isolated scenarios in a tempdir
    and reports pass rate, localization token efficiency, and ErrorMemory recall latency.
  - `harvest_recurring_lessons(repo_roots)` : Scans project-local `.vieforge/error_memory.db`
    and optional `qa_feedback.jsonl` logs to surface anonymized recurring failure patterns
    for continuous rule/harness self-improvement.
"""

from __future__ import annotations

import json
import sys
import tempfile
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.error_memory import ErrorMemory
from core.fault_locator import compute_error_fingerprint, locate_fault, parse_error_frames
from core.hooks_engine import HooksEngine
from core.impact_guard import assess_impact
from core.output_trimmer import redact_secrets_text, trim_output
from core.repair_loop import ApprovalRequiredError, RepairLoopManager
from core.repo_map import build_repo_map
from core.safe_patcher import PatchApplier, PatchEdit
from core.subprocess_runner import run_isolated
from core.workspace_snapshot import WorkspaceSnapshotManager


@dataclass
class EvalCaseResult:
    case_id: str
    category: str
    passed: bool
    duration_ms: float
    detail: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EvalSuiteReport:
    ran_at: str
    total_cases: int
    passed_cases: int
    pass_rate_pct: float
    duration_ms: float
    avg_localization_tokens: float
    memory_recall_ms: float
    cases: list[EvalCaseResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ran_at": self.ran_at,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "pass_rate_pct": self.pass_rate_pct,
            "duration_ms": self.duration_ms,
            "avg_localization_tokens": self.avg_localization_tokens,
            "memory_recall_ms": self.memory_recall_ms,
            "cases": [c.to_dict() for c in self.cases],
        }

    def to_markdown(self) -> str:
        lines = [
            f"# VieForge Repair Eval Suite ({self.passed_cases}/{self.total_cases} PASS — {self.pass_rate_pct:.1f}%)",
            "",
            f"- **Duration:** `{self.duration_ms:.1f} ms` (`$0` local, 0 cloud tokens)",
            f"- **Avg Fault Brief Size:** `~{self.avg_localization_tokens:.0f} tokens`",
            f"- **ErrorMemory Exact Recall:** `{self.memory_recall_ms:.2f} ms`",
            "",
            "| Case | Category | Status | Time (ms) | Detail |",
            "| :--- | :--- | :---: | ---: | :--- |",
        ]
        for c in self.cases:
            st = "PASS" if c.passed else "FAIL"
            lines.append(f"| `{c.case_id}` | {c.category} | **{st}** | {c.duration_ms:.1f} | {c.detail} |")
        return "\n".join(lines)


def _case_01_pytest_compression(_root: Path) -> tuple[bool, str, dict[str, Any]]:
    lines = ["=== test session starts ==="]
    lines.extend([f"tests/test_app.py::test_ok_{i} PASSED [{i}%]" for i in range(1, 41)])
    lines.extend(
        [
            "=== FAILURES ===",
            "E   assert 2 == 3",
            "FAILED tests/test_app.py::test_fail - assert 2 == 3",
        ]
    )
    res = trim_output("\n".join(lines), max_lines=30)
    ok = res.detected_mode == "pytest" and "FAILED tests/test_app.py::test_fail" in res.text and res.saved_pct > 45.0
    return ok, f"mode={res.detected_mode}, saved={res.saved_pct}%", {"saved_pct": res.saved_pct}


def _case_02_linter_grouping(_root: Path) -> tuple[bool, str, dict[str, Any]]:
    lines = [f"src/f{i}.py:{i + 1}:1: F401 unused import" for i in range(12)]
    lines.extend([f"web/c{i}.ts({i + 1},2): error TS2304: Cannot find name 'x'" for i in range(6)])
    res = trim_output("\n".join(lines))
    ok = res.detected_mode == "linter" and "F401 (12x)" in res.text and "TS2304 (6x)" in res.text
    return ok, f"grouped 18 diagnostics, saved={res.saved_pct}%", {"saved_pct": res.saved_pct}


def _case_03_js_stack_collapse(_root: Path) -> tuple[bool, str, dict[str, Any]]:
    log = "\n".join(
        [
            "TypeError: undefined is not a function",
            "    at handler (src/api.ts:22:5)",
            "    at next (node_modules/express/index.js:12:3)",
            "    at wrap (node_modules/express/router.js:50:7)",
            "    at boot (src/index.ts:8:1)",
        ]
    )
    res = trim_output(log)
    ok = res.detected_mode == "js_stack" and "handler (src/api.ts:22:5)" in res.text and "node_modules/internal frames collapsed" in res.text
    return ok, "collapsed vendor frames while keeping src frames", {}


def _case_04_go_rust_panic_trim(_root: Path) -> tuple[bool, str, dict[str, Any]]:
    log = "\n".join(["panic: nil pointer dereference", "goroutine 1 [running]:"] + [f"pkg.f{i}() /app/a.go:{i}" for i in range(30)])
    res = trim_output(log, max_traceback_lines=10)
    ok = res.detected_mode == "go_rust" and res.traceback_trimmed
    return ok, f"trimmed Go panic stack, saved={res.saved_pct}%", {}


def _case_05_secret_redaction(_root: Path) -> tuple[bool, str, dict[str, Any]]:
    raw = "ERROR auth failed with sk-proj-supersecret1234567890abcdef and Bearer tok_abcdefghijklmnop1234"
    res = trim_output(raw, redact_secrets=True)
    ok = "sk-proj-" not in res.text and "tok_abcdef" not in res.text and res.secrets_redacted >= 2
    return ok, f"redacted {res.secrets_redacted} secrets", {"secrets_redacted": res.secrets_redacted}


def _case_06_ast_fault_localization(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c06"
    d.mkdir()
    (d / "math_ops.py").write_text("def avg(nums):\n    return sum(nums) / len(nums)\n", encoding="utf-8")
    (d / "main.py").write_text("from math_ops import avg\n\ndef run():\n    return avg([])\n", encoding="utf-8")
    tb = (
        f'Traceback (most recent call last):\n  File "{d / "main.py"}", line 4, in run\n'
        f'  File "{d / "math_ops.py"}", line 2, in avg\nZeroDivisionError: division by zero\n'
    )
    rep = locate_fault(tb, repo_root=d, include_git=False)
    ok = (
        len(rep.located_symbols) >= 1
        and rep.located_symbols[0].symbol_name == "avg"
        and any("main.py:4" in c for c in rep.located_symbols[0].callers)
        and rep.estimated_tokens < 400
    )
    return ok, f"located avg() + caller in ~{rep.estimated_tokens} tokens", {"tokens": rep.estimated_tokens}


def _case_07_syntax_error_fallback(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c07"
    d.mkdir()
    broken = d / "broken.py"
    broken.write_text("def f(:\n    return 1\n", encoding="utf-8")
    rep = locate_fault(f'  File "{broken}", line 1\nSyntaxError: invalid syntax\n', repo_root=d, include_git=False)
    ok = len(rep.located_symbols) == 1 and rep.located_symbols[0].symbol_kind == "syntax_error_window"
    return ok, "graceful syntax_error_window fallback", {"tokens": rep.estimated_tokens}


def _case_08_fingerprint_stability(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c08"
    d.mkdir()
    f = d / "x.py"
    f.write_text("def k():\n    raise KeyError('a')\n", encoding="utf-8")
    frames = parse_error_frames(f'  File "{f}", line 2, in k', repo_root=d)
    fp1 = compute_error_fingerprint("KeyError", "obj at 0x1111aaaa missing", frames)
    fp2 = compute_error_fingerprint("KeyError", "obj at 0x9999ffff missing", frames)
    return fp1 == fp2 and len(fp1) == 12, f"stable fingerprint={fp1}", {}


def _case_09_snapshot_and_rollback(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c09"
    d.mkdir()
    orig = d / "svc.py"
    orig.write_text("V = 1\n", encoding="utf-8")
    mgr = WorkspaceSnapshotManager(d)
    snap = mgr.create_snapshot(["svc.py", "extra.py"], label="eval")
    orig.write_text("V = 99\n", encoding="utf-8")
    (d / "extra.py").write_text("junk\n", encoding="utf-8")
    mgr.rollback(snap.snapshot_id, dry_run=False)
    ok = orig.read_text(encoding="utf-8") == "V = 1\n" and not (d / "extra.py").exists()
    return ok, "restored modified file & cleaned new stray file", {}


def _case_10_syntax_precheck_all_or_nothing(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c10"
    d.mkdir()
    a = d / "a.py"
    b = d / "b.py"
    a.write_text("A = 1\n", encoding="utf-8")
    b.write_text("B = 2\n", encoding="utf-8")
    applier = PatchApplier(d)
    res = applier.apply(
        [PatchEdit("a.py", "A = 1", "A = 10"), PatchEdit("b.py", "B = 2", "def bad(:")],
        dry_run=False,
    )
    ok = (not res.ok) and a.read_text(encoding="utf-8") == "A = 1\n" and b.read_text(encoding="utf-8") == "B = 2\n"
    return ok, "rejected broken syntax in RAM with 0 disk writes", {}


def _case_11_approval_gate_enforcement(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c11"
    d.mkdir()
    f = d / "m.py"
    f.write_text("def x():\n    return 1\n", encoding="utf-8")
    mgr = RepairLoopManager(d)
    prop = mgr.propose_repair(
        session_id="s11",
        log_text=f'  File "{f}", line 2, in x\nValueError: bad\n',
        edits=[PatchEdit("m.py", "return 1", "return 2")],
        include_git=False,
    )
    untouched = f.read_text(encoding="utf-8") == "def x():\n    return 1\n"
    blocked = False
    try:
        mgr.approve_and_apply(session_id="s11", proposal_id=prop.proposal_id, approved_by="")
    except ApprovalRequiredError:
        blocked = True
    return prop.status == "AWAITING_APPROVAL" and untouched and blocked, "blocked unapproved patch execution", {}


def _case_12_approved_fix_and_memory_recall(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c12"
    d.mkdir()
    f = d / "calc.py"
    f.write_text("def div(a, b):\n    return a / b\n", encoding="utf-8")
    tb = f'Traceback (most recent call last):\n  File "{f}", line 2, in div\nZeroDivisionError: division by zero\n'
    mgr = RepairLoopManager(d)
    prop = mgr.propose_repair(
        session_id="s12",
        log_text=tb,
        hypothesis="Guard b == 0",
        edits=[PatchEdit("calc.py", "return a / b", "return 0 if b == 0 else a / b")],
        include_git=False,
    )
    done = mgr.approve_and_apply(
        session_id="s12",
        proposal_id=prop.proposal_id,
        approved_by="owner",
        verify_cmd=f'"{sys.executable}" -c "import calc; assert calc.div(5, 0) == 0"',
    )
    t0 = time.perf_counter()
    with ErrorMemory(mgr.memory_db_path) as mem:
        hits = mem.lookup(fingerprint=prop.fingerprint)
    recall_ms = (time.perf_counter() - t0) * 1000.0
    ok = done.status == "APPROVED_AND_FIXED" and len(hits) == 1 and hits[0].match_type == "exact_fingerprint"
    return ok, f"verified fix & recalled in {recall_ms:.2f}ms", {"memory_recall_ms": round(recall_ms, 3)}


def _case_13_auto_rollback_and_antiloop(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c13"
    d.mkdir()
    f = d / "num.py"
    f.write_text("def inc(x):\n    return x - 1\n", encoding="utf-8")
    tb = f'  File "{f}", line 2, in inc\nAssertionError: wrong\n'
    mgr = RepairLoopManager(d)
    wrong = PatchEdit("num.py", "return x - 1", "return x - 2")
    p1 = mgr.propose_repair(session_id="s13", log_text=tb, hypothesis="bad guess", edits=[wrong], include_git=False)
    r1 = mgr.approve_and_apply(
        session_id="s13",
        proposal_id=p1.proposal_id,
        approved_by="owner",
        verify_cmd=f'"{sys.executable}" -c "import num; assert num.inc(1) == 2"',
        auto_rollback_on_fail=True,
    )
    p2 = mgr.propose_repair(session_id="s13", log_text=tb, hypothesis="repeat", edits=[wrong], include_git=False)
    ok = (
        r1.status == "ROLLED_BACK_ON_FAIL"
        and f.read_text(encoding="utf-8") == "def inc(x):\n    return x - 1\n"
        and p2.status == "LOOP_DETECTED"
    )
    return ok, "auto-rolled back bad fix & blocked repeat loop", {}


def _case_14_repo_map_and_impact_guard(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c14"
    d.mkdir()
    (d / "policy_guard.py").write_text("def check():\n    return True\n", encoding="utf-8")
    (d / "worker.py").write_text("import policy_guard\n\ndef run():\n    return policy_guard.check()\n", encoding="utf-8")
    rmap = build_repo_map(d, token_budget=500, use_cache=False)
    imp = assess_impact(d, ["policy_guard.py"])
    ok = rmap.included_files == 2 and rmap.estimated_tokens <= 520 and imp.risk_level == "RED" and imp.requires_human_approval
    return ok, f"map={rmap.estimated_tokens} tok, risk={imp.risk_level}", {}


def _case_15_hooks_and_sandbox_isolation(root: Path) -> tuple[bool, str, dict[str, Any]]:
    d = root / "c15"
    d.mkdir()
    sec = d / "leak.py"
    sec.write_text("T = 'sk-proj-leakedtoken1234567890abcdef'\n", encoding="utf-8")
    engine = HooksEngine(d)
    pre = engine.run_pre_commit(["leak.py"])
    sandbox_blocked = False
    try:
        run_isolated([sys.executable, "../escape.py"], cwd=d, sandbox_root=d)
    except PermissionError:
        sandbox_blocked = True
    ok = (not pre.passed) and sandbox_blocked
    return ok, "blocked leaked secret in pre_commit & sandbox escape", {}


_CASES: list[tuple[str, str, Callable[[Path], tuple[bool, str, dict[str, Any]]]]] = [
    ("E01_pytest_noise_compression", "Log Trimmer", _case_01_pytest_compression),
    ("E02_linter_code_grouping", "Log Trimmer", _case_02_linter_grouping),
    ("E03_js_node_modules_collapse", "Log Trimmer", _case_03_js_stack_collapse),
    ("E04_go_rust_panic_trim", "Log Trimmer", _case_04_go_rust_panic_trim),
    ("E05_secret_redaction_safety", "Security", _case_05_secret_redaction),
    ("E06_ast_fault_localization", "Fault Locator", _case_06_ast_fault_localization),
    ("E07_syntax_error_fallback", "Fault Locator", _case_07_syntax_error_fallback),
    ("E08_fingerprint_stability", "Fault Locator", _case_08_fingerprint_stability),
    ("E09_snapshot_and_rollback", "Safety", _case_09_snapshot_and_rollback),
    ("E10_syntax_precheck_all_or_nothing", "Safe Patcher", _case_10_syntax_precheck_all_or_nothing),
    ("E11_approval_gate_enforcement", "Repair Loop", _case_11_approval_gate_enforcement),
    ("E12_approved_fix_and_memory_recall", "Repair Loop", _case_12_approved_fix_and_memory_recall),
    ("E13_auto_rollback_and_antiloop", "Repair Loop", _case_13_auto_rollback_and_antiloop),
    ("E14_repo_map_and_impact_guard", "Workspace Guard", _case_14_repo_map_and_impact_guard),
    ("E15_hooks_and_sandbox_isolation", "Hooks & Sandbox", _case_15_hooks_and_sandbox_isolation),
]


def run_repair_eval_suite() -> EvalSuiteReport:
    """Execute all 15 deterministic self-healing eval scenarios offline at $0."""
    suite_t0 = time.perf_counter()
    results: list[EvalCaseResult] = []
    loc_tokens: list[int] = []
    recall_ms = 0.0

    with tempfile.TemporaryDirectory(prefix="vieforge_eval_") as tmp_str:
        tmp_root = Path(tmp_str)
        for case_id, category, fn in _CASES:
            t0 = time.perf_counter()
            try:
                passed, detail, metrics = fn(tmp_root)
            except Exception as exc:
                passed, detail, metrics = False, f"Exception: {exc}", {}
            dt_ms = round((time.perf_counter() - t0) * 1000.0, 2)
            if "tokens" in metrics:
                loc_tokens.append(int(metrics["tokens"]))
            if "memory_recall_ms" in metrics:
                recall_ms = float(metrics["memory_recall_ms"])
            results.append(
                EvalCaseResult(
                    case_id=case_id,
                    category=category,
                    passed=passed,
                    duration_ms=dt_ms,
                    detail=detail,
                    metrics=metrics,
                )
            )

    total = len(results)
    passed_cnt = sum(1 for r in results if r.passed)
    total_ms = round((time.perf_counter() - suite_t0) * 1000.0, 2)
    avg_tok = round(sum(loc_tokens) / len(loc_tokens), 1) if loc_tokens else 0.0

    return EvalSuiteReport(
        ran_at=datetime.now(timezone.utc).isoformat(),
        total_cases=total,
        passed_cases=passed_cnt,
        pass_rate_pct=round((passed_cnt / total) * 100.0, 1) if total else 0.0,
        duration_ms=total_ms,
        avg_localization_tokens=avg_tok,
        memory_recall_ms=round(recall_ms, 2),
        cases=results,
    )


def harvest_recurring_lessons(
    repo_roots: list[Path | str],
    *,
    min_hits: int = 2,
) -> dict[str, Any]:
    """Extract anonymized recurring error lessons and disproven hypotheses across local projects."""
    recurring_incidents: list[dict[str, Any]] = []
    disproven_hypotheses: list[dict[str, Any]] = []
    soft_error_counts: Counter[str] = Counter()
    scanned_projects = 0

    for raw in repo_roots:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            continue
        scanned_projects += 1
        proj_label = root.name

        mem_db = root / ".vieforge" / "error_memory.db"
        if mem_db.is_file():
            with ErrorMemory(mem_db) as mem:
                for inc in mem.list_incidents(limit=200):
                    if inc.hit_count >= min_hits:
                        safe_cause, _ = redact_secrets_text(inc.root_cause)
                        safe_fix, _ = redact_secrets_text(inc.verified_fix)
                        safe_prev, _ = redact_secrets_text(inc.prevention)
                        recurring_incidents.append(
                            {
                                "project": proj_label,
                                "fingerprint": inc.fingerprint,
                                "error_type": inc.error_type,
                                "hit_count": inc.hit_count,
                                "root_cause": safe_cause,
                                "verified_fix": safe_fix,
                                "prevention": safe_prev,
                            }
                        )

        sessions_dir = root / ".vieforge" / "repair_sessions"
        if sessions_dir.is_dir():
            for sp in sorted(sessions_dir.glob("*.json")):
                try:
                    sdata = json.loads(sp.read_text(encoding="utf-8", errors="ignore"))
                except (OSError, ValueError):
                    continue
                for rej in sdata.get("rejected_hypotheses", []):
                    hyp_raw = str(rej.get("hypothesis", "")).strip()
                    reason_raw = str(rej.get("reason", "")).strip()
                    if not hyp_raw and not reason_raw:
                        continue
                    safe_hyp, _ = redact_secrets_text(hyp_raw)
                    safe_reason, _ = redact_secrets_text(reason_raw)
                    disproven_hypotheses.append(
                        {
                            "project": proj_label,
                            "proposal_id": str(rej.get("proposal_id", "")),
                            "hypothesis": safe_hyp[:200],
                            "reason": safe_reason[:200] or ("Same-error loop" if rej.get("same_error_loop") else "Verification failed"),
                        }
                    )

        for candidate in (root / "data" / "qa_feedback.jsonl", root / "qa" / "soft_errors.jsonl"):
            if not candidate.is_file():
                continue
            for ln in candidate.read_text(encoding="utf-8", errors="ignore").splitlines():
                if not ln.strip():
                    continue
                try:
                    rec = json.loads(ln)
                except json.JSONDecodeError:
                    continue
                code = str(rec.get("code", "")).strip()
                if code and code != "OK":
                    soft_error_counts[code] += 1

    recurring_incidents.sort(key=lambda x: (-int(x["hit_count"]), x["error_type"]))
    recurring_soft = [
        {"code": code, "count": cnt}
        for code, cnt in soft_error_counts.most_common()
        if cnt >= min_hits
    ]

    return {
        "harvested_at": datetime.now(timezone.utc).isoformat(),
        "scanned_projects": scanned_projects,
        "min_hits": min_hits,
        "recurring_hard_errors": recurring_incidents,
        "disproven_hypotheses": disproven_hypotheses[:30],
        "recurring_soft_errors": recurring_soft,
        "suggested_next_steps": [
            f"Add regression eval case for recurring `{item['error_type']}` ({item['hit_count']}x)"
            for item in recurring_incidents[:5]
        ]
        + [
            f"Do not retry disproven hypothesis ({item['proposal_id']}): {item['hypothesis']}"
            for item in disproven_hypotheses[:5]
            if item["hypothesis"]
        ]
        + [
            f"Review soft QA code `{item['code']}` ({item['count']}x) for prompt/rule refinement"
            for item in recurring_soft[:5]
        ],
    }


def export_self_learning_playbook(
    repo_roots: list[Path | str],
    out_path: Path | str | None = None,
    *,
    min_hits: int = 1,
) -> dict[str, Any]:
    """Generate a compact (<600-token) self-learning rules playbook from past wins and failures."""
    harvest = harvest_recurring_lessons(repo_roots, min_hits=max(1, min_hits))
    lines: list[str] = [
        "# VieForge Self-Learning Repair Playbook",
        "",
        f"> Generated: `{harvest['harvested_at']}` | Scanned projects: `{harvest['scanned_projects']}`",
        "",
        "## 1. Verified Fixes to Reuse Immediately ($0)",
    ]
    if harvest["recurring_hard_errors"]:
        for item in harvest["recurring_hard_errors"][:8]:
            lines.append(
                f"- **`{item['error_type']}`** (`{item['fingerprint']}`, {item['hit_count']}x): "
                f"Cause: {item['root_cause']} -> Fix: {item['verified_fix']}"
            )
    else:
        lines.append("- *(No verified hard-error postmortems recorded yet.)*")

    lines.extend(["", "## 2. Disproven Hypotheses (Do NOT Repeat)"])
    if harvest["disproven_hypotheses"]:
        for d in harvest["disproven_hypotheses"][:8]:
            lines.append(f"- `{d['proposal_id']}`: {d['hypothesis'] or '(unnamed)'} — *{d['reason']}*")
    else:
        lines.append("- *(No failed/rejected hypotheses recorded yet.)*")

    lines.extend(["", "## 3. Recurring Quality Signals"])
    if harvest["recurring_soft_errors"]:
        for s in harvest["recurring_soft_errors"][:8]:
            lines.append(f"- `{s['code']}`: {s['count']}x")
    else:
        lines.append("- *(No recurring soft QA error codes above threshold.)*")

    markdown = "\n".join(lines) + "\n"
    written_to = ""
    if out_path is not None:
        dest = Path(out_path).expanduser().resolve()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(markdown, encoding="utf-8")
        written_to = str(dest)

    return {
        "written_to": written_to,
        "estimated_tokens": max(1, (len(markdown) + 3) // 4),
        "markdown": markdown,
        "harvest": harvest,
    }
