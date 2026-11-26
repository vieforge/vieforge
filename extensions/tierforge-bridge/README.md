# TierForge AI Bridge Extension (Manifest V3)

Optional **Layer-3 (Zero-CDP)** localhost bridge connecting your already-open browser (**Chrome, Edge, Brave, Opera, Vivaldi, Coc Coc, Firefox**) to your local `tierforge-harness` (`127.0.0.1:19876`):

- No `--remote-debugging-port` flags required (compatible with Chrome 136+ security restrictions).
- Zero cloud servers, zero analytics, zero third-party telemetry, 100% unminified auditable JS.
- Strictly scoped to `127.0.0.1:19876` and the 4 supported AI web apps only — includes TOFU Origin Pinning and a 1-click **Pause Bridge** kill switch.
- **Compliance Note:** Intended for personal BYOA (Bring Your Own Account) productivity only. See [LEGAL_AND_COMPLIANCE.md](../../LEGAL_AND_COMPLIANCE.md) and [SECURITY.md](SECURITY.md).

## Quick Install (30 Seconds)

### Chrome / Edge / Brave / Opera / Coc Coc
1. Open the extensions page (`chrome://extensions`, `edge://extensions`, or `brave://extensions`).
2. Enable **Developer mode**.
3. Click **Load unpacked** and select this folder (`extensions/tierforge-bridge`).

### Firefox (115+)
1. Open `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…** and select `manifest.json` in this folder.

## Start Local Bridge (Zero Copy-Paste Pairing)
Run once from the repository root (or double-click `scripts/start_tierforge_bridge.bat` on Windows):

```bash
python scripts/extension_bridge_server.py serve
```

The extension automatically pairs over localhost (`POST /extension/pair`) within 2 seconds and shows **Connected (Auto-Paired)** in the popup.
