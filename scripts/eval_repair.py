#!/usr/bin/env python3
"""CLI for the 15-Case Self-Healing Eval Suite & Lesson Harvester (`vieforge eval`, stdlib).

Usage:
  vieforge eval [--markdown] [--json]
  vieforge eval harvest [--project DIR ...] [--min-hits 2] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.repair_eval import (
    export_self_learning_playbook,
    harvest_recurring_lessons,
    run_repair_eval_suite,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the 15-case offline ($0) repair eval suite or harvest recurring error lessons."
    )
    parser.add_argument("--markdown", action="store_true", help="Emit Markdown report")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report")
    sub = parser.add_subparsers(dest="action")

    p_harv = sub.add_parser("harvest", help="Harvest anonymized recurring error lessons across projects")
    p_harv.add_argument(
        "--project",
        action="append",
        default=None,
        help="Project directory to scan (default: current directory)",
    )
    p_harv.add_argument("--min-hits", type=int, default=2, help="Minimum recurrence count (default: 2)")

    p_learn = sub.add_parser("learn", help="Export compact self-learning repair playbook from wins & failures")
    p_learn.add_argument(
        "--project",
        action="append",
        default=None,
        help="Project directory to scan (default: current directory)",
    )
    p_learn.add_argument("--min-hits", type=int, default=1, help="Minimum recurrence count (default: 1)")
    p_learn.add_argument("--out", default="", help="Optional output path (e.g. .vieforge/learned_rules.md)")

    args = parser.parse_args(argv)

    if args.action == "learn":
        roots = args.project or [str(Path.cwd())]
        res = export_self_learning_playbook(
            roots,
            out_path=args.out or None,
            min_hits=max(1, args.min_hits),
        )
        if args.as_json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(res["markdown"].rstrip())
            if res["written_to"]:
                print(f"\n[Playbook saved to {res['written_to']} (~{res['estimated_tokens']} tokens)]")
        return 0

    if args.action == "harvest":
        roots = args.project or [str(Path.cwd())]
        report = harvest_recurring_lessons(roots, min_hits=max(1, args.min_hits))
        if args.as_json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            print(
                f"VieForge Lesson Harvest ({report['scanned_projects']} project(s), "
                f"min_hits>={report['min_hits']}):"
            )
            print(f"  Recurring hard errors: {len(report['recurring_hard_errors'])}")
            for item in report["recurring_hard_errors"][:10]:
                print(
                    f"    - [{item['fingerprint']}] {item['error_type']} ({item['hit_count']}x) "
                    f"in {item['project']}: {item['verified_fix']}"
                )
            print(f"  Recurring soft QA codes: {len(report['recurring_soft_errors'])}")
            for s in report["recurring_soft_errors"][:10]:
                print(f"    - {s['code']}: {s['count']}x")
            if report["suggested_next_steps"]:
                print("  Suggested self-improvement actions:")
                for step in report["suggested_next_steps"]:
                    print(f"    * {step}")
        return 0

    suite = run_repair_eval_suite()
    if args.as_json:
        print(json.dumps(suite.to_dict(), indent=2, ensure_ascii=False))
    elif args.markdown:
        print(suite.to_markdown())
    else:
        print(
            f"VieForge Repair Eval: {suite.passed_cases}/{suite.total_cases} PASS "
            f"({suite.pass_rate_pct:.1f}%) in {suite.duration_ms:.1f}ms ($0)"
        )
        print(
            f"  Avg Fault Brief: ~{suite.avg_localization_tokens:.0f} tokens | "
            f"ErrorMemory Recall: {suite.memory_recall_ms:.2f}ms"
        )
        for c in suite.cases:
            mark = "PASS" if c.passed else "FAIL"
            print(f"  [{mark}] {c.case_id:<36} ({c.duration_ms:>5.1f}ms) {c.detail}")
    return 0 if suite.passed_cases == suite.total_cases else 1


if __name__ == "__main__":
    raise SystemExit(main())
