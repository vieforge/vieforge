#!/usr/bin/env python3
"""Example 01: 5-Minute $0 Batch Document Processor (Dry-Run + Interrupt/Resume + Savings Receipt).

Demonstrates TierForge's core value proposition in 3 commands:
  1. Dry-run cost preview:
       python examples/01_batch_doc_processor/run_demo.py --dry-run
  2. Forced mid-run interrupt after 2 items (offline --mock-llm or real Ollama):
       python examples/01_batch_doc_processor/run_demo.py --mock-llm --simulate-interrupt-after 2
  3. Zero-cost checkpoint resume (skips already-done items, completes the rest):
       python examples/01_batch_doc_processor/run_demo.py --mock-llm
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
from core.output_trimmer import compact_job_summary
from core.state_db import StateDB, compute_input_hash
from core.vram_manager import recommend_local_model

SAMPLE_DOCS = [
    (
        "doc_01_auth",
        "Incident report: OAuth token refresh failed intermittently under 200 RPS due to clock skew of 90s.",
    ),
    (
        "doc_02_billing",
        "Customer invoice #8841 disputed duplicate charge of $49.00 caused by missing idempotency key on retry.",
    ),
    (
        "doc_03_gpu",
        "Worker OOM on 8GB VRAM card when keeping Ollama resident while spawning local diffusion pipeline.",
    ),
    (
        "doc_04_search",
        "Full-text search p99 latency spiked from 45ms to 1.8s after unindexed JSONB filter was deployed.",
    ),
    (
        "doc_05_release",
        "Release v2.4.0 checklist passed unit tests, license audit, and zero-downtime migration verification.",
    ),
]


def _mock_extractor(prompt: str, _system: str) -> str:
    body = prompt.splitlines()[-1].strip() if "\n" in prompt else prompt.strip()
    first_word = body.split(":")[0].strip() if ":" in body else "General"
    return json.dumps(
        {
            "category": first_word[:32],
            "severity": "high" if any(w in body.lower() for w in ("oom", "failed", "disputed")) else "normal",
            "summary": body[:90],
        }
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="TierForge 5-Minute $0 Batch Document Processor Demo.")
    parser.add_argument("--job-id", default="demo_01_batch", help="Job ID for SQLite checkpointing.")
    parser.add_argument("--db", default="data/demo_01_state.db", help="SQLite checkpoint path.")
    parser.add_argument("--model", default=None, help="Ollama model override (default: hardware-recommended).")
    parser.add_argument("--dry-run", action="store_true", help="Print cost estimate vs. Cloud baseline and exit.")
    parser.add_argument("--mock-llm", action="store_true", help="Run with offline mock LLM (no Ollama required).")
    parser.add_argument(
        "--simulate-interrupt-after",
        type=int,
        default=0,
        help="Simulate a forced crash/interrupt after N items to demonstrate $0 checkpoint resume.",
    )
    parser.add_argument("--reset", action="store_true", help="Delete existing demo SQLite DB before running.")
    args = parser.parse_args()

    db_path = REPO_ROOT / args.db
    if args.reset and db_path.exists():
        db_path.unlink()

    rec_hw = recommend_local_model()
    model_name = args.model or rec_hw["model"]
    tracker = CostTracker(job_id=args.job_id, daily_spend_path=REPO_ROOT / "data" / "demo_daily_spend.json")

    if args.dry_run:
        local_est = tracker.estimate_dry_run(
            provider="ollama/qwen2.5:7b",
            item_count=len(SAMPLE_DOCS),
            avg_input_tokens=1200,
            avg_output_tokens=300,
        )
        cloud_est = tracker.estimate_dry_run(
            provider="external/text-frontier",
            item_count=len(SAMPLE_DOCS),
            avg_input_tokens=1200,
            avg_output_tokens=300,
        )
        print("=== TierForge --dry-run Cost Comparison ===")
        print(f"Hardware profile  : {rec_hw['profile']} -> recommended local model: {model_name}")
        print(f"Items to process  : {len(SAMPLE_DOCS)}")
        print(f"Tier 2 Local LLM  : ${local_est.estimated_base_usd:.4f} USD (within_budget={local_est.within_budget})")
        print(f"Cloud Frontier    : ${cloud_est.estimated_base_usd:.4f} USD (worst-case w/ retries: ${cloud_est.estimated_worst_case_usd:.4f})")
        print(f"Approval required : {local_est.requires_approval}")
        return 0

    router = LLMRouter(
        model=model_name,
        mock=args.mock_llm,
        mock_handler=_mock_extractor if args.mock_llm else None,
    )

    doc_map = dict(SAMPLE_DOCS)
    reg_pairs = [(doc_id, compute_input_hash(text)) for doc_id, text in SAMPLE_DOCS]

    interrupted = False
    processed_now = 0
    raw_verbose_chars = 0

    with StateDB(db_path) as db:
        counts = db.register_items(args.job_id, reg_pairs)
        pending = db.get_pending_items(args.job_id, max_retries=tracker.max_retry_per_item)
        print(
            f"[Start] job={args.job_id} total={len(SAMPLE_DOCS)} "
            f"already_done={counts['done']} pending={len(pending)} "
            f"mode={'mock-llm' if args.mock_llm else f'ollama/{model_name}'}"
        )

        if pending:
            with router.batch_session(keep_alive="2m", unload_on_exit=True):
                for rec in pending:
                    text = doc_map[rec.item_id]
                    resp = router.generate_json(
                        f"Extract JSON with keys category, severity, summary from:\n{text}",
                        system="Return strictly valid JSON.",
                        required_keys=["category", "severity", "summary"],
                        max_retries=2,
                    )
                    db.mark_done(args.job_id, rec.item_id, output=resp.parsed_json, cost_usd=0.0)
                    tracker.record("extract", f"ollama/{model_name}", 0.0, item_id=rec.item_id)
                    raw_verbose_chars += len(text) + len(resp.text) + 450
                    processed_now += 1
                    print(f"  [DONE] {rec.item_id} (attempts={resp.attempts}) -> {resp.parsed_json}")

                    if args.simulate_interrupt_after and processed_now >= args.simulate_interrupt_after:
                        print(
                            f"\n[INTERRUPTED] Simulated crash after {processed_now} items! "
                            f"Re-run the same command to resume at $0 extra cost."
                        )
                        interrupted = True
                        break

        summary = db.get_job_summary(args.job_id)

    compact = compact_job_summary(summary)
    print(f"\n[SQLite Checkpoint Summary] {compact}")
    if not interrupted:
        receipt = tracker.build_savings_receipt(
            items_processed=summary["done"],
            cloud_baseline_provider="external/text-frontier",
            avg_input_tokens=1200,
            avg_output_tokens=300,
            raw_log_chars=max(raw_verbose_chars, len(compact) * 4),
            trimmed_log_chars=len(compact),
        )
        print("\n" + receipt.format_summary())
    return 130 if interrupted else 0


if __name__ == "__main__":
    raise SystemExit(main())
