# Changelog

All notable changes to this project will be documented in this file.

## [0.4.0] - 2026-11-26

### Added
- **Direct Disk-Input MCP Batching (`mcp/worker_mcp_server.py`):** `tool_run_local_batch` now supports `input_path` (directory, `.jsonl`, `.json`, `.csv`, or single text file) and `input_glob` (e.g. `"docs/*.md"`) so Tier 2 reads files locally from disk with zero Cloud token payload transfer.
- **Secret Redaction & Smart Error Retention (`core/output_trimmer.py`):** Automatically redacts API keys, GitHub/AWS/Google/Slack tokens, Bearer headers, and secret env assignments before returning logs to Tier 1 (`redact_secrets=True`), and retains high-signal error lines (`FAILED`, `ERROR`, `AssertionError`, `FATAL`) even when truncating huge log middles.
- **Unified `vieforge` CLI (`scripts/cli.py`):** Added single `vieforge <setup|check|bench|mcp|audit|verify-os|install-skill>` command (keeping all 5 legacy `vieforge-*` aliases intact) plus `vieforge --version`.
- **OpenAI-Compatible Preflight Detection (`scripts/preflight_check.py`):** `check_ollama()` now probes `/v1/models` for LM Studio, llama.cpp, and vLLM endpoints in addition to native Ollama `/api/tags`.
- **Flagship Website Overhaul (`https://vieforge.dev`):** Rebuilt Desktop & Mobile UI with interactive 3-tab Hero demo, asymmetrical Bento Grid, Calculator presets, 56px mobile drawer, bilingual FAQ + `FAQPage` JSON-LD, Cloudflare security `_headers`, custom `404.html`, and `hreflang`.

### Fixed
- **Windows UTF-8 Subprocess Safety (`core/subprocess_runner.py`, `scripts/preflight_check.py`):** Enforced explicit `encoding="utf-8", errors="replace"` and `PYTHONUTF8=1` on child processes to prevent `UnicodeDecodeError` on Unicode/Vietnamese output.

## [0.3.0] - 2026-11-26

### Added
- **2-Axis Cross-OS Quality Gate (`core/step_auditor.py`, `scripts/verify_cross_os.py`, `docs/CROSS_OS_QUALITY_GATE.md`):**
  - Separates **Axis A — 5 Mandatory Core Capability Contracts** (`C1_token_efficiency`, `C2_durability_resume`, `C3_budget_and_safety`, `C4_cross_os_portability`, `C5_pytest_and_hygiene`) from **Axis B — Performance (50 pts) & Developer Experience (50 pts)**.
  - Added static **Cross-Platform Portability AST Scanner** catching personal Windows paths, unguarded `msvcrt`/`fcntl`/`winreg` imports, and missing `encoding="utf-8"` text I/O.
  - Added **3-OS Parity CI Gate** in `.github/workflows/ci.yml`: builds a real `.whl`, installs offline into an isolated venv outside the repo, tests all CLI commands + interrupt/resume + real multi-process OS locks (`fcntl` / `msvcrt`) on Ubuntu, macOS, and Windows, and cross-verifies behavioral parity across all runners.
- **Clean Wheel Packaging (`core/paths.py` + `pyproject.toml`):**
  - Bundles `config/`, `skills/`, `AGENTS.md`, and `run_demo.py` into `core/_bundled_assets/` so `pip install` works cleanly from any directory on Linux, macOS, and Windows.

### Performance & Reliability Fixes
- **44x `StateDB` Bulk Registration Speedup (`core/state_db.py`):** Refactored `register_items()` into a single SQLite transaction (`executemany`) and added `mark_done_batch()`, reducing 1,000-item checkpoint + resume latency from ~1,242 ms to ~28 ms.
- **Zero-Warning Lazy Package Exports (`core/__init__.py`):** Adopted PEP 562 lazy `__getattr__`, speeding up CLI cold-start and eliminating Python `runpy` `RuntimeWarning` when running `python -m core.license_audit`.
- **Cross-OS Loopback HTTP Fix (`core/extension_bridge.py`):** Always drain POST request body before early `401`/`403`/`200` responses to prevent TCP RST (`WinError 10053` / `ConnectionResetError`).
- **Repository Hygiene:** Moved internal pre-launch notes out of public `docs/`, tightened Ruff lint (`C4`, `SIM`), and synced `examples/02_hybrid_media_pipeline/run_skeleton.py` to `default_data_dir()`.

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
