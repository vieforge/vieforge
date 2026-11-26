"""Generic Checkpointed Pipeline Runner with Approval Gates, Retry Caps & VRAM Profiler.

100% Python stdlib, cross-platform, domain-agnostic.
"""

from __future__ import annotations

import json
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from core.vram_manager import (
    VRAMPhaseRecord,
    unload_ollama_model,
    vram_phase_profiler,
)


class GateStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class GateBlockedError(RuntimeError):
    """Raised when a pipeline stage requires human approval at a mandatory Gate."""


class JobWorkspace:
    """Manages directory layout and JSON manifest persistence for a single job."""

    def __init__(self, jobs_root: Path, job_id: str) -> None:
        self.job_id = job_id
        self.root = jobs_root / job_id
        self.artifacts_dir = self.root / "artifacts"
        self.logs_dir = self.root / "logs"
        self.manifest_path = self.root / "manifest.json"

    def ensure_dirs(self) -> None:
        for d in (self.root, self.artifacts_dir, self.logs_dir):
            d.mkdir(parents=True, exist_ok=True)

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {"job_id": self.job_id, "gates": {}, "items": {}}
        return json.loads(self.manifest_path.read_text(encoding="utf-8"))

    def save_manifest(self, manifest: dict[str, Any]) -> Path:
        self.ensure_dirs()
        self.manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return self.manifest_path


def assert_gate_approved(status: GateStatus | str, gate_name: str = "Gate") -> None:
    val = status.value if isinstance(status, GateStatus) else str(status).lower()
    if val != GateStatus.APPROVED.value:
        raise GateBlockedError(f"{gate_name.upper()} BLOCKED: status is '{val}', approval required.")


def check_retry_cap(retries: int, max_retries: int = 2) -> None:
    if retries >= max_retries:
        raise RuntimeError(
            f"RETRY CAP REACHED ({retries}/{max_retries}): stopping to protect budget."
        )


def run_profiled_stage(
    stage_name: str,
    fn: Callable[..., Any],
    *args: Any,
    job_id: str = "",
    leak_threshold_mb: int = 200,
    unload_ollama: bool = False,
    log_path: Optional[Path] = None,
    on_leak: Optional[Callable[[VRAMPhaseRecord], None]] = None,
    **kwargs: Any,
) -> tuple[Any, VRAMPhaseRecord]:
    """Execute any pipeline stage wrapped in VRAM Phase Profiler & Leak Detector."""
    root_dir = Path(__file__).resolve().parent.parent
    telemetry_path = log_path or (root_dir / "data" / "vram_profile.jsonl")

    def _default_leak_handler(_rec: VRAMPhaseRecord) -> None:
        unload_ollama_model(timeout=5.0, settle_sec=0.5)

    with vram_phase_profiler(
        stage_name,
        episode_id=job_id,
        leak_threshold_mb=leak_threshold_mb,
        unload_ollama=unload_ollama,
        log_path=telemetry_path,
        on_leak=on_leak or _default_leak_handler,
    ) as prof:
        out = fn(*args, **kwargs)
    return out, prof["record"]
