#!/usr/bin/env python3
"""VieForge 30-Second Savings Receipt (`vieforge receipt`).

100% Python stdlib, zero dependencies, $0 local execution.
Runs the reproducible 3-workload token benchmark (and optionally scans local
project SQLite checkpoints / audit logs) in under 1 second, then renders a
shareable ASCII receipt, Markdown snippet, JSON payload, or standalone SVG card.

Usage:
  vieforge receipt                              # Instant $0 ASCII receipt
  vieforge receipt --markdown                   # Copy-paste Markdown for PRs / X / README
  vieforge receipt --svg savings_receipt.svg    # Export standalone dark-mode SVG card
  vieforge receipt --projects .                 # Include real local project telemetry
  vieforge receipt --json                       # Machine-readable JSON output
"""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import __version__
from scripts.benchmark_token_savings import run_all_benchmarks
from scripts.savings_report import collect_project_savings


def build_receipt_payload(
    project_dirs: list[Path] | None = None,
) -> dict[str, Any]:
    """Run the $0 benchmark (plus optional local scan) and assemble receipt data."""
    t0 = time.perf_counter()
    bench = run_all_benchmarks()

    workloads = bench["workloads"]
    without_usd = round(sum(float(w["without_vieforge_usd"]) for w in workloads), 4)
    with_usd = round(sum(float(w["with_vieforge_usd"]) for w in workloads), 4)
    # Include one-time session overhead at $3/1M input rate
    overhead_tokens = int(bench["baseline_overhead"]["total_session_overhead_tokens"])
    overhead_usd = round((overhead_tokens / 1_000_000.0) * 3.0, 4)
    net_with_usd = round(with_usd + overhead_usd, 4)
    saved_usd = round(max(0.0, without_usd - net_with_usd), 4)

    net = bench["combined_net"]
    without_tokens = int(net["without_vieforge_tokens"])
    with_tokens = int(net["with_vieforge_tokens_including_overhead"])
    saved_tokens = max(0, without_tokens - with_tokens)

    local_summary: dict[str, Any] | None = None
    if project_dirs:
        local_report = collect_project_savings(project_dirs)
        if local_report["totals"]["effective_units"] > 0:
            local_summary = local_report

    elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)

    return {
        "schema_version": "1.0",
        "vieforge_version": __version__,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "elapsed_ms": elapsed_ms,
        "benchmark": {
            "without_tokens": without_tokens,
            "with_tokens": with_tokens,
            "saved_tokens": saved_tokens,
            "reduction_pct": float(net["net_reduction_pct"]),
            "multiplier": float(net["net_multiplier"]),
            "without_usd": without_usd,
            "with_usd": net_with_usd,
            "saved_usd": saved_usd,
            "session_overhead_tokens": overhead_tokens,
            "workloads": workloads,
        },
        "local_projects": local_summary,
    }


def _bar(pct: float, width: int = 24) -> str:
    clamped = max(0.0, min(100.0, pct))
    filled = int(round((clamped / 100.0) * width))
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {clamped:5.1f}%"


def render_ascii_receipt(payload: dict[str, Any]) -> str:
    """Render a compact 76-col ASCII Savings Receipt for terminal screenshots."""
    b = payload["benchmark"]
    w = 74
    sep = "+" + ("-" * (w - 2)) + "+"

    def row(text: str) -> str:
        clipped = text[: w - 4]
        return f"| {clipped:<{w - 4}} |"

    lines = [
        sep,
        row(f"VIEFORGE v{payload['vieforge_version']} — 30-SECOND SAVINGS RECEIPT ($0 LOCAL RUN)"),
        row(f"Measured in {payload['elapsed_ms']:.1f} ms | 100% Python Stdlib | MIT"),
        sep,
        row(f"Cloud-Only Baseline : {b['without_tokens']:>9,} tokens  (${b['without_usd']:.4f} USD)"),
        row(f"VieForge + Overhead : {b['with_tokens']:>9,} tokens  (${b['with_usd']:.4f} USD)"),
        row(f"NET TOKENS SAVED    : {b['saved_tokens']:>9,} tokens  (-{b['reduction_pct']:.1f}% | {b['multiplier']:.1f}x)"),
        row(f"Savings Bar         : {_bar(b['reduction_pct'])}"),
        sep,
        row("Workload Breakdown (reproducible $0 run):"),
    ]
    for wl in b["workloads"]:
        name = str(wl["name"])[:36]
        lines.append(
            row(
                f"  - {name:<36} -{wl['reduction_pct']:>4.1f}% ({wl['multiplier']:>5.1f}x)"
            )
        )
    lines.append(row(f"  - One-time session overhead            +{b['session_overhead_tokens']:,} tok (paid once)"))

    local = payload.get("local_projects")
    if local:
        lt = local["totals"]
        lines.append(sep)
        lines.append(row(f"Real Local Projects Scanned ({local['projects_scanned']}): {local['usage_tier']['name']}"))
        lines.append(
            row(
                f"  Saved {lt['saved_tokens']:,} tok (-{lt['reduction_pct']:.1f}%) | "
                f"Est. ${lt['saved_usd']:.4f} USD"
            )
        )

    lines.append(sep)
    lines.append(row("Reproduce: pip install vieforge && vieforge receipt"))
    lines.append(row("Website  : https://vieforge.dev  |  GitHub: vieforge/vieforge"))
    lines.append(sep)
    return "\n".join(lines)


def render_markdown_receipt(payload: dict[str, Any]) -> str:
    """Render a shareable Markdown receipt for GitHub PRs, READMEs, or social posts."""
    b = payload["benchmark"]
    lines = [
        f"### VieForge v{payload['vieforge_version']} — 30-Second Savings Receipt (`$0` Local Run)",
        "",
        f"> Measured locally in **{payload['elapsed_ms']:.1f} ms** (100% Python stdlib, 0 dependencies).",
        "",
        "| Metric | Cloud-Only | With VieForge | Net Savings |",
        "| :--- | ---: | ---: | ---: |",
        (
            f"| **Total Tokens** *(incl. {b['session_overhead_tokens']:,} overhead)* "
            f"| {b['without_tokens']:,} | {b['with_tokens']:,} "
            f"| **-{b['reduction_pct']:.1f}% ({b['multiplier']:.1f}x)** |"
        ),
        (
            f"| **Est. Frontier API Cost** | `${b['without_usd']:.4f}` "
            f"| `${b['with_usd']:.4f}` | **`${b['saved_usd']:.4f}` saved** |"
        ),
        "",
        "| Workload | Cloud-Only | VieForge | Reduction |",
        "| :--- | ---: | ---: | ---: |",
    ]
    for wl in b["workloads"]:
        lines.append(
            f"| {wl['name']} | {wl['without_vieforge_tokens']:,} "
            f"| {wl['with_vieforge_tokens']:,} | **-{wl['reduction_pct']:.1f}% ({wl['multiplier']:.1f}x)** |"
        )

    local = payload.get("local_projects")
    if local:
        lt = local["totals"]
        lines.extend(
            [
                "",
                f"**Real Local Telemetry ({local['projects_scanned']} project(s) — {local['usage_tier']['name']}):** "
                f"saved **{lt['saved_tokens']:,} tokens** (`-{lt['reduction_pct']:.1f}%`, est. `${lt['saved_usd']:.4f}` USD).",
            ]
        )

    lines.extend(
        [
            "",
            "Reproduce on your own machine in 30 seconds (`$0`):",
            "```bash",
            "pip install vieforge && vieforge receipt",
            "```",
        ]
    )
    return "\n".join(lines)


def render_svg_receipt(payload: dict[str, Any]) -> str:
    """Render a crisp, standalone dark-mode SVG Savings Receipt card (zero deps)."""
    b = payload["benchmark"]
    pct = max(0.0, min(100.0, float(b["reduction_pct"])))
    bar_width = int(round((pct / 100.0) * 688))
    ver = html.escape(str(payload["vieforge_version"]))
    elapsed = f"{payload['elapsed_ms']:.1f} ms"

    wl_rows: list[str] = []
    y = 296
    for wl in b["workloads"][:3]:
        name = html.escape(str(wl["name"])[:44])
        stat = html.escape(
            f"{wl['without_vieforge_tokens']:,} -> {wl['with_vieforge_tokens']:,} tok  (-{wl['reduction_pct']:.1f}%)"
        )
        wl_rows.append(
            f'  <text x="56" y="{y}" fill="#cbd5e1" font-size="13" font-family="ui-monospace,SFMono-Regular,Consolas,monospace">{name}</text>'
        )
        wl_rows.append(
            f'  <text x="744" y="{y}" text-anchor="end" fill="#4ade80" font-size="13" font-weight="600" font-family="ui-monospace,SFMono-Regular,Consolas,monospace">{stat}</text>'
        )
        y += 26

    wl_svg = "\n".join(wl_rows)
    return f"""<svg xmlns="http://www.w3.org/2000/svg" width="800" height="440" viewBox="0 0 800 440" role="img" aria-label="VieForge Savings Receipt">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0%" stop-color="#0b1120"/>
      <stop offset="100%" stop-color="#111827"/>
    </linearGradient>
    <linearGradient id="bar" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0%" stop-color="#22c55e"/>
      <stop offset="100%" stop-color="#06b6d4"/>
    </linearGradient>
  </defs>
  <rect width="800" height="440" rx="18" fill="url(#bg)"/>
  <rect x="20" y="20" width="760" height="400" rx="14" fill="none" stroke="#1e293b" stroke-width="1.5"/>

  <text x="56" y="64" fill="#f8fafc" font-size="22" font-weight="700" font-family="system-ui,-apple-system,Segoe UI,sans-serif">VieForge v{ver} — Savings Receipt</text>
  <text x="56" y="88" fill="#94a3b8" font-size="13" font-family="system-ui,-apple-system,Segoe UI,sans-serif">Code First, Local LLM Second, Cloud Last &#183; Measured in {elapsed} ($0 Local Run)</text>
  <rect x="614" y="44" width="130" height="28" rx="14" fill="#052e16" stroke="#166534"/>
  <text x="679" y="62" text-anchor="middle" fill="#4ade80" font-size="12" font-weight="600" font-family="system-ui,sans-serif">MIT &#183; 0 DEPS</text>

  <rect x="56" y="112" width="216" height="84" rx="10" fill="#0f172a" stroke="#1e293b"/>
  <text x="72" y="136" fill="#94a3b8" font-size="11" font-family="system-ui,sans-serif">NET TOKEN REDUCTION</text>
  <text x="72" y="174" fill="#4ade80" font-size="30" font-weight="700" font-family="system-ui,sans-serif">-{b['reduction_pct']:.1f}%</text>

  <rect x="292" y="112" width="216" height="84" rx="10" fill="#0f172a" stroke="#1e293b"/>
  <text x="308" y="136" fill="#94a3b8" font-size="11" font-family="system-ui,sans-serif">EFFICIENCY MULTIPLIER</text>
  <text x="308" y="174" fill="#38bdf8" font-size="30" font-weight="700" font-family="system-ui,sans-serif">{b['multiplier']:.1f}x</text>

  <rect x="528" y="112" width="216" height="84" rx="10" fill="#0f172a" stroke="#1e293b"/>
  <text x="544" y="136" fill="#94a3b8" font-size="11" font-family="system-ui,sans-serif">TOKENS SAVED</text>
  <text x="544" y="174" fill="#f8fafc" font-size="28" font-weight="700" font-family="system-ui,sans-serif">{b['saved_tokens']:,}</text>

  <text x="56" y="226" fill="#94a3b8" font-size="12" font-family="system-ui,sans-serif">Cloud-Only ({b['without_tokens']:,} tok) vs. VieForge + Overhead ({b['with_tokens']:,} tok)</text>
  <rect x="56" y="236" width="688" height="14" rx="7" fill="#1e293b"/>
  <rect x="56" y="236" width="{bar_width}" height="14" rx="7" fill="url(#bar)"/>

  <line x1="56" y1="268" x2="744" y2="268" stroke="#1e293b" stroke-width="1"/>
{wl_svg}

  <line x1="56" y1="372" x2="744" y2="372" stroke="#1e293b" stroke-width="1"/>
  <text x="56" y="400" fill="#94a3b8" font-size="13" font-family="ui-monospace,SFMono-Regular,Consolas,monospace">$ pip install vieforge &amp;&amp; vieforge receipt</text>
  <text x="744" y="400" text-anchor="end" fill="#64748b" font-size="12" font-family="system-ui,sans-serif">vieforge.dev</text>
</svg>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a 30-second $0 VieForge Savings Receipt (ASCII, Markdown, JSON, or SVG)."
    )
    parser.add_argument(
        "--projects",
        nargs="*",
        default=None,
        help="Optional local project directories to include real checkpoint/QA telemetry.",
    )
    parser.add_argument("--markdown", action="store_true", help="Emit shareable Markdown receipt.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON payload.")
    parser.add_argument("--svg", type=Path, default=None, help="Export standalone SVG receipt card to path.")
    args = parser.parse_args(argv)

    project_dirs = [Path(p) for p in args.projects] if args.projects else None
    payload = build_receipt_payload(project_dirs=project_dirs)

    if args.svg is not None:
        args.svg.parent.mkdir(parents=True, exist_ok=True)
        args.svg.write_text(render_svg_receipt(payload), encoding="utf-8")

    if args.as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    elif args.markdown:
        print(render_markdown_receipt(payload))
    else:
        print(render_ascii_receipt(payload))
        if args.svg is not None:
            print(f"\nSVG receipt exported -> {args.svg.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
