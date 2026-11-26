"""Clean-Room Output Trimmer & Token-Savings Counter (MIT, 100% original stdlib code).

Compresses verbose Tier 2/3 stdout/stderr before returning context to Tier 1 Cloud Agents:
  - Strips ANSI escape codes and resolves carriage-return progress overwrites.
  - Filters noisy progress-bar lines (tqdm/pip/download percentage spam).
  - Deduplicates consecutive identical lines.
  - Caps Python tracebacks to the last N lines (default: 20).
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


@dataclass
class TrimResult:
    text: str
    raw_chars: int
    trimmed_chars: int
    saved_pct: float
    truncated: bool
    traceback_trimmed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
) -> TrimResult:
    """Compress raw worker stdout/stderr for low-token Tier 1 consumption."""
    raw_chars = len(raw_text)
    if raw_chars == 0:
        return TrimResult(
            text="",
            raw_chars=0,
            trimmed_chars=0,
            saved_pct=0.0,
            truncated=False,
            traceback_trimmed=False,
        )

    lines = _strip_ansi_and_cr(raw_text)
    lines = [ln for ln in lines if not PROGRESS_BAR_RE.match(ln)]
    lines = _dedupe_consecutive(lines)
    lines, tb_trimmed = _cap_tracebacks(lines, max_tb_lines=max(4, max_traceback_lines))

    truncated = False
    if len(lines) > max_lines:
        head_n = max(5, max_lines // 3)
        tail_n = max(5, max_lines - head_n - 1)
        omitted = len(lines) - head_n - tail_n
        lines = lines[:head_n] + [f"[... {omitted} lines omitted ...]"] + lines[-tail_n:]
        truncated = True

    text = "\n".join(lines).strip()
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
    )


def compact_job_summary(summary: dict[str, Any]) -> str:
    """Serialize a job summary dict into minimal-whitespace JSON for Tier 1."""
    return json.dumps(summary, ensure_ascii=False, separators=(",", ":"))
