#!/usr/bin/env python3
"""Cross-Platform Multi-Agent Skill Installer & Absolute-Path MCP Config Generator.

Supports:
  - Antigravity (`~/.gemini/config/skills/three-tier-orchestrator`)
  - Claude Code (`~/.claude/skills/three-tier-orchestrator`)
  - Cursor (`~/.cursor/skills/three-tier-orchestrator`)
  - Windsurf (`~/.codeium/windsurf/skills/three-tier-orchestrator`)
  - Codex CLI / Open Agent Standard (`~/.agents/skills/three-tier-orchestrator`)
  - `--target all` (auto-detects installed agent roots on this machine)

Generates both JSON (`mcpServers` for Antigravity/Claude/Cursor/Windsurf) and TOML
(`[mcp_servers.tierforge-worker]` for Codex CLI) using exact absolute paths.
Zero OS mutations.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_SRC = REPO_ROOT / "skills" / "three-tier-orchestrator"
MCP_SERVER_PATH = REPO_ROOT / "mcp" / "worker_mcp_server.py"

ALL_TARGETS = ("antigravity", "claude", "cursor", "windsurf", "codex")


def _agent_roots(home: Path | None = None) -> dict[str, tuple[Path, Path]]:
    """Return mapping of agent -> (detection_root, skill_destination_dir)."""
    h = home or Path.home()
    return {
        "antigravity": (h / ".gemini", h / ".gemini" / "config" / "skills" / "three-tier-orchestrator"),
        "claude": (h / ".claude", h / ".claude" / "skills" / "three-tier-orchestrator"),
        "cursor": (h / ".cursor", h / ".cursor" / "skills" / "three-tier-orchestrator"),
        "windsurf": (h / ".codeium" / "windsurf", h / ".codeium" / "windsurf" / "skills" / "three-tier-orchestrator"),
        "codex": (h / ".codex", h / ".agents" / "skills" / "three-tier-orchestrator"),
    }


def detect_installed_agents(home: Path | None = None) -> list[str]:
    roots = _agent_roots(home)
    found = [name for name, (detect_dir, _dest) in roots.items() if detect_dir.exists()]
    return found or ["antigravity"]


def resolve_target_dir(target: str, custom_dir: str | None = None, home: Path | None = None) -> Path:
    if custom_dir:
        return Path(custom_dir).expanduser().resolve() / "three-tier-orchestrator"
    roots = _agent_roots(home)
    if target in roots:
        return roots[target][1]
    raise ValueError(f"Unsupported target: {target}")


def build_mcp_snippet(python_exe: str | None = None) -> dict[str, Any]:
    """Generate a drop-in JSON MCP config snippet with machine-local absolute paths."""
    py = str(Path(python_exe or sys.executable).resolve())
    server = str(MCP_SERVER_PATH.resolve())
    db_path = str((REPO_ROOT / "data" / "tierforge_state.db").resolve())
    return {
        "mcpServers": {
            "tierforge-worker": {
                "command": py,
                "args": [server],
                "env": {
                    "TIERFORGE_LLM_BASE_URL": "http://127.0.0.1:11434",
                    "TIERFORGE_LLM_MODEL": "qwen2.5:7b",
                    "TIERFORGE_OLLAMA_KEEP_ALIVE": "0",
                    "TIERFORGE_STATE_DB": db_path,
                },
            }
        }
    }


def build_codex_toml_snippet(python_exe: str | None = None) -> str:
    """Generate a drop-in Codex CLI (`~/.codex/config.toml`) MCP config block."""
    snippet = build_mcp_snippet(python_exe)["mcpServers"]["tierforge-worker"]
    cmd = snippet["command"].replace("\\", "\\\\")
    arg0 = snippet["args"][0].replace("\\", "\\\\")
    db = snippet["env"]["TIERFORGE_STATE_DB"].replace("\\", "\\\\")
    return (
        "[mcp_servers.tierforge-worker]\n"
        f'command = "{cmd}"\n'
        f'args = ["{arg0}"]\n'
        "\n"
        "[mcp_servers.tierforge-worker.env]\n"
        'TIERFORGE_LLM_BASE_URL = "http://127.0.0.1:11434"\n'
        'TIERFORGE_LLM_MODEL = "qwen2.5:7b"\n'
        'TIERFORGE_OLLAMA_KEEP_ALIVE = "0"\n'
        f'TIERFORGE_STATE_DB = "{db}"\n'
    )


def install_skill(dest: Path, *, dry_run: bool = False) -> list[str]:
    if not SKILL_SRC.is_dir():
        raise FileNotFoundError(f"Skill source not found: {SKILL_SRC}")
    copied: list[str] = []
    for src_file in sorted(SKILL_SRC.rglob("*")):
        if src_file.is_dir():
            continue
        rel = src_file.relative_to(SKILL_SRC)
        dst_file = dest / rel
        copied.append(str(dst_file))
        if not dry_run:
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_file, dst_file)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Install TierForge Agent Skill across AI IDEs/CLIs and generate absolute-path MCP config."
    )
    parser.add_argument(
        "--target",
        choices=(*ALL_TARGETS, "all"),
        default="antigravity",
        help="Target agent ecosystem (default: antigravity; use 'all' to auto-detect installed agents).",
    )
    parser.add_argument(
        "--custom-dir",
        default=None,
        help="Override destination parent directory for the skill folder.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview destination paths and MCP configs without copying files.",
    )
    parser.add_argument(
        "--write-mcp-config",
        default=None,
        help="Optional path to write the generated absolute-path JSON MCP file.",
    )
    parser.add_argument(
        "--write-codex-toml",
        default=None,
        help="Optional path to write the generated Codex CLI TOML MCP snippet.",
    )
    args = parser.parse_args()

    targets = [args.target] if args.custom_dir or args.target != "all" else detect_installed_agents()
    snippet = build_mcp_snippet()
    toml_snippet = build_codex_toml_snippet()
    mode = "DRY-RUN" if args.dry_run else "INSTALLED"

    print(f"=== TierForge Multi-Agent Skill Installer ({mode}) ===")
    for t in targets:
        dest = resolve_target_dir(t, args.custom_dir)
        files = install_skill(dest, dry_run=args.dry_run)
        print(f"[{t}] -> {dest} ({len(files)} files)")

    if args.write_mcp_config and not args.dry_run:
        out_path = Path(args.write_mcp_config).expanduser().resolve()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(snippet, indent=2), encoding="utf-8")
        print(f"JSON MCP config saved : {out_path}")

    if args.write_codex_toml and not args.dry_run:
        out_toml = Path(args.write_codex_toml).expanduser().resolve()
        out_toml.parent.mkdir(parents=True, exist_ok=True)
        out_toml.write_text(toml_snippet, encoding="utf-8")
        print(f"Codex TOML saved      : {out_toml}")

    print("\n--- Ready-to-Paste JSON MCP Config (Antigravity / Claude Code / Cursor / Windsurf) ---")
    print(json.dumps(snippet, indent=2))
    print("\n--- Ready-to-Paste TOML MCP Config (Codex CLI: ~/.codex/config.toml) ---")
    print(toml_snippet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
