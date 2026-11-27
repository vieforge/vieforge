from __future__ import annotations

import json
from pathlib import Path

from core.qa_logger import emit_genai_span
from core.state_db import StateDB, compute_input_hash
from scripts.savings_report import collect_project_savings, format_terminal_report, main, render_html_report


def test_savings_report_collects_db_audit_and_traces(tmp_path: Path) -> None:
    proj = tmp_path / "demo_proj"
    data_dir = proj / "data"
    data_dir.mkdir(parents=True)

    with StateDB(data_dir / "state.db") as db:
        pairs = [(f"doc_{i}", compute_input_hash(f"body_{i}")) for i in range(12)]
        db.register_items("job_1", pairs)
        for item_id, _ in pairs:
            db.mark_done("job_1", item_id, output={"ok": True}, cost_usd=0.0)

    (data_dir / "step_audit_log.jsonl").write_text('{"step_name":"s1"}\n{"step_name":"s2"}\n', encoding="utf-8")
    emit_genai_span(
        operation="chat",
        provider="ollama",
        model="qwen2.5:7b",
        input_tokens=100,
        output_tokens=20,
        cost_usd=0.0,
        cache_hit=True,
        trace_path=data_dir / "genai_traces.jsonl",
    )

    rep = collect_project_savings([proj])
    assert rep["totals"]["checkpoint_done_items"] == 12
    assert rep["totals"]["tier0_qa_runs"] == 2
    assert rep["totals"]["llm_cache_hits"] == 1
    assert rep["totals"]["reduction_pct"] > 80.0
    assert rep["usage_tier"]["id"] == "L1_SOLO_DEV"

    txt = format_terminal_report(rep)
    assert "VieForge Real-World Project Savings Report" in txt
    assert "L1 — Solo Indie / Vibe Coder" in txt

    html_out = tmp_path / "out.html"
    rc = main(["--projects", str(proj), "--html", str(html_out), "--json"])
    assert rc == 0
    assert html_out.is_file()
    html_text = render_html_report(rep)
    assert "VieForge Real-World Savings Report" in html_text
    assert json.loads(json.dumps(rep))["projects_scanned"] == 1
