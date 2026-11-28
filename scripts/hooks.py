#!/usr/bin/env python3
"""CLI for Deterministic Lifecycle Hooks (`vieforge hooks`, 100% Python stdlib).

Usage:
  vieforge hooks init [--force]
  vieforge hooks run pre_command --cmd "pytest -q"
  vieforge hooks run post_edit --file core/state_db.py
  vieforge hooks run pre_commit [--file a.py]
  vieforge hooks install --target git [--dry-run]
  vieforge hooks install --target claudecode
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.hooks_engine import HooksEngine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Manage and execute deterministic VieForge lifecycle hooks."
    )
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    sub = parser.add_subparsers(dest="action")

    p_init = sub.add_parser("init", help="Create default .vieforge/hooks.json config")
    p_init.add_argument("--force", action="store_true", help="Overwrite existing hooks.json")

    p_run = sub.add_parser("run", help="Run a lifecycle hook event")
    p_run.add_argument("event", choices=["pre_command", "post_edit", "pre_commit"])
    p_run.add_argument("--cmd", default="", help="Command string for pre_command")
    p_run.add_argument("--role", default=None, help="PolicyGuard role override for pre_command")
    p_run.add_argument("--file", action="append", default=None, help="Target file(s) for post_edit / pre_commit")
    p_run.add_argument("--all", action="store_true", dest="check_all", help="Scan entire repo history even if no files are staged")

    p_inst = sub.add_parser("install", help="Install Git hook or print IDE hook snippet")
    p_inst.add_argument("--target", choices=["git", "claudecode"], default="git")
    p_inst.add_argument("--dry-run", action="store_true", help="Preview without modifying .git/hooks")

    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 0

    engine = HooksEngine(args.repo)
    try:
        if args.action == "init":
            path = engine.init_config(force=args.force)
            if args.as_json:
                print(json.dumps({"config_path": str(path)}, ensure_ascii=False))
            else:
                print(f"Hooks config ready: {path}")
            return 0

        if args.action == "run":
            if args.event == "pre_command":
                res = engine.run_pre_command(args.cmd, role=args.role)
            elif args.event == "post_edit":
                res = engine.run_post_edit(args.file or [])
            else:
                res = engine.run_pre_commit(args.file, check_all=args.check_all)

            if args.as_json:
                print(json.dumps(res.to_dict(), indent=2, ensure_ascii=False))
            else:
                status = "PASS" if res.passed else "BLOCKED"
                print(f"hooks[{res.event}]: {status}")
                for chk in res.checks:
                    mark = "OK" if chk.passed else "FAIL"
                    print(f"  [{mark}] {chk.name}: {chk.detail}")
            return 0 if res.passed else 1

        if args.action == "install":
            if args.target == "git":
                info = engine.install_git_pre_commit(dry_run=args.dry_run)
                if args.as_json:
                    print(json.dumps(info, indent=2, ensure_ascii=False))
                else:
                    prefix = "[DRY-RUN] " if args.dry_run else ""
                    print(f"{prefix}Installed Git pre-commit hook at: {info['hook_path']}")
                return 0
            snippet = engine.generate_claudecode_hooks_snippet()
            print(json.dumps(snippet, indent=2, ensure_ascii=False))
            return 0
    except FileNotFoundError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
