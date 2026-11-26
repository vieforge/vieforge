"""Zero-Touch Multi-Browser Bridge CLI for TierForge Harness (Chrome, Coc Coc, Edge, Brave).

Usage:
  python scripts/browser_bridge.py status
  python scripts/browser_bridge.py ensure --service gemini
  python scripts/browser_bridge.py ensure --service gemini --browser coccoc
  python scripts/browser_bridge.py login --service gemini
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.cdp_bridge import (  # noqa: E402
    ThreeTierRouter,
    detect_installed_browsers,
    discover_cdp_endpoints,
    get_default_profile_dir,
    launch_managed_browser,
    resolve_cdp_url,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="TierForge Universal Multi-Browser Auto-Bridge")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="Show installed browsers, live CDP ports, and 3-tier health")

    p_ensure = sub.add_parser("ensure", help="Attach to live CDP port or zero-touch auto-launch browser")
    p_ensure.add_argument("--service", default="gemini", choices=["gemini", "flow", "chatgpt", "claude", "any"])
    p_ensure.add_argument("--browser", default=None, help="Preferred browser (chrome, coccoc, edge, brave)")

    p_login = sub.add_parser("login", help="Launch dedicated worker profile once for one-time account login")
    p_login.add_argument("--service", default="gemini", choices=["gemini", "flow", "chatgpt", "claude"])
    p_login.add_argument("--browser", default=None, help="Preferred browser (chrome, coccoc, edge, brave)")

    args = parser.parse_args()

    if args.cmd == "status":
        router = ThreeTierRouter()
        out = {
            "health": router.health_snapshot(),
            "installed_browsers": [
                {"name": b.name, "executable": b.executable} for b in detect_installed_browsers()
            ],
            "live_endpoints": [
                {"port": ep.port, "url": ep.url, "tab_count": len(ep.tabs)} for ep in discover_cdp_endpoints()
            ],
            "managed_profile_dir": str(get_default_profile_dir()),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return

    if args.cmd == "ensure":
        url = resolve_cdp_url(service=args.service, auto_launch=True, preferred_browser=args.browser)
        print(json.dumps({"ready": True, "service": args.service, "cdp_url": url}, indent=2, ensure_ascii=False))
        return

    if args.cmd == "login":
        ep = launch_managed_browser(service=args.service, preferred_browser=args.browser, headless=False)
        print(
            json.dumps(
                {
                    "status": "browser_opened_for_login",
                    "browser": ep.browser,
                    "cdp_url": ep.url,
                    "profile_dir": str(get_default_profile_dir()),
                    "note": "Log in once in the opened window. Future TierForge runs will attach automatically.",
                },
                indent=2,
                ensure_ascii=False,
            )
        )


if __name__ == "__main__":
    main()
