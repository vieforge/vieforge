#!/usr/bin/env python3
"""Example 02: Hybrid 8 GB VRAM Isolated Subprocess + Budget-Guarded API Skeleton.

Illustrates the safe 3-stage pattern for consumer 8 GB GPUs (zero OS mutations):
  Stage 1 (Tier 2): Batch local LLM with `batch_session()` -> unloads Ollama (`keep_alive=0`).
  Stage 2 (Tier 3 Local GPU): Child subprocess isolation via `run_isolated(..., profile_vram=True)`.
  Stage 3 (Tier 3 Paid API): Mandatory `--dry-run`, Approval Gate, and `CostTracker` cap.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.cost_tracker import CostTracker
from core.llm_router import LLMRouter
from core.pipeline_runner import GateBlockedError, GateStatus, assert_gate_approved
from core.subprocess_runner import run_isolated


def main() -> int:
    parser = argparse.ArgumentParser(description="TierForge 8GB VRAM Hybrid Pipeline Skeleton.")
    parser.add_argument("--items", type=int, default=5, help="Number of media units to plan.")
    parser.add_argument("--dry-run", action="store_true", help="Estimate paid Stage 3 cost and exit.")
    parser.add_argument("--approve", action="store_true", help="Approve Gate before paid Stage 3.")
    args = parser.parse_args()

    tracker = CostTracker(
        job_id="demo_02_hybrid",
        daily_spend_path=REPO_ROOT / "data" / "demo_02_daily_spend.json",
    )
    estimate = tracker.estimate_dry_run("external/media-render-unit", item_count=args.items)

    print("=== Stage 0: Pre-Flight Dry-Run ===")
    print(
        f"Paid Stage 3 estimate: {args.items} items -> "
        f"${estimate.estimated_base_usd:.2f} base / ${estimate.estimated_worst_case_usd:.2f} worst-case "
        f"(within_budget={estimate.within_budget}, requires_approval={estimate.requires_approval})"
    )
    if args.dry_run:
        return 0

    print("\n=== Stage 1 (Tier 2): Local Batch Planning (Mock) ===")
    router = LLMRouter(mock=True)
    with router.batch_session(keep_alive="2m", unload_on_exit=True):
        for i in range(1, args.items + 1):
            resp = router.generate_json(f"Plan render parameters for unit {i}")
            print(f"  Planned unit_{i}: {resp.parsed_json}")

    print("\n=== Stage 2 (Tier 3 Local GPU): Isolated Child Subprocess ===")
    sub_res = run_isolated(
        [sys.executable, "-c", "print('Local GPU worker completed cleanly in isolated subprocess.')"],
        check_vram=False,
        profile_vram=True,
        stage_name="local_gpu_render",
        job_id="demo_02_hybrid",
    )
    print(f"  returncode={sub_res.returncode} output='{sub_res.stdout_trimmed}'")

    print("\n=== Stage 3 (Tier 3 Paid API): Approval Gate Check ===")
    gate = GateStatus.APPROVED if args.approve else GateStatus.PENDING
    try:
        assert_gate_approved(gate, gate_name="Paid API Gate")
    except GateBlockedError as exc:
        print(f"  [STOPPED] {exc} (pass --approve to execute paid calls)")
        return 0

    print("  [APPROVED] Gate passed — ready to execute capped external API calls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
