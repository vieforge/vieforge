# Real-World Token Savings Benchmark & Usage-Tier Archive

*Measured on workstation (`Windows 10 / Python 3.12.16 / NVIDIA RTX 3060 Ti 8 GB / Ollama qwen2.5:7b`) across 55 live Tier-0 audit runs and reproducible `$0` workloads (`ceil(chars / 4)` estimator; baseline `external/text-frontier` `$3/1M` input, `$15/1M` output).*

## 1. Empirical Measurements (Verified Locally)

| Real Workload / Local Case | Cloud-Only Tokens | VieForge Cloud Tokens | Reduction | Factor | Notes |
| :--- | ---: | ---: | ---: | ---: | :--- |
| **Session Overhead (Fixed One-Time)** | 0 | 1,459 | — | — | `SKILL.md` (691) + `AGENTS.md` (448) + MCP (320) |
| **Case 1 — 5-Doc Batch (`Example 01`)** | 543 | 27 | **-95.0%** | **20.1x** | `2,169 -> 108 chars` (`$0.0405 -> $0.0000`) |
| **Case 2 — 3-Job CI Log Triage (`Example 03`)** | 3,646 | 1,160 | **-68.2%** | **3.1x** | `14,582 -> 4,639 chars` + `2` secrets redacted |
| **Case 3 — 390-Line Pytest Traceback Trim** | 11,150 | 998 | **-91.0%** | **11.2x** | Preserves `FAILED`/`ERROR` lines automatically |
| **Case 4 — 50-Item Crash/Resume (30/50)** | 17,920 | 457 | **-97.4%** | **39.2x** | SQLite WAL resume skips 30 done items at `$0` |
| **Case 5 — 100-Doc Batch (MCP Disk-Input)** | 23,662 | 200 | **-99.2%** | **118.3x** | Reads directly from disk; zero MCP doc payload |
| **Case 6 — Combined Mixed Benchmark (incl. Overhead)** | **52,732** | **3,114** | **-94.1%** | **16.9x** | Break-even after 1 noisy CI log or 3–5 items |
| **Case 7 — 55 Live Tier-0 QA/Audit Runs (`step_audit_log.jsonl`)** | **231,000** | **0** | **-100.0%** | **∞** | Avg `StateDB 1k`: `29.57 ms` · Avg `Trimmer 10k`: `13.66 ms` |

## 2. Classification by Usage Tier & User Segment (`config/savings_tiers.json`)

| Usage Tier | Target User Segment | Typical Volume | Real Net Token Reduction | Recommended Setup |
| :--- | :--- | :---: | :---: | :--- |
| **L0 — Casual** | Quick Q&A, single-file edits | `< 5` items/session | `0%` *(below break-even)* | Call Cloud Agent directly; no harness needed |
| **L1 — Solo Indie / Vibe Coder** | Solo devs, freelancers, daily CI logs | `5–50` items/day | **70%–88%** (`3.5x–8.5x`) | `pip install vieforge && vieforge setup` |
| **L2 — Power Dev / AI Studio** | Multi-project devs, AI media/doc pipelines | `51–500` items/day | **88%–95%** (`8.5x–20x`) | `batch_session()` + `TIERFORGE_LLM_CACHE_DB` + `exclusive_gpu` |
| **L3 — Team / Enterprise** | Agencies, multi-repo CI, production workers | `500+` items/day | **94%–99%** (`16x–118x`) | Non-root `compose.yml` + `PolicyGuard` + OTel `gen_ai.*` |

## 3. Measure Your Own Real Savings Locally

Every installed copy of VieForge includes the visual, offline savings evaluator:

```bash
# Terminal summary + ASCII reduction bar across your current project
vieforge savings

# Aggregate across multiple local projects and export a standalone visual HTML dashboard
vieforge savings --projects ./project_a ./project_b --html data/savings_dashboard.html
```
