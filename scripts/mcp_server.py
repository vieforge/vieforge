#!/usr/bin/env python3
"""Lightweight MCP Worker Server exposing TierForge Tier 2/3 tools to Cloud Agents.

Works with Antigravity, Claude Code, Cursor, Windsurf, and Codex CLI.
All tool responses are compact JSON so Tier 1 Cloud Agents save 70-90% context tokens.

Run:
  python mcp/worker_mcp_server.py            # Starts stdio MCP server (requires `mcp` extra)
  python mcp/worker_mcp_server.py --check    # Self-check tool functions without MCP SDK
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import shlex
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cost_tracker import CostTracker
from core.llm_router import LLMRouter
from core.output_trimmer import compact_job_summary
from core.paths import default_data_dir
from core.state_db import StateDB, compute_input_hash, scoped_job_id
from core.subprocess_runner import run_isolated
from core.vram_manager import recommend_local_model
from scripts.benchmark_token_savings import run_all_benchmarks
from scripts.preflight_check import run_preflight
from scripts.savings_report import collect_project_savings

PANEL_APP_URI = "ui://vieforge/panel.html"
PANEL_HTML_PATH = REPO_ROOT / "plugins" / "vieforge-lens" / "ui" / "panel.html"
PANEL_BUNDLED_HTML_PATH = REPO_ROOT / "core" / "_bundled_assets" / "plugins" / "vieforge-lens" / "ui" / "panel.html"

_ENV_DB = os.getenv("TIERFORGE_STATE_DB", "").strip()
DEFAULT_DB = Path(_ENV_DB).expanduser() if _ENV_DB else (default_data_dir() / "tierforge_state.db")


def tool_preflight_env() -> str:
    """Return compact JSON of Python, SQLite, Ollama, GPU profile, and recommended local model."""
    url = os.getenv("TIERFORGE_LLM_BASE_URL", "http://127.0.0.1:11434")
    report = run_preflight(url)
    report["recommended_local_model"] = recommend_local_model()
    return compact_job_summary(report)


def tool_run_trimmed_command(
    command: str,
    cwd: str = "",
    timeout_sec: float = 120.0,
    max_lines: int = 60,
) -> str:
    """Run a shell command in an isolated child process and return trimmed stdout/stderr + savings."""
    raw_parts = shlex.split(command, posix=(os.name != "nt"))
    parts = [
        p[1:-1] if len(p) >= 2 and p[0] == p[-1] and p[0] in ('"', "'") else p
        for p in raw_parts
    ]
    if not parts:
        return compact_job_summary({"error": "Empty command."})
    res = run_isolated(
        parts,
        cwd=cwd or str(REPO_ROOT),
        timeout_sec=timeout_sec,
        max_output_lines=max_lines,
    )
    return compact_job_summary(res.to_dict())


def tool_estimate_batch_dry_run(
    provider: str,
    item_count: int,
    avg_input_tokens: int = 1500,
    avg_output_tokens: int = 500,
) -> str:
    """Estimate job cost via pricing.json before spending and report approval-gate status."""
    tracker = CostTracker(job_id="dry_run_preview")
    est = tracker.estimate_dry_run(
        provider=provider,
        item_count=item_count,
        avg_input_tokens=avg_input_tokens,
        avg_output_tokens=avg_output_tokens,
    )
    return compact_job_summary(est.to_dict())


_BINARY_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp",
    ".mp4", ".mov", ".mkv", ".avi", ".mp3", ".wav", ".flac",
    ".zip", ".tar", ".gz", ".7z", ".rar", ".pdf", ".exe", ".dll",
    ".so", ".dylib", ".pyc", ".whl", ".db", ".sqlite", ".sqlite3",
}


def _load_disk_items(
    *,
    input_path: str = "",
    input_glob: str = "",
    max_chars_per_item: int = 8000,
) -> tuple[list[dict[str, str]], str, int]:
    """Load batch items directly from disk so Tier 1 Cloud never pays to transmit file contents."""
    items: list[dict[str, str]] = []
    total_raw_chars = 0
    cap = max(200, int(max_chars_per_item))

    if input_path:
        p = Path(input_path).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"input_path not found: {p}")

        if p.is_file():
            suffix = p.suffix.lower()
            if suffix in (".jsonl", ".ndjson"):
                for lineno, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
                    s = line.strip()
                    if not s:
                        continue
                    obj = json.loads(s)
                    if isinstance(obj, dict):
                        item_id = str(obj.get("id") or f"line_{lineno}")
                        text = str(obj.get("text") or obj.get("content") or obj.get("body") or json.dumps(obj, ensure_ascii=False))
                    else:
                        item_id = f"line_{lineno}"
                        text = str(obj)
                    total_raw_chars += len(text)
                    items.append({"id": item_id, "text": text[:cap]})
                return items, "disk_jsonl", total_raw_chars

            if suffix == ".json":
                data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
                if not isinstance(data, list):
                    raise ValueError(f"JSON input file must contain a list of items: {p}")
                for idx, entry in enumerate(data, start=1):
                    if isinstance(entry, dict):
                        item_id = str(entry.get("id") or f"item_{idx}")
                        text = str(entry.get("text") or entry.get("content") or entry.get("body") or json.dumps(entry, ensure_ascii=False))
                    else:
                        item_id = f"item_{idx}"
                        text = str(entry)
                    total_raw_chars += len(text)
                    items.append({"id": item_id, "text": text[:cap]})
                return items, "disk_json", total_raw_chars

            if suffix == ".csv":
                with p.open("r", encoding="utf-8", errors="replace", newline="") as fh:
                    reader = csv.DictReader(fh)
                    for idx, row in enumerate(reader, start=1):
                        item_id = str(row.get("id") or f"row_{idx}")
                        text = str(
                            row.get("text")
                            or row.get("content")
                            or " | ".join(f"{k}={v}" for k, v in row.items() if v is not None)
                        )
                        total_raw_chars += len(text)
                        items.append({"id": item_id, "text": text[:cap]})
                return items, "disk_csv", total_raw_chars

            text = p.read_text(encoding="utf-8", errors="replace")
            total_raw_chars += len(text)
            items.append({"id": p.name, "text": text[:cap]})
            return items, "disk_file", total_raw_chars

        # Directory mode
        pattern = input_glob or "*"
        candidates = sorted(f for f in p.glob(pattern) if f.is_file())
        for f in candidates:
            if f.suffix.lower() in _BINARY_SUFFIXES or f.name.startswith("."):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                continue
            rel_id = str(f.relative_to(p)).replace("\\", "/")
            total_raw_chars += len(text)
            items.append({"id": rel_id, "text": text[:cap]})
        return items, "disk_dir", total_raw_chars

    if input_glob:
        matched = sorted(Path(m) for m in glob.glob(input_glob, recursive=True) if Path(m).is_file())
        for f in matched:
            if f.suffix.lower() in _BINARY_SUFFIXES or f.name.startswith("."):
                continue
            text = f.read_text(encoding="utf-8", errors="replace")
            if not text.strip():
                continue
            total_raw_chars += len(text)
            items.append({"id": str(f).replace("\\", "/"), "text": text[:cap]})
        return items, "disk_glob", total_raw_chars

    raise ValueError("Provide either items_json, input_path, or input_glob.")


def tool_run_local_batch(
    job_id: str,
    items_json: str = "",
    system_prompt: str = "Extract a concise JSON summary.",
    required_keys_csv: str = "",
    model: str = "",
    mock: bool = False,
    db_path: str = "",
    project_id: str = "",
    input_path: str = "",
    input_glob: str = "",
    max_chars_per_item: int = 8000,
) -> str:
    """Process a batch via Tier 2 local LLM with SQLite checkpointing and VRAM hygiene.

    Prefer `input_path` (directory / .jsonl / .json / .csv) or `input_glob` (e.g. `"docs/*.md"`)
    so Tier 2 reads files directly from disk with zero Cloud token transfer.
    Alternatively pass inline `items_json`: `[{"id": "item_1", "text": "..."}, ...]`.
    Pass `project_id` when running multiple workspaces concurrently so job_ids stay isolated.
    """
    input_source = "inline_json"
    total_raw_chars = 0
    if input_path or input_glob:
        try:
            raw_items, input_source, total_raw_chars = _load_disk_items(
                input_path=input_path,
                input_glob=input_glob,
                max_chars_per_item=max_chars_per_item,
            )
            if not raw_items:
                return compact_job_summary({"error": "No readable text items matched input_path/input_glob."})
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            return compact_job_summary({"error": f"Failed loading disk batch input: {exc}"})
    else:
        try:
            raw_items = json.loads(items_json)
            if not isinstance(raw_items, list):
                raise ValueError("items_json must be a JSON list.")
            total_raw_chars = sum(len(str(e.get("text", ""))) for e in raw_items if isinstance(e, dict))
        except (json.JSONDecodeError, ValueError) as exc:
            return compact_job_summary({"error": f"Invalid items_json: {exc}"})

    effective_job_id = scoped_job_id(project_id, job_id) if project_id else job_id
    req_keys = [k.strip() for k in required_keys_csv.split(",") if k.strip()]
    resolved_db = Path(db_path) if db_path else DEFAULT_DB
    router = LLMRouter(model=model or None, mock=mock)
    tracker = CostTracker(job_id=effective_job_id)

    item_map: dict[str, str] = {}
    reg_pairs: list[tuple[str, str]] = []
    for idx, entry in enumerate(raw_items):
        item_id = str(entry.get("id", f"item_{idx + 1}"))
        text = str(entry.get("text", ""))
        item_map[item_id] = text
        reg_pairs.append((item_id, compute_input_hash(text)))

    processed_now = 0
    with StateDB(resolved_db) as db:
        db.register_items(effective_job_id, reg_pairs)
        pending = db.get_pending_items(effective_job_id, max_retries=tracker.max_retry_per_item)

        if pending:
            with router.batch_session(keep_alive="2m", unload_on_exit=True):
                for rec in pending:
                    text = item_map.get(rec.item_id, "")
                    try:
                        resp = router.generate_json(
                            text,
                            system=system_prompt,
                            required_keys=req_keys or None,
                            max_retries=2,
                        )
                        db.mark_done(effective_job_id, rec.item_id, output=resp.parsed_json, cost_usd=0.0)
                        tracker.record("tier2_local", f"ollama/{router.model}", 0.0, item_id=rec.item_id)
                        processed_now += 1
                    except Exception as exc:
                        db.mark_failed(effective_job_id, rec.item_id, str(exc), cost_usd=0.0)

        summary = db.get_job_summary(effective_job_id)

    receipt = tracker.build_savings_receipt(
        items_processed=summary["done"],
        cloud_baseline_provider="external/text-frontier",
    )
    return compact_job_summary(
        {
            "input_source": input_source,
            "total_raw_chars_loaded": total_raw_chars,
            "summary": summary,
            "processed_this_run": processed_now,
            "skipped_from_checkpoint": len(raw_items) - len(pending),
            "savings_receipt": receipt.to_dict(),
        }
    )


def tool_get_job_summary(job_id: str, db_path: str = "", project_id: str = "") -> str:
    """Fetch a compact SQLite checkpoint summary for an existing job."""
    resolved_db = Path(db_path) if db_path else DEFAULT_DB
    effective_job_id = scoped_job_id(project_id, job_id) if project_id else job_id
    if not resolved_db.exists():
        return compact_job_summary({"job_id": effective_job_id, "error": "State DB not found."})
    with StateDB(resolved_db) as db:
        return compact_job_summary(db.get_job_summary(effective_job_id))


def _load_panel_html() -> str:
    for candidate in (PANEL_HTML_PATH, PANEL_BUNDLED_HTML_PATH):
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")
    return "<!doctype html><html><body><h3>VieForge Lens UI asset not found.</h3></body></html>"


def tool_open_vieforge_panel(project_dir: str = "") -> dict[str, Any]:
    """Return structured live savings + 3-tier health payload for the VieForge Lens Side Pane."""
    target = Path(project_dir).expanduser() if project_dir else (Path.cwd() if (Path.cwd() / "data").is_dir() else default_data_dir())
    url = os.getenv("TIERFORGE_LLM_BASE_URL", "http://127.0.0.1:11434")
    preflight = run_preflight(url)
    preflight["recommended_local_model"] = recommend_local_model()
    savings = collect_project_savings([target])
    return {
        "panel": "vieforge-lens",
        "savings": savings,
        "preflight": preflight,
    }


def tool_app_run_benchmark() -> dict[str, Any]:
    """App-only helper: run reproducible 0.3s benchmark and return updated panel telemetry."""
    payload = tool_open_vieforge_panel()
    payload["benchmark"] = run_all_benchmarks()
    return payload


def build_mcp_server() -> Any:
    """Create and configure the MCP server (supports both mcp 2.x MCPServer and 1.x FastMCP)."""
    server_cls: Any = None
    is_v2 = False
    try:
        from mcp.server.mcpserver import MCPServer

        server_cls = MCPServer
        is_v2 = True
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP

            server_cls = FastMCP
        except ImportError as exc:
            raise RuntimeError(
                "The optional 'mcp' package is not installed. "
                "Install it with: uv pip install '.[mcp]' or pip install 'mcp>=1.2.0'."
            ) from exc

    mcp = server_cls("tierforge-worker")
    mcp.tool(name="preflight_env")(tool_preflight_env)
    mcp.tool(name="run_trimmed_command")(tool_run_trimmed_command)
    mcp.tool(name="estimate_batch_dry_run")(tool_estimate_batch_dry_run)
    mcp.tool(name="run_local_batch")(tool_run_local_batch)
    mcp.tool(name="get_job_summary")(tool_get_job_summary)

    pane_meta = {
        "ui": {"resourceUri": PANEL_APP_URI},
        "com.google.antigravity/ui": {
            "entrypoints": [
                {"type": "aux_pane", "title": "VieForge Lens"},
                {"type": "global", "title": "VieForge Lens"},
            ]
        },
    }
    app_only_meta = {"ui": {"visibility": ["app"]}}

    if is_v2:
        mcp.tool(name="open_vieforge_panel", title="VieForge Lens", meta=pane_meta, structured_output=True)(
            tool_open_vieforge_panel
        )
        mcp.tool(name="app_refresh_panel", meta=app_only_meta, structured_output=True)(tool_open_vieforge_panel)
        mcp.tool(name="app_run_benchmark", meta=app_only_meta, structured_output=True)(tool_app_run_benchmark)
        mcp.resource(
            PANEL_APP_URI,
            name="VieForge Lens UI",
            mime_type="text/html;profile=mcp-app",
            meta={"ui": {"prefersBorder": False}},
        )(_load_panel_html)
    else:
        mcp.tool(name="open_vieforge_panel")(tool_open_vieforge_panel)
        mcp.resource(PANEL_APP_URI, mime_type="text/html;profile=mcp-app")(_load_panel_html)

    return mcp


def main() -> int:
    parser = argparse.ArgumentParser(description="TierForge lightweight MCP Worker Server.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Run a local self-check of tool functions and exit without starting stdio MCP.",
    )
    args = parser.parse_args()

    if args.check:
        out = json.loads(tool_preflight_env())
        print(json.dumps({"mcp_worker_self_check": "OK", "preflight": out["overall"]}, indent=2))
        return 0

    server = build_mcp_server()
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
