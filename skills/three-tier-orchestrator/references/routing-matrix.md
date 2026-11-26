# Task Routing Matrix (Tier 1 vs Tier 2 vs Tier 3)

Use this matrix to decide where every subtask executes. Default to the lowest-cost tier that satisfies accuracy requirements.

## Delegation Matrix

| Task Category | Assigned Tier | Executor | Rationale | Output Contract to Tier 1 |
| :--- | :---: | :--- | :--- | :--- |
| System architecture, API/DB schema design | **Tier 1** | Cloud Agent | High reasoning leverage, low volume | Markdown spec / code diff |
| Multi-file code implementation & refactoring | **Tier 1** | Cloud Agent | Needs full repo context & precision | Verified diff + test summary |
| Root-cause debugging of complex failures | **Tier 1** | Cloud Agent | Requires cross-module reasoning | Trimmed traceback (<= 20 lines) |
| File I/O, CSV/JSON/SQLite ETL, deduplication | **Tier 2** | Deterministic Python | 100% repeatable, `$0`, zero hallucination | Compact JSON counts |
| Bulk text extraction, tagging, JSON normalization | **Tier 2** | Local LLM (Ollama) | High volume, structured output, `$0` | SQLite checkpoint summary |
| Log compression & error clustering | **Tier 2** | Python + Local LLM | Prevents Cloud context bloat | <= 30-line digest |
| Heavy local GPU inference / media batching | **Tier 3** | Isolated Subprocess | Clean VRAM release on process exit | Per-item status + paths |
| Paid external API batch calls | **Tier 3** | Budget-Guarded Worker | Capped by `--dry-run`, `MAX_JOB_USD`, `DAILY_USD_CAP` | Cost ledger + failed IDs |

## Escalation & Approval Gates

1. **Tier 2 → Tier 1 Escalation:**
   - Escalate only after an item hits `MAX_RETRY_PER_ITEM` (<= 2) or fails schema validation repeatedly.
   - Pass only the failed item ID, input hash, and trimmed error (<= 20 lines)—never full batch logs.
2. **Tier 3 Approval Gate:**
   - Always execute `--dry-run` first using `config/pricing.example.json` (or `config/pricing.json`).
   - Pause for explicit human/Tier 1 approval whenever `estimated_cost_usd > require_approval_above_usd` or whenever switching from local `$0` to a paid provider.
   - Hard-stop immediately if `job_cost_usd >= max_job_usd` or `daily_cost_usd >= daily_usd_cap`.

## Anti-Patterns (Avoid)
- **Cloud Loops:** Asking a Cloud Agent to process 50+ files or rows turn-by-turn instead of writing a checkpointed Tier 2 script.
- **Unbounded Retries:** Retrying paid API calls more than twice per item (`MAX_RETRY_PER_ITEM <= 2`).
- **Raw Log Dumping:** Feeding progress bars, ANSI codes, or 500-line stack traces into Cloud context instead of running `core/output_trimmer.py`.
- **Co-Resident VRAM Thrashing:** Keeping Ollama resident while launching a heavy GPU worker—always set `"keep_alive": 0` and isolate GPU stages in child subprocesses.
