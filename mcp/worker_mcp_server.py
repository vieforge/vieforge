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
from core.state_db import StateDB, compute_input_hash, scoped_job_id
from core.subprocess_runner import run_isolated
from core.vram_manager import recommend_local_model
from scripts.preflight_check import run_preflight

DEFAULT_DB = REPO_ROOT / os.getenv("TIERFORGE_STATE_DB", "data/tierforge_state.db")


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


def tool_run_local_batch(
    job_id: str,
    items_json: str,
    system_prompt: str = "Extract a concise JSON summary.",
    required_keys_csv: str = "",
    model: str = "",
    mock: bool = False,
    db_path: str = "",
    project_id: str = "",
) -> str:
    """Process a batch of items via Tier 2 local LLM with SQLite checkpointing and VRAM hygiene.

    `items_json` must be a JSON array of objects: `[{"id": "item_1", "text": "..."}, ...]`.
    Pass `project_id` when running multiple workspaces concurrently so job_ids stay isolated.
    """
    try:
        raw_items = json.loads(items_json)
        if not isinstance(raw_items, list):
            raise ValueError("items_json must be a JSON list.")
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


def build_mcp_server() -> Any:
    """Create and configure the FastMCP server (requires `mcp` optional dependency)."""
    try:
        from mcp.server.fastmcp import FastMCP
    except ImportError as exc:
        raise RuntimeError(
            "The optional 'mcp' package is not installed. "
            "Install it with: uv pip install '.[mcp]' or pip install 'mcp>=1.2.0'."
        ) from exc

    mcp = FastMCP("tierforge-worker")
    mcp.tool(name="preflight_env")(tool_preflight_env)
    mcp.tool(name="run_trimmed_command")(tool_run_trimmed_command)
    mcp.tool(name="estimate_batch_dry_run")(tool_estimate_batch_dry_run)
    mcp.tool(name="run_local_batch")(tool_run_local_batch)
    mcp.tool(name="get_job_summary")(tool_get_job_summary)
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
