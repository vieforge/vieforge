"""Cross-Platform Application-Space VRAM Guard & Phase Leak Profiler.

Zero OS-mutation policy: never touches OS services, Registry, network stacks, or pagefiles.
- Unloads Ollama via `keep_alive=0` when safe across concurrent projects.
- Optional HTTP GPU server flush hook.
- Enforces minimum free VRAM via `nvidia-smi` (with CPU-only / Apple Silicon fallback).
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
import time
import urllib.request
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, Optional

try:
    import httpx
except ImportError:  # pragma: no cover - stdlib fallback for tierforge-harness
    httpx = None  # type: ignore[assignment]


def _post_json(url: str, payload: dict, timeout: float = 10.0) -> int:
    """POST JSON using httpx when available, falling back to Python stdlib urllib."""
    if httpx is not None:
        return int(httpx.post(url, json=payload, timeout=timeout).status_code)
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return int(getattr(resp, "status", 200))


@dataclass
class GPUStatus:
    name: str
    used_mb: int
    total_mb: int

    @property
    def free_mb(self) -> int:
        return max(0, self.total_mb - self.used_mb)


def query_gpu_status() -> Optional[GPUStatus]:
    """Query current VRAM usage via nvidia-smi."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=5,
        ).strip()
        first_line = out.splitlines()[0]
        name, used, total = [part.strip() for part in first_line.split(",")]
        return GPUStatus(name=name, used_mb=int(used), total_mb=int(total))
    except Exception:
        return None


def recommend_local_model(gpu: Optional[GPUStatus] = None) -> dict:
    """Recommend a safe local Ollama model based on available GPU VRAM (0-dependency)."""
    st = gpu if gpu is not None else query_gpu_status()
    total_mb = st.total_mb if st else 0
    if total_mb >= 16000:
        model, tier = "qwen2.5:14b", "high_vram"
    elif total_mb >= 7500:
        model, tier = "qwen2.5:7b", "mid_vram_8gb"
    elif total_mb >= 4000:
        model, tier = "llama3.2:3b", "low_vram_4gb"
    else:
        model, tier = "llama3.2:3b", "cpu_only"
    return {
        "model": model,
        "profile": tier,
        "gpu_name": st.name if st else "cpu_only",
        "vram_total_mb": total_mb,
        "vram_free_mb": st.free_mb if st else 0,
    }


def wait_for_free_vram(
    min_free_mb: Optional[int] = None,
    timeout_sec: float = 15.0,
    poll_interval_sec: float = 1.0,
    allow_cpu_fallback: bool = False,
) -> tuple[bool, Optional[GPUStatus]]:
    """Wait until GPU has at least `min_free_mb` free VRAM before starting a heavy GPU stage."""
    threshold = (
        min_free_mb
        if min_free_mb is not None
        else int(os.getenv("TIERFORGE_MIN_FREE_VRAM_MB", "4096"))
    )
    deadline = time.monotonic() + timeout_sec
    last_status: Optional[GPUStatus] = None
    while True:
        last_status = query_gpu_status()
        if last_status is None:
            return allow_cpu_fallback, None
        if last_status.free_mb >= threshold:
            return True, last_status
        if time.monotonic() >= deadline:
            return False, last_status
        time.sleep(poll_interval_sec)


def unload_ollama_model(
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    timeout: float = 10.0,
    settle_sec: float = 2.0,
) -> bool:
    """Force Ollama to release GPU VRAM immediately via keep_alive=0 and wait for settle."""
    model = (
        model
        or os.getenv("TIERFORGE_LLM_MODEL")
        or os.getenv("SCRIPT_LLM_MODEL")
        or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
    )
    base_url = (
        base_url
        or os.getenv("TIERFORGE_LLM_BASE_URL")
        or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    ).rstrip("/")
    if base_url.endswith("/v1"):
        base_url = base_url[:-3]
    try:
        status = _post_json(
            f"{base_url}/api/generate",
            {"model": model, "keep_alive": 0},
            timeout=timeout,
        )
        if status == 200 and settle_sec > 0:
            time.sleep(settle_sec)
        return status == 200
    except Exception:
        return False


def free_http_gpu_server(
    base_url: str,
    endpoint: str = "/free",
    payload: Optional[dict] = None,
    timeout: float = 5.0,
) -> bool:
    """Optional helper to request VRAM release from a local HTTP GPU server."""
    url = f"{base_url.rstrip('/')}/{endpoint.lstrip('/')}"
    body = payload if payload is not None else {"unload_models": True, "free_memory": True}
    try:
        return _post_json(url, body, timeout=timeout) == 200
    except Exception:
        return False


def free_comfyui_vram(
    base_url: Optional[str] = None,
    timeout: float = 10.0,
) -> bool:
    """Ask local ComfyUI server to unload models and free CUDA cache."""
    base_url = (base_url or os.getenv("COMFYUI_BASE_URL", "http://127.0.0.1:8188")).rstrip("/")
    try:
        status = _post_json(
            f"{base_url}/free",
            {"unload_models": True, "free_memory": True},
            timeout=timeout,
        )
        return status == 200
    except Exception:
        return False


def flush(
    base_url: Optional[str] = None,
    unload_ollama: bool = False,
    settle_sec: float = 0.5,
) -> Optional[GPUStatus]:
    """Strict inter-stage VRAM flush for 8 GB RTX 3060 Ti (ComfyUI + optional Ollama + GC)."""
    import gc

    free_comfyui_vram(base_url=base_url, timeout=5.0)
    if unload_ollama:
        unload_ollama_model(timeout=5.0, settle_sec=0.0)
    gc.collect()
    if settle_sec > 0:
        time.sleep(settle_sec)
    return query_gpu_status()


@dataclass
class VRAMPhaseRecord:
    timestamp: str
    stage: str
    episode_id: str
    pre_mb: int
    peak_mb: int
    post_flush_mb: int
    leak_mb: int
    duration_s: float
    leak_detected: bool
    gpu_name: str = "unknown"


@contextmanager
def vram_phase_profiler(
    stage_name: str,
    *,
    episode_id: str = "",
    leak_threshold_mb: int = 200,
    poll_interval_sec: float = 0.5,
    unload_ollama: bool = False,
    log_path: Optional[Path] = None,
    on_leak: Optional[Callable[[VRAMPhaseRecord], None]] = None,
    query_fn: Callable[[], Optional[GPUStatus]] = query_gpu_status,
    flush_fn: Callable[..., Optional[GPUStatus]] = flush,
) -> Iterator[dict]:
    """Clean-room VRAM Phase Profiler & Leak Detector for 8 GB RTX 3060 Ti.

    Records pre_mb, peak_mb (sampled during execution), post_flush_mb, leak_mb, and duration_s.
    Triggers warning + optional `on_leak` reset callback if leak_mb > leak_threshold_mb.
    """
    t0 = time.perf_counter()
    pre_status = query_fn()
    pre_mb = pre_status.used_mb if pre_status else 0
    gpu_name = pre_status.name if pre_status else "cpu_only"
    peak_box = [pre_mb]
    stop_evt = threading.Event()
    result_holder: dict = {}

    def _poll() -> None:
        while not stop_evt.is_set():
            st = query_fn()
            if st is not None and st.used_mb > peak_box[0]:
                peak_box[0] = st.used_mb
            stop_evt.wait(poll_interval_sec)

    poller = threading.Thread(target=_poll, daemon=True)
    poller.start()
    try:
        yield result_holder
    finally:
        stop_evt.set()
        poller.join(timeout=1.5)
        post_status = flush_fn(unload_ollama=unload_ollama, settle_sec=0.2)
        post_mb = post_status.used_mb if post_status else pre_mb
        if post_mb > peak_box[0]:
            peak_box[0] = post_mb
        leak_mb = max(0, post_mb - pre_mb)
        duration_s = round(time.perf_counter() - t0, 3)
        leak_detected = leak_mb > leak_threshold_mb
        rec = VRAMPhaseRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            stage=stage_name,
            episode_id=episode_id,
            pre_mb=pre_mb,
            peak_mb=peak_box[0],
            post_flush_mb=post_mb,
            leak_mb=leak_mb,
            duration_s=duration_s,
            leak_detected=leak_detected,
            gpu_name=gpu_name,
        )
        result_holder["record"] = rec
        if log_path is not None:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(rec), ensure_ascii=False) + "\n")
        if leak_detected:
            print(
                f"[VRAM LEAK ALERT] stage={stage_name} pre={pre_mb}MB peak={peak_box[0]}MB "
                f"post={post_mb}MB leak=+{leak_mb}MB (>{leak_threshold_mb}MB)!",
                flush=True,
            )
            if on_leak is not None:
                on_leak(rec)
