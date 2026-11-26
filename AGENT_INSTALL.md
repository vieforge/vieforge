# AGENT_INSTALL.md — Zero-Knowledge Playbook for AI Agents

> **Purpose:** If a user who does not know Git, Python, or MCP asks you (Antigravity, Claude Code, Cursor, Windsurf, or Codex CLI) to *"install TierForge / the 3-tier token-saving repo and set it up for me"*, follow these exact steps on their behalf and explain everything in simple, non-technical language.

## Automated Setup Steps (Execute Sequentially)

1. **Clone or locate the repository** (if not already in the workspace):
   ```bash
   git clone https://github.com/vieforge/vieforge.git
   cd vieforge
   ```
2. **Run the 1-command zero-knowledge setup** (100% Python stdlib, zero OS mutations):
   ```bash
   python scripts/quick_setup.py
   ```
   This automatically:
   - Checks Python (>= 3.10), SQLite WAL, GPU/VRAM, and Ollama.
   - Installs the `three-tier-orchestrator` skill for every AI agent detected on the user's machine.
   - Generates absolute-path MCP config files at `data/mcp_config.generated.json` (Antigravity / Claude Code / Cursor / Windsurf) and `data/codex_mcp.generated.toml` (Codex CLI).
   - Runs the 5-minute `$0` batch demo (automatically falling back to `--mock-llm` if Ollama is not installed yet) and displays a **Savings Receipt**.
3. **Optional — Enable Real Local LLM (`$0`):**
   - If `quick_setup.py` reported Ollama is not running, tell the user in 1 sentence how to install Ollama from https://ollama.com and pull the hardware-recommended model (e.g., `ollama pull qwen2.5:3b` for CPU-only, `ollama pull qwen2.5:7b` for 8–16 GB VRAM).
4. **Optional — Connect MCP Server:**
   - Install the optional MCP SDK (`pip install "mcp>=1.2.0"` or `uv sync --extra mcp`) and help the user paste the generated snippet from `data/mcp_config.generated.json` (or `data/codex_mcp.generated.toml` for Codex CLI) into their IDE's MCP settings.
5. **Report Back in Plain Language:**
   - Summarize: (a) what hardware profile was detected, (b) which AI agents received the skill, (c) the Savings Receipt from the demo, and (d) one example sentence the user can type next to start saving tokens.
