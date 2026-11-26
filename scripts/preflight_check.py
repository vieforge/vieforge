#!/usr/bin/env python3
"""Cross-platform, read-only pre-flight environment check for TierForge.

Checks:
  1. Python runtime (>= 3.10)
  2. SQLite3 runtime + WAL mode capability (in an ephemeral temp directory)
  3. Local LLM / Ollama reachability (read-only HTTP GET to /api/tags)
  4. GPU / VRAM hardware profile (nvidia-smi / Apple Silicon / CPU-only fallback)
  5. Optional clean-room companion tools on PATH (rtk-oss, token-saver, uv, pytest, git)
  6. Repository JSON configuration validity

Zero OS mutations: this script never modifies system settings, services, Registry,
network stacks, or persistent files.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 10)


def check_python() -> dict[str, Any]:
    ver = sys.version_info[:3]
    ok = ver >= MIN_PYTHON
    return {
        "status": "OK" if ok else "FAIL",
        "version": f"{ver[0]}.{ver[1]}.{ver[2]}",
        "executable": sys.executable,
        "platform": f"{platform.system()} {platform.release()} ({platform.machine()})",
        "required": f">={MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
    }


def check_sqlite() -> dict[str, Any]:
    version = sqlite3.sqlite_version
    wal_ok = False
    error = None
    try:
        with tempfile.TemporaryDirectory(prefix="tierforge_preflight_") as tmpdir:
            db_path = Path(tmpdir) / "check.db"
            conn = sqlite3.connect(db_path)
            try:
                mode = conn.execute("PRAGMA journal_mode=WAL;").fetchone()
                wal_ok = bool(mode and str(mode[0]).lower() == "wal")
                conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
                conn.execute("INSERT INTO t (v) VALUES ('ok')")
                conn.commit()
            finally:
                conn.close()
    except Exception as exc:  # pragma: no cover - defensive
        error = str(exc)

    return {
        "status": "OK" if wal_ok and error is None else "FAIL",
        "version": version,
        "wal_mode": wal_ok,
        "error": error,
    }


def _normalize_ollama_root(raw_url: str) -> str:
    url = raw_url.strip().rstrip("/")
    if url.endswith("/v1"):
        url = url[:-3].rstrip("/")
    return url or "http://127.0.0.1:11434"


def check_ollama(base_url: str, timeout: float = 2.5) -> dict[str, Any]:
    root = _normalize_ollama_root(base_url)
    tags_url = f"{root}/api/tags"
    req = urllib.request.Request(tags_url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        models = [m.get("name", "") for m in payload.get("models", []) if m.get("name")]
        return {
            "status": "OK",
            "endpoint": root,
            "models_count": len(models),
            "models_sample": models[:8],
            "note": "Ready for Tier 2 ($0) local inference (keep_alive=0 recommended).",
        }
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        return {
            "status": "WARN",
            "endpoint": root,
            "models_count": 0,
            "models_sample": [],
            "note": f"Ollama not reachable ({exc}). Start Ollama to enable Tier 2 local LLM.",
        }


def _classify_vram_profile(total_mb: int) -> str:
    if total_mb >= 22000:
        return "24GB+ VRAM"
    if total_mb >= 11000:
        return "12-16GB VRAM"
    if total_mb >= 7000:
        return "8GB VRAM"
    return "<8GB VRAM"


def check_gpu() -> dict[str, Any]:
    nvidia_smi = shutil.which("nvidia-smi")
    if nvidia_smi:
        try:
            proc = subprocess.run(
                [
                    nvidia_smi,
                    "--query-gpu=name,memory.total,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=3.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                gpus = []
                for line in proc.stdout.strip().splitlines():
                    parts = [p.strip() for p in line.split(",")]
                    if len(parts) >= 3:
                        total_mb = int(float(parts[1]))
                        free_mb = int(float(parts[2]))
                        gpus.append(
                            {
                                "name": parts[0],
                                "total_vram_mb": total_mb,
                                "free_vram_mb": free_mb,
                            }
                        )
                if gpus:
                    max_total = max(g["total_vram_mb"] for g in gpus)
                    return {
                        "status": "OK",
                        "backend": "nvidia-smi",
                        "profile": _classify_vram_profile(max_total),
                        "gpus": gpus,
                    }
        except (OSError, subprocess.SubprocessError, ValueError):
            pass

    # Apple Silicon read-only detection
    if sys.platform == "darwin" and platform.machine().lower() in ("arm64", "aarch64"):
        unified_gb = None
        try:
            proc = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                text=True,
                timeout=2.0,
                check=False,
            )
            if proc.returncode == 0 and proc.stdout.strip().isdigit():
                unified_gb = round(int(proc.stdout.strip()) / (1024**3))
        except (OSError, subprocess.SubprocessError):
            pass
        return {
            "status": "OK",
            "backend": "apple-silicon",
            "profile": f"Apple Silicon ({unified_gb}GB Unified)" if unified_gb else "Apple Silicon",
            "gpus": [],
        }

    return {
        "status": "INFO",
        "backend": "cpu-only",
        "profile": "CPU-Only",
        "gpus": [],
        "note": "No NVIDIA GPU or Apple Silicon detected; Tier 2 CPU / external capped Tier 3 mode.",
    }


def check_companions() -> dict[str, Any]:
    tools = {
        "rtk-oss": shutil.which("rtk-oss"),
        "rtk": shutil.which("rtk"),
        "token-saver": shutil.which("token-saver"),
        "uv": shutil.which("uv"),
        "pytest": shutil.which("pytest"),
        "git": shutil.which("git"),
    }
    found = {k: v for k, v in tools.items() if v}
    notes = []
    if found.get("rtk") and not found.get("rtk-oss"):
        notes.append(
            "Detected 'rtk' on PATH: for commercial clean-room compliance, verify it is "
            "Apache-2.0 'rtk-oss' rather than BUSL-1.1 standard 'rtk'."
        )
    return {
        "status": "OK",
        "detected": {k: bool(v) for k, v in tools.items()},
        "paths": found,
        "notes": notes,
    }


def check_configs() -> dict[str, Any]:
    from core.paths import resolve_asset

    required_files = [
        "config/pricing.example.json",
        "config/budget_limits.json",
        "config/qa_error_codes.json",
        "skills/three-tier-orchestrator/SKILL.md",
    ]
    results: dict[str, str] = {}
    all_ok = True
    for rel in required_files:
        path = resolve_asset(rel)
        if not path.is_file():
            results[rel] = "MISSING"
            all_ok = False
            continue
        if path.suffix == ".json":
            try:
                json.loads(path.read_text(encoding="utf-8"))
                results[rel] = "OK"
            except json.JSONDecodeError as exc:
                results[rel] = f"INVALID_JSON ({exc.msg})"
                all_ok = False
        else:
            results[rel] = "OK"
    return {
        "status": "OK" if all_ok else "FAIL",
        "files": results,
    }


def run_preflight(ollama_url: str) -> dict[str, Any]:
    py = check_python()
    sq = check_sqlite()
    ol = check_ollama(ollama_url)
    gpu = check_gpu()
    comp = check_companions()
    cfg = check_configs()

    critical_ok = all(x["status"] == "OK" for x in (py, sq, cfg))
    return {
        "overall": "PASS" if critical_ok else "FAIL",
        "checks": {
            "python": py,
            "sqlite": sq,
            "ollama": ol,
            "gpu": gpu,
            "companions": comp,
            "configs": cfg,
        },
    }


def print_human_report(report: dict[str, Any]) -> None:
    checks = report["checks"]
    py = checks["python"]
    sq = checks["sqlite"]
    ol = checks["ollama"]
    gpu = checks["gpu"]
    comp = checks["companions"]
    cfg = checks["configs"]

    print("=== TierForge Pre-Flight Check ===")
    print(f"[ {py['status']:4s} ] Python     : {py['version']} ({py['platform']})")
    print(f"[ {sq['status']:4s} ] SQLite3    : {sq['version']} (WAL={sq['wal_mode']})")

    if ol["status"] == "OK":
        sample = ", ".join(ol["models_sample"]) if ol["models_sample"] else "none pulled yet"
        print(
            f"[ {ol['status']:4s} ] Ollama     : {ol['endpoint']} "
            f"({ol['models_count']} models: {sample})"
        )
    else:
        print(f"[ {ol['status']:4s} ] Ollama     : {ol['endpoint']} — {ol['note']}")

    if gpu["gpus"]:
        gpu_desc = "; ".join(
            f"{g['name']} ({g['free_vram_mb']}/{g['total_vram_mb']} MB free)"
            for g in gpu["gpus"]
        )
        print(f"[ {gpu['status']:4s} ] GPU/VRAM   : {gpu['profile']} — {gpu_desc}")
    else:
        print(f"[ {gpu['status']:4s} ] GPU/VRAM   : {gpu['profile']} ({gpu['backend']})")

    detected_list = [k for k, v in comp["detected"].items() if v]
    comp_str = ", ".join(detected_list) if detected_list else "none"
    print(f"[ {comp['status']:4s} ] Companions : {comp_str}")
    for note in comp["notes"]:
        print(f"         NOTE       : {note}")

    bad_cfgs = [k for k, v in cfg["files"].items() if v != "OK"]
    cfg_str = "all valid" if not bad_cfgs else f"issues: {', '.join(bad_cfgs)}"
    print(f"[ {cfg['status']:4s} ] Configs    : {cfg_str}")
    print(f"Overall: {report['overall']}")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="TierForge read-only cross-platform pre-flight check."
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit compact JSON output for Tier 1 Cloud Agents.",
    )
    parser.add_argument(
        "--ollama-url",
        default=os.environ.get("TIERFORGE_LLM_BASE_URL", "http://127.0.0.1:11434"),
        help="Ollama base URL (default: $TIERFORGE_LLM_BASE_URL or http://127.0.0.1:11434).",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return non-zero exit code if Ollama is unreachable.",
    )
    args = parser.parse_args()

    report = run_preflight(args.ollama_url)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_human_report(report)

    if report["overall"] != "PASS":
        return 1
    if args.strict and report["checks"]["ollama"]["status"] != "OK":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
