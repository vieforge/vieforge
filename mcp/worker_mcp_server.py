#!/usr/bin/env python3
"""Backwards-compatible CLI shim for `scripts/mcp_server.py`.

The canonical MCP server lives in `scripts/mcp_server.py` so that installing
the official `mcp` PyPI SDK (`pip install "vieforge[mcp]"`) never collides
with a local `mcp/` directory on `sys.path`.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.mcp_server import (  # noqa: F401
    build_mcp_server,
    main,
    tool_estimate_batch_dry_run,
    tool_get_job_summary,
    tool_preflight_env,
    tool_run_local_batch,
    tool_run_trimmed_command,
)

if __name__ == "__main__":
    raise SystemExit(main())
