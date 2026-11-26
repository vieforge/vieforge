"""Cross-platform asset and writable runtime-directory resolver (100% Python stdlib).

Supports both:
  1. Editable / git clone checkouts (`REPO_ROOT/config/...`, `REPO_ROOT/skills/...`, `REPO_ROOT/data/...`)
  2. Clean wheel installs (`pip install vieforge`) where bundled assets live under
     `core/_bundled_assets/...` and runtime state defaults to `TIERFORGE_DATA_DIR` or
     `<cwd>/.vieforge` instead of writing inside read-only `site-packages`.
"""

from __future__ import annotations

import os
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
REPO_ROOT = PACKAGE_DIR.parent
BUNDLED_ASSETS_DIR = PACKAGE_DIR / "_bundled_assets"


def is_source_checkout() -> bool:
    """Return True when running directly from a source repository checkout."""
    return (REPO_ROOT / "pyproject.toml").is_file() and (REPO_ROOT / "config").is_dir()


def resolve_asset(rel_path: str) -> Path:
    """Resolve a read-only project asset from source checkout or bundled wheel data."""
    normalized = rel_path.replace("\\", "/").lstrip("/")
    source_candidate = REPO_ROOT / normalized
    if source_candidate.exists():
        return source_candidate
    bundled_candidate = BUNDLED_ASSETS_DIR / normalized
    if bundled_candidate.exists():
        return bundled_candidate
    return source_candidate


def default_data_dir() -> Path:
    """Return the default writable directory for SQLite state, logs, and generated configs."""
    env_dir = os.getenv("TIERFORGE_DATA_DIR", "").strip()
    if env_dir:
        return Path(env_dir).expanduser().resolve()
    if is_source_checkout():
        return REPO_ROOT / "data"
    return Path.cwd() / ".vieforge"
