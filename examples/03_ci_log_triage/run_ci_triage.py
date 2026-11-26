#!/usr/bin/env python3
"""Example 03: CI Failure Log Triage (Smart Trim + Secret Redaction + $0 Local LLM).

Demonstrates how VieForge shrinks multi-thousand-line CI logs before local LLM triage:
  1. Dry-run cost preview:
       python examples/03_ci_log_triage/run_ci_triage.py --dry-run
  2. Offline mock triage (no Ollama required):
       python examples/03_ci_log_triage/run_ci_triage.py --mock-llm --reset
  3. Triage real .log/.txt files from a folder:
       python examples/03_ci_log_triage/run_ci_triage.py --log-dir path/to/logs --mock-llm
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cost_tracker import CostTracker
from core.llm_router import LLMRouter
from core.output_trimmer import compact_job_summary, trim_output
from core.paths import default_data_dir
from core.state_db import StateDB, compute_input_hash
from core.vram_manager import recommend_local_model


def _build_sample_logs() -> list[tuple[str, str]]:
    """Generate realistic noisy CI logs with embedded failure lines and secrets."""
    noise = "\n".join(f"[INFO] 00:01:{i:02d} Running step check_{i} ... ok (12ms)" for i in range(1, 90))
    return [
        (
            "ci_ubuntu_py312",
            (
                f"{noise}\n"
                "[DEBUG] GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz\n"
                "FAILED tests/test_auth.py::test_token_refresh - AssertionError: expected 200, got 401\n"
                "Traceback (most recent call last):\n"
                '  File "tests/test_auth.py", line 44, in test_token_refresh\n'
                "    assert resp.status_code == 200\n"
                "AssertionError: expected 200, got 401 (clock skew > 60s)\n"
            ),
        ),
        (
            "ci_windows_py310",
            (
                f"{noise}\n"
                "[ENV] OPENAI_API_KEY=sk-proj-abc1234567890xyz9876543210\n"
                "ERROR: UnicodeEncodeError: 'charmap' codec can't encode character '\\u2713' in cp1252\n"
                "Process exited with code 1 during subprocess_runner UTF-8 test\n"
            ),
        ),
        (
            "ci_macos_wheel",
            (
                f"{noise}\n"
                "[INFO] Building wheel with hatchling...\n"
                "FAILED tests/test_wheel.py::test_cli_entrypoint - FileNotFoundError: config/default.json missing\n"
                "ERROR: Wheel verification failed on macos-latest\n"
            ),
        ),
    ]


def _load_logs(log_dir: Path | None) -> list[tuple[str, str]]:
    if log_dir is None:
        return _build_sample_logs()
    files = sorted([*log_dir.glob("*.log"), *log_dir.glob("*.txt")])
    if not files:
        raise FileNotFoundError(f"No .log or .txt files found in {log_dir}")
    return [(f.stem, f.read_text(encoding="utf-8", errors="replace")) for f in files]


def _mock_triage(prompt: str, _system: str) -> str:
    lower = prompt.lower()
    if "401" in lower or "clock skew" in lower:
        cause, severity, action = "OAuth token refresh 401 due to clock skew", "high", "Sync test clock / leeway"
    elif "unicodeencodeerror" in lower or "cp1252" in lower:
        cause, severity, action = "Windows cp1252 encoding failure", "high", "Set PYTHONUTF8=1 in subprocess"
    elif "filenotfounderror" in lower or "wheel" in lower:
        cause, severity, action = "Missing bundled asset in wheel", "medium", "Verify force-include in pyproject.toml"
    else:
        cause, severity, action = "Unclassified CI failure", "medium", "Inspect trimmed traceback"
    return json.dumps({"root_cause": cause, "severity": severity, "next_action": action})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="TierForge Example 03: CI Failure Log Triage.")
    parser.add_argument("--job-id", default="demo_03_ci_triage", help="Job ID for SQLite checkpointing.")
    parser.add_argument("--db", default="data/demo_03_state.db", help="SQLite checkpoint path.")
    parser.add_argument("--log-dir", type=Path, default=None, help="Optional directory of .log/.txt CI logs.")
    parser.add_argument("--model", default=None, help="Local model override (default: hardware-recommended).")
    parser.add_argument("--dry-run", action="store_true", help="Print cost estimate vs. Cloud baseline and exit.")
    parser.add_argument("--mock-llm", action="store_true", help="Run with offline mock LLM (no Ollama required).")
    parser.add_argument("--reset", action="store_true", help="Delete existing demo SQLite DB before running.")
    args = parser.parse_args(argv)

    data_dir = default_data_dir()
    db_path = (data_dir / "demo_03_state.db") if args.db == "data/demo_03_state.db" else Path(args.db).expanduser()
    if args.reset and db_path.exists():
        db_path.unlink()

    logs = _load_logs(args.log_dir)
    rec_hw = recommend_local_model()
    model_name = args.model or rec_hw["model"]
    tracker = CostTracker(job_id=args.job_id, daily_spend_path=data_dir / "demo_daily_spend.json")

    if args.dry_run:
        local_est = tracker.estimate_dry_run(
            provider=f"ollama/{model_name}",
            item_count=len(logs),
            avg_input_tokens=800,
            avg_output_tokens=150,
        )
        cloud_est = tracker.estimate_dry_run(
            provider="external/text-frontier",
            item_count=len(logs),
            avg_input_tokens=3200,
            avg_output_tokens=200,
        )
        print("=== VieForge Example 03 --dry-run (CI Log Triage) ===")
        print(f"Hardware profile  : {rec_hw['profile']} -> local model: {model_name}")
        print(f"CI logs to triage : {len(logs)}")
        print(f"Tier 2 Local LLM  : ${local_est.estimated_base_usd:.4f} USD (trimmed input)")
        print(f"Cloud Frontier    : ${cloud_est.estimated_base_usd:.4f} USD (raw untrimmed logs)")
        return 0

    router = LLMRouter(
        model=model_name,
        mock=args.mock_llm,
        mock_handler=_mock_triage if args.mock_llm else None,
    )

    log_map = dict(logs)
    reg_pairs = [(job_name, compute_input_hash(text)) for job_name, text in logs]
    raw_chars_total = 0
    trimmed_chars_total = 0
    secrets_redacted_total = 0

    with StateDB(db_path) as db:
        counts = db.register_items(args.job_id, reg_pairs)
        pending = db.get_pending_items(args.job_id, max_retries=tracker.max_retry_per_item)
        print(
            f"[Start] job={args.job_id} total={len(logs)} "
            f"already_done={counts['done']} pending={len(pending)} "
            f"mode={'mock-llm' if args.mock_llm else f'ollama/{model_name}'}"
        )

        if pending:
            with router.batch_session(keep_alive="2m", unload_on_exit=True):
                for rec in pending:
                    raw_log = log_map[rec.item_id]
                    trimmed = trim_output(
                        raw_log,
                        max_lines=30,
                        max_chars=2000,
                        max_traceback_lines=15,
                        redact_secrets=True,
                    )
                    raw_chars_total += trimmed.raw_chars
                    trimmed_chars_total += trimmed.trimmed_chars
                    secrets_redacted_total += trimmed.secrets_redacted

                    resp = router.generate_json(
                        f"Triage this CI failure log and return JSON with root_cause, severity, next_action:\n{trimmed.text}",
                        system="Return strictly valid JSON.",
                        required_keys=["root_cause", "severity", "next_action"],
                        max_retries=2,
                    )
                    db.mark_done(args.job_id, rec.item_id, output=resp.parsed_json, cost_usd=0.0)
                    tracker.record("triage", f"ollama/{model_name}", 0.0, item_id=rec.item_id)
                    print(
                        f"  [TRIAGED] {rec.item_id} "
                        f"(saved={trimmed.saved_pct:.1f}%, secrets_redacted={trimmed.secrets_redacted}) "
                        f"-> {resp.parsed_json}"
                    )

        summary = db.get_job_summary(args.job_id)

    compact = compact_job_summary(summary)
    print(f"\n[SQLite Checkpoint Summary] {compact}")
    print(
        f"[Log Compression] raw={raw_chars_total} chars -> trimmed={trimmed_chars_total} chars "
        f"| secrets_redacted={secrets_redacted_total}"
    )
    receipt = tracker.build_savings_receipt(
        items_processed=summary["done"],
        cloud_baseline_provider="external/text-frontier",
        avg_input_tokens=3200,
        avg_output_tokens=200,
        raw_log_chars=max(raw_chars_total, len(compact) * 4),
        trimmed_log_chars=max(trimmed_chars_total, len(compact)),
    )
    print("\n" + receipt.format_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
