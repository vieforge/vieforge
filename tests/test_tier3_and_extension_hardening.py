"""Customer-machine E2E tests for Tier 3 GPU/VRAM pipeline & Layer-3 Browser Extension Bridge."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

from core.extension_bridge import ExtensionBridgeClient, ExtensionBridgeServer
from core.subprocess_runner import run_isolated
from scripts.cli import main as cli_main

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_tier3_run_isolated_with_profile_vram_job_id_and_fast_flush() -> None:
    """Regression guard: run_isolated(profile_vram=True, job_id=...) must never raise TypeError
    and must not stall waiting on a non-existent local ComfyUI port 8188.
    """
    t0 = time.perf_counter()
    res = run_isolated(
        [sys.executable, "-c", "print('tier3_gpu_worker_ok')"],
        check_vram=False,
        profile_vram=True,
        stage_name="tier3_unit_stage",
        job_id="tier3_job_99",
    )
    elapsed = time.perf_counter() - t0
    assert res.returncode == 0
    assert "tier3_gpu_worker_ok" in res.stdout_trimmed
    assert res.vram_record is not None
    assert res.vram_record["episode_id"] == "tier3_job_99"
    assert res.vram_record["stage"] == "tier3_unit_stage"
    # Without ComfyUI configured, flush must complete quickly (< 3s, never blocking 5s on port 8188)
    assert elapsed < 3.0


def test_tier3_example_02_full_pipeline_runs_end_to_end() -> None:
    """Execute full Example 02 (Stage 0 -> Stage 1 mock -> Stage 2 isolated + profile_vram -> Stage 3 gate)."""
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "examples" / "02_hybrid_media_pipeline" / "run_skeleton.py"), "--items", "2"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    assert "Stage 1 (Tier 2)" in proc.stdout
    assert "Stage 2 (Tier 3 Local GPU)" in proc.stdout
    assert "returncode=0" in proc.stdout
    assert "[STOPPED]" in proc.stdout


def test_bridge_launcher_portable_and_extension_hardening(capsys) -> None:
    # 1. Windows launcher .bat must use relative %~dp0.. and NEVER hardcode D:\ or C:\
    bat_text = (REPO_ROOT / "scripts" / "start_tierforge_bridge.bat").read_text(encoding="utf-8")
    assert "%~dp0.." in bat_text
    assert "D:\\" not in bat_text
    assert "C:\\" not in bat_text

    # 2. `vieforge bridge path` resolves exact extension directory
    assert cli_main(["bridge", "path"]) == 0
    ext_dir = Path(capsys.readouterr().out.strip())
    assert (ext_dir / "manifest.json").is_file()

    # 3. Verify MV3 extension hardening (onStartup, CAPTCHA/login stop, execCommand insertText, zero innerHTML XSS)
    bg_js = (ext_dir / "background.js").read_text(encoding="utf-8")
    cs_js = (ext_dir / "content_bridge.js").read_text(encoding="utf-8")
    pop_js = (ext_dir / "popup.js").read_text(encoding="utf-8")
    assert "onStartup" in bg_js
    assert "ensurePollAlarm" in bg_js
    assert "LOGIN_OR_CAPTCHA_REQUIRED" in cs_js
    assert "waitForInputEl" in cs_js
    assert 'execCommand("insertText"' in cs_js
    assert "innerHTML" not in pop_js


def test_extension_bridge_client_redacts_secrets_and_trims_response() -> None:
    token = "tf_sec_test_tok"
    server = ExtensionBridgeServer(port=0, token=token)
    server.start_background()
    received_prompts: list[str] = []
    try:
        client = ExtensionBridgeClient(base_url=server.base_url, token=token)

        def _post(path: str, data: dict) -> dict:
            req = urllib.request.Request(
                f"{server.base_url}{path}",
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-TierForge-Token": token},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode("utf-8"))

        _post("/extension/heartbeat", {"client_id": "c1", "browser": "chrome", "tabs": ["https://gemini.google.com/app"]})

        fake_secret = "sk-proj-" + ("XyZ1234567890AbC" * 2)
        holder: dict[str, str] = {}

        def _caller() -> None:
            holder["resp"] = client.dispatch_prompt(
                f"Debug key {fake_secret}",
                service="gemini",
                timeout_sec=5.0,
                redact_secrets=True,
                trim_response=True,
                max_response_chars=500,
            )

        t = threading.Thread(target=_caller, daemon=True)
        t.start()

        job = None
        for _ in range(30):
            hb = _post("/extension/heartbeat", {"client_id": "c1", "browser": "chrome", "tabs": ["https://gemini.google.com/app"]})
            if hb.get("job"):
                job = hb["job"]
                break
            time.sleep(0.05)

        assert job is not None
        received_prompts.append(job["prompt"])
        assert fake_secret not in job["prompt"]
        assert "[REDACTED_API_KEY]" in job["prompt"]

        _post("/extension/result", {"job_id": job["job_id"], "ok": True, "response_text": "\x1b[32mClean answer\x1b[0m"})
        t.join(timeout=3.0)
        assert holder.get("resp") == "Clean answer"
    finally:
        server.stop()
