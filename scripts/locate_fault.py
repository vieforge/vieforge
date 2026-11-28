#!/usr/bin/env python3
"""CLI for Hierarchical Fault Localization (`vieforge locate`, 100% Python stdlib).

Usage:
  vieforge locate --log failure.log
  vieforge locate --cmd "pytest -q"
  cat failure.log | vieforge locate
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.fault_locator import locate_fault
from core.output_trimmer import trim_output
from core.subprocess_runner import run_isolated


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Locate exact failing functions, callers, and git context from error logs (< 500 tokens)."
    )
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--log", default="", help="Path to a failure log file (reads stdin if omitted)")
    parser.add_argument("--cmd", default="", help="Optional command to run and diagnose if it fails")
    parser.add_argument("--max-symbols", type=int, default=3, help="Max failing symbols to slice (default: 3)")
    parser.add_argument("--no-git", action="store_true", help="Skip recent git commit lookup")
    parser.add_argument("--no-callers", action="store_true", help="Skip caller reference lookup")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo).resolve()
    raw_log = ""
    if args.cmd:
        raw_parts = shlex.split(args.cmd, posix=(os.name != "nt"))
        parts = [
            p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'") else p
            for p in raw_parts
        ]
        res = run_isolated(parts, cwd=str(repo_path), max_output_lines=200, max_output_chars=24000)
        raw_log = "\n".join(p for p in (res.stdout_trimmed, res.stderr_trimmed) if p)
    elif args.log:
        log_file = Path(args.log).expanduser()
        if not log_file.is_file():
            print(f"Error: log file not found: {log_file}", file=sys.stderr)
            return 1
        raw_log = log_file.read_text(encoding="utf-8", errors="replace")
    elif not sys.stdin.isatty():
        raw_log = sys.stdin.read()
    else:
        parser.print_help()
        return 0

    trimmed = trim_output(raw_log, max_lines=160, max_chars=16000)
    report = locate_fault(
        raw_log,
        repo_root=repo_path,
        include_git=not args.no_git,
        include_callers=not args.no_callers,
        max_symbols=max(1, args.max_symbols),
    )

    if args.as_json:
        payload = report.to_dict()
        payload["log_compression"] = {
            "detected_mode": trimmed.detected_mode,
            "raw_chars": trimmed.raw_chars,
            "trimmed_chars": trimmed.trimmed_chars,
            "saved_pct": trimmed.saved_pct,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print(report.to_markdown())
        print(
            f"\n[Log mode: {trimmed.detected_mode} | Brief ~{report.estimated_tokens} tokens | "
            f"Raw log saved {trimmed.saved_pct:.1f}%]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
