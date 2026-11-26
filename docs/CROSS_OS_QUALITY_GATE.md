# VieForge 2-Axis Cross-OS Quality Gate

VieForge is designed to run identically across **Windows, macOS, and Linux** with zero third-party dependencies in `core/`. Every pull request and release is gated by a two-axis verification architecture (`core/step_auditor.py` + `scripts/verify_cross_os.py` + `.github/workflows/ci.yml`).

## Axis A — Core Capability Contract Gate (MUST-PASS 5/5)

Any failure on Axis A immediately fails the build and caps the score at `<= 60/100`:

1. **`C1_token_efficiency`**: Baseline session overhead (`SKILL.md` + `AGENTS.md` + MCP tools) stays strictly `<= 1,500` tokens, and `output_trimmer` strips `>= 90%` noise on 10,000-line logs.
2. **`C2_durability_resume`**: `StateDB` registers 1,000 items, commits partial completion, and resumes pending work with `0` duplicate runs and `$0` re-spend.
3. **`C3_budget_and_safety`**: Commercial MIT clean-room audit passes (`core/license_audit.py`), `core/` remains 100% Python stdlib, `config/*.json` validates cleanly, and `CostTracker` hard-stops with `BudgetExceededError`.
4. **`C4_cross_os_portability`**: Static AST scan confirms zero leaked personal Windows paths, zero unguarded `msvcrt`/`fcntl`/`winreg` imports, explicit `encoding="utf-8"` on text I/O, and live `CrossProcessLock` contention passes.
5. **`C5_pytest_and_hygiene`**: 100% `pytest` pass rate and zero stray `patch_*.py` scripts.

## Axis B — Performance & Experience Score (100 pts)

Once all 5 Core Contracts pass, improvements are measured strictly on **Performance (50 pts)** and **Developer Experience (50 pts)**:

- **Performance (50 pts)**: `StateDB` 1,000-item batch + resume latency (`< 250 ms` = 20 pts), `output_trimmer` 10,000-line latency (`< 80 ms` = 15 pts), and session token overhead headroom (`<= 1,500` = 15 pts).
- **Experience / DX (50 pts)**: Zero recorded VRAM leaks (15 pts), bilingual docs & version synchronization (15 pts), and cross-platform static cleanliness (20 pts).

## 3-OS Parity CI Matrix (`ubuntu-latest` / `macos-latest` / `windows-latest`)

Each CI runner builds a real `.whl` artifact (`hatchling`), installs it offline into a clean isolated `venv`, switches into an **empty directory outside the repository**, and executes:
- `vieforge-preflight --json`
- `vieforge-bench --json`
- `vieforge-mcp --check`
- `vieforge-setup --dry-run` (with isolated `HOME`)
- Bundled `run_demo.py` crash (`rc=130`) -> `$0` checkpoint resume (`rc=0`)
- Real 2-process OS lock contention (`fcntl` on Linux/macOS, `msvcrt` on Windows)

Finally, `cross-os-parity-gate` downloads all OS JSON reports and fails the workflow if any OS fails or diverges on deterministic token benchmark outputs.

```bash
# Run locally on your workstation
uv run python scripts/verify_cross_os.py run --step local_check --out data/os_report_local.json
uv run python scripts/verify_cross_os.py compare data/os_report_local.json
```
