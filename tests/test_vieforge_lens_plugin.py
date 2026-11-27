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


def test_tool_open_vieforge_panel_returns_structured_telemetry(tmp_path: Path) -> None:
    data = tool_open_vieforge_panel(project_dir=str(tmp_path))
    assert data["panel"] == "vieforge-lens"
    assert "savings" in data and "totals" in data["savings"]
    assert "preflight" in data and "checks" in data["preflight"]
