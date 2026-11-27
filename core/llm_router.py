"""Multi-Provider LLM Router (Ollama + OpenAI-compatible + Anthropic Claude) with VRAM Hygiene.

Key engineering features:
  1. 11 built-in 1-word provider presets (`ollama`, `lmstudio`, `llamacpp`, `vllm`, `jan`,
     `openai`, `anthropic`, `gemini`, `deepseek`, `groq`, `openrouter`) plus `provider="auto"`
     local server auto-discovery so users can plug in any model with zero URL memorization.
  2. Native Ollama (`/api/chat` + `keep_alive` VRAM control), OpenAI-compatible
     (`/chat/completions` — LM Studio, llama.cpp, vLLM, Jan, Gemini, DeepSeek, Groq,
     OpenRouter, OpenAI), AND native Anthropic Claude (`/v1/messages`, `x-api-key`).
  3. Reasoning-model friendly: strips `<think>...</think>` blocks (DeepSeek-R1, QwQ, Qwen3)
     before JSON extraction and auto-retries when servers reject `response_format` or
     `temperature` (e.g. `o1`/`o3`/`gpt-5`).
  4. `with router.batch_session(keep_alive="2m"):` keeps the Ollama model warm during
     a Tier 2 batch loop and unloads VRAM (`keep_alive=0`) when the last lease exits.
  5. `generate_json(...)` enforces JSON output + schema key checks with up to 2 local
     self-repair retries, plus built-in `mock=True` mode for $0 offline demos.
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
THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
VALID_API_STYLES = ("auto", "ollama", "openai", "anthropic")

_OPENAI_COMPAT_HOSTS = frozenset(
    {
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "api.deepseek.com",
        "api.groq.com",
        "openrouter.ai",
        "api.together.xyz",
        "api.fireworks.ai",
        "api.mistral.ai",
        "api.x.ai",
    }
)
_LOCAL_OPENAI_PORTS = frozenset({1234, 8080, 8000, 1337})
_REASONING_MODEL_PREFIXES = ("o1", "o3", "o4", "gpt-5")


@dataclass(frozen=True)
class ProviderPreset:
    name: str
    base_url: str
    api_style: str
    default_model: str
    api_key_envs: tuple[str, ...] = ()
    is_local: bool = False


PROVIDER_PRESETS: dict[str, ProviderPreset] = {
    "ollama": ProviderPreset(
        name="ollama",
        base_url="http://127.0.0.1:11434",
        api_style="ollama",
        default_model="qwen2.5:7b",
        is_local=True,
    ),
    "lmstudio": ProviderPreset(
        name="lmstudio",
        base_url="http://127.0.0.1:1234/v1",
        api_style="openai",
        default_model="local-model",
        is_local=True,
    ),
    "llamacpp": ProviderPreset(
        name="llamacpp",
        base_url="http://127.0.0.1:8080/v1",
        api_style="openai",
        default_model="local-model",
        is_local=True,
    ),
    "vllm": ProviderPreset(
        name="vllm",
        base_url="http://127.0.0.1:8000/v1",
        api_style="openai",
        default_model="local-model",
        is_local=True,
    ),
    "jan": ProviderPreset(
        name="jan",
        base_url="http://127.0.0.1:1337/v1",
        api_style="openai",
        default_model="local-model",
        is_local=True,
    ),
    "openai": ProviderPreset(
        name="openai",
        base_url="https://api.openai.com/v1",
        api_style="openai",
        default_model="gpt-4o-mini",
        api_key_envs=("OPENAI_API_KEY",),
    ),
    "anthropic": ProviderPreset(
        name="anthropic",
        base_url="https://api.anthropic.com/v1",
        api_style="anthropic",
        default_model="claude-3-5-haiku-latest",
        api_key_envs=("ANTHROPIC_API_KEY",),
    ),
    "gemini": ProviderPreset(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        api_style="openai",
        default_model="gemini-2.5-flash",
        api_key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    ),
    "deepseek": ProviderPreset(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        api_style="openai",
        default_model="deepseek-chat",
        api_key_envs=("DEEPSEEK_API_KEY",),
    ),
    "groq": ProviderPreset(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        api_style="openai",
        default_model="llama-3.3-70b-versatile",
        api_key_envs=("GROQ_API_KEY",),
    ),
    "openrouter": ProviderPreset(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_style="openai",
        default_model="qwen/qwen-2.5-7b-instruct",
        api_key_envs=("OPENROUTER_API_KEY",),
    ),
}

_PROVIDER_ALIASES: dict[str, str] = {
    "claude": "anthropic",
    "google": "gemini",
    "lm-studio": "lmstudio",
    "lm_studio": "lmstudio",
    "llama.cpp": "llamacpp",
    "llama-cpp": "llamacpp",
    "llama_cpp": "llamacpp",
}

_LOCAL_DISCOVERY_ORDER: tuple[str, ...] = ("ollama", "lmstudio", "llamacpp", "vllm", "jan")


def list_providers() -> list[str]:
    """Return sorted canonical provider preset names."""
    return sorted(PROVIDER_PRESETS.keys())


def resolve_provider_preset(provider: str) -> ProviderPreset:
    """Resolve a canonical provider name or friendly alias to its ProviderPreset."""
    key = provider.strip().lower()
    canonical = _PROVIDER_ALIASES.get(key, key)
    preset = PROVIDER_PRESETS.get(canonical)
    if preset is None:
        supported = ", ".join(list_providers())
        raise ValueError(f"Unsupported provider '{provider}'. Expected one of: {supported} (or 'auto').")
    return preset


def discover_local_llm(
    timeout: float = 0.8,
    candidates: tuple[str, ...] = _LOCAL_DISCOVERY_ORDER,
) -> dict[str, Any] | None:
    """Probe running local LLM servers (Ollama -> LM Studio -> llama.cpp -> vLLM -> Jan).

    Read-only loopback HTTP GET check. Returns the first reachable server metadata dict,
    or None if no local server is running.
    """
    for name in candidates:
        preset = PROVIDER_PRESETS.get(name)
        if preset is None or not preset.is_local:
            continue
        probe_url = f"{preset.base_url}/api/tags" if preset.api_style == "ollama" else f"{preset.base_url}/models"
        try:
            req = urllib.request.Request(probe_url, method="GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
            continue

        if preset.api_style == "ollama":
            raw_models = payload.get("models", []) if isinstance(payload, dict) else []
            models = [str(m.get("name", "")) for m in raw_models if isinstance(m, dict) and m.get("name")]
        else:
            raw_models = payload.get("data", []) if isinstance(payload, dict) else []
            models = [str(m.get("id", "")) for m in raw_models if isinstance(m, dict) and m.get("id")]

        return {
            "provider": preset.name,
            "api_style": preset.api_style,
            "base_url": preset.base_url,
            "models": models,
            "default_model": models[0] if models else preset.default_model,
        }
    return None


@dataclass
class LLMResponse:
    text: str
    model: str
    parsed_json: dict[str, Any] | list[Any] | None = None
    attempts: int = 1
    mocked: bool = False
    cache_hit: bool = False


def _extract_json_text(raw: str) -> str:
    cleaned = THINK_BLOCK_RE.sub("", raw).strip()
    m = CODE_FENCE_RE.search(cleaned)
    if m:
        return m.group(1).strip()
    return cleaned


def _normalize_openai_base(cleaned: str) -> str:
    """Ensure an OpenAI-compatible base URL preserves custom prefixes like `/v1beta/openai`."""
    if cleaned.endswith("/chat/completions"):
        cleaned = cleaned[: -len("/chat/completions")].rstrip("/")
    if cleaned.endswith("/v1") or cleaned.endswith("/openai"):
        return cleaned
    return f"{cleaned}/v1"


def _normalize_anthropic_base(cleaned: str) -> str:
    if cleaned.endswith("/messages"):
        cleaned = cleaned[: -len("/messages")].rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


def _resolve_api_style(raw_url: str, requested_style: str) -> tuple[str, str]:
    """Resolve effective API style ('ollama', 'openai', or 'anthropic') and normalized base URL.

    Preserves Ollama native `/api/chat` + `keep_alive=0` hygiene for default Ollama
    endpoints (`:11434`, even if `/v1` suffix is given) unless `api_style='openai'`
    is explicitly requested. Recognizes Anthropic, Gemini OpenAI-compat, DeepSeek,
    Groq, OpenRouter, OpenAI, and common local OpenAI-compatible servers (LM Studio
    `:1234`, llama.cpp `:8080`, vLLM `:8000`, Jan `:1337`) automatically.
    """
    style = requested_style.strip().lower()
    if style not in VALID_API_STYLES:
        raise ValueError(f"Unsupported api_style '{requested_style}'. Expected one of {VALID_API_STYLES}.")

    cleaned = raw_url.strip().rstrip("/")
    parsed = urllib.parse.urlparse(cleaned)
    host = (parsed.hostname or "").lower()
    is_ollama_port = parsed.port == 11434

    if style == "ollama":
        base = cleaned[:-3].rstrip("/") if cleaned.endswith("/v1") else cleaned
        return "ollama", base

    if style == "openai":
        return "openai", _normalize_openai_base(cleaned)

    if style == "anthropic":
        return "anthropic", _normalize_anthropic_base(cleaned)

    # auto mode
    if host == "api.anthropic.com" or cleaned.endswith("/messages"):
        return "anthropic", _normalize_anthropic_base(cleaned)

    if (
        host in _OPENAI_COMPAT_HOSTS
        or (parsed.port in _LOCAL_OPENAI_PORTS and not is_ollama_port)
        or ((cleaned.endswith("/v1") or cleaned.endswith("/openai")) and not is_ollama_port)
    ):
        return "openai", _normalize_openai_base(cleaned)

    base = cleaned[:-3].rstrip("/") if cleaned.endswith("/v1") else cleaned
    return "ollama", base


def _is_reasoning_model(model: str) -> bool:
    leaf = model.strip().lower().split("/")[-1]
    return any(leaf == p or leaf.startswith(f"{p}-") or leaf.startswith(f"{p}.") for p in _REASONING_MODEL_PREFIXES)


class LLMRouter:
    """Stdlib-only multi-provider LLM router (Ollama, OpenAI-compatible, Anthropic Claude)."""

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
        provider: str | None = None,
        auto_discover: bool = False,
    ) -> None:
        raw_provider = (provider or os.getenv("TIERFORGE_LLM_PROVIDER", "")).strip().lower()
        preset: ProviderPreset | None = None
        discovered: dict[str, Any] | None = None

        explicit_url = base_url or os.getenv("TIERFORGE_LLM_BASE_URL")
        if raw_provider == "auto" or (auto_discover and not explicit_url and not raw_provider):
            if not mock:
                discovered = discover_local_llm()
        elif raw_provider:
            preset = resolve_provider_preset(raw_provider)

        if explicit_url:
            raw_url = explicit_url.strip().rstrip("/")
        elif discovered is not None:
            raw_url = str(discovered["base_url"]).strip().rstrip("/")
        elif preset is not None:
            raw_url = preset.base_url
        else:
            raw_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip().rstrip("/")

        req_style = (
            api_style
            or os.getenv("TIERFORGE_LLM_API_STYLE")
            or (str(discovered["api_style"]) if discovered is not None else "")
            or (preset.api_style if preset is not None else "auto")
        )
        self.api_style, self.base_url = _resolve_api_style(raw_url, req_style)
        self.provider: str = (
            preset.name
            if preset is not None
            else (str(discovered["provider"]) if discovered is not None else self.api_style)
        )

        default_model = (
            str(discovered["default_model"])
            if discovered is not None
            else (preset.default_model if preset is not None else os.getenv("OLLAMA_MODEL", "qwen2.5:7b"))
        )
        self.model = model or os.getenv("TIERFORGE_LLM_MODEL") or default_model

        if api_key is not None:
            self.api_key = api_key
        else:
            env_key = os.getenv("TIERFORGE_LLM_API_KEY", "").strip()
            if not env_key and preset is not None:
                for env_name in preset.api_key_envs:
                    val = os.getenv(env_name, "").strip()
                    if val:
                        env_key = val
                        break
            if not env_key and self.api_style == "anthropic":
                env_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
            self.api_key = env_key if env_key else ("ollama" if self.api_style == "ollama" else "")

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
        if self.api_style == "anthropic":
            return f"{self.base_url}/messages"
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
            "stream": False,
        }
        if not _is_reasoning_model(self.model):
            payload["temperature"] = temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        try:
            data = self._post_json(self.chat_endpoint, payload, headers)
        except urllib.error.HTTPError as exc:
            # Some local/reasoning OpenAI-compatible servers reject response_format or temperature with HTTP 400/422.
            if exc.code in (400, 422) and ("response_format" in payload or "temperature" in payload):
                if json_mode and "response_format" in payload:
                    payload.pop("response_format", None)
                else:
                    payload.pop("temperature", None)
                try:
                    data = self._post_json(self.chat_endpoint, payload, headers)
                except urllib.error.HTTPError as retry_http:
                    if retry_http.code in (400, 422) and "temperature" in payload:
                        payload.pop("temperature", None)
                        try:
                            data = self._post_json(self.chat_endpoint, payload, headers)
                        except urllib.error.URLError as retry_exc:
                            raise RuntimeError(
                                f"OpenAI-compatible LLM request failed ({self.chat_endpoint}): {retry_exc}"
                            ) from retry_exc
                    else:
                        raise RuntimeError(
                            f"OpenAI-compatible LLM request failed ({self.chat_endpoint}): HTTP {retry_http.code}"
                        ) from retry_http
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
        return str(msg.get("content") or "").strip()

    def _generate_anthropic(
        self,
        prompt: str,
        *,
        system: str,
        temperature: float,
    ) -> str:
        headers: dict[str, str] = {
            "Content-Type": "application/json",
            "anthropic-version": os.getenv("TIERFORGE_ANTHROPIC_VERSION", "2023-06-01"),
        }
        if self.api_key:
            headers["x-api-key"] = self.api_key

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": int(os.getenv("TIERFORGE_ANTHROPIC_MAX_TOKENS", "4096")),
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            payload["system"] = system

        try:
            data = self._post_json(self.chat_endpoint, payload, headers)
        except urllib.error.HTTPError as exc:
            raise RuntimeError(
                f"Anthropic Messages API request failed ({self.chat_endpoint}): HTTP {exc.code}"
            ) from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"Anthropic Messages API request failed ({self.chat_endpoint}): {exc}"
            ) from exc

        blocks = data.get("content") or []
        if not isinstance(blocks, list) or not blocks:
            raise RuntimeError(f"Anthropic response missing 'content' blocks: {data}")
        texts = [
            str(b.get("text", ""))
            for b in blocks
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(t for t in texts if t).strip()

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

        if self.api_style == "anthropic":
            content = self._generate_anthropic(prompt, system=system, temperature=temperature)
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
