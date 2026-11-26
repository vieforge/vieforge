"""CLI for Layer-3 Browser Extension Local Bridge Server (Zero-CDP, All Browsers).

Usage:
  python scripts/extension_bridge_server.py serve
  python scripts/extension_bridge_server.py serve --no-auth
  python scripts/extension_bridge_server.py status
  python scripts/extension_bridge_server.py token
  python scripts/extension_bridge_server.py send --service gemini --prompt "Hello"
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.extension_bridge import (  # noqa: E402
    DEFAULT_BRIDGE_PORT,
    ExtensionBridgeClient,
    ExtensionBridgeServer,
    load_or_create_bridge_token,
)
from core.paths import resolve_asset  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="TierForge Layer-3 Browser Extension Bridge CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_serve = sub.add_parser("serve", help="Run localhost Extension Bridge daemon (127.0.0.1)")
    p_serve.add_argument("--port", type=int, default=DEFAULT_BRIDGE_PORT)
    p_serve.add_argument("--no-auth", action="store_true", help="Disable X-TierForge-Token check on localhost")

    p_status = sub.add_parser("status", help="Check live Extension Bridge and connected browsers")
    p_status.add_argument("--port", type=int, default=DEFAULT_BRIDGE_PORT)

    sub.add_parser("token", help="Print (or generate) the local Bridge Token")
    sub.add_parser("path", help="Print the exact extension folder path to select in Load Unpacked")

    p_send = sub.add_parser("send", help="Dispatch a prompt to a connected browser extension")
    p_send.add_argument("--port", type=int, default=DEFAULT_BRIDGE_PORT)
    p_send.add_argument("--service", default="gemini", choices=["gemini", "chatgpt", "claude", "flow"])
    p_send.add_argument("--prompt", required=True)
    p_send.add_argument("--timeout", type=float, default=120.0)

    args = parser.parse_args()

    if args.cmd == "path":
        ext_dir = resolve_asset("extensions/tierforge-bridge").resolve()
        print(str(ext_dir))
        return

    if args.cmd == "token":
        print(load_or_create_bridge_token())
        return

    if args.cmd == "status":
        client = ExtensionBridgeClient(base_url=f"http://127.0.0.1:{args.port}")
        health = client.health()
        if not health:
            print(json.dumps({"ok": False, "error": "bridge_server_offline", "port": args.port}, indent=2))
            sys.exit(1)
        print(json.dumps(health, indent=2, ensure_ascii=False))
        return

    if args.cmd == "send":
        client = ExtensionBridgeClient(base_url=f"http://127.0.0.1:{args.port}")
        text = client.dispatch_prompt(args.prompt, service=args.service, timeout_sec=args.timeout)
        print(json.dumps({"ok": True, "service": args.service, "response_text": text}, indent=2, ensure_ascii=False))
        return

    if args.cmd == "serve":
        token = "" if args.no_auth else load_or_create_bridge_token()
        server = ExtensionBridgeServer(port=args.port, token=token)
        print(
            json.dumps(
                {
                    "status": "listening",
                    "url": server.base_url,
                    "auth_enabled": bool(token),
                    "token": token if token else "(disabled)",
                    "extension_dir": str(resolve_asset("extensions/tierforge-bridge").resolve()),
                },
                indent=2,
                ensure_ascii=False,
            ),
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            server.stop()


if __name__ == "__main__":
    main()
