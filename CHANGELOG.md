# Changelog

All notable changes to this project will be documented in this file.

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
