#!/usr/bin/env python3
"""Third-Patch Mechanical Gate for VieForge (100% Python Stdlib, MIT).

Blocks a 3rd fix commit to the same file within a rolling window (default: 72h)
unless a root-cause diagnosis note exists under ``docs/diagnosis/*.md`` naming
the file and containing ``## Symptom``, ``## Hypothesis``, and ``## Observation``.

Inspired by the mechanical discipline tier in ``nextcore-workflow`` (MIT),
implemented from scratch in zero-dependency Python stdlib.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any

FIX_COMMIT_RE = re.compile(r"^(fix|hotfix|bugfix)(\([^)]+\))?[:! ]", re.IGNORECASE)
DIAGNOSIS_SECTIONS = ("## Symptom", "## Hypothesis", "## Observation")


def has_valid_diagnosis(repo_path: Path, target_file: str) -> bool:
    """Return True if any ``docs/diagnosis/*.md`` documents ``target_file``."""
    diag_dir = repo_path / "docs" / "diagnosis"
    if not diag_dir.is_dir():
        return False
    norm_target = target_file.replace("\\", "/")
    basename = Path(norm_target).name
    for md_file in diag_dir.glob("*.md"):
        try:
            text = md_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if (norm_target in text or basename in text) and all(
            sec in text for sec in DIAGNOSIS_SECTIONS
        ):
            return True
    return False


def get_staged_files(repo_path: Path) -> list[str]:
    """Return staged file paths relative to ``repo_path``."""
    res = subprocess.run(
        ["git", "diff", "--cached", "--name-only"],
        cwd=repo_path,
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


def evaluate_third_patch(
    repo_path: Path,
    target_files: list[str] | None = None,
    hours: int = 72,
    threshold: int = 2,
) -> dict[str, Any]:
    """Evaluate recent fix-commit density per file and flag un-diagnosed repeats."""
    if not (repo_path / ".git").exists():
        return {"status": "SKIPPED", "reason": "Not a git repository", "blocked": [], "counts": {}}

    res = subprocess.run(
        ["git", "log", f"--since={hours}.hours.ago", "--name-only", "--format=@%H|%s"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
        timeout=10,
        check=False,
    )
    if res.returncode != 0:
        return {
            "status": "SKIPPED",
            "reason": res.stderr.strip() or "git log failed",
            "blocked": [],
            "counts": {},
        }

    counts: dict[str, list[str]] = {}
    current_is_fix = False
    current_label = ""
    for line in res.stdout.splitlines():
        if line.startswith("@"):
            parts = line[1:].split("|", 1)
            sha = parts[0][:7]
            subj = parts[1] if len(parts) > 1 else ""
            current_is_fix = bool(FIX_COMMIT_RE.match(subj))
            current_label = f"{sha} {subj}"
            continue
        fname = line.strip().replace("\\", "/")
        if current_is_fix and fname:
            counts.setdefault(fname, []).append(current_label)

    filter_set = {f.replace("\\", "/") for f in target_files} if target_files is not None else None
    blocked: list[dict[str, Any]] = []
    for fname, commits in sorted(counts.items()):
        if filter_set is not None and fname not in filter_set:
            continue
        if len(commits) >= threshold and not has_valid_diagnosis(repo_path, fname):
            blocked.append({"file": fname, "fix_count": len(commits), "commits": commits})

    return {
        "status": "BLOCKED" if blocked else "PASS",
        "hours": hours,
        "threshold": threshold,
        "blocked": blocked,
        "counts": {k: len(v) for k, v in counts.items()},
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="VieForge third-patch gate — block repeated symptom fixes without diagnosis."
    )
    parser.add_argument("--repo", default=".", help="Path to git repository (default: .)")
    parser.add_argument("--hours", type=int, default=72, help="Lookback window in hours (default: 72)")
    parser.add_argument("--threshold", type=int, default=2, help="Fix commit threshold (default: 2)")
    parser.add_argument("--staged", action="store_true", help="Check only git-staged files (for pre-commit)")
    parser.add_argument("--file", action="append", default=None, help="Check specific relative file path(s)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo).resolve()
    targets: list[str] | None = args.file
    if args.staged:
        targets = get_staged_files(repo_path)

    result = evaluate_third_patch(
        repo_path,
        target_files=targets,
        hours=args.hours,
        threshold=args.threshold,
    )
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"third-patch: {result['status']} (window={args.hours}h, threshold>={args.threshold})")
        for item in result["blocked"]:
            print(f"  BLOCKED: {item['file']} ({item['fix_count']} fix commits)")
            for c in item["commits"]:
                print(f"    - {c}")
        if result["blocked"]:
            print(
                "  Write docs/diagnosis/<area>.md naming the file with: "
                "## Symptom · ## Hypothesis · ## Observation"
            )
    return 1 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
