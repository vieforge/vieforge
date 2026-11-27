# VieForge Lens — Antigravity 2.0 Side Pane UI Plugin

A 100% local, zero-CDP, zero-OS-mutation **MCP App Side Pane** plugin for Antigravity 2.0:
- Live **Token Savings Meter** (`Tokens Saved`, `Reduction %`, `Multiplier`, `Est. USD Saved`, `Usage Tier L0–L3`)
- Live **3-Tier Health Strip** (Python/SQLite WAL, Ollama/OpenAI-compat local LLM, GPU VRAM profile)
- Per-project checkpoint & Tier-0 QA telemetry + 1-click local refresh & 0.3s benchmark (0 Cloud tokens)
- Native Light/Dark Antigravity theme variables + EN/VI toggle

## 1-Command Install (Antigravity 2.0)

```bash
pip install -U "vieforge[mcp]"
# Copy plugin bundle into Antigravity's auto-discovered plugins folder:
# Windows (PowerShell):
Copy-Item -Recurse -Force plugins\vieforge-lens "$env:USERPROFILE\.gemini\config\plugins\vieforge-lens"
# macOS / Linux:
cp -r plugins/vieforge-lens ~/.gemini/config/plugins/vieforge-lens
```

Open **VieForge Lens** directly from Antigravity's right Side Pane `+` menu or Left Sidebar. To uninstall, delete `~/.gemini/config/plugins/vieforge-lens` or disable it in Customizations.
