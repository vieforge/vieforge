# AGENT_INSTALL.md — Zero-Knowledge Playbook for AI Agents

> **Purpose:** If a user who does not know Git, Python, or MCP asks you (Antigravity, Claude Code, Cursor, Codeium/Windsurf, Cline, VS Code Copilot, or Codex CLI) to *"install VieForge and set it up for me"*, follow these exact steps on their behalf and explain everything in plain, non-technical language.

## Automated Setup Steps (Execute Sequentially)

1. **Install VieForge from PyPI (recommended, 100% stdlib MIT, zero required dependencies):**
   ```bash
   pip install -U vieforge
   vieforge setup
   ```
   *(Alternative — from source: `git clone https://github.com/vieforge/vieforge.git && cd vieforge && python scripts/quick_setup.py`.)*

   This automatically:
   - Checks Python (>= 3.10), SQLite WAL, GPU/VRAM, and Ollama / OpenAI-compatible local LLMs (`vieforge doctor`).
   - Installs the `three-tier-orchestrator` skill for every AI IDE/CLI detected on the user's machine (**Antigravity, Claude Code, Cursor, Codeium/Windsurf, Codex CLI, Cline/Roo**).
   - Generates absolute-path MCP configs (JSON for Antigravity/Claude/Cursor/Codeium Windsurf/Cline, `.vscode/mcp.json` for VS Code Copilot, TOML for Codex CLI, and a 1-line `claude mcp add` command for Claude Code).
   - Runs the 5-minute `$0` batch demo (automatically falling back to `--mock-llm` if Ollama is not installed yet) and displays a **Savings Receipt**.

2. **Optional — Enable Real Local LLM (`$0`):**
   - If `vieforge doctor` reported no local LLM running, tell the user in 1 sentence how to install Ollama from https://ollama.com and pull the hardware-recommended model (`ollama pull qwen2.5:3b` for CPU-only, `ollama pull qwen2.5:7b` for 8–16 GB VRAM) — or point `TIERFORGE_LLM_BASE_URL` to LM Studio (`:1234/v1`), llama.cpp (`:8080/v1`), or vLLM (`:8000/v1`).

3. **Optional — Connect MCP Server (`mcp 2.x` & `1.x` compatible):**
   - Install the optional MCP extra: `pip install -U "vieforge[mcp]"` (or `uv sync --extra mcp`).
   - Run `vieforge install-skill --target <antigravity|claude|codeium|cursor|cline|codex|all>` and paste the generated snippet (or run the printed `claude mcp add` command).

4. **Report Back in Plain Language:**
   - Summarize: (a) what hardware profile was detected, (b) which AI agents/IDEs received the skill, (c) the Savings Receipt from the demo, and (d) one example sentence the user can type next to start saving tokens.
