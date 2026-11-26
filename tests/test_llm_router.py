"""Offline $0 HTTP tests for LLMRouter (Ollama native + OpenAI-compatible servers)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from core.concurrency import OllamaLeaseManager
from core.llm_router import LLMRouter, _resolve_api_style


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
