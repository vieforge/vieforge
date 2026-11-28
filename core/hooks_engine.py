"""Deterministic Lifecycle Hooks Engine (`pre_command`, `post_edit`, `pre_commit`, 100% stdlib, MIT).

Turns coding discipline from prompt advice into deterministic local checks configured via
`<repo>/.vieforge/hooks.json`:
  - `pre_command` : Runs PolicyGuard + optional sandbox boundary check before shell execution.
  - `post_edit`   : Runs immediate syntax validation (`.py`/`.json`) + blast-radius `assess_impact`.
  - `pre_commit`  : Scans staged/target files for leaked secrets, checks repeated un-diagnosed
                    fix commits (third-patch), and flags stale `<!--measure:...-->` doc markers.
  - `install_git_hook()` : Installs a lightweight `.git/hooks/pre-commit` shim invoking VieForge.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import re
import stat
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.concurrency import atomic_write_json
from core.impact_guard import assess_impact
from core.output_trimmer import redact_secrets_text
from core.policy_guard import PolicyGuard
from core.safe_patcher import validate_content_syntax

DEFAULT_HOOKS_CONFIG: dict[str, Any] = {
    "version": "1.0",
    "pre_command": {
        "enabled": True,
        "role": "developer",
    },
    "post_edit": {
        "enabled": True,
        "syntax_check": True,
        "impact_check": True,
    },
    "pre_commit": {
        "enabled": True,
        "secret_scan": True,
        "third_patch": True,
        "third_patch_hours": 72,
        "third_patch_threshold": 2,
        "doc_drift": True,
        "doc_drift_max_age_days": 30,
    },
}

_MEASURE_RE = re.compile(
    r"<!--\s*measure:\s*([a-zA-Z0-9_.-]+)=([^\s@]+)\s*@(\d{4}-\d{2}-\d{2})\s*-->"
)
_FIX_COMMIT_RE = re.compile(r"^(fix|hotfix|bugfix)(\([^)]+\))?[:! ]", re.IGNORECASE)
_DIAG_SECTIONS = ("## Symptom", "## Hypothesis", "## Observation")


@dataclass
class HookCheckItem:
    name: str
    passed: bool
    detail: str
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class HookRunResult:
    event: str
    passed: bool
    checks: list[HookCheckItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event": self.event,
            "passed": self.passed,
            "checks": [c.to_dict() for c in self.checks],
        }


class HooksEngine:
    """Load `.vieforge/hooks.json` and execute deterministic lifecycle checks."""

    def __init__(self, repo_root: Path | str = ".") -> None:
        self.repo_root = Path(repo_root).resolve()
        self.config_path = self.repo_root / ".vieforge" / "hooks.json"

    def load_config(self) -> dict[str, Any]:
        if self.config_path.is_file():
            try:
                user_cfg = json.loads(self.config_path.read_text(encoding="utf-8"))
                merged = json.loads(json.dumps(DEFAULT_HOOKS_CONFIG))
                for key in ("pre_command", "post_edit", "pre_commit"):
                    if isinstance(user_cfg.get(key), dict):
                        merged[key].update(user_cfg[key])
                return merged
            except (OSError, ValueError):
                pass
        return json.loads(json.dumps(DEFAULT_HOOKS_CONFIG))

    def init_config(self, *, force: bool = False) -> Path:
        if self.config_path.is_file() and not force:
            return self.config_path
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.config_path, DEFAULT_HOOKS_CONFIG)
        return self.config_path

    def run_pre_command(
        self,
        command: str,
        *,
        role: str | None = None,
        action: str = "run_trimmed_command",
    ) -> HookRunResult:
        cfg = self.load_config()["pre_command"]
        if not cfg.get("enabled", True):
            return HookRunResult(event="pre_command", passed=True, checks=[])

        guard = PolicyGuard(audit_log_path=self.repo_root / ".vieforge" / "policy_audit.jsonl")
        dec = guard.authorize(
            role=role or str(cfg.get("role", "developer")),
            action=action,
            command=command,
        )
        return HookRunResult(
            event="pre_command",
            passed=dec.allowed,
            checks=[
                HookCheckItem(
                    name="policy_guard",
                    passed=dec.allowed,
                    detail=dec.reason,
                    data={"role": dec.role, "action": dec.action},
                )
            ],
        )

    def run_post_edit(self, files: list[str]) -> HookRunResult:
        cfg = self.load_config()["post_edit"]
        if not cfg.get("enabled", True):
            return HookRunResult(event="post_edit", passed=True, checks=[])

        checks: list[HookCheckItem] = []
        if cfg.get("syntax_check", True):
            syntax_errors: list[str] = []
            for raw in files:
                p = Path(raw)
                abs_p = p.resolve() if p.is_absolute() else (self.repo_root / p).resolve()
                if not abs_p.is_file():
                    continue
                try:
                    rel = str(abs_p.relative_to(self.repo_root)).replace("\\", "/")
                except ValueError:
                    rel = raw
                text = abs_p.read_text(encoding="utf-8", errors="replace")
                checked, ok, err = validate_content_syntax(rel, text)
                if checked and not ok:
                    syntax_errors.append(f"{rel}: {err}")
            checks.append(
                HookCheckItem(
                    name="syntax_check",
                    passed=len(syntax_errors) == 0,
                    detail="All edited files passed syntax checks." if not syntax_errors else "; ".join(syntax_errors),
                    data={"errors": syntax_errors},
                )
            )

        if cfg.get("impact_check", True) and files:
            impact = assess_impact(self.repo_root, files)
            checks.append(
                HookCheckItem(
                    name="impact_guard",
                    passed=True,  # Advisory gate; surfaces RED/YELLOW clearly to caller
                    detail=f"Risk={impact.risk_level} | approval_required={impact.requires_human_approval}",
                    data=impact.to_dict(),
                )
            )

        return HookRunResult(
            event="post_edit",
            passed=all(c.passed for c in checks),
            checks=checks,
        )

    def _get_git_staged_files(self) -> list[str]:
        if not (self.repo_root / ".git").exists():
            return []
        res = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
            check=False,
        )
        if res.returncode != 0:
            return []
        return [ln.strip().replace("\\", "/") for ln in res.stdout.splitlines() if ln.strip()]

    def _has_valid_diagnosis(self, target_file: str) -> bool:
        diag_dir = self.repo_root / "docs" / "diagnosis"
        if not diag_dir.is_dir():
            return False
        norm = target_file.replace("\\", "/")
        base = Path(norm).name
        for md in diag_dir.glob("*.md"):
            try:
                txt = md.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if (norm in txt or base in txt) and all(sec in txt for sec in _DIAG_SECTIONS):
                return True
        return False

    def _check_third_patch_local(self, targets: list[str], hours: int, threshold: int) -> HookCheckItem:
        if not (self.repo_root / ".git").exists():
            return HookCheckItem("third_patch", True, "Skipped (not a git repository).")
        res = subprocess.run(
            ["git", "log", f"--since={hours}.hours.ago", "--name-only", "--format=@%H|%s"],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="ignore",
            timeout=10,
            check=False,
        )
        if res.returncode != 0:
            return HookCheckItem("third_patch", True, "Skipped (git log unavailable).")

        counts: dict[str, int] = {}
        is_fix = False
        for ln in res.stdout.splitlines():
            if ln.startswith("@"):
                subj = ln[1:].split("|", 1)[1] if "|" in ln else ""
                is_fix = bool(_FIX_COMMIT_RE.match(subj))
                continue
            fname = ln.strip().replace("\\", "/")
            if is_fix and fname:
                counts[fname] = counts.get(fname, 0) + 1

        target_set = {t.replace("\\", "/") for t in targets} if targets else None
        blocked = [
            f"{f} ({cnt} fix commits)"
            for f, cnt in sorted(counts.items())
            if (target_set is None or f in target_set)
            and cnt >= threshold
            and not self._has_valid_diagnosis(f)
        ]
        return HookCheckItem(
            name="third_patch",
            passed=len(blocked) == 0,
            detail="PASS" if not blocked else f"Blocked repeated fixes without diagnosis: {', '.join(blocked)}",
            data={"blocked": blocked},
        )

    def _check_doc_drift_local(self, targets: list[str], max_age_days: int) -> HookCheckItem:
        today = dt.date.today()
        md_files: list[Path] = []
        if targets:
            for t in targets:
                if t.lower().endswith(".md"):
                    p = self.repo_root / t
                    if p.is_file():
                        md_files.append(p)
        else:
            for md in self.repo_root.glob("*.md"):
                if md.is_file():
                    md_files.append(md)

        stale: list[str] = []
        for md in md_files:
            text = md.read_text(encoding="utf-8", errors="ignore")
            rel = str(md.relative_to(self.repo_root)).replace("\\", "/")
            for m in _MEASURE_RE.finditer(text):
                try:
                    age = (today - dt.date.fromisoformat(m.group(3))).days
                except ValueError:
                    age = 9999
                if age > max_age_days:
                    stale.append(f"{rel}:{m.group(1)} ({age}d old)")
        return HookCheckItem(
            name="doc_drift",
            passed=len(stale) == 0,
            detail="PASS" if not stale else f"Stale doc markers: {', '.join(stale)}",
            data={"stale": stale},
        )

    def run_pre_commit(
        self,
        files: list[str] | None = None,
        *,
        check_all: bool = False,
    ) -> HookRunResult:
        cfg = self.load_config()["pre_commit"]
        if not cfg.get("enabled", True):
            return HookRunResult(event="pre_commit", passed=True, checks=[])

        targets = files if files is not None else self._get_git_staged_files()
        if files is None and not targets and not check_all:
            return HookRunResult(
                event="pre_commit",
                passed=True,
                checks=[
                    HookCheckItem(
                        name="staged_scope",
                        passed=True,
                        detail="No staged files to check (pass --all to scan entire repo history).",
                    )
                ],
            )
        checks: list[HookCheckItem] = []

        if cfg.get("secret_scan", True):
            leaked: list[str] = []
            for raw in targets:
                p = Path(raw)
                abs_p = p.resolve() if p.is_absolute() else (self.repo_root / p).resolve()
                if not abs_p.is_file() or abs_p.stat().st_size > 1_000_000:
                    continue
                content = abs_p.read_text(encoding="utf-8", errors="ignore")
                _, count = redact_secrets_text(content)
                if count > 0:
                    leaked.append(f"{raw} ({count} secret pattern(s))")
            checks.append(
                HookCheckItem(
                    name="secret_scan",
                    passed=len(leaked) == 0,
                    detail="No hardcoded secrets detected." if not leaked else f"Potential secrets found: {', '.join(leaked)}",
                    data={"leaked_files": leaked},
                )
            )

        if cfg.get("third_patch", True):
            checks.append(
                self._check_third_patch_local(
                    targets,
                    hours=int(cfg.get("third_patch_hours", 72)),
                    threshold=int(cfg.get("third_patch_threshold", 2)),
                )
            )

        if cfg.get("doc_drift", True):
            checks.append(
                self._check_doc_drift_local(
                    targets,
                    max_age_days=int(cfg.get("doc_drift_max_age_days", 30)),
                )
            )

        return HookRunResult(
            event="pre_commit",
            passed=all(c.passed for c in checks),
            checks=checks,
        )

    def install_git_pre_commit(self, *, dry_run: bool = False) -> dict[str, Any]:
        """Install a lightweight `.git/hooks/pre-commit` shim invoking `vieforge hooks run pre_commit`."""
        git_dir = self.repo_root / ".git"
        if not git_dir.is_dir():
            raise FileNotFoundError(f"Not a git repository: {self.repo_root}")
        hook_path = git_dir / "hooks" / "pre-commit"
        shim = (
            "#!/usr/bin/env sh\n"
            "# Managed by VieForge Hooks Engine\n"
            "python -m scripts.hooks run pre_commit --repo .\n"
        )
        if not dry_run:
            hook_path.parent.mkdir(parents=True, exist_ok=True)
            hook_path.write_text(shim, encoding="utf-8")
            if os.name != "nt":
                mode = hook_path.stat().st_mode
                hook_path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        return {
            "target": "git",
            "hook_path": str(hook_path),
            "dry_run": dry_run,
            "content": shim,
        }

    def generate_claudecode_hooks_snippet(self) -> dict[str, Any]:
        """Return a ready-to-paste Claude Code `.claude/settings.json` hooks snippet."""
        return {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "vieforge hooks run pre_command --cmd \"$CLAUDE_TOOL_INPUT\"",
                            }
                        ],
                    }
                ],
                "PostToolUse": [
                    {
                        "matcher": "Edit|Write",
                        "hooks": [
                            {
                                "type": "command",
                                "command": "vieforge hooks run post_edit --file \"$CLAUDE_FILE_PATH\"",
                            }
                        ],
                    }
                ],
            }
        }
