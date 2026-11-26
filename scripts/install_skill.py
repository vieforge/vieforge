#!/usr/bin/env python3
"""Cross-Platform Multi-Agent Skill Installer & Absolute-Path MCP Config Generator.

Supports:
  - Antigravity (`~/.gemini/config/skills/three-tier-orchestrator`)
  - Claude Code / `claudecode` (`~/.claude/skills/three-tier-orchestrator` + `claude mcp add`)
  - Cursor (`~/.cursor/skills/three-tier-orchestrator`)
  - Codeium / Windsurf (`~/.codeium/windsurf/skills/three-tier-orchestrator` + `~/.codeium/windsurf/mcp_config.json`)
  - Codex CLI / Open Agent Standard (`~/.agents/skills/three-tier-orchestrator` + `~/.codex/config.toml`)
  - Cline / Roo Code (`~/.cline/skills/three-tier-orchestrator`)
  - VS Code / GitHub Copilot (`.vscode/mcp.json` using `"servers"` schema)
  - `--target all` (auto-detects installed agent roots on Windows, macOS, and Linux)

Zero OS mutations.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.paths import default_data_dir, resolve_asset

SKILL_SRC = resolve_asset("skills/three-tier-orchestrator")
MCP_SERVER_PATH = Path(__file__).resolve().parent / "mcp_server.py"

ALL_TARGETS = ("antigravity", "claude", "cursor", "windsurf", "codex", "cline")
TARGET_ALIASES: dict[str, str] = {
    "codeium": "windsurf",
    "claudecode": "claude",
    "claude-code": "claude",
    "roo": "cline",
}


def normalize_target(target: str) -> str:
    key = target.strip().lower()
    return TARGET_ALIASES.get(key, key)


def _agent_roots(home: Path | None = None) -> dict[str, tuple[Path, Path]]:
    """Return mapping of agent -> (detection_root, skill_destination_dir)."""
    h = home or Path.home()
    return {
        "antigravity": (h / ".gemini", h / ".gemini" / "config" / "skills" / "three-tier-orchestrator"),
        "claude": (h / ".claude", h / ".claude" / "skills" / "three-tier-orchestrator"),
        "cursor": (h / ".cursor", h / ".cursor" / "skills" / "three-tier-orchestrator"),
        "windsurf": (h / ".codeium" / "windsurf", h / ".codeium" / "windsurf" / "skills" / "three-tier-orchestrator"),
        "codex": (h / ".codex", h / ".agents" / "skills" / "three-tier-orchestrator"),
        "cline": (h / ".cline", h / ".cline" / "skills" / "three-tier-orchestrator"),
    }


def detect_installed_agents(home: Path | None = None) -> list[str]:
    roots = _agent_roots(home)
    found = [name for name, (detect_dir, _dest) in roots.items() if detect_dir.exists()]
    return found or ["antigravity"]


def resolve_target_dir(target: str, custom_dir: str | None = None, home: Path | None = None) -> Path:
    if custom_dir:
        return Path(custom_dir).expanduser().resolve() / "three-tier-orchestrator"
    canonical = normalize_target(target)
    roots = _agent_roots(home)
    if canonical in roots:
        return roots[canonical][1]
    raise ValueError(f"Unsupported target: {target}")


def _preserve_venv_python(python_exe: str | None = None) -> str:
    """Keep virtualenv symlink paths intact on macOS/Linux (do NOT dereference via .resolve())."""
    return os.path.abspath(os.path.expanduser(python_exe or sys.executable))


def build_mcp_snippet(python_exe: str | None = None) -> dict[str, Any]:
    """Generate a drop-in JSON MCP config snippet with machine-local absolute paths."""
    py = _preserve_venv_python(python_exe)
    server = str(MCP_SERVER_PATH.resolve())
    db_path = str((default_data_dir() / "tierforge_state.db").resolve())
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


def build_vscode_mcp_snippet(python_exe: str | None = None) -> dict[str, Any]:
    """Generate a drop-in `.vscode/mcp.json` snippet for VS Code / GitHub Copilot (`servers` schema)."""
    srv = build_mcp_snippet(python_exe)["mcpServers"]["tierforge-worker"]
    return {
        "servers": {
            "tierforge-worker": {
                "type": "stdio",
                **srv,
            }
        }
    }


def build_claude_cli_command(python_exe: str | None = None) -> str:
    """Generate a 1-line `claude mcp add` command for Claude Code."""
    srv = build_mcp_snippet(python_exe)["mcpServers"]["tierforge-worker"]
    py = shlex.quote(srv["command"])
    server = shlex.quote(srv["args"][0])
    return f"claude mcp add tierforge-worker -- {py} {server}"


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
    valid_choices = (*ALL_TARGETS, *TARGET_ALIASES.keys(), "all")
    parser = argparse.ArgumentParser(
        description="Install TierForge Agent Skill across AI IDEs/CLIs and generate absolute-path MCP config."
    )
    parser.add_argument(
        "--target",
        choices=valid_choices,
        default="antigravity",
        help="Target agent ecosystem (default: antigravity; supports codeium/windsurf, claude/claudecode, cursor, codex, cline/roo, or 'all').",
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

    canonical = normalize_target(args.target)
    targets = [canonical] if args.custom_dir or canonical != "all" else detect_installed_agents()
    snippet = build_mcp_snippet()
    toml_snippet = build_codex_toml_snippet()
    vscode_snippet = build_vscode_mcp_snippet()
    claude_cmd = build_claude_cli_command()
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

    print("\n--- Ready-to-Paste JSON MCP Config (Antigravity / Claude Code / Cursor / Codeium Windsurf / Cline) ---")
    print("# Windsurf (Codeium): ~/.codeium/windsurf/mcp_config.json | Cursor: ~/.cursor/mcp.json")
    print(json.dumps(snippet, indent=2))
    print("\n--- 1-Line Claude Code CLI Registration ---")
    print(claude_cmd)
    print("\n--- Ready-to-Paste VS Code / GitHub Copilot Config (.vscode/mcp.json) ---")
    print(json.dumps(vscode_snippet, indent=2))
    print("\n--- Ready-to-Paste TOML MCP Config (Codex CLI: ~/.codex/config.toml) ---")
    print(toml_snippet)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
