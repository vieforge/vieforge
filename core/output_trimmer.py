"""Clean-Room Output Trimmer & Token-Savings Counter (MIT, 100% original stdlib code).

Compresses verbose Tier 2/3 stdout/stderr before returning context to Tier 1 Cloud Agents:
  - Strips ANSI escape codes and resolves carriage-return progress overwrites.
  - Filters noisy progress-bar lines (tqdm/pip/download percentage spam).
  - Redacts common API keys / Bearer tokens / secrets before text leaves the machine.
  - Deduplicates consecutive identical lines.
  - Tool-aware smart compression (`auto`, `pytest`, `linter`, `js_stack`, `go_rust`, `generic`):
      * `pytest`: drops noisy PASSED lines, keeps FAILURES, assertions, and short summary.
      * `linter`: groups repeated ruff/mypy/flake8/tsc/eslint error codes with sample locations.
      * `js_stack` / `go_rust`: collapses repetitive node_modules/runtime frames while keeping project frames.
  - Caps Python tracebacks to the last N lines (default: 20).
  - Preserves high-signal error lines (`FAILED`, `ERROR`, `AssertionError`, `FATAL`) even
    when truncating the middle of huge logs.
  - Returns exact character reduction metrics for Savings Receipts.
"""

from __future__ import annotations

import json
import re
from collections import OrderedDict
from dataclasses import asdict, dataclass
from typing import Any

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
PROGRESS_BAR_RE = re.compile(
    r"^\s*(\d{1,3}%\|.*\|.*|[\[\(]?[=#\-\u2588\u2591\u2592\u2593]{4,}[\]\)]?.*\d+%.*|"
    r"(downloading|extracting|loading)\s+.*\d{1,3}%\s*)$",
    re.IGNORECASE,
)

# High-signal error patterns preserved even when middle lines are truncated
HIGH_SIGNAL_ERROR_RE = re.compile(
    r"(?:^|\b)(?:ERROR|FAILED|FATAL|CRITICAL|Traceback|AssertionError|SyntaxError|"
    r"TypeError|ValueError|RuntimeError|ModuleNotFoundError|ImportError|KeyError|"
    r"ReferenceError|RangeError|panic:|UnhandledPromiseRejection|ECONNREFUSED|"
    r"exit code [1-9]|TS\d{4}:|error\[E\d{4}\]|thread .* panicked)",
    re.IGNORECASE,
)

# Tool-specific detection and parsing regexes
_PYTEST_MARKER_RE = re.compile(
    r"(?:={3,}\s*(?:test session starts|FAILURES|ERRORS|short test summary info)\s*={3,}|"
    r"^\s*(?:PASSED|FAILED|ERROR)\s+\S+::\S+)",
    re.IGNORECASE,
)
_PYTEST_PASSED_LINE_RE = re.compile(
    r"^\s*(?:PASSED\s+\S+|\S+::\S+\s+PASSED(?:\s+\[\s*\d+%\])?)\s*$"
)
_LINTER_PY_RE = re.compile(
    r"^(?P<file>[^:\s][^:]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*"
    r"(?:(?P<code>[A-Z]{1,4}\d{2,5})\s+(?P<msg>.+)|(?P<sev>error|warning|note):\s*(?P<msg2>.+?)(?:\s+\[(?P<code2>[^\]]+)\])?)$"
)
_LINTER_TS_RE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\):\s*(?P<sev>error|warning)\s+(?P<code>TS\d+):\s*(?P<msg>.+)$"
)
_JS_FRAME_RE = re.compile(r"^\s+at\s+.+:\d+:\d+\)?\s*$")
_JS_NODE_MODULES_RE = re.compile(r"(?:node_modules[/\\]|node:internal/|internal/modules/)")
_GO_RUST_PANIC_RE = re.compile(r"(?:^panic:|^\s*goroutine\s+\d+\s+\[|^thread\s+'.+'\s+panicked\s+at)")

# Clean-room secret redaction patterns (prevents accidental credential leaks to Cloud Agents)
_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(?:sk-(?:proj-|ant-)?[A-Za-z0-9_-]{16,})\b"), "[REDACTED_API_KEY]"),
    (re.compile(r"\b(?:(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"\bAIza[0-9A-Za-z\-_]{25,}\b"), "[REDACTED_GOOGLE_KEY]"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "[REDACTED_AWS_KEY]"),
    (re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"(?i)(Bearer\s+)[A-Za-z0-9\-._~+/=]{16,}"), r"\1[REDACTED]"),
    (
        re.compile(
            r"(?i)(\b[A-Z0-9_]*(?:API_KEY|SECRET_KEY|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD)\b\s*[:=]\s*['\"]?)"
            r"(?!\[REDACTED|your_|example|placeholder|ollama\b)[A-Za-z0-9\-._~+/=]{12,}(['\"]?)"
        ),
        r"\1[REDACTED]\2",
    ),
)


@dataclass
class TrimResult:
    text: str
    raw_chars: int
    trimmed_chars: int
    saved_pct: float
    truncated: bool
    traceback_trimmed: bool
    secrets_redacted: int = 0
    detected_mode: str = "generic"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def redact_secrets_text(text: str) -> tuple[str, int]:
    """Redact common API keys, Bearer tokens, and secret env assignments in log text."""
    redacted = text
    total = 0
    for pattern, repl in _SECRET_PATTERNS:
        redacted, count = pattern.subn(repl, redacted)
        total += count
    return redacted, total


def _strip_ansi_and_cr(raw: str) -> list[str]:
    cleaned = ANSI_RE.sub("", raw)
    lines: list[str] = []
    for raw_line in cleaned.splitlines():
        if "\r" in raw_line:
            parts = [p for p in raw_line.split("\r") if p.strip()]
            raw_line = parts[-1] if parts else ""
        lines.append(raw_line.rstrip())
    return lines


def _dedupe_consecutive(lines: list[str]) -> list[str]:
    if not lines:
        return []
    out: list[str] = []
    prev = lines[0]
    count = 1
    for line in lines[1:]:
        if line == prev:
            count += 1
        else:
            out.append(f"{prev} (repeated {count}x)" if count > 2 else prev)
            if count == 2:
                out.append(prev)
            prev = line
            count = 1
    out.append(f"{prev} (repeated {count}x)" if count > 2 else prev)
    if count == 2:
        out.append(prev)
    return out


def detect_log_mode(lines: list[str]) -> str:
    """Fast O(sample) classifier for common developer tool outputs."""
    if not lines:
        return "generic"
    # Sample first 80 + last 40 lines for speed even on 10,000+ line logs
    sample = lines[:80] + (lines[-40:] if len(lines) > 80 else [])
    pytest_hits = 0
    linter_hits = 0
    js_hits = 0
    go_rust_hits = 0
    for ln in sample:
        if _PYTEST_MARKER_RE.search(ln) or _PYTEST_PASSED_LINE_RE.match(ln):
            pytest_hits += 1
        if _LINTER_PY_RE.match(ln) or _LINTER_TS_RE.match(ln):
            linter_hits += 1
        if _JS_FRAME_RE.match(ln):
            js_hits += 1
        if _GO_RUST_PANIC_RE.search(ln):
            go_rust_hits += 1

    if pytest_hits >= 2:
        return "pytest"
    if linter_hits >= 4:
        return "linter"
    if js_hits >= 2:
        return "js_stack"
    if go_rust_hits >= 1:
        return "go_rust"
    return "generic"


def _compress_pytest_lines(lines: list[str]) -> tuple[list[str], bool]:
    """Drop verbose PASSED test lines while preserving failures, assertions, and summary."""
    out: list[str] = []
    passed_omitted = 0
    for ln in lines:
        if _PYTEST_PASSED_LINE_RE.match(ln):
            passed_omitted += 1
            continue
        out.append(ln)
    if passed_omitted > 0:
        insert_idx = min(3, len(out))
        out.insert(insert_idx, f"[... {passed_omitted} PASSED pytest lines omitted by VieForge ...]")
        return out, True
    return lines, False


def _compress_linter_lines(lines: list[str], *, max_samples_per_code: int = 4) -> tuple[list[str], bool]:
    """Group repetitive ruff/mypy/flake8/tsc diagnostics by error code."""
    grouped: OrderedDict[str, dict[str, Any]] = OrderedDict()
    passthrough: list[str] = []
    matched_total = 0

    for ln in lines:
        m_py = _LINTER_PY_RE.match(ln)
        m_ts = _LINTER_TS_RE.match(ln) if m_py is None else None
        if m_py is not None:
            code = m_py.group("code") or m_py.group("code2") or m_py.group("sev") or "LINT"
            msg = (m_py.group("msg") or m_py.group("msg2") or "").strip()
            loc = f"{m_py.group('file')}:{m_py.group('line')}"
        elif m_ts is not None:
            code = m_ts.group("code")
            msg = m_ts.group("msg").strip()
            loc = f"{m_ts.group('file')}:{m_ts.group('line')}"
        else:
            passthrough.append(ln)
            continue

        matched_total += 1
        bucket = grouped.setdefault(code, {"msg": msg, "locs": [], "count": 0})
        bucket["count"] += 1
        if len(bucket["locs"]) < max_samples_per_code:
            bucket["locs"].append(loc)

    # Only compress when there are enough diagnostics to gain clarity & tokens
    if matched_total < 8 or matched_total <= len(grouped) + 2:
        return lines, False

    summary_lines: list[str] = [
        f"[VieForge linter summary: {matched_total} diagnostics grouped into {len(grouped)} codes]"
    ]
    for code, info in grouped.items():
        sample_locs = ", ".join(info["locs"])
        extra = info["count"] - len(info["locs"])
        suffix = f" (+{extra} more)" if extra > 0 else ""
        summary_lines.append(f"  {code} ({info['count']}x): {info['msg']} -> {sample_locs}{suffix}")

    for extra_ln in passthrough:
        if extra_ln.strip():
            summary_lines.append(extra_ln)
    return summary_lines, True


def _compress_js_stack(lines: list[str]) -> tuple[list[str], bool]:
    """Collapse repetitive node_modules / internal JS frames while keeping user frames."""
    out: list[str] = []
    skipped_vendor = 0
    changed = False
    for ln in lines:
        if _JS_FRAME_RE.match(ln) and _JS_NODE_MODULES_RE.search(ln):
            skipped_vendor += 1
            changed = True
            continue
        if skipped_vendor > 0:
            out.append(f"    [... {skipped_vendor} node_modules/internal frames collapsed ...]")
            skipped_vendor = 0
        out.append(ln)
    if skipped_vendor > 0:
        out.append(f"    [... {skipped_vendor} node_modules/internal frames collapsed ...]")
    return out, changed


def _compress_go_rust_stack(lines: list[str], max_stack_lines: int) -> tuple[list[str], bool]:
    """Keep panic header and top user frames; cap long Go/Rust runtime dumps."""
    panic_idx = -1
    for idx, ln in enumerate(lines):
        if _GO_RUST_PANIC_RE.search(ln):
            panic_idx = idx
            break
    if panic_idx < 0:
        return lines, False
    tail = lines[panic_idx:]
    if len(tail) <= max_stack_lines:
        return lines, False
    head_keep = max(4, max_stack_lines // 2)
    tail_keep = max(3, max_stack_lines - head_keep - 1)
    omitted = len(tail) - head_keep - tail_keep
    capped = (
        tail[:head_keep]
        + [f"  [... {omitted} Go/Rust stack frames trimmed by VieForge ...]"]
        + tail[-tail_keep:]
    )
    return lines[:panic_idx] + capped, True


def _cap_tracebacks(lines: list[str], max_tb_lines: int) -> tuple[list[str], bool]:
    """If a Python traceback is present, cap its body to the last `max_tb_lines` lines."""
    tb_indices = [
        idx for idx, ln in enumerate(lines) if ln.startswith("Traceback (most recent call last):")
    ]
    if not tb_indices:
        return lines, False

    last_tb_start = tb_indices[-1]
    prefix = lines[:last_tb_start]
    tb_block = lines[last_tb_start:]
    if len(tb_block) <= max_tb_lines:
        return lines, False

    omitted = len(tb_block) - max_tb_lines
    capped = [
        tb_block[0],
        f"  [... {omitted} traceback lines trimmed by TierForge ...]",
        *tb_block[-(max_tb_lines - 1) :],
    ]
    return prefix + capped, True


def trim_output(
    raw_text: str,
    *,
    max_lines: int = 60,
    max_chars: int = 4000,
    max_traceback_lines: int = 20,
    redact_secrets: bool = True,
    mode: str = "auto",
) -> TrimResult:
    """Compress raw worker stdout/stderr for low-token, secret-safe Tier 1 consumption."""
    raw_chars = len(raw_text)
    if raw_chars == 0:
        return TrimResult(
            text="",
            raw_chars=0,
            trimmed_chars=0,
            saved_pct=0.0,
            truncated=False,
            traceback_trimmed=False,
            secrets_redacted=0,
            detected_mode="generic",
        )

    lines = _strip_ansi_and_cr(raw_text)
    lines = [ln for ln in lines if not PROGRESS_BAR_RE.match(ln)]
    lines = _dedupe_consecutive(lines)

    norm_mode = (mode or "auto").strip().lower()
    detected = detect_log_mode(lines) if norm_mode == "auto" else norm_mode

    truncated = False
    tb_trimmed = False

    if detected == "pytest":
        lines, did_pytest = _compress_pytest_lines(lines)
        truncated = truncated or did_pytest
    elif detected == "linter":
        lines, did_lint = _compress_linter_lines(lines)
        truncated = truncated or did_lint
    elif detected == "js_stack":
        lines, did_js = _compress_js_stack(lines)
        tb_trimmed = tb_trimmed or did_js
    elif detected == "go_rust":
        lines, did_gr = _compress_go_rust_stack(lines, max_stack_lines=max(8, max_traceback_lines))
        tb_trimmed = tb_trimmed or did_gr

    lines, py_tb_trimmed = _cap_tracebacks(lines, max_tb_lines=max(4, max_traceback_lines))
    tb_trimmed = tb_trimmed or py_tb_trimmed

    if len(lines) > max_lines:
        head_n = max(4, max_lines // 3)
        tail_n = max(5, max_lines - head_n - 1)
        middle = lines[head_n : len(lines) - tail_n]

        # Retain high-signal error lines from the omitted middle so root causes are never lost
        max_retained = max(2, min(8, max_lines // 5))
        retained_errors = [ln for ln in middle if HIGH_SIGNAL_ERROR_RE.search(ln)][:max_retained]
        if retained_errors:
            head_n = max(3, head_n - len(retained_errors) // 2)
            tail_n = max(4, max_lines - head_n - len(retained_errors) - 2)
            middle_count = max(0, len(lines) - head_n - tail_n - len(retained_errors))
            lines = (
                lines[:head_n]
                + [f"[... {middle_count} non-error lines omitted; {len(retained_errors)} key error lines retained ...]"]
                + retained_errors
                + ["[... end of retained error lines ...]"]
                + lines[-tail_n:]
            )
        else:
            omitted = len(lines) - head_n - tail_n
            lines = lines[:head_n] + [f"[... {omitted} lines omitted ...]"] + lines[-tail_n:]
        truncated = True

    text = "\n".join(lines).strip()
    secrets_redacted = 0
    if redact_secrets and text:
        text, secrets_redacted = redact_secrets_text(text)

    if len(text) > max_chars:
        head_c = max(200, max_chars // 3)
        tail_c = max(200, max_chars - head_c - 40)
        omitted_c = len(text) - head_c - tail_c
        text = f"{text[:head_c]}\n[... {omitted_c} chars omitted ...]\n{text[-tail_c:]}"
        truncated = True

    trimmed_chars = len(text)
    saved_pct = (
        round(max(0.0, (raw_chars - trimmed_chars) / raw_chars) * 100.0, 1)
        if raw_chars > 0
        else 0.0
    )
    return TrimResult(
        text=text,
        raw_chars=raw_chars,
        trimmed_chars=trimmed_chars,
        saved_pct=saved_pct,
        truncated=truncated,
        traceback_trimmed=tb_trimmed,
        secrets_redacted=secrets_redacted,
        detected_mode=detected,
    )


def compact_job_summary(summary: dict[str, Any]) -> str:
    """Serialize a job summary dict into minimal-whitespace JSON for Tier 1."""
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
