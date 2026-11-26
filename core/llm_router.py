"""Local LLM Router (Ollama / OpenAI-compatible) with Batch-Scoped VRAM Hygiene.

Key engineering features:
  1. Single calls default to `keep_alive=0` for immediate VRAM release.
  2. `with router.batch_session(keep_alive="2m"):` keeps the model warm during a Tier 2
     batch loop (avoiding 5-10x cold-start reload overhead) and explicitly unloads VRAM
     (`keep_alive=0`) once in the `finally` block when the batch ends.
  3. `generate_json(...)` enforces JSON output + schema key checks with up to 2 local
     self-repair retries so compact 3B/7B models stay reliable.
  4. Built-in `mock=True` mode for $0 offline testing and 5-minute onboarding demos.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from core.concurrency import OllamaLeaseManager
from core.vram_manager import unload_ollama_model

CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)


@dataclass
class LLMResponse:
    text: str
    model: str
    parsed_json: dict[str, Any] | list[Any] | None = None
    attempts: int = 1
    mocked: bool = False


def _extract_json_text(raw: str) -> str:
    stripped = raw.strip()
    m = CODE_FENCE_RE.search(stripped)
    if m:
        return m.group(1).strip()
    return stripped


class LLMRouter:
    """Stdlib-only Ollama / OpenAI-compatible local LLM router."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_sec: float | None = None,
        default_keep_alive: int | str = 0,
        mock: bool = False,
        mock_handler: Callable[[str, str], str] | None = None,
    ) -> None:
        raw_url = (
            base_url
            or os.getenv("TIERFORGE_LLM_BASE_URL")
            or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).strip().rstrip("/")
        self.base_url = raw_url[:-3].rstrip("/") if raw_url.endswith("/v1") else raw_url
        self.model = model or os.getenv("TIERFORGE_LLM_MODEL") or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.api_key = api_key or os.getenv("TIERFORGE_LLM_API_KEY", "ollama")
        self.timeout_sec = (
            timeout_sec
            if timeout_sec is not None
            else float(os.getenv("TIERFORGE_LLM_TIMEOUT_SEC", "120"))
        )
        self.default_keep_alive: int | str = default_keep_alive
        self._active_keep_alive: int | str | None = None
        self.mock = mock
        self.mock_handler = mock_handler

    @contextmanager
    def batch_session(
        self,
        keep_alive: str | int = "2m",
        unload_on_exit: bool = True,
        lease_manager: OllamaLeaseManager | None = None,
    ) -> Iterator[LLMRouter]:
        """Keep model loaded in VRAM across a batch, then unload only if no other project holds a lease."""
        prev = self._active_keep_alive
        self._active_keep_alive = keep_alive
        leases = lease_manager or (None if self.mock else OllamaLeaseManager())
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
            if unload_on_exit and safe_to_unload and not self.mock:
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

    def generate(
        self,
        prompt: str,
        *,
        system: str = "",
        json_mode: bool = False,
        temperature: float = 0.2,
        keep_alive: int | str | None = None,
    ) -> LLMResponse:
        if self.mock:
            if self.mock_handler is not None:
                text = self.mock_handler(prompt, system)
            elif json_mode:
                text = json.dumps({"summary": prompt[:80], "status": "ok", "mock": True})
            else:
                text = f"[mock:{self.model}] {prompt[:120]}"
            return LLMResponse(text=text, model=self.model, mocked=True)

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "keep_alive": self._effective_keep_alive(keep_alive),
            "options": {"temperature": temperature},
        }
        if json_mode:
            payload["format"] = "json"

        req = urllib.request.Request(
            f"{self.base_url}/api/chat",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_sec) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Ollama request failed ({self.base_url}): {exc}") from exc

        content = str(data.get("message", {}).get("content", "")).strip()
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
