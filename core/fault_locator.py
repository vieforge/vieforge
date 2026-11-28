"""Hierarchical Fault Locator & AST Context Slicer (100% Python stdlib, MIT clean-room).

Turns noisy test/runtime failure logs into a compact, high-precision Error Brief (< 500 tokens):
  1. Parses multi-language failure frames (Python tracebacks, pytest, ruff/mypy, JS/TS, Go, Rust).
  2. Computes a stable Error Fingerprint (SHA-256 prefix) for loop detection and error memory lookup.
  3. Uses Python `ast` to slice only the exact enclosing function/class + imports around the failing line
     (falling back cleanly to a tight line window for non-Python or syntax-broken files).
  4. Optionally attaches callers inside the repository and the last 3 git commits touching that file.
"""

from __future__ import annotations

import ast
import hashlib
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from core.output_trimmer import redact_secrets_text

# Frame parsers across common ecosystems
_PY_TB_FRAME_RE = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<symbol>[^\s]+))?'
)
_PYTEST_LOC_RE = re.compile(
    r"^(?P<file>[^\s:]+\.py):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?:in (?P<symbol>[A-Za-z_][A-Za-z0-9_]*)|(?P<detail>.+))$"
)
_JS_FRAME_PAREN_RE = re.compile(
    r"^\s*at\s+(?P<symbol>[^\s(]+)\s+\((?P<file>[^():]+):(?P<line>\d+):\d+\)"
)
_JS_FRAME_BARE_RE = re.compile(
    r"^\s*at\s+(?P<file>[^():\s]+):(?P<line>\d+):\d+"
)
_GO_FRAME_RE = re.compile(
    r"^\s*(?P<file>[^\s:]+\.go):(?P<line>\d+)(?:\s+\+0x[0-9a-fA-F]+)?"
)
_RUST_FRAME_RE = re.compile(
    r"^\s*(?:-->|at)\s+(?P<file>[^\s:]+\.rs):(?P<line>\d+)(?::\d+)?"
)

_ERROR_LINE_RE = re.compile(
    r"^\s*(?:E\s+)?(?P<etype>[A-Z][A-Za-z0-9_]*(?:Error|Exception|Warning|Panic)|"
    r"panic|FATAL|AssertionError)(?:\s*[:\-]\s*(?P<emsg>.*))?$"
)
_HEX_ADDR_RE = re.compile(r"0x[0-9a-fA-F]+")
_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "node_modules",
    "dist",
    "build",
    ".vieforge",
}
_VENDOR_PATH_MARKERS = ("site-packages", "dist-packages", "node_modules", "<frozen ")


@dataclass
class FaultFrame:
    file_path: str
    line: int
    symbol: str = ""
    language: str = "python"
    is_project_file: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LocatedSymbol:
    file_path: str
    symbol_name: str
    symbol_kind: str
    start_line: int
    end_line: int
    error_line: int
    snippet: str
    imports: list[str] = field(default_factory=list)
    callers: list[str] = field(default_factory=list)
    recent_git_changes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class FaultLocationReport:
    error_type: str
    error_message: str
    fingerprint: str
    frames: list[FaultFrame] = field(default_factory=list)
    located_symbols: list[LocatedSymbol] = field(default_factory=list)
    estimated_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_type": self.error_type,
            "error_message": self.error_message,
            "fingerprint": self.fingerprint,
            "frames": [f.to_dict() for f in self.frames],
            "located_symbols": [s.to_dict() for s in self.located_symbols],
            "estimated_tokens": self.estimated_tokens,
        }

    def to_markdown(self) -> str:
        lines: list[str] = [
            f"## Fault Brief (`{self.fingerprint}`)",
            f"- **Error:** `{self.error_type}`: {self.error_message or '(no message)'}",
        ]
        if self.located_symbols:
            lines.append(f"- **Located targets:** {len(self.located_symbols)}")
            for sym in self.located_symbols:
                lines.append("")
                lines.append(
                    f"### `{sym.file_path}` :: `{sym.symbol_name}` ({sym.symbol_kind}, L{sym.start_line}-L{sym.end_line}, hit L{sym.error_line})"
                )
                if sym.imports:
                    lines.append("- **Imports:** " + "; ".join(sym.imports[:6]))
                if sym.callers:
                    lines.append("- **Callers:** " + ", ".join(sym.callers[:5]))
                if sym.recent_git_changes:
                    lines.append("- **Recent commits:** " + " | ".join(sym.recent_git_changes[:3]))
                lines.append("```")
                lines.append(sym.snippet)
                lines.append("```")
        elif self.frames:
            lines.append("- **Parsed frames (outside repo or unreadable):**")
            for fr in self.frames[-4:]:
                lines.append(f"  - `{fr.file_path}:{fr.line}` ({fr.symbol or fr.language})")
        else:
            lines.append("- **Frames:** none parsed from log.")
        return "\n".join(lines)


def _normalize_repo_relpath(raw_path: str, repo_root: Path) -> tuple[str, bool]:
    """Resolve `raw_path` relative to `repo_root` when possible and flag project ownership."""
    cleaned = raw_path.strip().strip('"').strip("'")
    if not cleaned or any(m in cleaned for m in _VENDOR_PATH_MARKERS):
        return cleaned.replace("\\", "/"), False

    cand = Path(cleaned)
    resolved_root = repo_root.resolve()
    try:
        target = cand.resolve() if cand.is_absolute() else (resolved_root / cand).resolve()
        rel = target.relative_to(resolved_root)
        return str(rel).replace("\\", "/"), target.is_file()
    except (OSError, ValueError):
        return cleaned.replace("\\", "/"), False


def extract_error_headline(log_text: str) -> tuple[str, str]:
    """Extract the most informative error type and message from raw/trimmed failure logs."""
    lines = [ln.strip() for ln in log_text.splitlines() if ln.strip()]
    for ln in reversed(lines):
        m = _ERROR_LINE_RE.match(ln)
        if m:
            etype = m.group("etype")
            emsg = (m.group("emsg") or "").strip()
            safe_msg, _ = redact_secrets_text(emsg)
            return etype, safe_msg[:240]
    for ln in reversed(lines):
        if "FAILED" in ln or "ERROR" in ln or "error:" in ln.lower():
            safe_ln, _ = redact_secrets_text(ln)
            return "UnknownError", safe_ln[:240]
    return "UnknownError", ""


def parse_error_frames(log_text: str, repo_root: Path | str = ".") -> list[FaultFrame]:
    """Parse ordered stack/diagnostic frames from multi-language failure logs."""
    root = Path(repo_root).resolve()
    frames: list[FaultFrame] = []
    seen: set[tuple[str, int]] = set()

    for line in log_text.splitlines():
        matched: tuple[str, int, str, str] | None = None
        m = _PY_TB_FRAME_RE.match(line)
        if m or (m := _PYTEST_LOC_RE.match(line)) is not None:
            matched = (m.group("file"), int(m.group("line")), m.group("symbol") or "", "python")
        elif (m := _JS_FRAME_PAREN_RE.match(line)) is not None:
            matched = (m.group("file"), int(m.group("line")), m.group("symbol") or "", "javascript")
        elif (m := _JS_FRAME_BARE_RE.match(line)) is not None:
            matched = (m.group("file"), int(m.group("line")), "", "javascript")
        elif (m := _GO_FRAME_RE.match(line)) is not None:
            matched = (m.group("file"), int(m.group("line")), "", "go")
        elif (m := _RUST_FRAME_RE.match(line)) is not None:
            matched = (m.group("file"), int(m.group("line")), "", "rust")

        if matched is None:
            continue

        raw_file, lineno, sym, lang = matched
        rel_path, is_proj = _normalize_repo_relpath(raw_file, root)
        key = (rel_path, lineno)
        if key in seen:
            continue
        seen.add(key)
        frames.append(
            FaultFrame(
                file_path=rel_path,
                line=lineno,
                symbol=sym,
                language=lang,
                is_project_file=is_proj,
            )
        )
    return frames


def compute_error_fingerprint(
    error_type: str,
    error_message: str,
    frames: list[FaultFrame],
) -> str:
    """Compute a deterministic 12-char SHA-256 fingerprint for loop detection and error memory."""
    norm_msg = _HEX_ADDR_RE.sub("0xADDR", error_message.strip())
    proj_frames = [f for f in frames if f.is_project_file]
    anchor_frames = proj_frames[-2:] if proj_frames else frames[-2:]
    frame_sig = "|".join(f"{f.file_path}:{f.line}:{f.symbol}" for f in anchor_frames)
    payload = f"{error_type.strip()}|{norm_msg[:160]}|{frame_sig}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def _format_numbered_snippet(lines: list[str], start_line: int, end_line: int, error_line: int) -> str:
    rendered: list[str] = []
    for idx in range(start_line, end_line + 1):
        marker = ">>>" if idx == error_line else "   "
        content = lines[idx - 1] if 1 <= idx <= len(lines) else ""
        rendered.append(f"{marker} {idx:>4d} | {content}")
    return "\n".join(rendered)


def extract_symbol_context(
    file_path: Path,
    error_line: int,
    *,
    rel_display_path: str = "",
    max_snippet_lines: int = 45,
) -> LocatedSymbol:
    """Extract the narrowest AST function/class enclosing `error_line` (or a tight window)."""
    display_path = rel_display_path or str(file_path).replace("\\", "/")
    source = file_path.read_text(encoding="utf-8", errors="replace")
    all_lines = source.splitlines()
    total_lines = max(1, len(all_lines))
    clamped_line = max(1, min(error_line, total_lines))

    imports: list[str] = []
    symbol_name = "<module>"
    symbol_kind = "window"
    start_line = max(1, clamped_line - 12)
    end_line = min(total_lines, clamped_line + 12)

    if file_path.suffix.lower() == ".py":
        try:
            tree = ast.parse(source, filename=str(file_path))
            for node in tree.body:
                if isinstance(node, (ast.Import, ast.ImportFrom)) and hasattr(node, "lineno"):
                    imp_text = all_lines[node.lineno - 1].strip()
                    if imp_text:
                        imports.append(imp_text)

            best_node: ast.AST | None = None
            best_span = total_lines + 1
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    n_start = getattr(node, "lineno", 0)
                    n_end = getattr(node, "end_lineno", n_start) or n_start
                    if n_start <= clamped_line <= n_end:
                        span = n_end - n_start
                        if span < best_span:
                            best_span = span
                            best_node = node

            if best_node is not None:
                symbol_name = getattr(best_node, "name", "<unknown>")
                symbol_kind = "class" if isinstance(best_node, ast.ClassDef) else "function"
                start_line = getattr(best_node, "lineno", start_line)
                end_line = getattr(best_node, "end_lineno", end_line) or end_line
        except SyntaxError:
            symbol_kind = "syntax_error_window"

    # Keep snippet tightly bounded around `clamped_line` if enclosing symbol is huge
    if (end_line - start_line + 1) > max_snippet_lines:
        half = max_snippet_lines // 2
        start_line = max(start_line, clamped_line - half)
        end_line = min(end_line, start_line + max_snippet_lines - 1)

    snippet = _format_numbered_snippet(all_lines, start_line, end_line, clamped_line)
    safe_snippet, _ = redact_secrets_text(snippet)
    return LocatedSymbol(
        file_path=display_path,
        symbol_name=symbol_name,
        symbol_kind=symbol_kind,
        start_line=start_line,
        end_line=end_line,
        error_line=clamped_line,
        snippet=safe_snippet,
        imports=imports[:10],
    )


def find_symbol_callers(
    repo_root: Path,
    symbol_name: str,
    *,
    exclude_relpath: str = "",
    max_callers: int = 5,
) -> list[str]:
    """Find lightweight repo references calling `symbol_name(...)` without third-party deps."""
    if not symbol_name or symbol_name.startswith("<") or len(symbol_name) < 3:
        return []
    call_re = re.compile(rf"\b{re.escape(symbol_name)}\s*\(")
    callers: list[str] = []
    root = repo_root.resolve()

    for py_file in sorted(root.rglob("*.py")):
        if any(part in _SKIP_DIRS for part in py_file.parts):
            continue
        rel = str(py_file.relative_to(root)).replace("\\", "/")
        if rel == exclude_relpath:
            continue
        try:
            text = py_file.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for idx, ln in enumerate(text.splitlines(), start=1):
            stripped = ln.strip()
            if stripped.startswith(("def ", "async def ", "class ", "#")):
                continue
            if call_re.search(ln):
                callers.append(f"{rel}:{idx}")
                break
        if len(callers) >= max_callers:
            break
    return callers


def get_recent_git_context(
    repo_root: Path,
    rel_file: str,
    *,
    max_commits: int = 3,
) -> list[str]:
    """Return up to `max_commits` recent one-line git commits touching `rel_file`."""
    if not (repo_root / ".git").exists():
        return []
    try:
        res = subprocess.run(
            ["git", "log", f"-n{max_commits}", "--format=%h %s", "--", rel_file],
            cwd=repo_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if res.returncode != 0:
        return []
    return [ln.strip() for ln in res.stdout.splitlines() if ln.strip()]


def locate_fault(
    log_text: str,
    repo_root: Path | str = ".",
    *,
    include_git: bool = True,
    include_callers: bool = True,
    max_symbols: int = 3,
    max_snippet_lines: int = 45,
) -> FaultLocationReport:
    """Build a compact hierarchical fault localization brief from raw/trimmed failure logs."""
    root = Path(repo_root).resolve()
    error_type, error_message = extract_error_headline(log_text)
    frames = parse_error_frames(log_text, repo_root=root)
    fingerprint = compute_error_fingerprint(error_type, error_message, frames)

    # Prioritize deepest (most recent) project frames first
    project_frames = [f for f in reversed(frames) if f.is_project_file]
    located: list[LocatedSymbol] = []
    seen_targets: set[tuple[str, int]] = set()

    for fr in project_frames:
        key = (fr.file_path, fr.line)
        if key in seen_targets:
            continue
        seen_targets.add(key)
        abs_path = root / fr.file_path
        if not abs_path.is_file():
            continue
        sym = extract_symbol_context(
            abs_path,
            fr.line,
            rel_display_path=fr.file_path,
            max_snippet_lines=max_snippet_lines,
        )
        if include_callers and sym.symbol_name and not sym.symbol_name.startswith("<"):
            sym.callers = find_symbol_callers(
                root,
                sym.symbol_name,
                exclude_relpath=fr.file_path,
            )
        if include_git:
            sym.recent_git_changes = get_recent_git_context(root, fr.file_path)
        located.append(sym)
        if len(located) >= max_symbols:
            break

    report = FaultLocationReport(
        error_type=error_type,
        error_message=error_message,
        fingerprint=fingerprint,
        frames=frames,
        located_symbols=located,
    )
    md = report.to_markdown()
    report.estimated_tokens = max(1, (len(md) + 3) // 4)
    return report
