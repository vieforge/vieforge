#!/usr/bin/env python3
"""Zero-Knowledge 1-Command Setup & Live Demo for TierForge Harness.

Designed for both beginners and AI Agents (Antigravity, Claude Code, Cursor, Windsurf,
Codex CLI) running on behalf of a non-technical user:
  1. Runs cross-platform pre-flight check (Python, SQLite, Ollama, GPU profile).
  2. Installs the `three-tier-orchestrator` skill for all detected agents on this machine.
  3. Writes ready-to-paste absolute-path MCP configs (`data/mcp_config.generated.json` &
     `data/codex_mcp.generated.toml`).
  4. Runs the 5-minute $0 demo (`--mock-llm` if Ollama is offline, or live Ollama if ready)
     and prints a clear Savings Receipt.

Zero OS mutations; 100% Python stdlib.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.vram_manager import recommend_local_model
from scripts.install_skill import (
    build_codex_toml_snippet,
    build_mcp_snippet,
    detect_installed_agents,
    install_skill,
    resolve_target_dir,
)
from scripts.preflight_check import print_human_report, run_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description="TierForge 1-Command Quick Setup & Demo.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview all actions without installing skills or writing config files.",
    )
    parser.add_argument(
        "--skip-demo",
        action="store_true",
        help="Skip running the 5-minute batch demo after setup.",
    )
    parser.add_argument(
        "--mock-llm",
        action="store_true",
        help="Force offline mock mode for the demo even if Ollama is running.",
    )
    args = parser.parse_args()

    print("====================================================")
    print("  TierForge Harness — 1-Command Zero-Knowledge Setup")
    print("====================================================\n")

    # 1. Pre-flight check
    ollama_url = os.getenv("TIERFORGE_LLM_BASE_URL", "http://127.0.0.1:11434")
    report = run_preflight(ollama_url)
    print_human_report(report)
    rec = recommend_local_model()
    print(f"\nRecommended local model for this machine: {rec['model']} ({rec['profile']})")

    if report["overall"] != "PASS":
        print("\n[ACTION REQUIRED] Fix the FAIL items above before continuing.")
        return 1

    # 2. Install Agent Skill for all detected agents
    agents = detect_installed_agents()
    mode = "DRY-RUN" if args.dry_run else "INSTALLING"
    print(f"\n=== Step 2: Agent Skill Setup ({mode}) ===")
    for agent in agents:
        dest = resolve_target_dir(agent)
        files = install_skill(dest, dry_run=args.dry_run)
        print(f"  [{agent}] {dest} ({len(files)} files)")

    # 3. Generate absolute-path MCP configs
    mcp_json_path = REPO_ROOT / "data" / "mcp_config.generated.json"
    codex_toml_path = REPO_ROOT / "data" / "codex_mcp.generated.toml"
    snippet = build_mcp_snippet()
    toml_text = build_codex_toml_snippet()
    if not args.dry_run:
        mcp_json_path.parent.mkdir(parents=True, exist_ok=True)
        mcp_json_path.write_text(json.dumps(snippet, indent=2), encoding="utf-8")
        codex_toml_path.write_text(toml_text, encoding="utf-8")
        print("\n=== Step 3: Generated Ready-to-Paste MCP Configs ===")
        print(f"  JSON (Antigravity/Claude/Cursor/Windsurf): {mcp_json_path}")
        print(f"  TOML (Codex CLI)                         : {codex_toml_path}")
    else:
        print("\n=== Step 3: [DRY-RUN] Would write MCP configs to data/ ===")

    # 4. Run 5-minute demo
    if not args.skip_demo and not args.dry_run:
        ollama_ready = report["checks"]["ollama"]["status"] == "OK" and report["checks"]["ollama"]["models_count"] > 0
        use_mock = args.mock_llm or not ollama_ready
        print("\n=== Step 4: Running Live 5-Minute $0 Demo ===")
        if use_mock and not args.mock_llm:
            print(
                f"  (Ollama not ready yet -> running offline --mock-llm demo. "
                f"To enable real local LLM later, run: ollama pull {rec['model']})"
            )
        demo_cmd = [
            sys.executable,
            str(REPO_ROOT / "examples" / "01_batch_doc_processor" / "run_demo.py"),
            "--reset",
        ]
        if use_mock:
            demo_cmd.append("--mock-llm")
        subprocess.run(demo_cmd, cwd=str(REPO_ROOT), check=False)

    print("\n=== Setup Complete! ===")
    print("Next steps:")
    print("  1. Your AI Agent skill is installed. Ask your agent: 'Use TierForge to batch-process my files.'")
    print(f"  2. Optional: install MCP SDK (`pip install 'mcp>=1.2.0'`) and paste {mcp_json_path.name} into your IDE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
