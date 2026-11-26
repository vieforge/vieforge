"""Cross-Process Concurrency & Multi-Project GPU/Budget Coordination (100% Python stdlib).

Solves multi-project contention when 2-5 AI Agent sessions (e.g. multiple Antigravity /
Claude Code / Cursor workspaces) run concurrently on the same consumer workstation:
  1. `CrossProcessLock`: OS-level advisory file lock (`msvcrt` on Windows, `fcntl` on POSIX).
  2. `atomic_write_json`: Temp-file + `os.replace` atomic write preventing corrupted ledgers.
  3. `OllamaLeaseManager`: Reference-counted lease tracking active PIDs using Ollama, so
     Project B finishing a batch NEVER prematurely unloads (`keep_alive=0`) VRAM while
     Project A is still mid-batch. Stale/dead PIDs are pruned automatically.
  4. `ExclusiveGPULock`: Serializes heavy Tier 3 GPU subprocesses across concurrent projects
     so 8 GB / 12 GB GPUs queue cleanly instead of hitting OOM.

Zero OS mutations: uses only user-space files under `~/.tierforge/locks/` (or custom dir).
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any, Iterator

if os.name == "nt":
    import msvcrt
else:  # pragma: no cover - POSIX path
    import fcntl


def get_lock_dir(override: Path | None = None) -> Path:
    if override is not None:
        d = override
    else:
        env_dir = os.getenv("TIERFORGE_LOCK_DIR", "").strip()
        d = Path(env_dir).expanduser() if env_dir else (Path.home() / ".tierforge" / "locks")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except OSError:
        return False


class CrossProcessLock:
    """Cross-platform advisory file lock with timeout (Windows msvcrt / POSIX fcntl)."""

    def __init__(self, lock_path: Path, timeout_sec: float = 30.0, poll_sec: float = 0.05) -> None:
        self.lock_path = Path(lock_path)
        self.timeout_sec = timeout_sec
        self.poll_sec = poll_sec
        self._fh: Any = None

    def acquire(self) -> None:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.lock_path.open("a+b")
        deadline = time.monotonic() + self.timeout_sec
        while True:
            try:
                if os.name == "nt":
                    fh.seek(0)
                    if self.lock_path.stat().st_size == 0:
                        fh.write(b"0")
                        fh.flush()
                    fh.seek(0)
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                else:  # pragma: no cover - POSIX path
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._fh = fh
                return
            except OSError as exc:
                if time.monotonic() >= deadline:
                    fh.close()
                    raise TimeoutError(
                        f"Timed out after {self.timeout_sec}s waiting for lock: {self.lock_path}"
                    ) from exc
                time.sleep(self.poll_sec)

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            if os.name == "nt":
                self._fh.seek(0)
                msvcrt.locking(self._fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover - POSIX path
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> CrossProcessLock:
        self.acquire()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.release()


def atomic_write_json(path: Path, data: Any) -> None:
    """Write JSON atomically via a sibling temp file + `os.replace`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        with suppress(OSError):
            os.unlink(tmp_name)
        raise


class OllamaLeaseManager:
    """Reference-counted cross-process lease for shared Ollama VRAM.

    Multiple concurrent TierForge projects can share a warm Ollama model safely.
    `release()` returns `True` ONLY when the active lease count drops to 0, signaling
    that it is safe to unload VRAM (`keep_alive=0`) without disrupting another project.
    """

    def __init__(self, lock_dir: Path | None = None) -> None:
        self.lock_dir = get_lock_dir(lock_dir)
        self.lock_file = self.lock_dir / "ollama_leases.lock"
        self.state_file = self.lock_dir / "ollama_leases.json"

    def _read_leases(self) -> dict[str, dict[str, Any]]:
        if not self.state_file.exists():
            return {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
        except (OSError, json.JSONDecodeError):
            pass
        return {}

    def _prune_dead(self, leases: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
        return {
            lease_id: meta
            for lease_id, meta in leases.items()
            if _is_pid_alive(int(meta.get("pid", -1)))
        }

    def acquire(self, lease_id: str, model: str = "") -> int:
        with CrossProcessLock(self.lock_file, timeout_sec=10.0):
            leases = self._prune_dead(self._read_leases())
            leases[lease_id] = {"pid": os.getpid(), "model": model, "ts": time.time()}
            atomic_write_json(self.state_file, leases)
            return len(leases)

    def release(self, lease_id: str) -> bool:
        """Release lease and return True iff NO other active leases remain (safe to unload)."""
        with CrossProcessLock(self.lock_file, timeout_sec=10.0):
            leases = self._prune_dead(self._read_leases())
            leases.pop(lease_id, None)
            atomic_write_json(self.state_file, leases)
            return len(leases) == 0

    def active_count(self) -> int:
        with CrossProcessLock(self.lock_file, timeout_sec=10.0):
            leases = self._prune_dead(self._read_leases())
            return len(leases)


@contextmanager
def exclusive_gpu_lock(
    lock_dir: Path | None = None,
    timeout_sec: float = 300.0,
) -> Iterator[None]:
    """Serialize heavy Tier 3 GPU subprocesses across concurrent workspaces."""
    lock_file = get_lock_dir(lock_dir) / "gpu_exclusive.lock"
    with CrossProcessLock(lock_file, timeout_sec=timeout_sec):
        yield
