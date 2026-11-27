"""Offline $0 HTTP tests for LLMRouter (Ollama native + OpenAI-compatible servers)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from core.concurrency import OllamaLeaseManager
from core.llm_router import (
    PROVIDER_PRESETS,
    LLMRouter,
    _extract_json_text,
    _resolve_api_style,
    discover_local_llm,
    list_providers,
    resolve_provider_preset,
)


def test_resolve_api_style_auto_and_explicit() -> None:
    # Default Ollama endpoints stay native ollama (preserving keep_alive=0 VRAM control)
    assert _resolve_api_style("http://127.0.0.1:11434", "auto") == ("ollama", "http://127.0.0.1:11434")
    assert _resolve_api_style("http://127.0.0.1:11434/v1", "auto") == ("ollama", "http://127.0.0.1:11434")

    # LM Studio, llama.cpp, vLLM, OpenRouter /v1 endpoints auto-select openai
    assert _resolve_api_style("http://127.0.0.1:1234/v1", "auto") == ("openai", "http://127.0.0.1:1234/v1")
    assert _resolve_api_style("http://127.0.0.1:8080/v1/", "auto") == ("openai", "http://127.0.0.1:8080/v1")
    assert _resolve_api_style("https://openrouter.ai/api/v1", "auto") == ("openai", "https://openrouter.ai/api/v1")

    # Explicit overrides
    assert _resolve_api_style("http://127.0.0.1:11434", "openai") == ("openai", "http://127.0.0.1:11434/v1")
    assert _resolve_api_style("http://127.0.0.1:9000/v1", "ollama") == ("ollama", "http://127.0.0.1:9000")

    with pytest.raises(ValueError):
        _resolve_api_style("http://127.0.0.1:11434", "unknown_style")


class _Recorder:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.reject_response_format: bool = False
        self.fail_first_json: bool = False
        self.json_calls: int = 0


def _start_stub_server(rec: _Recorder) -> tuple[HTTPServer, str]:
    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8")
            body = json.loads(raw) if raw else {}
            rec.requests.append(
                {
                    "path": self.path,
                    "auth": self.headers.get("Authorization"),
                    "body": body,
                }
            )

            if self.path == "/v1/chat/completions":
                if rec.reject_response_format and "response_format" in body:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(b'{"error": "response_format unsupported"}')
                    return

                rec.json_calls += 1
                if rec.fail_first_json and rec.json_calls == 1:
                    content = "not valid json"
                else:
                    content = '```json\n{"category": "ops", "ok": true}\n```'

                resp = {
                    "id": "chatcmpl-test",
                    "choices": [{"message": {"role": "assistant", "content": content}}],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode("utf-8"))
                return

            if self.path == "/api/chat":
                resp = {"message": {"role": "assistant", "content": '{"tag": "ollama_ok"}'}}
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode("utf-8"))
                return

            if self.path == "/api/generate":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"done": true}')
                return

            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, f"http://127.0.0.1:{port}"


def test_openai_compatible_http_roundtrip_fallback_and_json_repair() -> None:
    rec = _Recorder()
    rec.reject_response_format = True
    rec.fail_first_json = True
    srv, base = _start_stub_server(rec)
    try:
        router = LLMRouter(
            base_url=f"{base}/v1",
            model="local-qwen-7b",
            api_key="sk-local-test",
        )
        assert router.api_style == "openai"
        assert router.chat_endpoint == f"{base}/v1/chat/completions"

        # batch_session in openai mode should not hit Ollama /api/generate unload
        with router.batch_session():
            resp = router.generate_json(
                "Classify log",
                system="Return JSON",
                required_keys=["category", "ok"],
                max_retries=2,
            )

        assert resp.parsed_json == {"category": "ops", "ok": True}
        assert resp.attempts == 2
        paths = [r["path"] for r in rec.requests]
        assert "/api/generate" not in paths
        assert all(r["auth"] == "Bearer sk-local-test" for r in rec.requests)
        # First request sent response_format and got 400 -> auto-retried without response_format
        assert "response_format" in rec.requests[0]["body"]
        assert "response_format" not in rec.requests[1]["body"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_ollama_native_http_roundtrip_and_batch_session_keep_alive(tmp_path: Path) -> None:
    rec = _Recorder()
    srv, base = _start_stub_server(rec)
    try:
        router = LLMRouter(base_url=base, model="qwen2.5:7b", api_style="ollama")
        assert router.api_style == "ollama"
        assert router.chat_endpoint == f"{base}/api/chat"

        leases = OllamaLeaseManager(lock_dir=tmp_path)
        with router.batch_session(keep_alive="2m", unload_on_exit=True, lease_manager=leases):
            resp = router.generate_json("Ping", required_keys=["tag"])
            assert resp.parsed_json == {"tag": "ollama_ok"}

        chat_reqs = [r for r in rec.requests if r["path"] == "/api/chat"]
        assert len(chat_reqs) == 1
        assert chat_reqs[0]["body"]["keep_alive"] == "2m"
        assert chat_reqs[0]["body"]["format"] == "json"
        # After last lease exits, unload_ollama_model hits /api/generate with keep_alive=0
        unload_reqs = [r for r in rec.requests if r["path"] == "/api/generate"]
        assert len(unload_reqs) == 1
        assert unload_reqs[0]["body"]["keep_alive"] == 0
    finally:
        srv.shutdown()
        srv.server_close()


def test_sqlite_response_cache_hit_and_bypass(tmp_path: Path) -> None:
    calls = {"n": 0}

    def _handler(prompt: str, _system: str) -> str:
        calls["n"] += 1
        return f"ans:{calls['n']}:{prompt}"

    cache_db = tmp_path / "llm_cache.db"
    router = LLMRouter(model="qwen2.5:7b", mock=True, mock_handler=_handler, cache_db_path=cache_db)

    r1 = router.generate("hello")
    assert r1.cache_hit is False
    assert r1.text == "ans:1:hello"

    r2 = router.generate("hello")
    assert r2.cache_hit is True
    assert r2.text == "ans:1:hello"
    assert calls["n"] == 1

    r3 = router.generate("hello", use_cache=False)
    assert r3.cache_hit is False
    assert r3.text == "ans:2:hello"
    assert calls["n"] == 2


def test_emit_genai_span_jsonl_and_module_cli(tmp_path: Path) -> None:
    import subprocess
    import sys

    from core.qa_logger import emit_genai_span

    trace_file = tmp_path / "traces.jsonl"
    span = emit_genai_span(
        operation="chat",
        provider="ollama",
        model="qwen2.5:7b",
        input_tokens=120,
        output_tokens=45,
        cost_usd=0.0,
        cache_hit=True,
        trace_path=trace_file,
    )
    assert span["gen_ai.operation.name"] == "chat"
    assert span["gen_ai.request.model"] == "qwen2.5:7b"
    assert span["vieforge.cache_hit"] is True
    assert trace_file.exists()

    proc = subprocess.run(
        [sys.executable, "-m", "core", "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert proc.returncode == 0
    assert "vieforge" in proc.stdout.lower()


def test_resolve_api_style_cloud_and_local_without_v1_suffix() -> None:
    # Gemini OpenAI-compat preserves /v1beta/openai without appending /v1
    assert _resolve_api_style("https://generativelanguage.googleapis.com/v1beta/openai", "auto") == (
        "openai",
        "https://generativelanguage.googleapis.com/v1beta/openai",
    )
    # DeepSeek / OpenAI / LM Studio without /v1 suffix auto-select openai
    assert _resolve_api_style("https://api.deepseek.com", "auto") == ("openai", "https://api.deepseek.com/v1")
    assert _resolve_api_style("http://127.0.0.1:1234", "auto") == ("openai", "http://127.0.0.1:1234/v1")
    assert _resolve_api_style("http://127.0.0.1:8080", "auto") == ("openai", "http://127.0.0.1:8080/v1")
    # Anthropic native URL auto-selects anthropic
    assert _resolve_api_style("https://api.anthropic.com", "auto") == ("anthropic", "https://api.anthropic.com/v1")
    assert _resolve_api_style("https://api.anthropic.com/v1/messages", "anthropic") == (
        "anthropic",
        "https://api.anthropic.com/v1",
    )


def test_provider_presets_aliases_and_env_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    providers = list_providers()
    for expected in ("ollama", "lmstudio", "llamacpp", "vllm", "jan", "openai", "anthropic", "gemini", "deepseek", "groq", "openrouter"):
        assert expected in providers

    assert resolve_provider_preset("claude").name == "anthropic"
    assert resolve_provider_preset("google").name == "gemini"
    assert resolve_provider_preset("lm-studio").name == "lmstudio"
    assert resolve_provider_preset("llama.cpp").name == "llamacpp"

    with pytest.raises(ValueError):
        resolve_provider_preset("nonexistent_provider")

    monkeypatch.delenv("TIERFORGE_LLM_API_KEY", raising=False)
    monkeypatch.delenv("TIERFORGE_LLM_BASE_URL", raising=False)
    monkeypatch.delenv("TIERFORGE_LLM_MODEL", raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", "gm-test-key")
    r_gem = LLMRouter(provider="gemini")
    assert r_gem.provider == "gemini"
    assert r_gem.api_style == "openai"
    assert r_gem.chat_endpoint == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    assert r_gem.api_key == "gm-test-key"
    assert r_gem.model == "gemini-2.5-flash"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    r_ant = LLMRouter(provider="claude")
    assert r_ant.provider == "anthropic"
    assert r_ant.api_style == "anthropic"
    assert r_ant.chat_endpoint == "https://api.anthropic.com/v1/messages"
    assert r_ant.api_key == "sk-ant-test"


def test_extract_json_strips_reasoning_think_blocks() -> None:
    raw = '<think>\nLet me reason step by step...\n{"not": "this"}\n</think>\n```json\n{"status": "ok", "score": 99}\n```'
    assert json.loads(_extract_json_text(raw)) == {"status": "ok", "score": 99}

    raw_plain = "<THINK>internal chain</THINK>\n{\"ok\": true}"
    assert json.loads(_extract_json_text(raw_plain)) == {"ok": True}


def test_anthropic_native_http_roundtrip_and_reasoning_temperature_omission() -> None:
    requests_log: list[dict[str, Any]] = []

    class _MultiHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            requests_log.append(
                {
                    "path": self.path,
                    "x-api-key": self.headers.get("x-api-key"),
                    "anthropic-version": self.headers.get("anthropic-version"),
                    "body": body,
                }
            )
            if self.path == "/v1/messages":
                resp = {
                    "id": "msg_test",
                    "type": "message",
                    "content": [{"type": "text", "text": '<think>plan</think>\n{"answer": "claude_ok"}'}],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode("utf-8"))
                return

            if self.path == "/v1/chat/completions":
                resp = {
                    "id": "chatcmpl-reason",
                    "choices": [{"message": {"role": "assistant", "content": '{"reasoned": true}'}}],
                }
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(resp).encode("utf-8"))
                return

            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _MultiHandler)
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        r_claude = LLMRouter(
            base_url=base,
            api_style="anthropic",
            model="claude-3-5-haiku-latest",
            api_key="sk-ant-live-stub",
        )
        res = r_claude.generate_json("Hi", system="Be concise", required_keys=["answer"])
        assert res.parsed_json == {"answer": "claude_ok"}
        ant_req = requests_log[0]
        assert ant_req["path"] == "/v1/messages"
        assert ant_req["x-api-key"] == "sk-ant-live-stub"
        assert ant_req["anthropic-version"] == "2023-06-01"
        assert ant_req["body"]["system"] == "Be concise"

        # Reasoning model (o3-mini) should omit temperature automatically
        r_reason = LLMRouter(base_url=f"{base}/v1", model="o3-mini", api_key="sk-oai")
        res2 = r_reason.generate_json("Solve", required_keys=["reasoned"])
        assert res2.parsed_json == {"reasoned": True}
        assert "temperature" not in requests_log[1]["body"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_discover_local_llm_detects_running_openai_compat_server(monkeypatch: pytest.MonkeyPatch) -> None:
    class _ModelsHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:
            return

        def do_GET(self) -> None:
            if self.path == "/v1/models":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"data": [{"id": "qwen2.5-7b-instruct"}]}).encode("utf-8"))
                return
            self.send_response(404)
            self.end_headers()

    srv = HTTPServer(("127.0.0.1", 0), _ModelsHandler)
    base = f"http://127.0.0.1:{srv.server_address[1]}/v1"
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        from core.llm_router import ProviderPreset

        patched = dict(PROVIDER_PRESETS)
        patched["lmstudio"] = ProviderPreset(
            name="lmstudio",
            base_url=base,
            api_style="openai",
            default_model="local-model",
            is_local=True,
        )
        monkeypatch.setattr("core.llm_router.PROVIDER_PRESETS", patched)

        found = discover_local_llm(timeout=1.0, candidates=("lmstudio",))
        assert found is not None
        assert found["provider"] == "lmstudio"
        assert found["api_style"] == "openai"
        assert found["default_model"] == "qwen2.5-7b-instruct"
    finally:
        srv.shutdown()
        srv.server_close()
