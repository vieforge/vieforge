#!/usr/bin/env python3
"""CLI for Instant Workspace Snapshots & Rollback (`vieforge rollback`, 100% Python stdlib).

Usage:
  vieforge rollback create file1.py file2.py --label pre_fix
  vieforge rollback list
  vieforge rollback diff [--id SNAPSHOT_ID]
  vieforge rollback restore [--id SNAPSHOT_ID] [--dry-run]
  vieforge rollback prune --keep 20
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.workspace_snapshot import WorkspaceSnapshotManager


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Create, diff, and restore instant pre-edit workspace snapshots."
    )
    parser.add_argument("--repo", default=".", help="Repository root directory (default: .)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON output")
    sub = parser.add_subparsers(dest="action")

    p_create = sub.add_parser("create", help="Snapshot one or more target files before editing")
    p_create.add_argument("files", nargs="+", help="Relative or absolute file paths inside repo")
    p_create.add_argument("--label", default="manual", help="Short label (e.g. pre_fix)")
    p_create.add_argument("--note", default="", help="Optional note describing why snapshot was taken")

    p_list = sub.add_parser("list", help="List recent snapshots")
    p_list.add_argument("--limit", type=int, default=20, help="Max snapshots to show")

    p_diff = sub.add_parser("diff", help="Show unified diff between snapshot and live workspace")
    p_diff.add_argument("--id", dest="snapshot_id", default=None, help="Snapshot ID (default: latest)")

    p_restore = sub.add_parser("restore", help="Rollback files to a saved snapshot")
    p_restore.add_argument("--id", dest="snapshot_id", default=None, help="Snapshot ID (default: latest)")
    p_restore.add_argument("--dry-run", action="store_true", help="Preview rollback without modifying files")

    p_prune = sub.add_parser("prune", help="Delete oldest snapshots beyond --keep")
    p_prune.add_argument("--keep", type=int, default=20, help="Number of newest snapshots to retain")

    args = parser.parse_args(argv)
    if not args.action:
        parser.print_help()
        return 0

    mgr = WorkspaceSnapshotManager(repo_root=args.repo)
    try:
        if args.action == "create":
            rec = mgr.create_snapshot(args.files, label=args.label, note=args.note)
            if args.as_json:
                print(json.dumps(rec.to_dict(), indent=2, ensure_ascii=False))
            else:
                print(f"Snapshot created: {rec.snapshot_id} ({len(rec.files)} file(s), label={rec.label})")
            return 0

        if args.action == "list":
            records = mgr.list_snapshots(limit=args.limit)
            if args.as_json:
                print(json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False))
            elif not records:
                print("No workspace snapshots found.")
            else:
                for r in records:
                    print(f"{r.snapshot_id}  [{r.label}]  {len(r.files)} file(s)  {r.created_at}")
            return 0

        if args.action == "diff":
            res = mgr.diff_snapshot(args.snapshot_id)
            if args.as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                print(f"Snapshot diff: {res['snapshot_id']} ({res['label']})")
                for item in res["files"]:
                    print(f"- {item['rel_path']}: {item['status']}")
                    if item["unified_diff"]:
                        print(item["unified_diff"])
            return 0

        if args.action == "restore":
            res = mgr.rollback(args.snapshot_id, dry_run=args.dry_run)
            if args.as_json:
                print(json.dumps(res, indent=2, ensure_ascii=False))
            else:
                prefix = "[DRY-RUN] " if res["dry_run"] else ""
                print(
                    f"{prefix}Rollback {res['snapshot_id']}: "
                    f"{len(res['restored'])} restored, {len(res['removed'])} removed, {len(res['unchanged'])} unchanged."
                )
            return 0

        if args.action == "prune":
            removed = mgr.prune(keep=args.keep)
            if args.as_json:
                print(json.dumps({"pruned": removed, "keep": args.keep}, ensure_ascii=False))
            else:
                print(f"Pruned {removed} old snapshot(s) (kept latest {args.keep}).")
            return 0
    except (FileNotFoundError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
