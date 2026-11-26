# VieForge v0.4.1

**Code First, Local LLM Second, Cloud Last.** — 100% Python stdlib MIT core (`dependencies = []`), zero OS-mutation, 3-OS verified (Ubuntu, macOS, Windows).

## Highlights (v0.4.0 → v0.4.1)

- **Tier 3 & VRAM Profiler Fixes (`core/vram_manager.py`, `core/subprocess_runner.py`):**
  - Fixed `vram_phase_profiler(job_id=...)` crash and removed blind ComfyUI `:8188` wait when unconfigured.
  - Enforced `PYTHONUTF8=1` in child subprocesses.
- **Portable Bridge & MV3 Extension Hardening (`extensions/tierforge-bridge/`, `core/extension_bridge.py`):**
  - Portable `scripts/start_tierforge_bridge.bat` using `%~dp0..`.
  - Hardened `background.js` (`onStartup`), `content_bridge.js` (12s SPA wait, `execCommand("insertText")`, instant stop on Login/CAPTCHA), `popup.js` (zero `innerHTML`), and `core/extension_bridge.py` (drain POST body against `WinError 10053`, secret redaction on dispatch).
- **Disk-Input MCP Batching (`mcp/worker_mcp_server.py`):**
  - `tool_run_local_batch` reads directly from disk (`input_path` directory / `.jsonl` / `.json` / `.csv` and `input_glob`), cutting MCP payload overhead by `118x`.
- **Smart Log Trimming & Secret Redaction (`core/output_trimmer.py`):**
  - Automatic secret redaction (`redact_secrets=True` for `sk-`, `ghp_`, `AIza`, `AKIA`, `Bearer`, env secrets) while preserving `FAILED`/`ERROR`/`AssertionError` lines when middle-truncating logs.
- **Unified CLI & OpenAI-Compat Preflight (`scripts/cli.py`, `core/llm_router.py`):**
  - Single `vieforge <setup|check|bench|mcp|audit|verify-os|install-skill|bridge>` CLI (preserving legacy `vieforge-*` aliases).
  - Native Ollama (`/api/chat` + `keep_alive=0` VRAM lease) and OpenAI-compatible (`/v1/chat/completions` for LM Studio `:1234/v1`, llama.cpp `:8080/v1`, vLLM `:8000/v1`).

## Quick Install

```bash
pip install vieforge-0.4.1-py3-none-any.whl
vieforge check
```
