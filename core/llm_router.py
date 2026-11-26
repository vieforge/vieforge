"""Local LLM Router (Ollama + OpenAI-compatible) with Batch-Scoped VRAM Hygiene.

Key engineering features:
  1. Works natively with Ollama (`/api/chat` + `keep_alive` VRAM control) AND any
     OpenAI-compatible local/remote server (`/v1/chat/completions` — LM Studio,
     llama.cpp server, vLLM, Jan, OpenRouter) via `api_style="auto"|"ollama"|"openai"`.
  2. Single Ollama calls default to `keep_alive=0` for immediate VRAM release.
  3. `with router.batch_session(keep_alive="2m"):` keeps the Ollama model warm during
     a Tier 2 batch loop (avoiding 5-10x cold-start reload overhead) and explicitly
     unloads VRAM (`keep_alive=0`) once in the `finally` block when the last lease exits.
  4. `generate_json(...)` enforces JSON output + schema key checks with up to 2 local
     self-repair retries so compact 3B/7B models stay reliable.
  5. Built-in `mock=True` mode for $0 offline testing and 5-minute onboarding demos.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator

from core.concurrency import OllamaLeaseManager
from core.vram_manager import unload_ollama_model

CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
VALID_API_STYLES = ("auto", "ollama", "openai")


@dataclass
class LLMResponse:
    text: str
    model: str
    parsed_json: dict[str, Any] | list[Any] | None = None
    attempts: int = 1
    mocked: bool = False
    cache_hit: bool = False


def _extract_json_text(raw: str) -> str:
    stripped = raw.strip()
    m = CODE_FENCE_RE.search(stripped)
    if m:
        return m.group(1).strip()
    return stripped


def _resolve_api_style(raw_url: str, requested_style: str) -> tuple[str, str]:
    """Resolve effective API style ('ollama' or 'openai') and normalized base URL.

    Preserves Ollama native `/api/chat` + `keep_alive=0` hygiene for default Ollama
    endpoints (`:11434`, even if `/v1` suffix is given) unless `api_style='openai'`
    is explicitly requested. Non-Ollama `/v1` URLs (e.g. LM Studio `:1234/v1`,
    llama.cpp `:8080/v1`, vLLM `:8000/v1`) auto-select OpenAI-compatible mode.
    """
    style = requested_style.strip().lower()
    if style not in VALID_API_STYLES:
        raise ValueError(f"Unsupported api_style '{requested_style}'. Expected one of {VALID_API_STYLES}.")

    cleaned = raw_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    is_ollama_port = parsed.port == 11434

    if style == "ollama":
        base = cleaned[:-3].rstrip("/") if cleaned.endswith("/v1") else cleaned
        return "ollama", base

    if style == "openai":
        base = cleaned if cleaned.endswith("/v1") else f"{cleaned}/v1"
        return "openai", base

    # auto mode
    if cleaned.endswith("/v1") and not is_ollama_port:
        return "openai", cleaned
    base = cleaned[:-3].rstrip("/") if cleaned.endswith("/v1") else cleaned
    return "ollama", base


class LLMRouter:
    """Stdlib-only Ollama & OpenAI-compatible local LLM router."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_sec: float | None = None,
        default_keep_alive: int | str = 0,
        api_style: str | None = None,
        mock: bool = False,
        mock_handler: Callable[[str, str], str] | None = None,
        cache_db_path: Path | str | None = None,
    ) -> None:
        raw_url = (
            base_url
            or os.getenv("TIERFORGE_LLM_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).strip().rstrip("/")
        req_style = api_style or os.getenv("TIERFORGE_LLM_API_STYLE", "auto")
        self.api_style, self.base_url = _resolve_api_style(raw_url, req_style)
        self.model = model or os.getenv("TIERFORGE_LLM_MODEL") or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.api_key = api_key if api_key is not None else os.getenv("TIERFORGE_LLM_API_KEY", "ollama")
        self.timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else float(os.getenv("TIERFORGE_LLM_TIMEOUT_SEC", "120"))
        )
        self.default_keep_alive: int | str = default_keep_alive
        self._active_keep_alive: int | str | None = None
        self.mock = mock
        self.mock_handler = mock_handler
        raw_cache = cache_db_path or os.getenv("TIERFORGE_LLM_CACHE_DB")
        self.cache_db_path: Path | None = Path(raw_cache).expanduser() if raw_cache else None
        if self.cache_db_path is not None:
            self._init_cache_db()

    @property
    def chat_endpoint(self) -> str:
        if self.api_style == "openai":
            return f"{self.base_url}/chat/completions"
        return f"{self.base_url}/api/chat"

    @contextmanager
    def batch_session(
        self,
        keep_alive: str | int = "2m",
        unload_on_exit: bool = True,
        lease_manager: OllamaLeaseManager | None = None,
    ) -> Iterator[LLMRouter]:
        """Keep Ollama model loaded across a batch, then unload when the last lease exits."""
        prev = self._active_keep_alive
        self._active_keep_alive = keep_alive
        use_ollama_lease = self.api_style == "ollama" and not self.mock
        leases = lease_manager if use_ollama_lease else None
        if use_ollama_lease and leases is None:
            leases = OllamaLeaseManager()
        lease_id = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        if leases is not None:
            leases.acquire(lease_id, model=self.model)
        try:
            yield self
        finally:
            self._active_keep_alive = prev
            safe_to_unload = True
            if leases is not None:
                safe_to_unload = leases.release(lease_id)
            if unload_on_exit and safe_to_unload and use_ollama_lease:
                unload_ollama_model(
                    model=self.model,
                    base_url=self.base_url,
                    timeout=10.0,
                    settle_sec=0.5,
                )

    def _effective_keep_alive(self, override: int | str | None) -> int | str:
        if override is not None:
            return override
        if self._active_keep_alive is not None:
            return self._active_keep_alive
        return self.default_keep_alive

    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))

    def _generate_openai(
        self,
        messages: list[dict[str, str]],
        *,
        json_mode: bool,
        temperature: float,
    ) -> str:
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = self._post_json(self.chat_endpoint, payload, headers)
        except urllib.error.HTTPError as exc:
            # Some local OpenAI-compatible servers reject response_format={"type":"json_object"} with HTTP 400/422.
            if json_mode and exc.code in (400, 422):
                payload.pop("response_format", None)
                try:
                    data = self._post_json(self.chat_endpoint, payload, headers)
                except urllib.error.URLError as retry_exc:
                    raise RuntimeError(
                        f"OpenAI-compatible LLM request failed ({self.chat_endpoint}): {retry_exc}"
                    ) from retry_exc
            else:
                raise RuntimeError(
                    f"OpenAI-compatible LLM request failed ({self.chat_endpoint}): HTTP {exc.code}"
                ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"OpenAI-compatible LLM request failed ({self.chat_endpoint}): {exc}"
            ) from exc

        choices = data.get("choices") or []
        if not choices or not isinstance(choices, list):
            raise RuntimeError(f"OpenAI-compatible response missing 'choices': {data}")
        msg = choices[0].get("message", {}) if isinstance(choices[0], dict) else {}
        return str(msg.get("content", "")).strip()

    def _init_cache_db(self) -> None:
        if self.cache_db_path is None:
            return
        self.cache_db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.cache_db_path, timeout=5.0) as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS llm_cache ("
                "cache_key TEXT PRIMARY KEY, model TEXT NOT NULL, "
                "response_text TEXT NOT NULL, created_at REAL NOT NULL)"
            )

    def _cache_key(self, prompt: str, system: str, json_mode: bool) -> str:
        raw = json.dumps(
            {"model": self.model, "system": system, "prompt": prompt, "json_mode": json_mode},
            sort_keys=True,
            ensure_ascii=False,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cache_get(self, key: str) -> str | None:
        if self.cache_db_path is None:
            return None
        with sqlite3.connect(self.cache_db_path, timeout=5.0) as conn:
            row = conn.execute("SELECT response_text FROM llm_cache WHERE cache_key = ?", (key,)).fetchone()
            return str(row[0]) if row is not None else None

    def _cache_put(self, key: str, text: str) -> None:
        if self.cache_db_path is None:
            return
        with sqlite3.connect(self.cache_db_path, timeout=5.0) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO llm_cache (cache_key, model, response_text, created_at) VALUES (?, ?, ?, ?)",
                (key, self.model, text, time.time()),
            )

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        json_mode: bool = False,
        temperature: float = 0.2,
        keep_alive: int | str | None = None,
        use_cache: bool = True,
    ) -> LLMResponse:
        cache_key = self._cache_key(prompt, system, json_mode) if (use_cache and self.cache_db_path is not None) else None
        if cache_key is not None:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return LLMResponse(text=cached, model=self.model, mocked=self.mock, cache_hit=True)

        if self.mock:
            if self.mock_handler is not None:
                text = self.mock_handler(prompt, system)
            elif json_mode:
                text = json.dumps({"summary": prompt[:80], "status": "ok", "mock": True})
            else:
                text = f"[mock:{self.model}] {prompt[:120]}"
            if cache_key is not None:
                self._cache_put(cache_key, text)
            return LLMResponse(text=text, model=self.model, mocked=True)

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        if self.api_style == "openai":
            content = self._generate_openai(messages, json_mode=json_mode, temperature=temperature)
            if cache_key is not None:
                self._cache_put(cache_key, content)
            return LLMResponse(text=content, model=self.model, mocked=False)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._effective_keep_alive(keep_alive),
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        try:
            data = self._post_json(self.chat_endpoint, payload, {"Content-Type": "application/json"})
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed ({self.base_url}): {exc}") from exc

        content = str(data.get("message", {}).get("content", "")).strip()
        if cache_key is not None:
            self._cache_put(cache_key, content)
        return LLMResponse(text=content, model=self.model, mocked=False)

    def generate_json(
        self,
        prompt: str,
        *,
        system: str = "",
        required_keys: list[str] | None = None,
        max_retries: int = 2,
        temperature: float = 0.1,
    ) -> LLMResponse:
        """Request strict JSON with up to `max_retries` local repair passes for 3B/7B models."""
        req_keys = required_keys or []
        cur_prompt = prompt
        last_err = ""
        attempts_allowed = max(1, min(max_retries, 2) + 1)

        for attempt in range(1, attempts_allowed + 1):
            resp = self.generate(
                cur_prompt,
                system=system,
                json_mode=True,
                temperature=temperature,
            )
            candidate = _extract_json_text(resp.text)
            try:
                parsed = json.loads(candidate)
                if req_keys:
                    if not isinstance(parsed, dict):
                        raise ValueError("Expected a JSON object.")
                    missing = [k for k in req_keys if k not in parsed]
                    if missing:
                        raise ValueError(f"Missing required keys: {missing}")
                resp.parsed_json = parsed
                resp.attempts = attempt
                return resp
            except (json.JSONDecodeError, ValueError) as exc:
                last_err = str(exc)
                cur_prompt = (
                    f"{prompt}\n\n"
                    f"PREVIOUS OUTPUT FAILED VALIDATION ({last_err}). "
                    f"Return ONLY valid JSON"
                    + (f" containing keys {req_keys}." if req_keys else ".")
                )

        raise ValueError(
            f"Local LLM failed JSON validation after {attempts_allowed} attempts: {last_err}"
        )
