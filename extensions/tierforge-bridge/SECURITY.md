# TierForge AI Bridge — Security & Privacy Architecture (Glass-Box Trust)

1. **Zero Cloud Telemetry:** 100% traffic stays strictly inside your machine (`127.0.0.1:19876`). Zero bytes are ever sent to any third-party server.
2. **Least-Privilege Permissions:**
   - NEVER requests `<all_urls>`, `cookies`, `history`, or `webRequest`.
   - Restricted strictly to 1 localhost port (`http://127.0.0.1:19876/*`) and 4 AI web apps (`gemini.google.com`, `chatgpt.com`, `claude.ai`, `labs.google`).
   - Cannot read banking, email, social media, or any non-AI tab.
3. **TOFU (Trust-On-First-Use) Origin Pinning:**
   - Auto-pairing (`POST /extension/pair`) accepts ONLY browser extension origins (`chrome-extension://` / `moz-extension://`) and permanently pins the first paired Extension ID.
   - Any website or second unknown extension attempting to pair is hard-blocked with `HTTP 403`.
4. **Visible Execution & Instant Kill Switch:**
   - Icon badge switches to orange `RUN` whenever a prompt is actively executing.
   - Popup displays the last 10 local jobs (`time • service • chars • status`) and provides a 1-click red **Pause Bridge** kill switch.
5. **100% Readable Unminified Source:** All JS files (`background.js`, `content_bridge.js`, `popup.js`) are plain, unminified JavaScript (~200 lines total) so any user or security auditor can inspect every line in under 1 minute.
