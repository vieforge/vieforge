"""Unit tests for CostTracker --dry-run estimation, daily cap, and SavingsReceipt."""

from __future__ import annotations

from pathlib import Path

import pytest

from core.cost_tracker import BudgetExceededError, CostTracker


def test_dry_run_estimation_and_approval_gate(tmp_path: Path) -> None:
    tracker = CostTracker(
        job_id="job_dry",
        max_job_usd=2.0,
        daily_usd_cap=5.0,
        daily_spend_path=tmp_path / "daily.json",
    )

    # Local $0 model: within budget, no approval needed
    local_est = tracker.estimate_dry_run(
        provider="ollama/qwen2.5:7b",
        item_count=100,
        avg_input_tokens=1500,
        avg_output_tokens=500,
    )
    assert local_est.estimated_base_usd == 0.0
    assert local_est.within_budget is True
    assert local_est.requires_approval is False

    # Paid external media: 20 items * $0.08 = $1.60 base, $2.00 worst-case -> requires approval (> $1.00)
    paid_est = tracker.estimate_dry_run(
        provider="external/media-render-unit",
        item_count=20,
    )
    assert paid_est.estimated_base_usd == 1.6
    assert paid_est.within_budget is True
    assert paid_est.requires_approval is True

    # Over job cap: 30 items * $0.08 = $2.40 > $2.00 MAX_JOB_USD
    over_est = tracker.estimate_dry_run(
        provider="external/media-render-unit",
        item_count=30,
    )
    assert over_est.within_budget is False
    assert "MAX_JOB_USD" in over_est.reason


def test_daily_cap_enforced_across_jobs(tmp_path: Path) -> None:
    daily_file = tmp_path / "daily.json"
    t1 = CostTracker(job_id="j1", max_job_usd=5.0, daily_usd_cap=1.0, daily_spend_path=daily_file)
    t1.record("stage", "external/media-render-unit", 0.70, item_id="1")

    t2 = CostTracker(job_id="j2", max_job_usd=5.0, daily_usd_cap=1.0, daily_spend_path=daily_file)
    with pytest.raises(BudgetExceededError, match="DAILY_USD_CAP"):
        t2.record("stage", "external/media-render-unit", 0.40, item_id="2")


def test_savings_receipt_shows_local_vs_cloud_savings(tmp_path: Path) -> None:
    tracker = CostTracker(job_id="job_local", daily_spend_path=tmp_path / "daily.json")
    for idx in range(50):
        tracker.record("extract", "ollama/qwen2.5:7b", 0.0, item_id=idx)

    receipt = tracker.build_savings_receipt(
        items_processed=50,
        cloud_baseline_provider="external/text-frontier",
        avg_input_tokens=2000,
        avg_output_tokens=500,
        raw_log_chars=10000,
        trimmed_log_chars=1200,
    )
    assert receipt.actual_usd == 0.0
    assert receipt.cloud_baseline_usd > 0.0
    assert receipt.saved_pct == 100.0
    assert receipt.log_reduction_pct == 88.0
    text = receipt.format_summary()
    assert "TierForge Savings Receipt" in text
    assert "-88.0%" in text
