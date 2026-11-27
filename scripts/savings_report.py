#!/usr/bin/env python3
"""VieForge Real-World Project Savings & Usage-Tier Evaluator (`vieforge savings`).

100% Python stdlib (0 tokens). Scans local SQLite checkpoint DBs (`*.db`),
OTel GenAI traces (`genai_traces.jsonl`), and Tier-0 audit logs
(`step_audit_log.jsonl`) across one or more user projects to compute actual
tokens saved, USD saved, reduction multiplier, and Usage Tier (L0–L3).

Usage:
  vieforge savings                          # Scan current project / default data dir
  vieforge savings --projects dir1 dir2     # Aggregate across multiple projects
  vieforge savings --json                   # Machine-readable JSON output
  vieforge savings --html savings.html      # Export standalone visual HTML dashboard
"""

from __future__ import annotations

import argparse
import html
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.paths import default_data_dir, resolve_asset

TIERS_CONFIG_PATH = resolve_asset("config/savings_tiers.json")

# Conservative baseline tokens per unit when estimating Cloud-only cost
AVG_CLOUD_TOKENS_PER_BATCH_ITEM = 1500
AVG_VIEFORGE_TOKENS_PER_BATCH_ITEM = 30
AVG_CLOUD_TOKENS_PER_QA_AUDIT = 4200


def load_savings_tiers() -> dict[str, Any]:
    if TIERS_CONFIG_PATH.is_file():
        return json.loads(TIERS_CONFIG_PATH.read_text(encoding="utf-8"))
    return {
        "session_overhead_tokens": 1459,
        "cloud_input_usd_per_1m": 3.0,
        "cloud_output_usd_per_1m": 15.0,
        "tiers": [
            {"id": "L0_CASUAL", "name": "L0 — Casual", "min_items": 0, "max_items": 4, "expected_reduction_pct": "0%", "recommendation": ""},
            {"id": "L1_SOLO_DEV", "name": "L1 — Solo Indie / Vibe Coder", "min_items": 5, "max_items": 50, "expected_reduction_pct": "70%–88%", "recommendation": ""},
            {"id": "L2_POWER_STUDIO", "name": "L2 — Power Dev / AI Studio", "min_items": 51, "max_items": 500, "expected_reduction_pct": "88%–95%", "recommendation": ""},
            {"id": "L3_TEAM_ENTERPRISE", "name": "L3 — Team / Enterprise", "min_items": 501, "max_items": 999999999, "expected_reduction_pct": "94%–99%", "recommendation": ""},
        ],
    }


def classify_usage_tier(total_units: int, tiers_cfg: dict[str, Any]) -> dict[str, Any]:
    for tier in tiers_cfg.get("tiers", []):
        if int(tier["min_items"]) <= total_units <= int(tier["max_items"]):
            return tier
    return tiers_cfg["tiers"][-1]


def _scan_sqlite_db(db_path: Path) -> dict[str, Any] | None:
    try:
        with sqlite3.connect(db_path, timeout=3.0) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "items" not in tables:
                return None
            done = conn.execute("SELECT COUNT(*) FROM items WHERE status = 'done'").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
            cols = {r[1] for r in conn.execute("PRAGMA table_info(items)").fetchall()}
            cost = 0.0
            if "cost_usd" in cols:
                row = conn.execute("SELECT COALESCE(SUM(cost_usd), 0.0) FROM items").fetchone()
                cost = float(row[0] or 0.0)
            return {"db": db_path.name, "done_items": int(done), "total_items": int(total), "actual_usd": round(cost, 6)}
    except Exception:
        return None


def collect_project_savings(project_dirs: list[Path]) -> dict[str, Any]:
    tiers_cfg = load_savings_tiers()
    per_project: list[dict[str, Any]] = []

    total_done_items = 0
    total_qa_runs = 0
    total_cache_hits = 0
    total_actual_usd = 0.0
    scanned_dbs: list[str] = []

    seen_dirs: set[Path] = set()
    for raw_dir in project_dirs:
        p = raw_dir.expanduser().resolve()
        if p in seen_dirs or not p.exists():
            continue
        seen_dirs.add(p)

        search_roots = [p / "data"] if (p / "data").is_dir() else [p]
        p_done = 0
        p_qa = 0
        p_cache_hits = 0
        p_usd = 0.0
        p_dbs: list[dict[str, Any]] = []

        for root in search_roots:
            for db_file in sorted(root.glob("*.db")):
                info = _scan_sqlite_db(db_file)
                if info is not None:
                    p_dbs.append(info)
                    p_done += info["done_items"]
                    p_usd += info["actual_usd"]
                    scanned_dbs.append(str(db_file))

            audit_log = root / "step_audit_log.jsonl"
            if audit_log.is_file():
                lines = [ln for ln in audit_log.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
                p_qa += len(lines)

            trace_log = root / "genai_traces.jsonl"
            if trace_log.is_file():
                for ln in trace_log.read_text(encoding="utf-8", errors="replace").splitlines():
                    if not ln.strip():
                        continue
                    try:
                        rec = json.loads(ln)
                        if rec.get("vieforge.cache_hit"):
                            p_cache_hits += 1
                        p_usd += float(rec.get("vieforge.cost_usd", 0.0) or 0.0)
                    except json.JSONDecodeError:
                        continue

        total_done_items += p_done
        total_qa_runs += p_qa
        total_cache_hits += p_cache_hits
        total_actual_usd += p_usd

        per_project.append(
            {
                "project": p.name,
                "path": str(p),
                "checkpoint_dbs": len(p_dbs),
                "done_items": p_done,
                "tier0_qa_runs": p_qa,
                "llm_cache_hits": p_cache_hits,
                "actual_usd": round(p_usd, 6),
            }
        )

    overhead = int(tiers_cfg.get("session_overhead_tokens", 1459))
    effective_units = total_done_items + total_qa_runs + total_cache_hits

    cloud_tokens = (
        total_done_items * AVG_CLOUD_TOKENS_PER_BATCH_ITEM
        + total_qa_runs * AVG_CLOUD_TOKENS_PER_QA_AUDIT
        + total_cache_hits * 800
    )
    vieforge_tokens = (
        (overhead if effective_units > 0 else 0)
        + total_done_items * AVG_VIEFORGE_TOKENS_PER_BATCH_ITEM
    )
    saved_tokens = max(0, cloud_tokens - vieforge_tokens)
    reduction_pct = round((saved_tokens / cloud_tokens) * 100.0, 1) if cloud_tokens > 0 else 0.0
    multiplier = round(cloud_tokens / vieforge_tokens, 1) if vieforge_tokens > 0 and cloud_tokens > 0 else 1.0

    # Blended input/output cost estimate (~80% input @ $3/1M, 20% output @ $15/1M => ~$5.40/1M blended)
    blended_per_1m = 0.8 * float(tiers_cfg.get("cloud_input_usd_per_1m", 3.0)) + 0.2 * float(
        tiers_cfg.get("cloud_output_usd_per_1m", 15.0)
    )
    cloud_usd = round((cloud_tokens / 1_000_000.0) * blended_per_1m, 4)
    vieforge_usd = round(total_actual_usd + (vieforge_tokens / 1_000_000.0) * blended_per_1m, 4)
    saved_usd = round(max(0.0, cloud_usd - vieforge_usd), 4)

    tier = classify_usage_tier(effective_units, tiers_cfg)

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "projects_scanned": len(per_project),
        "totals": {
            "effective_units": effective_units,
            "checkpoint_done_items": total_done_items,
            "tier0_qa_runs": total_qa_runs,
            "llm_cache_hits": total_cache_hits,
            "cloud_only_tokens": cloud_tokens,
            "vieforge_tokens": vieforge_tokens,
            "saved_tokens": saved_tokens,
            "reduction_pct": reduction_pct,
            "multiplier": multiplier,
            "cloud_only_usd": cloud_usd,
            "vieforge_usd": vieforge_usd,
            "saved_usd": saved_usd,
        },
        "usage_tier": tier,
        "per_project": per_project,
    }


def _ascii_bar(pct: float, width: int = 28) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {clamped:5.1f}%"


def format_terminal_report(report: dict[str, Any]) -> str:
    t = report["totals"]
    tier = report["usage_tier"]
    lines = [
        "=== VieForge Real-World Project Savings Report ===",
        f"Projects scanned : {report['projects_scanned']}",
        f"Usage Tier       : {tier['name']}",
        f"Segment          : {tier.get('segment', '')}",
        f"Tracked Work     : {t['checkpoint_done_items']} batch items | {t['tier0_qa_runs']} Tier-0 QA runs | {t['llm_cache_hits']} cache hits",
        f"Token Reduction  : {_ascii_bar(t['reduction_pct'])} ({t['multiplier']}x fewer)",
        f"Tokens           : {t['cloud_only_tokens']:,} -> {t['vieforge_tokens']:,} (saved {t['saved_tokens']:,} tokens)",
        f"Estimated Spend  : ${t['cloud_only_usd']:.4f} -> ${t['vieforge_usd']:.4f} USD (saved ${t['saved_usd']:.4f} USD)",
    ]
    if tier.get("recommendation"):
        lines.append(f"Recommendation   : {tier['recommendation']}")
    if report["per_project"]:
        lines.append("\nPer-Project Breakdown:")
        for proj in report["per_project"]:
            lines.append(
                f"  - {proj['project']:<22} items={proj['done_items']:<5} qa_runs={proj['tier0_qa_runs']:<4} cache_hits={proj['llm_cache_hits']}"
            )
    return "\n".join(lines)


def render_html_report(report: dict[str, Any]) -> str:
    t = report["totals"]
    tier = report["usage_tier"]
    rows = "\n".join(
        f"<tr><td>{html.escape(str(p['project']))}</td><td>{p['checkpoint_dbs']}</td>"
        f"<td>{p['done_items']}</td><td>{p['tier0_qa_runs']}</td><td>{p['llm_cache_hits']}</td></tr>"
        for p in report["per_project"]
    )
    pct = max(0.0, min(100.0, float(t["reduction_pct"])))
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>VieForge — Real-World Token Savings Report</title>
  <style>
    body {{ margin:0; padding:32px; background:#0b0f19; color:#e2e8f0; font-family:system-ui,-apple-system,sans-serif; }}
    .wrap {{ max-width:860px; margin:0 auto; }}
    h1 {{ margin:0 0 6px; font-size:24px; color:#f8fafc; }}
    .sub {{ color:#94a3b8; margin-bottom:24px; font-size:14px; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-bottom:22px; }}
    .card {{ background:#111827; border:1px solid #1f2937; border-radius:12px; padding:16px; }}
    .label {{ font-size:12px; color:#94a3b8; text-transform:uppercase; letter-spacing:.05em; }}
    .val {{ font-size:26px; font-weight:700; margin-top:6px; color:#4ade80; }}
    .bar-bg {{ height:12px; background:#1f2937; border-radius:999px; overflow:hidden; margin:12px 0 6px; }}
    .bar-fg {{ height:100%; width:{pct:.1f}%; background:linear-gradient(90deg,#22c55e,#06b6d4); }}
    table {{ width:100%; border-collapse:collapse; background:#111827; border-radius:12px; overflow:hidden; margin-top:16px; }}
    th, td {{ text-align:left; padding:10px 14px; border-bottom:1px solid #1f2937; font-size:14px; }}
    th {{ color:#94a3b8; font-weight:600; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>VieForge Real-World Savings Report</h1>
    <div class="sub">Usage Tier: <strong>{html.escape(str(tier['name']))}</strong> &middot; Generated {html.escape(report['generated_at'])}</div>
    <div class="grid">
      <div class="card"><div class="label">Tokens Saved</div><div class="val">{t['saved_tokens']:,}</div></div>
      <div class="card"><div class="label">Reduction</div><div class="val">-{t['reduction_pct']:.1f}%</div></div>
      <div class="card"><div class="label">Multiplier</div><div class="val">{t['multiplier']}x</div></div>
      <div class="card"><div class="label">Est. USD Saved</div><div class="val">${t['saved_usd']:.4f}</div></div>
    </div>
    <div class="card">
      <div class="label">Cloud-Only ({t['cloud_only_tokens']:,} tok) vs. VieForge ({t['vieforge_tokens']:,} tok)</div>
      <div class="bar-bg"><div class="bar-fg"></div></div>
      <div class="sub" style="margin:6px 0 0;">{html.escape(str(tier.get('recommendation', '')))}</div>
    </div>
    <table>
      <thead><tr><th>Project</th><th>SQLite DBs</th><th>Batch Items</th><th>Tier-0 QA Runs</th><th>Cache Hits</th></tr></thead>
      <tbody>{rows}</tbody>
    </table>
  </div>
</body>
</html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate real local token & cost savings across your projects.")
    parser.add_argument(
        "--projects",
        nargs="*",
        default=None,
        help="One or more project directories to scan (default: current directory / VieForge data dir).",
    )
    parser.add_argument("--json", action="store_true", help="Output raw JSON report.")
    parser.add_argument("--html", type=Path, default=None, help="Export standalone visual HTML dashboard to path.")
    args = parser.parse_args(argv)

    if args.projects:
        dirs = [Path(d) for d in args.projects]
    else:
        cwd = Path.cwd()
        dirs = [cwd] if (cwd / "data").is_dir() else [default_data_dir()]

    report = collect_project_savings(dirs)
    if args.html is not None:
        args.html.parent.mkdir(parents=True, exist_ok=True)
        args.html.write_text(render_html_report(report), encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_terminal_report(report))
        if args.html is not None:
            print(f"\nVisual HTML dashboard exported -> {args.html.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
