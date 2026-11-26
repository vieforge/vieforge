"""Unit tests for v0.4.0 functional upgrades (P1-P4):
  - P1: MCP `tool_run_local_batch` direct disk input (dir, .jsonl, .csv, glob)
  - P2: UTF-8 Unicode/Vietnamese safety in `core/subprocess_runner.py`
  - P3: Secret redaction & smart middle error retention in `core/output_trimmer.py`
  - P4: Unified `vieforge` CLI & OpenAI-compatible `/v1/models` preflight check
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

from core import __version__
from core.output_trimmer import redact_secrets_text, trim_output
from core.subprocess_runner import run_isolated
from mcp.worker_mcp_server import tool_run_local_batch
from scripts.cli import main as cli_main
from scripts.preflight_check import check_ollama


def test_p1_mcp_local_batch_disk_dir_jsonl_csv_and_glob(tmp_path: Path) -> None:
    db_path = tmp_path / "batch_disk.db"

    # 1. Directory input (must skip binary/hidden files and read text files directly from disk)
    docs_dir = tmp_path / "docs"
    docs_dir.mkdir()
    (docs_dir / "a_report.md").write_text("Incident: API 502 spike on checkout.", encoding="utf-8")
    (docs_dir / "b_note.txt").write_text("Backup succeeded in 42 seconds.", encoding="utf-8")
    (docs_dir / ".hidden.txt").write_text("should be ignored", encoding="utf-8")
    (docs_dir / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    res_dir = json.loads(
        tool_run_local_batch(
            job_id="disk_dir_job",
            input_path=str(docs_dir),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res_dir["input_source"] == "disk_dir"
    assert res_dir["summary"]["done"] == 2
    assert res_dir["processed_this_run"] == 2
    assert res_dir["total_raw_chars_loaded"] >= 60

    # Re-run same directory -> 100% skipped from SQLite checkpoint ($0)
    res_dir_2 = json.loads(
        tool_run_local_batch(
            job_id="disk_dir_job",
            input_path=str(docs_dir),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res_dir_2["processed_this_run"] == 0
    assert res_dir_2["skipped_from_checkpoint"] == 2

    # 2. JSONL input
    jsonl_file = tmp_path / "tickets.jsonl"
    jsonl_file.write_text(
        json.dumps({"id": "t1", "text": "Refund request #101"}) + "\n"
        + json.dumps({"id": "t2", "content": "Login timeout on iOS"}) + "\n",
        encoding="utf-8",
    )
    res_jsonl = json.loads(
        tool_run_local_batch(
            job_id="disk_jsonl_job",
            input_path=str(jsonl_file),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res_jsonl["input_source"] == "disk_jsonl"
    assert res_jsonl["summary"]["done"] == 2

    # 3. CSV input
    csv_file = tmp_path / "rows.csv"
    csv_file.write_text("id,text\nr1,Disk usage 92%\nr2,CPU normal\n", encoding="utf-8")
    res_csv = json.loads(
        tool_run_local_batch(
            job_id="disk_csv_job",
            input_path=str(csv_file),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res_csv["input_source"] == "disk_csv"
    assert res_csv["summary"]["done"] == 2

    # 4. Glob input
    res_glob = json.loads(
        tool_run_local_batch(
            job_id="disk_glob_job",
            input_glob=str(docs_dir / "*.md"),
            mock=True,
            db_path=str(db_path),
        )
    )
    assert res_glob["input_source"] == "disk_glob"
    assert res_glob["summary"]["done"] == 1


def test_p2_subprocess_runner_handles_utf8_unicode_cleanly() -> None:
    sample = "Kiểm tra tiếng Việt ✓ → thành công 100% ██"
    res = run_isolated([sys.executable, "-c", f"print({sample!a})"])
    assert res.returncode == 0
    assert "Kiểm tra tiếng Việt" in res.stdout_trimmed
    assert "✓ → thành công 100%" in res.stdout_trimmed


def test_p3_output_trimmer_secret_redaction_and_smart_error_retention() -> None:
    fake_openai = "sk-proj-" + ("AbCdEf1234567890" * 2)
    fake_gh = "ghp_" + ("9876543210abcdef" * 2)
    fake_bearer = "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload12345"
    raw_secret_log = (
        f"Connecting with {fake_openai}\n"
        f"GitHub token: {fake_gh}\n"
        f"Authorization: {fake_bearer}\n"
        "MY_SECRET_KEY=super_secret_prod_value_999\n"
        "TIERFORGE_LLM_API_KEY=ollama\n"
    )
    redacted, count = redact_secrets_text(raw_secret_log)
    assert count >= 4
    assert fake_openai not in redacted
    assert fake_gh not in redacted
    assert "eyJhbGci" not in redacted
    assert "super_secret_prod_value_999" not in redacted
    assert "TIERFORGE_LLM_API_KEY=ollama" in redacted

    # Build a 200-line log where a critical FAILED line sits right in the middle (line 100)
    lines = [f"step {i}: building module_{i} ok" for i in range(200)]
    lines[100] = f"FAILED tests/test_auth.py::test_login - AssertionError: token {fake_openai} rejected"
    trimmed = trim_output("\n".join(lines), max_lines=30, redact_secrets=True)
    assert trimmed.truncated is True
    assert trimmed.secrets_redacted >= 1
    assert "FAILED tests/test_auth.py::test_login" in trimmed.text
    assert fake_openai not in trimmed.text
    assert "[REDACTED_API_KEY]" in trimmed.text


def test_p4_unified_cli_and_openai_compat_preflight(capsys) -> None:
    assert cli_main(["--version"]) == 0
    out = capsys.readouterr().out.strip()
    assert out == f"vieforge {__version__}"

    assert cli_main(["--help"]) == 0
    help_out = capsys.readouterr().out
    assert "verify-os" in help_out and "bench" in help_out

    assert cli_main(["unknown_cmd"]) == 2
    capsys.readouterr()

    # Spin up local OpenAI-compatible stub `/v1/models` (like LM Studio / vLLM)
    class _ModelsHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/v1/models":
                body = json.dumps({"object": "list", "data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        res = check_ollama(f"http://127.0.0.1:{port}/v1")
        assert res["status"] == "OK"
        assert res["api_style"] == "openai"
        assert res["models_count"] == 1
        assert res["models_sample"] == ["qwen2.5-7b-instruct"]
    finally:
        srv.shutdown()
        srv.server_close()
