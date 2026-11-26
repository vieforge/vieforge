---
name: three-tier-orchestrator
description: 3-Tier Hybrid AI Agent orchestration rules ("Code First, Local LLM Second, Cloud Last"). Use to route bulk work to deterministic local code and Ollama ($0), isolate GPU/paid tasks behind checkpoints and budget caps, and minimize Cloud Agent token burn.
---

# Three-Tier Orchestrator

**Core Rule:** *Code First, Local LLM Second, Cloud Last.*

## 1. Tier Routing
- **Tier 1 (Cloud Brain):** Architecture, schemas, code edits, code review, root-cause debugging, and Approval Gates. Never iterate over bulk items in Cloud context.
- **Tier 2 (Local Backbone — `$0`):** Deterministic Python/SQLite scripts for I/O, parsing, deduplication, and checkpointing; local LLM (Ollama / OpenAI-compat) for bulk extraction, classification, formatting, and log summarization.
- **Tier 3 (Isolated Muscle — `$0` GPU or Capped API):** Heavy local GPU jobs in isolated child subprocesses; paid external APIs strictly behind `--dry-run` estimation, retry caps, and USD budgets.

Load `references/routing-matrix.md` for task-by-task delegation rules and `references/hardware-profiles.md` for VRAM/model sizing.

## 2. Non-Negotiable Guardrails
1. **Zero OS-Mutation:** Never generate scripts that modify OS services, Windows Registry, network/IPv6 stacks, or system pagefiles. Keep all resource hygiene in application space: Ollama `"keep_alive": 0`, subprocess isolation per stage, and pre-flight VRAM checks.
2. **Clean-Room Licensing:** Use MIT / Apache-2.0 / BSD dependencies only. Never embed or vendor `rtk-ai/rtk` standard (`BUSL-1.1`), `AGPL-3.0`, or `CC-BY-NC` code. Companion CLIs (`rtk-oss` Apache-2.0, `ppgranger/token-saver` Apache-2.0) are optional side-by-side tools only.
3. **Checkpoint & Budget Integrity:**
   - Persist per-item state (`pending` | `done` | `failed`, `retry_count`, `actual_cost_usd`) in SQLite (`core/state_db.py`) so interrupted runs resume at `$0` extra cost.
   - Require `--dry-run` via `config/pricing.example.json` (or `pricing.json`) before any paid Tier 3 run.
   - Enforce `MAX_RETRY_PER_ITEM <= 2`, `MAX_JOB_USD`, and `DAILY_USD_CAP` (`config/budget_limits.json`).
4. **Compact Tier 1 Handoffs:** Pass Tier 2/3 stdout/stderr through `core/output_trimmer.py` (strip progress bars, cap Python tracebacks to last 20 lines, return compact JSON job summaries).

## 3. Execution Loop
1. Run `python scripts/preflight_check.py` to verify Python, SQLite, Ollama, GPU profile, and configs.
2. Design schema/script in Tier 1; delegate batch execution to Tier 2/3.
3. For paid jobs, run `--dry-run` first and stop at an Approval Gate if estimated cost exceeds `require_approval_above_usd`.
4. Inspect only trimmed summaries and failed item IDs in Tier 1; patch and resume from SQLite checkpoints.
