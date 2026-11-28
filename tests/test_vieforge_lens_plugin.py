from __future__ import annotations

import json
from pathlib import Path

from scripts.mcp_server import PANEL_APP_URI, _load_panel_html, tool_open_vieforge_panel


def test_vieforge_lens_plugin_manifest_and_csp_safe_html() -> None:
    plugin_dir = Path(__file__).resolve().parent.parent / "plugins" / "vieforge-lens"
    manifest = json.loads((plugin_dir / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "vieforge-lens"
    assert manifest["logo"] == "assets/logo.svg"

    mcp_cfg = json.loads((plugin_dir / "mcp_config.json").read_text(encoding="utf-8"))
    assert "lens" in mcp_cfg["mcpServers"]

    html = _load_panel_html()
    assert PANEL_APP_URI == "ui://vieforge/panel.html"
    assert "VieForge Lens" in html
    assert "innerHTML" not in html
    assert "http://" not in html and "https://" not in html
    assert "--color-background-primary" in html
    for label in ("English", "Tiếng Việt", "日本語", "Deutsch", "한국어"):
        assert label in html
    for elem_id in ("balanceCard", "valBalSpent", "valBalSaved", "valBalNet", "valSessionDelta", "quotaSelect", "customQuotaInput", "winTabs", "valRemWith", "valRemWithout", "valBalDisclaimer"):
        assert elem_id in html


def test_tool_open_vieforge_panel_returns_structured_telemetry(tmp_path: Path) -> None:
    data = tool_open_vieforge_panel(project_dir=str(tmp_path))
    assert data["panel"] == "vieforge-lens"
    assert "savings" in data and "totals" in data["savings"]
    assert "user_plan" in data and int(data["user_plan"]["quota_tokens"]) >= 10_000
    assert "account_balance" in data
    assert "window_summaries" in data
    for w in ("5h", "7d", "30d", "all"):
        assert w in data["window_summaries"]
        wb = data["window_summaries"][w]
        assert wb["net_tokens"] == wb["saved_tokens"] - wb["spent_tokens"]
    bal = data["account_balance"]
    assert bal["net_tokens"] == bal["saved_tokens"] - bal["spent_tokens"]
    assert "burn_pct" in bal and "shield_ratio" in bal
    assert "preflight" in data and "checks" in data["preflight"]


def test_rolling_window_since_ts_and_user_plan_persistence(tmp_path: Path) -> None:
    import time
    from scripts.mcp_server import load_user_plan, tool_app_set_user_plan
    from scripts.savings_report import collect_project_savings

    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    now = time.time()
    (data_dir / "bridge_offload_log.jsonl").write_text(
        json.dumps({"timestamp": now - 10 * 86400, "offloaded_tokens": 5000, "service": "gemini"}) + "\n"
        + json.dumps({"timestamp": now - 600, "offloaded_tokens": 1200, "service": "gemini"}) + "\n",
        encoding="utf-8",
    )
    all_sav = collect_project_savings([tmp_path])
    win_5h = collect_project_savings([tmp_path], since_ts=now - 5 * 3600)
    assert all_sav["totals"]["bridge_offload_tokens"] == 6200
    assert win_5h["totals"]["bridge_offload_tokens"] == 1200

    plan_file = tmp_path / "user_plan.json"
    tool_app_set_user_plan(
        plan_id="custom",
        quota_tokens=1_200_000,
        plan_label="Custom 1.2M",
        project_dir=str(tmp_path),
        plan_path=plan_file,
    )
    loaded = load_user_plan(plan_path=plan_file)
    assert loaded["quota_tokens"] == 1_200_000
    assert loaded["source"] == "saved"
