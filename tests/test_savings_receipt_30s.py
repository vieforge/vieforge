"""Tests for VieForge 30-second Savings Receipt and CLI discipline wiring."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from scripts import cli, savings_receipt_30s


def test_receipt_payload_metrics_consistent() -> None:
    payload = savings_receipt_30s.build_receipt_payload()
    assert payload["schema_version"] == "1.0"
    b = payload["benchmark"]
    assert b["without_tokens"] > b["with_tokens"] > 0
    assert b["saved_tokens"] == b["without_tokens"] - b["with_tokens"]
    assert 70.0 <= b["reduction_pct"] <= 99.9
    assert b["multiplier"] >= 5.0
    assert b["saved_usd"] >= 0.0
    assert len(b["workloads"]) == 3


def test_receipt_ascii_markdown_and_svg_outputs(tmp_path: Path) -> None:
    payload = savings_receipt_30s.build_receipt_payload()

    ascii_out = savings_receipt_30s.render_ascii_receipt(payload)
    assert "30-SECOND SAVINGS RECEIPT" in ascii_out
    assert "pip install vieforge && vieforge receipt" in ascii_out

    md_out = savings_receipt_30s.render_markdown_receipt(payload)
    assert "| Metric | Cloud-Only | With VieForge | Net Savings |" in md_out
    assert "```bash" in md_out

    svg_path = tmp_path / "receipt.svg"
    rc = savings_receipt_30s.main(["--svg", str(svg_path)])
    assert rc == 0
    assert svg_path.is_file()
    svg_text = svg_path.read_text(encoding="utf-8")
    root = ET.fromstring(svg_text)
    assert root.tag.endswith("svg")
    assert "Savings Receipt" in svg_text


def test_receipt_cli_json_and_discipline_subcommands(capsys) -> None:
    rc = cli.main(["receipt", "--json"])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["benchmark"]["reduction_pct"] > 70.0

    assert "receipt" in cli.SUBCOMMANDS
    assert "third-patch" in cli.SUBCOMMANDS
    assert "doc-drift" in cli.SUBCOMMANDS


def test_receipt_with_real_local_project_telemetry(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "step_audit_log.jsonl").write_text(
        "\n".join(json.dumps({"step": f"s{i}", "passed": True}) for i in range(6)) + "\n",
        encoding="utf-8",
    )
    payload = savings_receipt_30s.build_receipt_payload(project_dirs=[tmp_path])
    assert payload["local_projects"] is not None
    assert payload["local_projects"]["totals"]["tier0_qa_runs"] == 6
    ascii_out = savings_receipt_30s.render_ascii_receipt(payload)
    assert "Real Local Projects Scanned" in ascii_out
