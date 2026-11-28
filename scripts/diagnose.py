#!/usr/bin/env python3
"""CLI for Approval-Gated Repair Loop & Local Error Memory (`vieforge diagnose`, stdlib).

Usage:
  vieforge diagnose propose --session s1 --log fail.log [--patch fix.patch] [--hypothesis "..."]
  vieforge diagnose approve --session s1 --proposal prop_1_abc123 --verify-cmd "pytest -q"
  vieforge diagnose reject  --session s1 --proposal prop_1_abc123 --reason "Use safer fix"
  vieforge diagnose sessions
  vieforge diagnose memory list
  vieforge diagnose memory search --query "ZeroDivisionError" [--fingerprint abc123]
  vieforge diagnose memory add --fingerprint abc123 --type ZeroDivisionError --symptom "..." --cause "..." --fix "..."
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

from core.error_memory import ErrorMemory
from core.repair_loop import ApprovalRequiredError, RepairLoopManager
from core.subprocess_runner import run_isolated


def _handle_memory(args: argparse.Namespace) -> int:
    db_path = Path(args.repo).resolve() / ".vieforge" / "error_memory.db"
    with ErrorMemory(db_path) as mem:
        if args.mem_action == "list":
            items = mem.list_incidents(limit=args.limit)
            if args.as_json:
                print(json.dumps([i.to_dict() for i in items], indent=2, ensure_ascii=False))
            elif not items:
                print("Error memory is empty.")
            else:
                for inc in items:
                    print(f"[{inc.fingerprint}] {inc.error_type} (hits={inc.hit_count}) -> {inc.verified_fix}")
            return 0

        if args.mem_action == "search":
            matches = mem.lookup(
                fingerprint=args.fingerprint,
                error_type=args.error_type,
                query_text=args.query,
                limit=args.limit,
            )
            if args.as_json:
                print(json.dumps([m.to_dict() for m in matches], indent=2, ensure_ascii=False))
            elif not matches:
                print("No matching past incidents found.")
            else:
                for m in matches:
                    inc = m.incident
                    print(
                        f"[{inc.fingerprint}] score={m.score:.2f} ({m.match_type}) "
                        f"{inc.error_type}: {inc.root_cause} | Fix: {inc.verified_fix}"
                    )
            return 0

        if args.mem_action == "add":
            inc = mem.record_incident(
                fingerprint=args.fingerprint,
                error_type=args.error_type,
                symptom=args.symptom,
                root_cause=args.cause,
                verified_fix=args.fix,
                prevention=args.prevention,
                files=args.file or [],
            )
            if args.as_json:
                print(json.dumps(inc.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(f"Recorded incident [{inc.fingerprint}] (hits={inc.hit_count}).")
            return 0
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Approval-gated repair loop & local SQLite error postmortem memory."
    )
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    sub = parser.add_subparsers(dest="action")

    p_prop = sub.add_parser("propose", help="Phase 1: Diagnose & stage patch proposal (never edits files)")
    p_prop.add_argument("--session", required=True, help="Repair session ID")
    p_prop.add_argument("--log", default="", help="Path to failure log file")
    p_prop.add_argument("--cmd", default="", help="Command to run and diagnose on failure")
    p_prop.add_argument("--patch", default="", help="Path to SEARCH/REPLACE patch file to pre-validate")
    p_prop.add_argument("--hypothesis", default="", help="Root-cause hypothesis for this repair attempt")
    p_prop.add_argument("--no-git", action="store_true", help="Skip git log lookup")

    p_app = sub.add_parser("approve", help="Phase 2: Explicitly approve a staged proposal, snapshot & apply")
    p_app.add_argument("--session", required=True, help="Repair session ID")
    p_app.add_argument("--proposal", required=True, help="Proposal ID to approve")
    p_app.add_argument("--by", default="user", help="Approver name/role (default: user)")
    p_app.add_argument("--verify-cmd", default="", help="Regression verification command to run after applying")
    p_app.add_argument(
        "--no-auto-rollback",
        action="store_true",
        help="Do not automatically rollback if verification fails",
    )
    p_app.add_argument("--prevention", default="", help="Optional prevention note for ErrorMemory")

    p_rej = sub.add_parser("reject", help="Reject a staged repair proposal")
    p_rej.add_argument("--session", required=True, help="Repair session ID")
    p_rej.add_argument("--proposal", required=True, help="Proposal ID to reject")
    p_rej.add_argument("--reason", default="Rejected by reviewer", help="Rejection reason")

    sub.add_parser("sessions", help="List recent repair sessions")

    p_mem = sub.add_parser("memory", help="Manage project-local SQLite error postmortem memory")
    mem_sub = p_mem.add_subparsers(dest="mem_action")

    p_mlist = mem_sub.add_parser("list", help="List stored postmortem incidents")
    p_mlist.add_argument("--limit", type=int, default=20)

    p_msearch = mem_sub.add_parser("search", help="Search error memory by fingerprint or query")
    p_msearch.add_argument("--fingerprint", default="")
    p_msearch.add_argument("--type", dest="error_type", default="")
    p_msearch.add_argument("--query", default="")
    p_msearch.add_argument("--limit", type=int, default=3)

    p_madd = mem_sub.add_parser("add", help="Record a verified hard-error postmortem")
    p_madd.add_argument("--fingerprint", required=True)
    p_madd.add_argument("--type", dest="error_type", required=True)
    p_madd.add_argument("--symptom", required=True)
    p_madd.add_argument("--cause", required=True)
    p_madd.add_argument("--fix", required=True)
    p_madd.add_argument("--prevention", default="")
    p_madd.add_argument("--file", action="append", default=None)

    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 0

    if args.action == "memory":
        if not args.mem_action:
            p_mem.print_help()
            return 0
        return _handle_memory(args)

    mgr = RepairLoopManager(repo_root=args.repo)
    try:
        if args.action == "sessions":
            items = mgr.list_sessions()
            if args.as_json:
                print(json.dumps(items, indent=2, ensure_ascii=False))
            elif not items:
                print("No repair sessions found.")
            else:
                for s in items:
                    print(f"{s['session_id']}  proposals={s['proposal_count']}  last={s['last_status']}")
            return 0

        if args.action == "propose":
            raw_log = ""
            if args.cmd:
                raw_parts = shlex.split(args.cmd, posix=(os.name != "nt"))
                parts = [
                    p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'") else p
                    for p in raw_parts
                ]
                res = run_isolated(parts, cwd=str(Path(args.repo).resolve()), max_output_lines=200)
                raw_log = "\n".join(p for p in (res.stdout_trimmed, res.stderr_trimmed) if p)
            elif args.log:
                raw_log = Path(args.log).expanduser().read_text(encoding="utf-8", errors="replace")
            elif not sys.stdin.isatty():
                raw_log = sys.stdin.read()
            else:
                print("Error: provide --log, --cmd, or pipe failure log via stdin.", file=sys.stderr)
                return 2

            patch_text = ""
            if args.patch:
                patch_text = Path(args.patch).expanduser().read_text(encoding="utf-8")

            prop = mgr.propose_repair(
                session_id=args.session,
                log_text=raw_log,
                hypothesis=args.hypothesis,
                patch_text=patch_text,
                include_git=not args.no_git,
            )
            if args.as_json:
                print(json.dumps(prop.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(
                    f"Proposal {prop.proposal_id} [{prop.status}] "
                    f"(fingerprint={prop.fingerprint}, risk={prop.impact_risk})"
                )
                if prop.impact_summary:
                    print(f"  Impact: {prop.impact_summary}")
                if prop.known_fixes:
                    print(f"  Known past fixes matched: {len(prop.known_fixes)}")
                    for kf in prop.known_fixes:
                        inc = kf["incident"]
                        print(f"    * ({kf['match_type']}, score={kf['score']}) {inc['verified_fix']}")
                if prop.preview_diffs:
                    for d in prop.preview_diffs:
                        if d.get("unified_diff"):
                            print(d["unified_diff"])
                if prop.status == "AWAITING_APPROVAL":
                    verify_hint = (
                        f' --verify-cmd "pytest {" ".join(prop.impact_related_tests[:3])}"'
                        if prop.impact_related_tests
                        else ""
                    )
                    print(
                        f"\nApproval required before editing files. Run:\n"
                        f"  vieforge diagnose approve --session {args.session} --proposal {prop.proposal_id}{verify_hint}"
                    )
                elif prop.rejected_reason:
                    print(f"  Note: {prop.rejected_reason}")
            return 0

        if args.action == "approve":
            prop = mgr.approve_and_apply(
                session_id=args.session,
                proposal_id=args.proposal,
                approved_by=args.by,
                verify_cmd=args.verify_cmd,
                auto_rollback_on_fail=not args.no_auto_rollback,
                prevention=args.prevention,
            )
            if args.as_json:
                print(json.dumps(prop.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(
                    f"Proposal {prop.proposal_id}: {prop.status} "
                    f"(snapshot={prop.snapshot_id}, verify_rc={prop.verification_rc})"
                )
            return 0 if prop.status == "APPROVED_AND_FIXED" else 1

        if args.action == "reject":
            prop = mgr.reject_proposal(
                session_id=args.session,
                proposal_id=args.proposal,
                reason=args.reason,
            )
            if args.as_json:
                print(json.dumps(prop.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(f"Proposal {prop.proposal_id} marked REJECTED ({prop.rejected_reason}).")
            return 0
    except (ApprovalRequiredError, FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
