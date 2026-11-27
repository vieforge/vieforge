#!/usr/bin/env python3
"""Doc-Drift Mechanical Gate for VieForge (100% Python Stdlib, MIT).

Scans Markdown files for dated metric markers:
    <!--measure: name=value @YYYY-MM-DD-->

Flags any marker older than ``--max-age-days`` so documentation numbers never
silently go stale. Inspired by ``nextcore-workflow`` (MIT), implemented from
scratch in zero-dependency Python stdlib.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
from pathlib import Path
from typing import Any

MEASURE_MARKER_RE = re.compile(
    r"<!--\s*measure:\s*([a-zA-Z0-9_.-]+)=([^\s@]+)\s*@(\d{4}-\d{2}-\d{2})\s*-->"
)
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", ".pytest_cache"}


def evaluate_doc_drift(
    repo_path: Path,
    max_age_days: int = 30,
    today: dt.date | None = None,
) -> dict[str, Any]:
    """Scan all Markdown files under ``repo_path`` for stale measure markers."""
    ref_day = today or dt.date.today()
    markers: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []

    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for fname in files:
            if not fname.lower().endswith(".md"):
                continue
            fpath = Path(root) / fname
            try:
                text = fpath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            rel = str(fpath.relative_to(repo_path)).replace("\\", "/")
            for match in MEASURE_MARKER_RE.finditer(text):
                name, value, date_str = match.group(1), match.group(2), match.group(3)
                try:
                    age_days = (ref_day - dt.date.fromisoformat(date_str)).days
                except ValueError:
                    age_days = 9999
                entry = {
                    "file": rel,
                    "name": name,
                    "value": value,
                    "date": date_str,
                    "age_days": age_days,
                }
                markers.append(entry)
                if age_days > max_age_days:
                    stale.append(entry)

    return {
        "status": "STALE" if stale else "PASS",
        "total_markers": len(markers),
        "max_age_days": max_age_days,
        "stale": stale,
        "markers": markers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="VieForge doc-drift gate — detect expired metric markers in Markdown docs."
    )
    parser.add_argument("--repo", default=".", help="Path to repository root (default: .)")
    parser.add_argument("--max-age-days", type=int, default=30, help="Maximum marker age in days (default: 30)")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON report")
    args = parser.parse_args(argv)

    result = evaluate_doc_drift(Path(args.repo).resolve(), max_age_days=args.max_age_days)
    if args.as_json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(
            f"doc-drift: {result['status']} "
            f"({result['total_markers']} marker(s), max_age={args.max_age_days}d)"
        )
        for item in result["stale"]:
            print(
                f"  STALE: {item['file']} -> {item['name']}={item['value']} "
                f"(@{item['date']}, {item['age_days']}d old)"
            )
    return 1 if result["stale"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
