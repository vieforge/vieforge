"""Unit tests for clean-room Output Trimmer (ANSI, progress bars, tracebacks, JSON summary)."""

from __future__ import annotations

from core.output_trimmer import compact_job_summary, trim_output


def test_trim_strips_ansi_progress_bars_and_duplicates() -> None:
    noisy = "\n".join(
        [
            "\x1b[32mStarting worker...\x1b[0m",
            "Downloading weights 10%\rDownloading weights 50%\rDownloading weights 100%",
            " 45%|████▌     | 45/100 [00:05<00:06, 8.9it/s]",
            "100%|██████████| 100/100 [00:11<00:00, 9.0it/s]",
            *["heartbeat ok"] * 25,
            "Finished batch cleanly.",
        ]
    )
    res = trim_output(noisy)
    assert "\x1b[" not in res.text
    assert "45%|" not in res.text
    assert "heartbeat ok (repeated 25x)" in res.text
    assert "Finished batch cleanly." in res.text
    assert res.saved_pct > 50.0


def test_trim_caps_long_python_traceback_to_last_20_lines() -> None:
    tb_lines = ["Traceback (most recent call last):"]
    for i in range(40):
        tb_lines.append(f'  File "module_{i}.py", line {i + 1}, in step_{i}')
        tb_lines.append(f"    call_next({i})")
    tb_lines.append("ValueError: schema mismatch on item_42")

    res = trim_output("\n".join(tb_lines), max_traceback_lines=20)
    assert res.traceback_trimmed is True
    assert "ValueError: schema mismatch on item_42" in res.text
    assert "traceback lines trimmed by TierForge" in res.text
    assert len(res.text.splitlines()) <= 25


def test_compact_job_summary_is_single_line_json() -> None:
    s = compact_job_summary({"job_id": "demo", "done": 10, "failed": 0, "cost": 0.0})
    assert "\n" not in s
    assert '"done":10' in s
