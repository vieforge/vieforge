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


def _recent_fix_diff(repo_path: Path, target_file: str, max_lines: int = 80) -> str:
    """Return a concise diff preview of the last 2 commits touching `target_file`."""
    try:
        res = subprocess.run(
            ["git", "log", "-n2", "-p", "--", target_file],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if res.returncode != 0 or not res.stdout.strip():
        return ""
    lines = res.stdout.splitlines()
    if len(lines) > max_lines:
        return "\n".join(lines[:max_lines] + [f"... ({len(lines) - max_lines} diff lines truncated)"])
    return "\n".join(lines)


def init_diagnosis_scaffold(
    repo_path: Path,
    target_file: str,
    commits: list[str],
    *,
    force: bool = False,
) -> Path:
    """Generate a pre-filled `docs/diagnosis/<slug>.md` template for a blocked file."""
    diag_dir = repo_path / "docs" / "diagnosis"
    diag_dir.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", target_file.replace("\\", "/").strip("/"))
    out_path = diag_dir / f"{slug}.md"
    if out_path.exists() and not force:
        return out_path

    commit_bullets = "\n".join(f"- `{c}`" for c in commits) or "- (no recent commits parsed)"
    diff_preview = _recent_fix_diff(repo_path, target_file)
    diff_section = f"\n```diff\n{diff_preview}\n```\n" if diff_preview else "\n*(No git diff preview available.)*\n"

    content = (
        f"# Root-Cause Diagnosis: `{target_file}`\n\n"
        f"> Auto-scaffolded by `vieforge third-patch --init-diagnosis`.\n"
        f"> Complete all 3 sections below with real evidence before applying another fix.\n\n"
        f"## Recent Fix Commits\n{commit_bullets}\n\n"
        f"## Symptom\n"
        f"- Target file: `{target_file}`\n"
        f"- Describe the exact failing test, error message, or broken behavior here.\n\n"
        f"## Hypothesis\n"
        f"- Explain the underlying root cause (not just the surface crash line).\n"
        f"- Explain why the previous {len(commits)} fix commit(s) did not eliminate the root cause.\n\n"
        f"## Observation\n"
        f"- Paste the concrete verification command output or minimal reproduction evidence.\n"
        f"\n### Recent Diff Context\n{diff_section}"
    )
    out_path.write_text(content, encoding="utf-8")
    return out_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="VieForge third-patch gate — block repeated symptom fixes without diagnosis."
    )
    parser.add_argument("--repo", default=".", help="Path to git repository (default: .)")
    parser.add_argument("--hours", type=int, default=72, help="Lookback window in hours (default: 72)")
    parser.add_argument("--threshold", type=int, default=2, help="Fix commit threshold (default: 2)")
    parser.add_argument("--staged", action="store_true", help="Check only git-staged files (for pre-commit)")
    parser.add_argument("--file", action="append", default=None, help="Check specific relative file path(s)")
    parser.add_argument(
        "--init-diagnosis",
        action="store_true",
        help="Auto-create pre-filled docs/diagnosis/<file>.md scaffold(s) for blocked files",
    )
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
    scaffolded: list[str] = []
    if args.init_diagnosis and result["blocked"]:
        for item in result["blocked"]:
            created = init_diagnosis_scaffold(repo_path, item["file"], item["commits"])
            scaffolded.append(str(created.relative_to(repo_path)).replace("\\", "/"))
        result["scaffolded_diagnosis"] = scaffolded

    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"third-patch: {result['status']} (window={args.hours}h, threshold>={args.threshold})")
        for item in result["blocked"]:
            print(f"  BLOCKED: {item['file']} ({item['fix_count']} fix commits)")
            for c in item["commits"]:
                print(f"    - {c}")
        if scaffolded:
            print("  Scaffolded diagnosis note(s):")
            for sc in scaffolded:
                print(f"    + {sc}")
        elif result["blocked"]:
            print(
                "  Write docs/diagnosis/<area>.md naming the file with: "
                "## Symptom · ## Hypothesis · ## Observation "
                "(or re-run with --init-diagnosis)"
            )
    return 1 if result["status"] == "BLOCKED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
