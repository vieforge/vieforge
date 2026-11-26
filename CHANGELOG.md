# Changelog

All notable changes to this project will be documented in this file.

## [0.2.0] - 2026-11-26

### Added
- **True OpenAI-Compatible Local LLM Support (`core/llm_router.py`):**
  - Added `api_style="auto"|"ollama"|"openai"` (`TIERFORGE_LLM_API_STYLE`) supporting LM Studio (`:1234/v1`), llama.cpp (`:8080/v1`), vLLM (`:8000/v1`), and OpenRouter via `/v1/chat/completions` + `Authorization: Bearer` header, with automatic fallback when servers reject `response_format={"type":"json_object"}`.
  - Preserved native Ollama `/api/chat` + `keep_alive=0` lease-managed VRAM hygiene for Ollama endpoints (`:11434`).
- **Reproducible `$0` Token Savings Benchmark (`scripts/benchmark_token_savings.py` + `tests/test_benchmark.py`):**
  - Verifies exact Cloud token burn across 100-doc batch (`118.3x`, `-99.2%`), 390-line CI log compression (`11.2x`, `-91.0%`), and 50-item crash/resume (`39.2x`, `-97.4%`) — **16.9x net reduction (`-94.1%`)** including the 1,459-token one-time session overhead.
- **Bilingual Website (`https://vieforge.dev`) + SEO & Open Graph:**
  - Interactive EN/VI savings calculator, verified benchmark charts, honest break-even scope, Ko-fi + VietQR community support modal, Cloudflare Workers Static Assets deploy (`wrangler.jsonc`), `1200x630` Open Graph card (`website/assets/og-card.png`), Twitter Card, JSON-LD `SoftwareApplication`, `robots.txt`, and `sitemap.xml`.
- **Developer Experience, CLI Entrypoints & CI Hardening:**
  - Added `[project.scripts]` console entrypoints (`vieforge-setup`, `vieforge-preflight`, `vieforge-bench`, `vieforge-mcp`, `vieforge-audit`).
  - Expanded GitHub Actions CI matrix to verify `mcp/worker_mcp_server.py --check`, `benchmark_token_savings.py --json`, and `run_demo.py --dry-run`.
  - Added real localhost `http.server` tests for both Ollama and OpenAI-compatible routing (`tests/test_llm_router.py`), plus GitHub Issue & PR templates (`.github/`).

## [0.1.0] - 2026-11-26

### Added
- **3-Tier Hybrid Core (`core/`, 100% Python stdlib, MIT):**
  - `state_db.py`: SQLite WAL per-item checkpointing, SHA-256 deduplication, and `scoped_job_id()` for multi-project isolation.
  - `llm_router.py`: Ollama / OpenAI-compatible router with `batch_session()` VRAM lease hygiene, JSON auto-repair retries (`<= 2`), and offline `--mock-llm` mode.
  - `concurrency.py`: Cross-process locks (`msvcrt`/`fcntl`), atomic JSON writes, ref-counted `OllamaLeaseManager`, and `exclusive_gpu_lock`.
  - `cost_tracker.py`: External `pricing.json` loader, `--dry-run` cost estimator, `MAX_JOB_USD` & `DAILY_USD_CAP` guards, and `SavingsReceipt`.
  - `output_trimmer.py`: Clean-room ANSI/progress-bar/traceback compressor with exact character reduction telemetry.
  - `subprocess_runner.py`, `vram_manager.py`, `pipeline_runner.py`, `qa_logger.py`, `license_audit.py`, `step_auditor.py`.
- **Multi-Agent Ecosystem & Zero-Knowledge Onboarding:**
  - `mcp/worker_mcp_server.py` (5 compact MCP tools), `AGENTS.md`, `AGENT_INSTALL.md`, `scripts/quick_setup.py`, and `scripts/install_skill.py` supporting Antigravity, Claude Code, Cursor, Windsurf, and Codex CLI (JSON + TOML).
- **Examples & Optional BYOA Bridge:**
  - `examples/01_batch_doc_processor/run_demo.py` (5-min `$0` interrupt/resume demo) and `examples/02_hybrid_media_pipeline/run_skeleton.py`.
  - Optional Manifest V3 localhost bridge (`extensions/tierforge-bridge/`) with bilingual EN/VI setup wizard and `LEGAL_AND_COMPLIANCE.md`.
- **CI & Localization:**
  - Cross-platform GitHub Actions CI (Windows / macOS / Linux) and bilingual `README.md` (EN) + `README.vi.md` (VI).
