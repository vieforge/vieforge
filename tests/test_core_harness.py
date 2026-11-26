"""Offline $0 unit tests for TierForge Harness synced core modules."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.cost_tracker import BudgetExceededError, CostTracker, compute_provider_cost_usd, load_pricing
from core.license_audit import run_license_audit
from core.pipeline_runner import (
    GateBlockedError,
    GateStatus,
    JobWorkspace,
    assert_gate_approved,
    check_retry_cap,
    run_profiled_stage,
)
from core.vram_manager import GPUStatus, vram_phase_profiler


def test_vram_phase_profiler_detects_leak_and_logs(tmp_path: Path) -> None:
    leaks = []
    states = iter([
        GPUStatus(name="RTX 3060 Ti", used_mb=500, total_mb=8192),
        GPUStatus(name="RTX 3060 Ti", used_mb=4200, total_mb=8192),
    ])

    def _query() -> GPUStatus:
        try:
            return next(states)
        except StopIteration:
            return GPUStatus(name="RTX 3060 Ti", used_mb=4200, total_mb=8192)

    def _flush(**_kwargs) -> GPUStatus:
        return GPUStatus(name="RTX 3060 Ti", used_mb=950, total_mb=8192)

    log_file = tmp_path / "vram.jsonl"
    with vram_phase_profiler(
        "unit_stage",
        episode_id="job01",
        leak_threshold_mb=200,
        poll_interval_sec=0.05,
        log_path=log_file,
        on_leak=leaks.append,
        query_fn=_query,
        flush_fn=_flush,
    ) as holder:
        pass

    rec = holder["record"]
    assert rec.leak_detected is True
    assert rec.leak_mb == 450
    assert len(leaks) == 1
    assert log_file.exists()
    logged = json.loads(log_file.read_text(encoding="utf-8").strip())
    assert logged["stage"] == "unit_stage"


def test_license_audit_clean_repo_and_banned_detection(tmp_path: Path) -> None:
    # Clean tierforge-harness repo itself must pass
    rep_self = run_license_audit(Path(__file__).resolve().parent.parent, check_installed_pkgs=False)
    assert rep_self.passed is True

    # Synthetic repo with banned AGPL import must fail
    bad_dir = tmp_path / "core"
    bad_dir.mkdir(parents=True)
    (bad_dir / "bad.py").write_text("import depthflow\n", encoding="utf-8")
    rep_bad = run_license_audit(tmp_path, check_installed_pkgs=False)
    assert rep_bad.passed is False
    assert any(f.component == "DepthFlow" for f in rep_bad.findings)


def test_pipeline_runner_gates_retry_and_workspace(tmp_path: Path) -> None:
    ws = JobWorkspace(tmp_path, "job_alpha")
    mf = ws.load_manifest()
    assert mf["job_id"] == "job_alpha"
    ws.save_manifest(mf)
    assert ws.manifest_path.exists()

    with pytest.raises(GateBlockedError):
        assert_gate_approved(GateStatus.PENDING, "Gate 1")
    assert_gate_approved(GateStatus.APPROVED, "Gate 1")

    check_retry_cap(1, max_retries=2)
    with pytest.raises(RuntimeError):
        check_retry_cap(2, max_retries=2)

    res, rec = run_profiled_stage(
        "echo_stage",
        lambda x: x * 2,
        21,
        job_id="job_alpha",
        log_path=tmp_path / "profile.jsonl",
    )
    assert res == 42
    assert rec.stage == "echo_stage"


def test_cost_tracker_two_tier_budget_guard(tmp_path: Path) -> None:
    pricing = load_pricing()
    assert compute_provider_cost_usd("ollama/qwen2.5:7b", pricing, input_tokens=500_000) == 0.0
    assert compute_provider_cost_usd("external/media-render-unit", pricing, count=2) == 0.16

    tracker = CostTracker(
        job_id="job_budget",
        max_job_usd=1.0,
        daily_usd_cap=1.5,
        daily_spend_path=tmp_path / "daily.json",
    )
    tracker.record("stage1", "external/media-render-unit", 0.60, item_id=1)
    assert tracker.total_usd == 0.60

    with pytest.raises(BudgetExceededError):
        tracker.record("stage2", "external/media-render-unit", 0.50, item_id=2)

    out = tracker.save_report(tmp_path / "report.json")
    assert out.exists()
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["total_usd"] == 0.60


def test_cdp_bridge_service_matching_and_router(monkeypatch) -> None:
    from core import cdp_bridge

    fake_eps = [
        cdp_bridge.CDPEndpoint(port=61552, url="http://127.0.0.1:61552", tabs=["https://flow.google.com/project/abc"]),
        cdp_bridge.CDPEndpoint(port=56632, url="http://127.0.0.1:56632", tabs=["https://gemini.google.com/app?hl=vi"]),
        cdp_bridge.CDPEndpoint(port=59999, url="http://127.0.0.1:59999", tabs=["https://chatgpt.com/c/123"]),
    ]
    monkeypatch.setattr(cdp_bridge, "discover_cdp_endpoints", lambda **_kw: fake_eps)

    assert cdp_bridge.resolve_cdp_url(service="flow") == "http://127.0.0.1:61552"
    assert cdp_bridge.resolve_cdp_url(service="gemini") == "http://127.0.0.1:56632"
    assert cdp_bridge.resolve_cdp_url(service="chatgpt") == "http://127.0.0.1:59999"
    assert cdp_bridge.resolve_cdp_url(service="any") == "http://127.0.0.1:59999"

    router = cdp_bridge.ThreeTierRouter()
    assert router.run_tier0_local(lambda a, b: a + b, 3, 4) == 7
    assert router.get_tier1_cdp_url("flow") == "http://127.0.0.1:61552"


def test_cdp_bridge_zero_touch_auto_launch_fallback(monkeypatch, tmp_path: Path) -> None:
    from core import cdp_bridge

    monkeypatch.setattr(cdp_bridge, "discover_cdp_endpoints", lambda **_kw: [])
    monkeypatch.setattr(
        cdp_bridge,
        "detect_installed_browsers",
        lambda: [
            cdp_bridge.BrowserCandidate(name="chrome", executable="C:\\Fake\\chrome.exe"),
            cdp_bridge.BrowserCandidate(name="coccoc", executable="C:\\Fake\\browser.exe"),
        ],
    )
    monkeypatch.setattr(cdp_bridge, "find_free_port", lambda *_a, **_kw: 9244)
    monkeypatch.setattr(cdp_bridge, "get_default_profile_dir", lambda: tmp_path / "worker-profile")

    launched_cmds: list[list[str]] = []

    class _DummyProc:
        pass

    def _fake_popen(cmd, **_kw):
        launched_cmds.append(list(cmd))
        return _DummyProc()

    monkeypatch.setattr(cdp_bridge.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        cdp_bridge,
        "_probe_cdp_tabs",
        lambda port, timeout=1.0: ["https://gemini.google.com/app"] if port == 9244 else None,
    )

    # Without auto_launch -> raises actionable error
    with pytest.raises(RuntimeError):
        cdp_bridge.resolve_cdp_url(service="gemini", auto_launch=False)

    # With auto_launch=True & preferred_browser='coccoc' -> zero-touch launches Coc Coc on isolated profile
    url = cdp_bridge.resolve_cdp_url(service="gemini", auto_launch=True, preferred_browser="coccoc")
    assert url == "http://127.0.0.1:9244"
    assert len(launched_cmds) == 1
    assert launched_cmds[0][0] == "C:\\Fake\\browser.exe"
    assert "--remote-debugging-port=9244" in launched_cmds[0]
    assert any(arg.startswith("--user-data-dir=") for arg in launched_cmds[0])
    assert "--remote-allow-origins=*" in launched_cmds[0]


def test_step_auditor_local_telemetry_and_prompt_export(tmp_path: Path) -> None:
    from core.step_auditor import export_step_3judge_audit, run_step_telemetry

    repo_root = Path(__file__).resolve().parent.parent
    tel = run_step_telemetry(repo_root, "unit_check", run_tests=False)
    assert tel.passed is True
    assert tel.stdlib_pure is True
    assert tel.license_passed is True
    assert tel.local_score >= 95.0

    # Synthetic workspace with stray patch_* script must deduct points & log finding
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "ok.py").write_text("import json\n", encoding="utf-8")
    (tmp_path / "patch_quickfix.py").write_text("print('bad')\n", encoding="utf-8")
    out = export_step_3judge_audit(tmp_path, "step_bad_hygiene", run_tests=False)
    assert out["telemetry"]["checks"]["zero_patch_scripts"] is False
    assert Path(out["log_path"]).exists()
    assert Path(out["prompt_path"]).exists()


def test_extension_bridge_e2e_roundtrip_and_manifest() -> None:
    import threading
    import urllib.request

    from core.extension_bridge import ExtensionBridgeClient, ExtensionBridgeServer

    # 1. Verify MV3 extension bundle exists and manifest.json is valid
    repo_root = Path(__file__).resolve().parent.parent
    manifest_path = repo_root / "extensions" / "tierforge-bridge" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    assert "http://127.0.0.1:19876/*" in manifest["host_permissions"]
    assert "<all_urls>" not in manifest["host_permissions"]
    assert "cookies" not in manifest["permissions"]
    assert "gecko" in manifest["browser_specific_settings"]

    # 2. Spin up real localhost ExtensionBridgeServer on ephemeral port=0
    token = "tf_unit_test_secret"
    server = ExtensionBridgeServer(port=0, token=token)
    server.start_background()
    try:
        client = ExtensionBridgeClient(base_url=server.base_url, token=token)

        def _post(path: str, data: dict, auth_token: str = token) -> dict:
            req = urllib.request.Request(
                f"{server.base_url}{path}",
                data=json.dumps(data).encode("utf-8"),
                headers={"Content-Type": "application/json", "X-TierForge-Token": auth_token},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                return json.loads(resp.read().decode("utf-8"))

        # 1b. Interactive Onboarding Wizard (/setup) must serve 200 HTML for non-technical users
        with urllib.request.urlopen(f"{server.base_url}/setup", timeout=5.0) as resp:
            assert resp.status == 200
            html_body = resp.read().decode("utf-8")
            assert "TierForge AI Bridge" in html_body
            assert "/setup/launch-browser" in html_body

        # 2a. Zero-config auto-pair MUST reject web origins (403) and accept extension origins (200)
        import urllib.error

        evil_req = urllib.request.Request(
            f"{server.base_url}/extension/pair",
            data=b"{}",
            headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            urllib.request.urlopen(evil_req, timeout=5.0)
        assert exc_info.value.code == 403

        ext_pair_req = urllib.request.Request(
            f"{server.base_url}/extension/pair",
            data=json.dumps({"browser": "coccoc"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "chrome-extension://abcdef123456"},
            method="POST",
        )
        with urllib.request.urlopen(ext_pair_req, timeout=5.0) as resp:
            pair_data = json.loads(resp.read().decode("utf-8"))
        assert pair_data["ok"] is True
        assert pair_data["auto_paired"] is True
        assert pair_data["token"] == token
        assert pair_data["pinned_origin"] == "chrome-extension://abcdef123456"

        # 2b. TOFU Lock: a second rogue extension origin MUST be hard-blocked with HTTP 403
        rogue_ext_req = urllib.request.Request(
            f"{server.base_url}/extension/pair",
            data=json.dumps({"browser": "chrome"}).encode("utf-8"),
            headers={"Content-Type": "application/json", "Origin": "chrome-extension://rogue999999"},
            method="POST",
        )
        with pytest.raises(urllib.error.HTTPError) as rogue_exc:
            urllib.request.urlopen(rogue_ext_req, timeout=5.0)
        assert rogue_exc.value.code == 403

        # Register simulated Coc Coc extension via heartbeat using auto-paired token
        hb = _post(
            "/extension/heartbeat",
            {"client_id": "coccoc_01", "browser": "coccoc", "tabs": ["https://gemini.google.com/app"]},
            auth_token=pair_data["token"],
        )
        assert hb["ok"] is True
        assert hb["job"] is None
        assert client.is_ready() is True

        # Dispatch prompt in background thread while simulated extension polls & replies
        result_holder: dict[str, str] = {}

        def _caller() -> None:
            result_holder["text"] = client.dispatch_prompt("Ping Gemini", service="gemini", timeout_sec=5.0)

        t = threading.Thread(target=_caller, daemon=True)
        t.start()

        # Poll heartbeat until job arrives
        job = None
        for _ in range(30):
            hb2 = _post(
                "/extension/heartbeat",
                {"client_id": "coccoc_01", "browser": "coccoc", "tabs": ["https://gemini.google.com/app"]},
            )
            if hb2.get("job"):
                job = hb2["job"]
                break
            threading.Event().wait(0.05)

        assert job is not None
        assert job["service"] == "gemini"
        assert job["prompt"] == "Ping Gemini"

        # Extension submits response back to local server
        res_ack = _post(
            "/extension/result",
            {"job_id": job["job_id"], "ok": True, "response_text": "Pong from CocCoc Gemini"},
        )
        assert res_ack["ok"] is True

        t.join(timeout=3.0)
        assert result_holder.get("text") == "Pong from CocCoc Gemini"
    finally:
        server.stop()


