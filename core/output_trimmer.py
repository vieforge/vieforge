"""Clean-Room Output Trimmer & Token-Savings Counter (MIT, 100% original stdlib code).

Compresses verbose Tier 2/3 stdout/stderr before returning context to Tier 1 Cloud Agents:
  - Strips ANSI escape codes and resolves carriage-return progress overwrites.
  - Filters noisy progress-bar lines (tqdm/pip/download percentage spam).
  - Redacts common API keys / Bearer tokens / secrets before text leaves the machine.
  - Deduplicates consecutive identical lines.
  - Caps Python tracebacks to the last N lines (default: 20).
  - Preserves high-signal error lines (`FAILED`, `ERROR`, `AssertionError`, `FATAL`) even
    when truncating the middle of huge logs.
  - Returns exact character reduction metrics for Savings Receipts.
"""

from __future__ import annotations

import json
import re
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
    r"panic:|UnhandledPromiseRejection|ECONNREFUSED|exit code [1-9])",
    re.IGNORECASE,
)

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
        )

    lines = _strip_ansi_and_cr(raw_text)
    lines = [ln for ln in lines if not PROGRESS_BAR_RE.match(ln)]
    lines = _dedupe_consecutive(lines)
    lines, tb_trimmed = _cap_tracebacks(lines, max_tb_lines=max(4, max_traceback_lines))

    truncated = False
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
    )


def compact_job_summary(summary: dict[str, Any]) -> str:
    """Serialize a job summary dict into minimal-whitespace JSON for Tier 1."""
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
