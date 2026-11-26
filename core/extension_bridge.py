"""Layer-3 Browser Extension Local Bridge Server & Client (100% Python stdlib).

Allows TierForge Harness to talk directly to the user's ALREADY-OPEN personal browser
(Chrome, Coc Coc, Edge, Brave, Opera, Firefox) via the lightweight TierForge MV3 Extension:
  - Zero `--remote-debugging-port` flags required (immune to Chrome 136+ CDP restrictions).
  - Zero second browser window or re-login required (uses existing logged-in AI tabs).
  - Binds strictly to `127.0.0.1` with optional shared token (`X-TierForge-Token`).
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

DEFAULT_BRIDGE_HOST = "127.0.0.1"
DEFAULT_BRIDGE_PORT = 19876

SETUP_WIZARD_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TierForge — 30-Second Quick Setup</title>
  <style>
    body { margin: 0; font-family: system-ui, -apple-system, sans-serif; background: #0b1120; color: #e2e8f0; }
    .wrap { max-width: 720px; margin: 32px auto; padding: 0 20px 48px; }
    .top { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
    .lang-btn { background: #1e293b; color: #cbd5e1; border: 1px solid #334155; border-radius: 8px; padding: 6px 12px; cursor: pointer; font-size: 12px; font-weight: 600; }
    .card { background: #111827; border: 1px solid #1f2937; border-radius: 14px; padding: 20px 22px; margin-top: 16px; }
    h1 { font-size: 22px; margin: 0 0 6px; }
    h2 { font-size: 16px; margin: 0 0 10px; color: #f8fafc; }
    p, li { font-size: 14px; line-height: 1.55; color: #cbd5e1; }
    .status-bar { display: flex; align-items: center; gap: 10px; padding: 12px 16px; border-radius: 10px; background: #1e293b; margin-top: 14px; font-weight: 600; font-size: 14px; }
    .dot { width: 12px; height: 12px; border-radius: 50%; background: #ef4444; flex-shrink: 0; }
    .dot.ok { background: #22c55e; box-shadow: 0 0 10px #22c55e88; }
    .btn { display: inline-block; padding: 10px 16px; border-radius: 8px; border: 0; cursor: pointer; font-weight: 600; font-size: 14px; background: #2563eb; color: #fff; margin-top: 8px; }
    .btn.green { background: #16a34a; }
    .btn:disabled { opacity: 0.6; cursor: wait; }
    .tag { display: inline-block; font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #1e3a8a; color: #93c5fd; margin-left: 6px; vertical-align: middle; }
    code { background: #0f172a; padding: 2px 6px; border-radius: 5px; color: #93c5fd; font-size: 12px; }
    .legal { font-size: 12px; color: #94a3b8; border-left: 3px solid #334155; padding-left: 12px; margin-top: 16px; }
  </style>
</head>
<body>
  <div class="wrap">
    <div class="top">
      <h1 id="tTitle">TierForge AI Bridge — 30-Second Setup</h1>
      <button id="langToggle" class="lang-btn">EN / VI</button>
    </div>
    <p id="tSub">Choose either option below to connect TierForge with your browser. No command-line flags, no password sharing.</p>

    <div class="status-bar">
      <span id="dot" class="dot"></span>
      <span id="statusMsg">Checking local browser connection...</span>
    </div>

    <div class="card">
      <h2 id="tOpt1">Option 1: Open an Isolated AI Window <span class="tag">Zero-Install • Private</span></h2>
      <p id="tOpt1Desc">Launches an isolated Chrome / Edge / Coc Coc window completely separate from your personal browser (never touches personal email or banking tabs). Sign in once.</p>
      <button id="launchBtn" class="btn green">Open AI Window (1-Click)</button>
      <div id="launchMsg" style="font-size:13px;color:#86efac;margin-top:8px;"></div>
    </div>

    <div class="card">
      <h2 id="tOpt2">Option 2: Use Your Current Browser <span class="tag">Chrome / Edge / Brave / Firefox</span></h2>
      <ol id="tOpt2List">
        <li>Load the <b>TierForge AI Bridge</b> extension from <code>extensions/tierforge-bridge</code>.</li>
        <li>Wait 2 seconds — the extension auto-pairs securely (TOFU Origin Lock) and turns the indicator green.</li>
      </ol>
    </div>

    <div id="tLegal" class="legal">
      <b>Transparency &amp; Security (BYOA — 100% Localhost):</b> TierForge runs strictly on <code>127.0.0.1</code>, collects zero telemetry, and never reads banking/email tabs. Use the red <b>Pause Bridge</b> button anytime to disconnect.
    </div>
  </div>
  <script>
    const I18N = {
      en: {
        title: 'TierForge AI Bridge — 30-Second Setup',
        sub: 'Choose either option below to connect TierForge with your browser. No command-line flags, no password sharing.',
        opt1: 'Option 1: Open an Isolated AI Window <span class="tag">Zero-Install • Private</span>',
        opt1Desc: 'Launches an isolated Chrome / Edge / Coc Coc window completely separate from your personal browser. Sign in once.',
        launchBtn: 'Open AI Window (1-Click)',
        opt2: 'Option 2: Use Your Current Browser <span class="tag">Chrome / Edge / Brave / Firefox</span>',
        opt2List: '<li>Load the <b>TierForge AI Bridge</b> extension from <code>extensions/tierforge-bridge</code>.</li><li>Wait 2 seconds — the extension auto-pairs securely (TOFU Lock) and turns green.</li>',
        legal: '<b>Transparency &amp; Security (BYOA — 100% Localhost):</b> TierForge runs strictly on <code>127.0.0.1</code>, collects zero telemetry, and never reads banking/email tabs.',
        connected: 'CONNECTED via Extension: ',
        ready: 'Local Bridge is ready — pick Option 1 or Option 2 below.'
      },
      vi: {
        title: 'TierForge AI Bridge — Thiết lập 30 giây',
        sub: 'Chọn 1 trong 2 cách bên dưới để nối TierForge với trình duyệt của bạn. Không cần dòng lệnh, không cần chia sẻ mật khẩu.',
        opt1: 'Cách 1: Mở cửa sổ AI riêng biệt <span class="tag">0 cài đặt • Riêng tư</span>',
        opt1Desc: 'Tự mở cửa sổ Chrome / Cốc Cốc / Edge tách biệt hoàn toàn khỏi trình duyệt cá nhân (không đụng Gmail hay ngân hàng). Đăng nhập 1 lần duy nhất.',
        launchBtn: 'Mở cửa sổ AI ngay (1-Click)',
        opt2: 'Cách 2: Dùng trực tiếp trình duyệt hiện tại <span class="tag">Chrome / Cốc Cốc / Edge / Firefox</span>',
        opt2List: '<li>Tải tiện ích <b>TierForge AI Bridge</b> từ thư mục <code>extensions/tierforge-bridge</code>.</li><li>Đợi 2 giây — tiện ích tự ghép cặp an toàn (TOFU Lock) và chuyển đèn xanh.</li>',
        legal: '<b>Cam kết Minh bạch &amp; Bảo mật (BYOA — 100% Localhost):</b> Chạy hoàn toàn trên <code>127.0.0.1</code>, không thu thập dữ liệu, có nút <b>Pause Bridge</b> ngắt bất cứ lúc nào.',
        connected: 'ĐÃ KẾT NỐI qua Extension: ',
        ready: 'Local Bridge sẵn sàng — hãy chọn Cách 1 hoặc Cách 2 bên dưới.'
      }
    };
    let lang = (navigator.language || '').toLowerCase().startsWith('vi') ? 'vi' : 'en';
    function applyLang() {
      const t = I18N[lang];
      document.documentElement.lang = lang;
      document.getElementById('tTitle').textContent = t.title;
      document.getElementById('tSub').textContent = t.sub;
      document.getElementById('tOpt1').innerHTML = t.opt1;
      document.getElementById('tOpt1Desc').textContent = t.opt1Desc;
      document.getElementById('launchBtn').textContent = t.launchBtn;
      document.getElementById('tOpt2').innerHTML = t.opt2;
      document.getElementById('tOpt2List').innerHTML = t.opt2List;
      document.getElementById('tLegal').innerHTML = t.legal;
    }
    document.getElementById('langToggle').addEventListener('click', () => { lang = lang === 'en' ? 'vi' : 'en'; applyLang(); pollHealth(); });
    async function pollHealth() {
      try {
        const r = await fetch('/health');
        const d = await r.json();
        const dot = document.getElementById('dot');
        const msg = document.getElementById('statusMsg');
        if (d.connected_count > 0) {
          dot.className = 'dot ok';
          msg.textContent = I18N[lang].connected + (d.browsers || []).map(b => b.browser).join(', ');
        } else {
          dot.className = 'dot';
          msg.textContent = I18N[lang].ready;
        }
      } catch (e) {
        document.getElementById('statusMsg').textContent = 'Cannot reach Local Bridge.';
      }
    }
    document.getElementById('launchBtn').addEventListener('click', async () => {
      const btn = document.getElementById('launchBtn');
      const out = document.getElementById('launchMsg');
      btn.disabled = true;
      try {
        const r = await fetch('/setup/launch-browser', {
          method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({service: 'gemini'})
        });
        const d = await r.json();
        out.textContent = d.ok ? ('Opened ' + d.browser + ' at ' + d.cdp_url) : ('Error: ' + (d.error || 'unknown'));
      } catch (e) { out.textContent = 'Error: ' + e; } finally { btn.disabled = false; }
    });
    applyLang();
    pollHealth();
    setInterval(pollHealth, 2000);
  </script>
</body>
</html>
"""


@dataclass
class ConnectedBrowser:
    client_id: str
    browser: str
    tabs: list[str] = field(default_factory=list)
    last_seen: float = field(default_factory=time.time)

    def has_service(self, service: str) -> bool:
        s = service.lower().strip()
        if s == "gemini":
            return any("gemini.google.com" in t.lower() for t in self.tabs)
        if s == "flow":
            return any("flow.google.com" in t.lower() or "labs.google" in t.lower() for t in self.tabs)
        if s == "chatgpt":
            return any("chatgpt.com" in t.lower() or "chat.openai.com" in t.lower() for t in self.tabs)
        if s == "claude":
            return any("claude.ai" in t.lower() for t in self.tabs)
        return True


@dataclass
class BridgeJob:
    job_id: str
    service: str
    prompt: str
    created_at: float = field(default_factory=time.time)
    status: str = "queued"  # queued | dispatched | done | error
    assigned_to: str = ""
    response_text: str = ""
    error: str = ""
    done_event: threading.Event = field(default_factory=threading.Event)


def get_bridge_token_path() -> Path:
    return Path.home() / ".tierforge" / "bridge_token"


def load_or_create_bridge_token() -> str:
    env_tok = os.getenv("TIERFORGE_BRIDGE_TOKEN", "").strip()
    if env_tok:
        return env_tok
    p = get_bridge_token_path()
    if p.exists():
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    p.parent.mkdir(parents=True, exist_ok=True)
    tok = "tf_" + secrets.token_hex(12)
    p.write_text(tok, encoding="utf-8")
    return tok


class ExtensionBridgeState:
    """Thread-safe queue & browser presence registry for ExtensionBridgeServer."""

    def __init__(self, token: str = "", browser_ttl_sec: float = 15.0, allow_auto_pair: bool = True) -> None:
        self.token = token
        self.browser_ttl_sec = browser_ttl_sec
        self.allow_auto_pair = allow_auto_pair
        self.paired_origin: str = ""
        self._lock = threading.Lock()
        self._browsers: dict[str, ConnectedBrowser] = {}
        self._jobs: dict[str, BridgeJob] = {}

    def try_pair_origin(self, origin: str) -> tuple[bool, str]:
        """Trust-On-First-Use (TOFU) pinning: lock pairing to the first valid Extension Origin."""
        with self._lock:
            if not self.paired_origin:
                self.paired_origin = origin
                return True, origin
            if self.paired_origin == origin:
                return True, origin
            return False, "already_paired_to_another_origin"

    def prune_stale_browsers(self) -> None:
        now = time.time()
        with self._lock:
            stale = [cid for cid, b in self._browsers.items() if now - b.last_seen > self.browser_ttl_sec]
            for cid in stale:
                del self._browsers[cid]

    def active_browsers(self) -> list[ConnectedBrowser]:
        self.prune_stale_browsers()
        with self._lock:
            return list(self._browsers.values())

    def register_heartbeat(self, client_id: str, browser: str, tabs: list[str]) -> Optional[dict[str, Any]]:
        now = time.time()
        with self._lock:
            self._browsers[client_id] = ConnectedBrowser(
                client_id=client_id,
                browser=browser or "unknown",
                tabs=[str(t) for t in tabs],
                last_seen=now,
            )
            # Pick the oldest queued job (prefer matching service tab if available)
            queued = [j for j in self._jobs.values() if j.status == "queued"]
            queued.sort(key=lambda j: j.created_at)
            if not queued:
                return None

            chosen = queued[0]
            b_obj = self._browsers[client_id]
            for j in queued:
                if b_obj.has_service(j.service):
                    chosen = j
                    break

            chosen.status = "dispatched"
            chosen.assigned_to = client_id
            return {
                "job_id": chosen.job_id,
                "service": chosen.service,
                "prompt": chosen.prompt,
            }

    def enqueue_job(self, service: str, prompt: str) -> BridgeJob:
        job_id = "job_" + secrets.token_hex(6)
        job = BridgeJob(job_id=job_id, service=service.lower().strip(), prompt=prompt)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def complete_job(self, job_id: str, *, ok: bool, response_text: str = "", error: str = "") -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            job.status = "done" if ok else "error"
            job.response_text = response_text
            job.error = error
            job.done_event.set()
            return True

    def health_payload(self) -> dict[str, Any]:
        browsers = self.active_browsers()
        return {
            "ok": True,
            "bridge": "tierforge-extension-bridge",
            "connected_count": len(browsers),
            "paired_origin": self.paired_origin,
            "browsers": [
                {"client_id": b.client_id, "browser": b.browser, "tabs": b.tabs}
                for b in browsers
            ],
            "services_ready": {
                svc: any(b.has_service(svc) for b in browsers)
                for svc in ("gemini", "chatgpt", "claude", "flow")
            },
        }


def _make_handler(state: ExtensionBridgeState):
    class _BridgeHandler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return  # Quiet local daemon

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            origin = self.headers.get("Origin", "")
            if origin.startswith(("chrome-extension://", "moz-extension://", "extension://")):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Access-Control-Allow-Headers", "Content-Type, X-TierForge-Token")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.end_headers()
            self.wfile.write(raw)

        def _check_auth(self) -> bool:
            if not state.token:
                return True
            got = self.headers.get("X-TierForge-Token", "").strip()
            return secrets.compare_digest(got, state.token)

        def _drain_body(self) -> bytes:
            length = int(self.headers.get("Content-Length", "0") or "0")
            if length <= 0:
                return b""
            return self.rfile.read(min(length, 1_048_576))

        def _parse_json_bytes(self, raw: bytes) -> dict[str, Any]:
            if not raw:
                return {}
            data = json.loads(raw.decode("utf-8", errors="replace"))
            return data if isinstance(data, dict) else {}

        def _read_json(self) -> dict[str, Any]:
            return self._parse_json_bytes(self._drain_body())

        def _send_html(self, status: int, html: str) -> None:
            raw = html.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_OPTIONS(self) -> None:  # noqa: N802
            self._send_json(200, {"ok": True})

        def do_GET(self) -> None:  # noqa: N802
            route = self.path.split("?")[0]
            if route in ("/", "/setup"):
                self._send_html(200, SETUP_WIZARD_HTML)
                return
            if route == "/health":
                self._send_json(200, state.health_payload())
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            route = self.path.split("?")[0]
            # Always drain POST body before any early 401/403/200 return so TCP never resets on Windows/POSIX
            raw_body = self._drain_body()

            # 1-Click Layer-2 browser launch from local /setup wizard (localhost same-origin only)
            if route == "/setup/launch-browser":
                origin = self.headers.get("Origin", "")
                if origin and not origin.startswith(("http://127.0.0.1:", "http://localhost:")):
                    self._send_json(403, {"ok": False, "error": "localhost_origin_required"})
                    return
                try:
                    body = self._parse_json_bytes(raw_body)
                    from core.cdp_bridge import launch_managed_browser

                    ep = launch_managed_browser(
                        service=str(body.get("service") or "gemini"),
                        preferred_browser=body.get("browser"),
                    )
                    self._send_json(200, {"ok": True, "browser": ep.browser, "cdp_url": ep.url})
                except Exception as exc:
                    self._send_json(500, {"ok": False, "error": str(exc)})
                return

            # Zero-config TOFU auto-pairing: ONLY the first valid browser extension on localhost can self-pair
            if route == "/extension/pair":
                origin = self.headers.get("Origin", "")
                if not state.allow_auto_pair:
                    self._send_json(403, {"ok": False, "error": "auto_pair_disabled"})
                    return
                if not origin.startswith(("chrome-extension://", "moz-extension://", "extension://")):
                    self._send_json(403, {"ok": False, "error": "extension_origin_required"})
                    return
                ok_pair, info = state.try_pair_origin(origin)
                if not ok_pair:
                    self._send_json(403, {"ok": False, "error": info})
                    return
                self._send_json(
                    200,
                    {"ok": True, "token": state.token, "auto_paired": True, "pinned_origin": info},
                )
                return

            if not self._check_auth():
                self._send_json(401, {"ok": False, "error": "unauthorized"})
                return
            try:
                body = self._parse_json_bytes(raw_body)
            except Exception as exc:
                self._send_json(400, {"ok": False, "error": f"invalid_json: {exc}"})
                return

            if route == "/extension/heartbeat":
                cid = str(body.get("client_id") or "default")
                browser = str(body.get("browser") or "chromium")
                tabs = body.get("tabs") if isinstance(body.get("tabs"), list) else []
                next_job = state.register_heartbeat(cid, browser, tabs)
                self._send_json(200, {"ok": True, "job": next_job})
                return

            if route == "/extension/result":
                job_id = str(body.get("job_id") or "")
                ok = bool(body.get("ok", True))
                text = str(body.get("response_text") or "")
                err = str(body.get("error") or "")
                found = state.complete_job(job_id, ok=ok, response_text=text, error=err)
                self._send_json(200 if found else 404, {"ok": found})
                return

            if route == "/dispatch":
                service = str(body.get("service") or "gemini")
                prompt = str(body.get("prompt") or "").strip()
                timeout_sec = float(body.get("timeout_sec") or 120.0)
                if not prompt:
                    self._send_json(400, {"ok": False, "error": "empty_prompt"})
                    return
                if not state.active_browsers():
                    self._send_json(
                        503,
                        {
                            "ok": False,
                            "error": "No browser extension connected. Open Chrome/CocCoc/Edge/Firefox with TierForge Bridge Extension enabled.",
                        },
                    )
                    return
                job = state.enqueue_job(service=service, prompt=prompt)
                finished = job.done_event.wait(timeout=max(1.0, timeout_sec))
                if not finished:
                    state.complete_job(job.job_id, ok=False, error="timeout")
                    self._send_json(504, {"ok": False, "job_id": job.job_id, "error": "extension_timeout"})
                    return
                if job.status == "done":
                    self._send_json(200, {"ok": True, "job_id": job.job_id, "response_text": job.response_text})
                else:
                    self._send_json(500, {"ok": False, "job_id": job.job_id, "error": job.error or "extension_error"})
                return

            self._send_json(404, {"ok": False, "error": "not_found"})

    return _BridgeHandler


class ExtensionBridgeServer:
    """Localhost-only HTTP bridge daemon (`127.0.0.1`) for the TierForge MV3 Extension."""

    def __init__(
        self,
        host: str = DEFAULT_BRIDGE_HOST,
        port: int = DEFAULT_BRIDGE_PORT,
        token: Optional[str] = None,
        browser_ttl_sec: float = 15.0,
        allow_auto_pair: bool = True,
    ) -> None:
        if host not in ("127.0.0.1", "localhost"):
            raise ValueError("ExtensionBridgeServer must bind strictly to localhost (127.0.0.1).")
        self.host = host
        self.token = "" if token == "" else (token or load_or_create_bridge_token())
        self.state = ExtensionBridgeState(
            token=self.token,
            browser_ttl_sec=browser_ttl_sec,
            allow_auto_pair=allow_auto_pair,
        )
        self._httpd = ThreadingHTTPServer((self.host, port), _make_handler(self.state))
        self.port: int = int(self._httpd.server_address[1])
        self._thread: Optional[threading.Thread] = None

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start_background(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self) -> None:
        self._httpd.serve_forever()

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()


class ExtensionBridgeClient:
    """Stdlib client for querying and dispatching prompts through the Layer-3 Extension Bridge."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        port = int(os.getenv("TIERFORGE_BRIDGE_PORT", str(DEFAULT_BRIDGE_PORT)))
        self.base_url = (base_url or f"http://{DEFAULT_BRIDGE_HOST}:{port}").rstrip("/")
        self.token = "" if token == "" else (token if token is not None else self._read_existing_token())

    @staticmethod
    def _read_existing_token() -> str:
        env_tok = os.getenv("TIERFORGE_BRIDGE_TOKEN", "").strip()
        if env_tok:
            return env_tok
        p = get_bridge_token_path()
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        return ""

    def health(self, timeout: float = 1.5) -> Optional[dict[str, Any]]:
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=timeout) as resp:
                if getattr(resp, "status", 200) != 200:
                    return None
                data = json.loads(resp.read().decode("utf-8"))
                return data if isinstance(data, dict) else None
        except Exception:
            return None

    def is_ready(self, timeout: float = 1.5) -> bool:
        h = self.health(timeout=timeout)
        return bool(h and h.get("ok") and int(h.get("connected_count", 0)) > 0)

    def dispatch_prompt(
        self,
        prompt: str,
        *,
        service: str = "gemini",
        timeout_sec: float = 120.0,
        redact_secrets: bool = True,
        trim_response: bool = False,
        max_response_chars: int = 4000,
    ) -> str:
        outgoing = prompt
        if redact_secrets:
            from core.output_trimmer import redact_secrets_text

            outgoing, _ = redact_secrets_text(prompt)

        payload = json.dumps(
            {"service": service, "prompt": outgoing, "timeout_sec": timeout_sec},
            ensure_ascii=False,
        ).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["X-TierForge-Token"] = self.token
        req = urllib.request.Request(f"{self.base_url}/dispatch", data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec + 5.0) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                if not body.get("ok"):
                    raise RuntimeError(body.get("error", "extension_bridge_failed"))
                text = str(body.get("response_text", ""))
                if trim_response and text:
                    from core.output_trimmer import trim_output

                    return trim_output(text, max_chars=max_response_chars).text
                return text
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"ExtensionBridge HTTP {exc.code}: {err_body}") from exc
