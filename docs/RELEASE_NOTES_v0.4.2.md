# VieForge v0.4.2

**Code First, Local LLM Second, Cloud Last.** — 100% Python stdlib MIT core (`dependencies = []`), zero OS-mutation, 3-OS verified (Ubuntu, macOS, Windows), with PEP 740 build provenance on PyPI.

## Highlights

- **MCP 2.x Compatibility & Zero SDK Collision (`scripts/mcp_server.py`):**
  - Resolved package name collision when installing `pip install "vieforge[mcp]"` by moving the canonical server to `scripts/mcp_server.py` (keeping `mcp/worker_mcp_server.py` as a backwards-compatible shim).
  - Supports both **`mcp 2.x` (`MCPServer`)** and **`mcp 1.x` (`FastMCP`)** out of the box.
- **Expanded IDE & Cross-OS Integration (`scripts/install_skill.py`):**
  - Preserves `.venv/bin/python` symlinks on macOS/Linux.
  - First-class support for **Codeium / Windsurf** (`--target codeium`), **Claude Code** (`--target claudecode` + `claude mcp add` one-liner), **Cline / Roo Code** (`--target cline`), **VS Code / GitHub Copilot** (`.vscode/mcp.json`), **Cursor**, **Antigravity**, and **Codex CLI**.
- **SQLite WAL Response Cache & OTel `gen_ai.*` Spans (`core/llm_router.py`, `core/qa_logger.py`):**
  - Optional `cache_db_path` / `TIERFORGE_LLM_CACHE_DB` for `<1 ms` `$0` repeated local LLM calls.
  - `emit_genai_span(...)` for vendor-neutral OpenTelemetry `gen_ai.*` JSONL traces.
- **Example 03 (`examples/03_ci_log_triage/`), `vieforge doctor`, `core/py.typed` & `python -m core`:**
  - Bundled CI failure log triage demo with smart trimming + secret redaction.
  - Added PEP 561 `py.typed`, `vieforge doctor`, `CODE_OF_CONDUCT.md`, `.pre-commit-config.yaml`, and Dependabot.

## Quick Install

```bash
pip install -U vieforge
vieforge doctor
```
