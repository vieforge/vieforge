"""Allow running `python -m core <command>` as an alias for `vieforge`."""

from __future__ import annotations

from scripts.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
