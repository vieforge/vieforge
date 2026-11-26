"""Unit tests for the reproducible token savings benchmark."""

from __future__ import annotations

from scripts.benchmark_token_savings import run_all_benchmarks


def test_reproducible_benchmark_numbers_are_honest_and_high_impact() -> None:
    report = run_all_benchmarks()
    assert report["baseline_overhead"]["total_session_overhead_tokens"] > 500
    assert len(report["workloads"]) == 3

    by_id = {w["id"]: w for w in report["workloads"]}
    assert by_id["batch_100_docs"]["multiplier"] >= 20.0
    assert by_id["noisy_ci_log"]["multiplier"] >= 10.0
    assert by_id["interrupt_resume_50"]["multiplier"] >= 20.0
    assert report["combined_net"]["net_reduction_pct"] >= 90.0
    assert report["combined_net"]["net_multiplier"] >= 10.0
