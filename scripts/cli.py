#!/usr/bin/env python3
"""Unified `vieforge` CLI entrypoint (100% Python stdlib).

Usage:
  vieforge setup [--dry-run] [--skip-demo] [--mock-llm]
  vieforge check [--json] [--ollama-url URL]
  vieforge bench [--json]
  vieforge mcp [--check]
  vieforge audit --step <name> [--no-pytest]
  vieforge verify-os <run|compare> [...]
  vieforge install-skill [--target all] [--dry-run]
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core import __version__

SUBCOMMANDS: dict[str, tuple[str, str]] = {
    "setup": ("scripts.quick_setup", "1-command zero-knowledge setup, skill installer & $0 demo"),
    "check": ("scripts.preflight_check", "Check Python, SQLite WAL, Ollama/OpenAI-compat LLM & GPU"),
    "bench": ("scripts.benchmark_token_savings", "Run reproducible $0 Cloud token savings benchmark"),
    "mcp": ("mcp.worker_mcp_server", "Start lightweight stdio MCP worker server (or --check)"),
    "audit": ("scripts.audit_step", "Run 2-Axis quality gate & export 3-Judge audit prompt"),
    "verify-os": ("scripts.verify_cross_os", "Run clean-wheel E2E + multi-process OS lock & parity gate"),
    "install-skill": ("scripts.install_skill", "Install Agent Skill & generate JSON/TOML MCP configs"),
}


def _print_help() -> None:
    print(f"VieForge v{__version__} — Code First, Local LLM Second, Cloud Last.\n")
    print("Usage: vieforge <command> [options]\n")
    print("Commands:")
    for name, (_mod, desc) in SUBCOMMANDS.items():
        print(f"  {name:<15} {desc}")
    print("\nRun `vieforge <command> --help` for command-specific options.")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or args[0] in ("-h", "--help"):
        _print_help()
        return 0
    if args[0] in ("-V", "--version"):
        print(f"vieforge {__version__}")
        return 0

    cmd = args[0]
    target = SUBCOMMANDS.get(cmd)
    if target is None:
        print(f"Unknown command: '{cmd}'. Run `vieforge --help` to see available commands.", file=sys.stderr)
        return 2

    mod_name, _desc = target
    mod = importlib.import_module(mod_name)
    prev_argv = sys.argv
    sys.argv = [f"vieforge {cmd}", *args[1:]]
    try:
        rc = mod.main()
        return int(rc) if isinstance(rc, int) else 0
    except SystemExit as exc:
        return int(exc.code) if isinstance(exc.code, int) else 0
    finally:
        sys.argv = prev_argv


if __name__ == "__main__":
    raise SystemExit(main())
