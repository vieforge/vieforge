# Changelog

All notable changes to this project will be documented in this file.

## [0.4.3] - 2026-11-27

### Added
- **30-Second `$0` Savings Receipt (`vieforge receipt`, `scripts/savings_receipt_30s.py`):** Instant reproducible token & USD savings receipt in under 1 second (100% Python stdlib, 0 dependencies), supporting terminal ASCII box, `--markdown`, `--json`, standalone dark-mode `--svg` card, and `--projects` real local telemetry rollup.
- **Stdlib Mechanical Discipline Gates (`vieforge third-patch`, `vieforge doc-drift`):** Added zero-dependency `scripts/third_patch_gate.py` (blocks a 3rd un-diagnosed fix commit to the same file within 72h) and `scripts/doc_drift_gate.py` (flags expired `<!--measure:...-->` doc metric markers).
- **11 LLM Provider Presets & Local Auto-Discovery (`core/llm_router.py`):** Added presets including native Claude API, `<think>` reasoning tag stripping, and automatic local server discovery.
- **VieForge Lens Side Pane UI (`plugins/vieforge-lens/`):** Beginner-friendly 5-language (EN/VI/JA/DE/KO) Antigravity 2.0 MCP App panel.

## [0.4.2] - 2026-11-27

### Added
- **SQLite WAL Response Cache (`core/llm_router.py`):** Optional `cache_db_path` (`TIERFORGE_LLM_CACHE_DB`) + `use_cache=True` and `LLMResponse.cache_hit` for sub-millisecond `$0` local cache hits on repeated prompts.
- **OpenTelemetry `gen_ai.*` JSONL Tracing (`core/qa_logger.py`):** Added `emit_genai_span(...)` following OTel GenAI Semantic Conventions (`gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.*`).
- **Example 03 — CI Failure Log Triage (`examples/03_ci_log_triage/`):** Bundled into the wheel (`pyproject.toml`), showcasing smart log trimming, secret redaction, SQLite checkpointing, and `--mock-llm` / `--log-dir`.
- **OSS & Supply-Chain Standards:** Added PEP 561 `core/py.typed`, `core/__main__.py` (`python -m core`), `vieforge doctor` CLI alias, `CODE_OF_CONDUCT.md`, `.pre-commit-config.yaml`, `.github/dependabot.yml`, and PEP 740 / SLSA build provenance attestations in `.github/workflows/publish.yml`.
- **Expanded IDE Integrations (`scripts/install_skill.py`):** First-class support and aliases for **Codeium / Windsurf** (`--target codeium`), **Claude Code** (`--target claudecode` + `claude mcp add` one-liner), **Cline / Roo Code** (`--target cline`), **VS Code / GitHub Copilot** (`.vscode/mcp.json` `"servers"` schema), **Cursor**, **Antigravity**, and **Codex CLI**.

### Fixed
- **Official `mcp` PyPI SDK Collision & `mcp 2.x` Compatibility (`scripts/mcp_server.py`, `pyproject.toml`, `scripts/cli.py`):** Moved the canonical MCP server to `scripts/mcp_server.py` (removing the colliding `"mcp"` package from wheel `packages`) and added dual support for `mcp 2.x` (`MCPServer`) and `mcp 1.x` (`FastMCP`), keeping `mcp/worker_mcp_server.py` as a backwards-compatible CLI shim.
- **macOS/Linux Virtualenv Symlink Preservation (`scripts/install_skill.py`):** Preserved `.venv/bin/python` symlink paths via `os.path.abspath(...)` instead of dereferencing with `.resolve()`.

## [0.4.1] - 2026-11-27

### Fixed
- **Tier 3 VRAM Profiler Crash (`core/vram_manager.py`):** Accepted `job_id=` alongside backwards-compatible `episode_id=` in `vram_phase_profiler()`, fixing `TypeError` when running `run_isolated(..., profile_vram=True)` and full `examples/02_hybrid_media_pipeline/run_skeleton.py`.
- **Eliminated 2–5s Inter-Stage Flush Stall (`core/vram_manager.py`):** Only probe ComfyUI `/free` when `COMFYUI_BASE_URL` or explicit `base_url` is set.
- **Portable Windows Bridge Launcher & Wheel Bundling (`scripts/start_tierforge_bridge.bat`, `scripts/cli.py`, `pyproject.toml`):** Replaced hardcoded path with `%~dp0..`, added `vieforge bridge <serve|status|token|send|path>`, and bundled `extensions/tierforge-bridge/` inside the wheel.
- **MV3 Extension Reliability & Security (`extensions/tierforge-bridge/`, `core/extension_bridge.py`):** Added `chrome.runtime.onStartup` alarm recovery, 12s SPA input wait + native `execCommand("insertText")` editor input, immediate `LOGIN_OR_CAPTCHA_REQUIRED` halt, XSS-safe DOM popup logs, port-19876 warning, and default `redact_secrets=True` on `ExtensionBridgeClient.dispatch_prompt()`.

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
