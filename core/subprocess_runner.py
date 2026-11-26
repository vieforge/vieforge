"""OS-Isolated Subprocess Runner for Tier 3 GPU Workers & Trimmed Shell Execution.

Runs heavy or verbose commands inside a child process so the OS kernel reclaims 100% of
RAM/VRAM cleanly on exit, while automatically trimming stdout/stderr for Tier 1 context.
Zero OS mutations; 100% Python stdlib.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Sequence

from core.concurrency import exclusive_gpu_lock
from core.output_trimmer import trim_output
from core.vram_manager import (
    VRAMPhaseRecord,
    unload_ollama_model,
    vram_phase_profiler,
    wait_for_free_vram,
)


class VRAMInsufficientError(RuntimeError):
    """Raised when pre-flight free VRAM is below the required threshold."""


@dataclass
class SubprocessResult:
    command: list[str]
    returncode: int
    stdout_trimmed: str
    stderr_trimmed: str
    raw_chars: int
    trimmed_chars: int
    saved_pct: float
    duration_s: float
    timed_out: bool = False
    vram_record: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_isolated(
    command: Sequence[str],
    *,
    cwd: Path | str | None = None,
    env: dict[str, str] | None = None,
    timeout_sec: float = 300.0,
    check_vram: bool = False,
    min_free_vram_mb: int | None = None,
    unload_ollama_before: bool = False,
    profile_vram: bool = False,
    exclusive_gpu: bool = False,
    stage_name: str = "subprocess",
    job_id: str = "",
    max_output_lines: int = 60,
    max_output_chars: int = 4000,
) -> SubprocessResult:
    """Execute a command in an isolated child process with optional VRAM guard & log trimming."""
    cmd_list = [str(c) for c in command]

    def _run_body() -> SubprocessResult:
        if unload_ollama_before:
            unload_ollama_model(timeout=5.0, settle_sec=0.5)

        if check_vram:
            ok, status = wait_for_free_vram(
                min_free_mb=min_free_vram_mb,
                timeout_sec=10.0,
                allow_cpu_fallback=True,
            )
            if not ok:
                free_info = f"{status.free_mb}/{status.total_mb} MB" if status else "unknown"
                raise VRAMInsufficientError(
                    f"VRAM_INSUFFICIENT: free VRAM ({free_info}) below threshold."
                )

        merged_env = os.environ.copy()
        merged_env.setdefault("PYTHONUTF8", "1")
        merged_env.setdefault("PYTHONIOENCODING", "utf-8")
        if env:
            merged_env.update(env)

        t0 = time.perf_counter()
        timed_out = False
        raw_stdout = ""
        raw_stderr = ""
        returncode = -1
        vram_rec: VRAMPhaseRecord | None = None

        def _exec() -> None:
            nonlocal timed_out, raw_stdout, raw_stderr, returncode
            try:
                proc = subprocess.run(
                    cmd_list,
                    cwd=str(cwd) if cwd else None,
                    env=merged_env,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_sec,
                    check=False,
                )
                returncode = proc.returncode
                raw_stdout = proc.stdout or ""
                raw_stderr = proc.stderr or ""
            except subprocess.TimeoutExpired as exc:
                timed_out = True
                returncode = 124
                raw_stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
                raw_stderr = (
                    (exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or ""))
                    + f"\n[TierForge] Process timed out after {timeout_sec}s."
                )

        if profile_vram:
            with vram_phase_profiler(stage_name, job_id=job_id) as holder:
                _exec()
            vram_rec = holder.get("record")
        else:
            _exec()

        duration_s = round(time.perf_counter() - t0, 3)
        out_trim = trim_output(raw_stdout, max_lines=max_output_lines, max_chars=max_output_chars)
        err_trim = trim_output(raw_stderr, max_lines=max_output_lines, max_chars=max_output_chars)

        raw_total = out_trim.raw_chars + err_trim.raw_chars
        trimmed_total = out_trim.trimmed_chars + err_trim.trimmed_chars
        saved_pct = (
            round(max(0.0, (raw_total - trimmed_total) / raw_total) * 100.0, 1)
            if raw_total > 0
            else 0.0
        )

        return SubprocessResult(
            command=cmd_list,
            returncode=returncode,
            stdout_trimmed=out_trim.text,
            stderr_trimmed=err_trim.text,
            raw_chars=raw_total,
            trimmed_chars=trimmed_total,
            saved_pct=saved_pct,
            duration_s=duration_s,
            timed_out=timed_out,
            vram_record=asdict(vram_rec) if vram_rec is not None else None,
        )

    if exclusive_gpu:
        with exclusive_gpu_lock(timeout_sec=max(30.0, timeout_sec)):
            return _run_body()
    return _run_body()
