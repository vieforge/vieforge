# Cross-Platform Hardware Profiles

TierForge adapts to consumer hardware across Windows, macOS, and Linux using **application-space controls only** (zero OS/Registry/service/pagefile mutations).

## Profile Matrix

| Profile | Typical Hardware | Tier 2 Local LLM (Ollama) | `keep_alive` | Tier 3 GPU Strategy | Min Free VRAM Guard |
| :--- | :--- | :--- | :---: | :--- | :---: |
| **CPU-Only** | Laptop / Server without dGPU (16–32 GB RAM) | `qwen2.5:3b` or `llama3.2:3b` (Q4_K_M) | `0` | External capped API or CPU subprocess | N/A (CPU fallback) |
| **8 GB VRAM** | RTX 3060 Ti / 4060 / 3070 (8 GB) | `qwen2.5:7b` (Q4_K_M, ~4.7 GB) | `0` | **Strict stage-by-stage subprocess isolation**; unload Ollama before GPU stage | `4096 MB` |
| **12–16 GB VRAM** | RTX 3060 12GB / 4070 / 4060 Ti 16GB | `qwen2.5:7b` or `qwen2.5:14b` (Q4_K_M) | `0` | Subprocess isolation per stage; larger context window (`8k–16k`) | `6144 MB` |
| **24 GB+ VRAM** | RTX 3090 / 4090 / Workstation GPU | `qwen2.5:14b` or `qwen2.5:32b` (Q4_K_M) | `0` | Subprocess isolation; higher batch throughput | `10240 MB` |
| **Apple Silicon** | M1/M2/M3/M4 (16 / 32 / 64+ GB Unified) | 16 GB: `7b` Q4; 32 GB+: `14b–32b` Q4 | `0` | Unified memory subprocess isolation; avoid concurrent heavy MLX + Ollama loads | Unified (leave >= 4 GB headroom) |

## Application-Space Memory Hygiene Rules

1. **Force Immediate Unload:** Always send `"keep_alive": 0` on Ollama `/api/generate` and `/api/chat` requests (or configure `TIERFORGE_OLLAMA_KEEP_ALIVE=0`) so VRAM is reclaimed before Tier 3 starts.
2. **Stage-by-Stage Batching (Critical on 8 GB VRAM):** Run all Tier 2 LLM items for a job first, let Ollama unload, then run Tier 3 GPU subprocesses in a separate stage. Never interleave LLM and heavy GPU inference per item on 8 GB cards.
3. **Subprocess Isolation:** Run every Tier 3 GPU worker via `core/subprocess_runner.py` so the OS kernel reclaims 100% of CUDA/Metal allocations cleanly upon child process exit.
4. **Pre-Flight Check:** Run `core/vram_guard.py` (or `scripts/preflight_check.py`) before launching GPU batches; abort with `VRAM_INSUFFICIENT` if free VRAM is below threshold.
5. **Zero OS-Mutation Policy:** Never alter OS services, Windows Registry, pagefiles, or network stacks to chase extra memory.
