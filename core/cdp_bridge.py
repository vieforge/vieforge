"""Universal Multi-Browser Auto-Bridge & 3-Tier Execution Router (100% Python stdlib).

Layer 1 — Multi-Browser Auto-Discovery:
  Discovers active `--remote-debugging-port=<N>` endpoints across Chrome, Coc Coc, Edge,
  Brave, Opera, Vivaldi, Chromium, and GPM profiles on Windows/macOS/Linux.

Layer 2 — Zero-Touch Auto-Launch (Chrome 136+ Safe):
  When no CDP port is open, automatically detects installed browsers (Chrome -> Coc Coc ->
  Edge -> Brave -> Vivaldi -> Opera -> Chromium), picks a free localhost port, and launches
  a dedicated isolated worker profile (`~/.tierforge/browser-profile`) without touching the
  user's personal browser session. Log in once; every subsequent run is 100% zero-touch.

3-Tier Router:
  Tier 0 (Local Python/FFmpeg, $0) -> Tier 0.5 (Ollama Local, $0) -> Tier 1 (Web AI via CDP, $0).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

SERVICE_URL_MAP: dict[str, str] = {
    "gemini": "https://gemini.google.com/app",
    "flow": "https://labs.google/fx/tools/flow",
    "chatgpt": "https://chatgpt.com/",
    "claude": "https://claude.ai/",
    "any": "https://gemini.google.com/app",
}

WINDOWS_CHROMIUM_PROCESSES: tuple[str, ...] = (
    "chrome.exe",
    "browser.exe",  # Coc Coc
    "msedge.exe",
    "brave.exe",
    "opera.exe",
    "vivaldi.exe",
    "chromium.exe",
)


@dataclass
class BrowserCandidate:
    name: str
    executable: str


@dataclass
class CDPEndpoint:
    port: int
    url: str
    tabs: list[str] = field(default_factory=list)
    browser: str = "chromium"

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


def get_default_profile_dir() -> Path:
    """Return dedicated isolated profile directory (safe for Chrome 136+ CDP security rules)."""
    override = os.getenv("TIERFORGE_BROWSER_PROFILE", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".tierforge" / "browser-profile"


def find_free_port(start: int = 9222, end: int = 9299) -> int:
    """Find an unused localhost TCP port in [start, end]."""
    for port in range(start, end + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"No free localhost TCP port found in range {start}-{end}.")


def detect_installed_browsers() -> list[BrowserCandidate]:
    """Detect installed Chromium-family browsers in priority order (Chrome -> Coc Coc -> Edge -> Brave)."""
    found: list[BrowserCandidate] = []
    seen_paths: set[str] = set()

    def _add(name: str, candidate: Optional[str]) -> None:
        if not candidate:
            return
        p = Path(candidate)
        if p.is_file():
            norm = str(p.resolve()).lower()
            if norm not in seen_paths:
                seen_paths.add(norm)
                found.append(BrowserCandidate(name=name, executable=str(p)))

    if os.name == "nt":
        local_app = os.getenv("LOCALAPPDATA", "")
        prog_files = os.getenv("PROGRAMFILES", r"C:\Program Files")
        prog_x86 = os.getenv("PROGRAMFILES(X86)", r"C:\Program Files (x86)")

        win_candidates: list[tuple[str, list[str]]] = [
            (
                "chrome",
                [
                    os.path.join(prog_files, r"Google\Chrome\Application\chrome.exe"),
                    os.path.join(prog_x86, r"Google\Chrome\Application\chrome.exe"),
                    os.path.join(local_app, r"Google\Chrome\Application\chrome.exe"),
                ],
            ),
            (
                "coccoc",
                [
                    os.path.join(local_app, r"CocCoc\Browser\Application\browser.exe"),
                    os.path.join(prog_files, r"CocCoc\Browser\Application\browser.exe"),
                    os.path.join(prog_x86, r"CocCoc\Browser\Application\browser.exe"),
                ],
            ),
            (
                "edge",
                [
                    os.path.join(prog_x86, r"Microsoft\Edge\Application\msedge.exe"),
                    os.path.join(prog_files, r"Microsoft\Edge\Application\msedge.exe"),
                    os.path.join(local_app, r"Microsoft\Edge\Application\msedge.exe"),
                ],
            ),
            (
                "brave",
                [
                    os.path.join(prog_files, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                    os.path.join(prog_x86, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                    os.path.join(local_app, r"BraveSoftware\Brave-Browser\Application\brave.exe"),
                ],
            ),
            (
                "vivaldi",
                [
                    os.path.join(local_app, r"Vivaldi\Application\vivaldi.exe"),
                    os.path.join(prog_files, r"Vivaldi\Application\vivaldi.exe"),
                ],
            ),
            (
                "opera",
                [
                    os.path.join(local_app, r"Programs\Opera\opera.exe"),
                    os.path.join(prog_files, r"Opera\opera.exe"),
                ],
            ),
        ]
        for bname, paths in win_candidates:
            for path in paths:
                _add(bname, path)
    elif sys.platform == "darwin":
        mac_candidates: list[tuple[str, str]] = [
            ("chrome", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            ("coccoc", "/Applications/CocCoc.app/Contents/MacOS/CocCoc"),
            ("edge", "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
            ("brave", "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"),
            ("vivaldi", "/Applications/Vivaldi.app/Contents/MacOS/Vivaldi"),
            ("chromium", "/Applications/Chromium.app/Contents/MacOS/Chromium"),
        ]
        for bname, path in mac_candidates:
            _add(bname, path)
    else:
        linux_candidates: list[tuple[str, list[str]]] = [
            ("chrome", ["google-chrome", "google-chrome-stable"]),
            ("chromium", ["chromium", "chromium-browser"]),
            ("edge", ["microsoft-edge", "microsoft-edge-stable"]),
            ("brave", ["brave-browser", "brave"]),
            ("vivaldi", ["vivaldi"]),
            ("opera", ["opera"]),
        ]
        for bname, cmds in linux_candidates:
            for cmd in cmds:
                _add(bname, shutil.which(cmd))

    return found


def _probe_cdp_tabs(port: int, timeout: float = 1.5) -> Optional[list[str]]:
    """Query `http://127.0.0.1:<port>/json/list` and return open page URLs, or None if unreachable."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=timeout) as resp:
            if getattr(resp, "status", 200) != 200:
                return None
            data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            if not isinstance(data, list):
                return None
            return [
                str(item.get("url", ""))
                for item in data
                if isinstance(item, dict) and item.get("type") == "page"
            ]
    except Exception:
        return None


def _scan_process_cdp_ports() -> list[int]:
    """Extract `--remote-debugging-port=<N>` from ALL running Chromium-family browser processes."""
    ports: list[int] = []
    if os.name == "nt":
        try:
            proc_filter = " OR ".join(f"name='{p}'" for p in WINDOWS_CHROMIUM_PROCESSES)
            ps_cmd = (
                f"Get-CimInstance Win32_Process -Filter \"{proc_filter}\" "
                "| Where-Object { $_.CommandLine -match 'remote-debugging-port' } "
                "| ForEach-Object { $_.CommandLine }"
            )
            out = subprocess.check_output(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                text=True,
                timeout=6,
                stderr=subprocess.DEVNULL,
            )
            for m in re.finditer(r"--remote-debugging-port=(\d+)", out):
                p = int(m.group(1))
                if p not in ports:
                    ports.append(p)
        except Exception:
            pass
    else:
        try:
            out = subprocess.check_output(["ps", "-eo", "args"], text=True, timeout=5)
            for m in re.finditer(r"--remote-debugging-port=(\d+)", out):
                p = int(m.group(1))
                if p not in ports:
                    ports.append(p)
        except Exception:
            pass
    return ports


def discover_cdp_endpoints(
    extra_ports: Optional[list[int]] = None,
    timeout: float = 1.5,
) -> list[CDPEndpoint]:
    """Return all live, responsive local Chromium/GPM/CocCoc/Edge/Brave CDP endpoints."""
    candidates: list[int] = []
    env_port = os.getenv("CHROME_CDP_PORT") or os.getenv("GPM_CDP_PORT") or os.getenv("TIERFORGE_CDP_PORT")
    if env_port and env_port.isdigit():
        candidates.append(int(env_port))
    for p in _scan_process_cdp_ports():
        if p not in candidates:
            candidates.append(p)
    for p in (extra_ports or []) + [9222, 9223, 9224]:
        if p not in candidates:
            candidates.append(p)

    live: list[CDPEndpoint] = []
    for p in candidates:
        tabs = _probe_cdp_tabs(p, timeout=timeout)
        if tabs is not None:
            live.append(CDPEndpoint(port=p, url=f"http://127.0.0.1:{p}", tabs=tabs))
    return live


def launch_managed_browser(
    *,
    service: str = "gemini",
    preferred_browser: Optional[str] = None,
    port: Optional[int] = None,
    profile_dir: Optional[Path] = None,
    start_url: Optional[str] = None,
    headless: bool = False,
    wait_timeout: float = 15.0,
) -> CDPEndpoint:
    """Zero-touch launch of an installed browser using an isolated TierForge worker profile.

    Works on Chrome, Coc Coc, Edge, Brave, Vivaldi, Opera, and Chromium without touching
    the user's personal profile (100% compliant with Chrome 136+ CDP security rules).
    """
    installed = detect_installed_browsers()
    if not installed:
        raise RuntimeError(
            "No supported Chromium browser found (Chrome, Coc Coc, Edge, Brave, Vivaldi, Opera). "
            "Please install one to enable Tier-1 Web AI automation."
        )

    chosen = installed[0]
    if preferred_browser:
        pref = preferred_browser.lower().strip()
        for b in installed:
            if b.name == pref:
                chosen = b
                break

    target_port = port or find_free_port(9222, 9299)
    user_data = Path(profile_dir) if profile_dir else get_default_profile_dir()
    user_data.mkdir(parents=True, exist_ok=True)

    url_to_open = start_url or SERVICE_URL_MAP.get(service.lower().strip(), SERVICE_URL_MAP["gemini"])

    cmd = [
        chosen.executable,
        f"--remote-debugging-port={target_port}",
        f"--user-data-dir={user_data}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-timer-throttling",
    ]
    if headless:
        cmd.append("--headless=new")
    cmd.append(url_to_open)

    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if os.name == "nt":
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        popen_kwargs["start_new_session"] = True

    subprocess.Popen(cmd, **popen_kwargs)

    deadline = time.monotonic() + max(1.0, wait_timeout)
    while time.monotonic() < deadline:
        tabs = _probe_cdp_tabs(target_port, timeout=1.0)
        if tabs is not None:
            return CDPEndpoint(
                port=target_port,
                url=f"http://127.0.0.1:{target_port}",
                tabs=tabs,
                browser=chosen.name,
            )
        time.sleep(0.4)

    raise RuntimeError(
        f"Launched {chosen.name} on port {target_port}, but CDP did not respond within {wait_timeout:.1f}s."
    )


def resolve_cdp_url(
    preferred_url: Optional[str] = None,
    service: str = "any",
    extra_ports: Optional[list[int]] = None,
    auto_launch: Optional[bool] = None,
    preferred_browser: Optional[str] = None,
) -> str:
    """Resolve the best live CDP URL automatically (with optional zero-touch auto-launch).

    Priority:
      1. Explicit `preferred_url` (if passed and responsive).
      2. Live browser CDP port that ALREADY has a matching tab (`flow`/`gemini`/`chatgpt`/`claude`).
      3. Newest live browser CDP port discovered on localhost.
      4. If `auto_launch=True` (or `TIERFORGE_AUTO_LAUNCH_BROWSER=1`), automatically launch
         an installed browser with the dedicated `~/.tierforge/browser-profile` and return its URL.
    """
    if preferred_url and preferred_url.strip().lower() not in ("auto", ""):
        m = re.search(r":(\d+)", preferred_url)
        if m and _probe_cdp_tabs(int(m.group(1))) is not None:
            return preferred_url.rstrip("/")

    endpoints = discover_cdp_endpoints(extra_ports=extra_ports)
    if not endpoints:
        should_launch = (
            auto_launch
            if auto_launch is not None
            else os.getenv("TIERFORGE_AUTO_LAUNCH_BROWSER", "0").strip() in ("1", "true", "yes")
        )
        if should_launch:
            ep = launch_managed_browser(service=service, preferred_browser=preferred_browser)
            return ep.url
        raise RuntimeError(
            "No live Chrome/CocCoc/Edge/GPM CDP port detected on 127.0.0.1. "
            "Pass `auto_launch=True`, set `TIERFORGE_AUTO_LAUNCH_BROWSER=1`, "
            "or run `python scripts/browser_bridge.py ensure`."
        )

    if service.lower() in ("flow", "gemini", "chatgpt", "claude"):
        for ep in reversed(endpoints):
            if ep.has_service(service):
                return ep.url

    return endpoints[-1].url


class ThreeTierRouter:
    """Unified 3-Tier execution router: Tier-0 Local Code -> Tier-0.5 Ollama -> Tier-1 Web AI CDP."""

    def __init__(
        self,
        ollama_base_url: Optional[str] = None,
        ollama_model: Optional[str] = None,
        preferred_browser: Optional[str] = None,
    ) -> None:
        self.ollama_base_url = (
            ollama_base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
        ).rstrip("/")
        if self.ollama_base_url.endswith("/v1"):
            self.ollama_base_url = self.ollama_base_url[:-3]
        self.ollama_model = ollama_model or os.getenv("OLLAMA_MODEL", "qwen2.5:7b")
        self.preferred_browser = preferred_browser or os.getenv("TIERFORGE_BROWSER")

    def run_tier0_local(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Tier 0 ($0): Deterministic local Python/FFmpeg execution."""
        return fn(*args, **kwargs)

    def check_tier05_ollama(self, timeout: float = 2.0) -> bool:
        """Check whether local Ollama (Tier 0.5, $0) is alive."""
        try:
            with urllib.request.urlopen(f"{self.ollama_base_url}/api/tags", timeout=timeout) as resp:
                return getattr(resp, "status", 200) == 200
        except Exception:
            return False

    def run_tier05_ollama(
        self,
        prompt: str,
        *,
        system: str = "",
        keep_alive: int = 0,
        timeout: float = 60.0,
    ) -> str:
        """Tier 0.5 ($0): Offline local LLM via Ollama with automatic VRAM release (`keep_alive=0`)."""
        payload: dict[str, Any] = {
            "model": self.ollama_model,
            "prompt": prompt,
            "stream": False,
            "keep_alive": keep_alive,
        }
        if system:
            payload["system"] = system
        req = urllib.request.Request(
            f"{self.ollama_base_url}/api/generate",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return str(body.get("response", "")).strip()

    def get_tier1_cdp_url(
        self,
        service: str = "gemini",
        *,
        auto_launch: bool = True,
        preferred_browser: Optional[str] = None,
    ) -> str:
        """Tier 1 ($0 Antigravity tokens): Auto-resolve or zero-touch launch local browser CDP URL."""
        return resolve_cdp_url(
            service=service,
            auto_launch=auto_launch,
            preferred_browser=preferred_browser or self.preferred_browser,
        )

    def run_tier1_extension(
        self,
        prompt: str,
        *,
        service: str = "gemini",
        timeout_sec: float = 120.0,
    ) -> str:
        """Tier 1 Layer-3 ($0): Dispatch prompt to user's open browser via TierForge MV3 Extension."""
        from core.extension_bridge import ExtensionBridgeClient

        client = ExtensionBridgeClient()
        return client.dispatch_prompt(prompt, service=service, timeout_sec=timeout_sec)

    def health_snapshot(self) -> dict[str, Any]:
        """Return live availability of all 3 tiers + Extension Bridge + installed Chromium browsers."""
        eps = discover_cdp_endpoints()
        browsers = detect_installed_browsers()
        ext_ready = False
        ext_browsers: list[str] = []
        try:
            from core.extension_bridge import ExtensionBridgeClient

            ext_h = ExtensionBridgeClient().health(timeout=0.5)
            if ext_h and ext_h.get("ok"):
                ext_ready = int(ext_h.get("connected_count", 0)) > 0
                ext_browsers = [str(b.get("browser", "")) for b in ext_h.get("browsers", [])]
        except Exception:
            pass
        return {
            "tier0_local": True,
            "tier05_ollama": self.check_tier05_ollama(),
            "tier1_extension_bridge_ready": ext_ready,
            "tier1_extension_browsers": ext_browsers,
            "tier1_cdp_ports": [ep.port for ep in eps],
            "tier1_gemini_ready": ext_ready or any(ep.has_service("gemini") for ep in eps),
            "tier1_flow_ready": any(ep.has_service("flow") for ep in eps),
            "installed_browsers": [b.name for b in browsers],
            "managed_profile_dir": str(get_default_profile_dir()),
        }
