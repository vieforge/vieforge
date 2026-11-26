# AGENTS.md — TierForge Harness Instructions

This repository implements **TierForge** (*Code First, Local LLM Second, Cloud Last*): a 3-Tier Hybrid AI Agent Harness designed to cut Cloud Agent token burn and API spend by 70–90%.

## Operating Rules for AI Agents (Antigravity, Claude Code, Cursor, Windsurf, Codex CLI)

1. **Delegate Bulk Work Off Cloud Context:**
   - **Tier 1 (You / Cloud Brain):** Architecture, code edits, reviews, Approval Gates, root-cause debugging.
   - **Tier 2 (Local Backbone — `$0`):** Deterministic Python + SQLite checkpoints (`core/state_db.py`) + Ollama local LLM (`core/llm_router.py`).
   - **Tier 3 (Isolated Muscle):** Heavy local GPU jobs in child subprocesses (`core/subprocess_runner.py`, `exclusive_gpu=True`) and paid external APIs guarded by `--dry-run`, `MAX_RETRY_PER_ITEM <= 2`, `MAX_JOB_USD`, and `DAILY_USD_CAP` (`core/cost_tracker.py`).
2. **Multi-Project Concurrency Safety:**
   - When running multiple workspaces concurrently on one machine, pass `project_id` to `tool_run_local_batch` / `scoped_job_id()`, use `LLMRouter.batch_session()` (ref-counted Ollama VRAM lease), and `exclusive_gpu=True` for heavy Tier 3 GPU subprocesses.
3. **Non-Negotiable Guardrails:**
   - **Zero OS-Mutation:** Never edit OS services, Windows Registry, network stacks, or pagefiles.
   - **Clean-Room MIT Licensing:** `core/` stays 100% Python stdlib. Never embed `BUSL-1.1` (`rtk-engine`), `AGPL-3.0`, or `CC-BY-NC`.
   - **Verify Before Reporting Done:** Run `uv run --extra dev pytest -v` and `uv run python scripts/audit_step.py --step <name>`.
4. **If a Beginner User Asks You to Install TierForge:**
   - Read and follow [AGENT_INSTALL.md](AGENT_INSTALL.md) directly — run `python scripts/quick_setup.py` and explain the result in plain language.
