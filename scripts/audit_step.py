"""CLI Post-Step Quality Auditor for TierForge Harness ($0 Local + 3-Judge Prompt Export).

Usage:
  python scripts/audit_step.py --step v0.2_cdp_and_auditor
  python scripts/audit_step.py --step v0.2_cdp_and_auditor --no-pytest
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.cdp_bridge import ThreeTierRouter
from core.step_auditor import export_step_3judge_audit


def main() -> None:
    parser = argparse.ArgumentParser(description="TierForge Post-Step Quality Auditor & 3-Judge Exporter")
    parser.add_argument("--step", required=True, help="Step or milestone identifier (e.g. step02_core_sync)")
    parser.add_argument("--no-pytest", action="store_true", help="Skip running pytest inside telemetry")
    args = parser.parse_args()

    res = export_step_3judge_audit(ROOT, args.step, run_tests=not args.no_pytest)
    router = ThreeTierRouter()
    res["three_tier_health"] = router.health_snapshot()
    print(json.dumps(res, indent=2, ensure_ascii=False))
    if not res["telemetry"]["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
