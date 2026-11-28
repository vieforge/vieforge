#!/usr/bin/env python3
"""CLI for Token-Budgeted Repo Map & Blast-Radius Impact Guard (`vieforge map`, stdlib).

Usage:
  vieforge map [--budget 1200] [--include-tests] [--no-cache] [--json]
  vieforge map --impact core/state_db.py [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.impact_guard import assess_impact
from core.repo_map import build_repo_map


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a token-budgeted repo map or assess file blast-radius impact."
    )
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--budget", type=int, default=1200, help="Target token budget (default: 1200)")
    parser.add_argument("--include-tests", action="store_true", help="Include test files in repo map")
    parser.add_argument("--no-cache", action="store_true", help="Disable SQLite mtime cache")
    parser.add_argument(
        "--impact",
        action="append",
        default=None,
        help="Assess GREEN/YELLOW/RED blast-radius impact for one or more file paths",
    )
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    args = parser.parse_args(argv)

    repo_path = Path(args.repo).resolve()
    if args.impact:
        report = assess_impact(repo_path, args.impact)
        if args.as_json:
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        else:
            print(
                f"Impact Guard: [{report.risk_level}] "
                f"(approval_required={report.requires_human_approval})"
            )
            print(f"  Targets    : {', '.join(report.target_files)}")
            print(f"  Direct deps: {len(report.direct_dependents)} | Transitive: {len(report.transitive_dependents)}")
            if report.related_tests:
                print(f"  Tests      : {', '.join(report.related_tests[:8])}")
            for r in report.reasons:
                print(f"  Reason     : {r}")
            for act in report.recommended_actions:
                print(f"  Action     : {act}")
        return 0

    rep = build_repo_map(
        repo_path,
        token_budget=args.budget,
        include_tests=args.include_tests,
        use_cache=not args.no_cache,
    )
    if args.as_json:
        print(json.dumps(rep.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(rep.markdown)
        print(
            f"\n[Included {rep.included_files}/{rep.total_files} files | "
            f"~{rep.estimated_tokens}/{rep.token_budget} tokens | cache_hits={rep.cache_hits}]"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
