# Example 03: CI Failure Log Triage

Demonstrates how **VieForge** compresses noisy CI/test logs, automatically redacts leaked secrets (`ghp_`, `sk-`, `Bearer`, etc.) while keeping `FAILED` / `ERROR` / `Traceback` lines, and triages root causes via a `$0` checkpointed Local LLM loop.

## Quick Run

```bash
# 1. Dry-run cost preview
python examples/03_ci_log_triage/run_ci_triage.py --dry-run

# 2. Run offline with mock LLM (no Ollama required)
python examples/03_ci_log_triage/run_ci_triage.py --mock-llm --reset

# 3. Re-run to verify $0 SQLite checkpoint resume (0 pending items)
python examples/03_ci_log_triage/run_ci_triage.py --mock-llm

# 4. Run against your own CI logs folder using local Ollama
python examples/03_ci_log_triage/run_ci_triage.py --log-dir path/to/ci_logs --reset
```
