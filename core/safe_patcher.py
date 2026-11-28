"""Safe Narrow Patch Applier with Pre-Write Syntax Verification (100% Python stdlib, MIT).

Applies exact SEARCH/REPLACE edits cleanly without risky whole-file overwrites:
  - Strict repository containment (rejects path traversal outside `repo_root`).
  - Requires a unique exact match per edit block (refuses missing or ambiguous matches).
  - Validates resulting syntax IN MEMORY before touching disk:
      * `.py` files via `ast.parse()`
      * `.json` files via `json.loads()`
  - Supports `dry_run=True` to generate unified diffs for human approval before applying.
  - All-or-nothing batch validation: if any edit in a patch fails validation, zero files are written.
"""

from __future__ import annotations

import ast
import difflib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_BLOCK_RE = re.compile(
    r"(?:^|\n)FILE:\s*(?P<file>[^\r\n]+)\r?\n"
    r"<<<<<<< SEARCH\r?\n(?P<search>.*?)\r?\n=======\r?\n(?P<replace>.*?)\r?\n>>>>>>> REPLACE",
    re.DOTALL,
)


@dataclass
class PatchEdit:
    file_path: str
    search: str
    replace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatchFileResult:
    rel_path: str
    status: str  # "valid" | "applied" | "error"
    reason: str = ""
    unified_diff: str = ""
    syntax_checked: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PatchApplyReport:
    ok: bool
    dry_run: bool
    applied_files: list[str] = field(default_factory=list)
    results: list[PatchFileResult] = field(default_factory=list)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "dry_run": self.dry_run,
            "applied_files": self.applied_files,
            "results": [r.to_dict() for r in self.results],
            "error": self.error,
        }


def parse_search_replace_blocks(patch_text: str) -> list[PatchEdit]:
    """Parse one or more `FILE: ... / <<<<<<< SEARCH / ======= / >>>>>>> REPLACE` blocks."""
    edits: list[PatchEdit] = []
    for match in _BLOCK_RE.finditer(patch_text):
        rel = match.group("file").strip().strip('"').strip("'")
        edits.append(
            PatchEdit(
                file_path=rel,
                search=match.group("search"),
                replace=match.group("replace"),
            )
        )
    return edits


def validate_content_syntax(rel_path: str, content: str) -> tuple[bool, bool, str]:
    """Validate syntax in memory before disk write. Returns `(checked, ok, error_msg)`."""
    suffix = Path(rel_path).suffix.lower()
    if suffix == ".py":
        try:
            ast.parse(content, filename=rel_path)
            return True, True, ""
        except SyntaxError as exc:
            return True, False, f"Python SyntaxError at L{exc.lineno}: {exc.msg}"
    if suffix == ".json":
        try:
            json.loads(content)
            return True, True, ""
        except json.JSONDecodeError as exc:
            return True, False, f"JSON syntax error at L{exc.lineno}: {exc.msg}"
    return False, True, ""


class PatchApplier:
    """Validate and apply narrow SEARCH/REPLACE patches atomically inside `repo_root`."""

    def __init__(self, repo_root: Path | str = ".") -> None:
        self.repo_root = Path(repo_root).resolve()

    def _resolve_contained(self, target: str) -> tuple[Path, str]:
        p = Path(target.strip())
        abs_path = p.resolve() if p.is_absolute() else (self.repo_root / p).resolve()
        try:
            rel = abs_path.relative_to(self.repo_root)
        except ValueError as exc:
            raise ValueError(f"Patch target escapes repo root: {target}") from exc
        rel_str = str(rel).replace("\\", "/")
        if not rel_str or rel_str == ".":
            raise ValueError(f"Invalid patch target path: {target}")
        return abs_path, rel_str

    def apply(
        self,
        edits: list[PatchEdit],
        *,
        dry_run: bool = True,
    ) -> PatchApplyReport:
        """Validate all edits in memory, generate unified diffs, and optionally write to disk."""
        if not edits:
            return PatchApplyReport(ok=False, dry_run=dry_run, error="No patch edits provided.")

        staged_before: dict[str, str] = {}
        staged_after: dict[str, str] = {}
        abs_map: dict[str, Path] = {}
        results: list[PatchFileResult] = []

        # Step 1: Validate every edit in memory against current/staged file content
        for idx, edit in enumerate(edits, start=1):
            try:
                abs_path, rel_str = self._resolve_contained(edit.file_path)
            except ValueError as exc:
                results.append(PatchFileResult(rel_path=edit.file_path, status="error", reason=str(exc)))
                return PatchApplyReport(ok=False, dry_run=dry_run, results=results, error=str(exc))

            if not abs_path.is_file():
                msg = f"Target file not found: {rel_str}"
                results.append(PatchFileResult(rel_path=rel_str, status="error", reason=msg))
                return PatchApplyReport(ok=False, dry_run=dry_run, results=results, error=msg)

            if rel_str not in staged_before:
                original = abs_path.read_text(encoding="utf-8")
                staged_before[rel_str] = original
                staged_after[rel_str] = original
                abs_map[rel_str] = abs_path

            current = staged_after[rel_str]
            if not edit.search:
                msg = f"Edit #{idx} for {rel_str} has empty SEARCH block."
                results.append(PatchFileResult(rel_path=rel_str, status="error", reason=msg))
                return PatchApplyReport(ok=False, dry_run=dry_run, results=results, error=msg)

            count = current.count(edit.search)
            if count == 0:
                msg = f"Edit #{idx} SEARCH block not found in {rel_str}."
                results.append(PatchFileResult(rel_path=rel_str, status="error", reason=msg))
                return PatchApplyReport(ok=False, dry_run=dry_run, results=results, error=msg)
            if count > 1:
                msg = f"Edit #{idx} SEARCH block is ambiguous ({count} matches) in {rel_str}; include more context."
                results.append(PatchFileResult(rel_path=rel_str, status="error", reason=msg))
                return PatchApplyReport(ok=False, dry_run=dry_run, results=results, error=msg)

            staged_after[rel_str] = current.replace(edit.search, edit.replace, 1)

        # Step 2: Pre-write syntax verification + diff generation for every touched file
        for rel_str, new_content in staged_after.items():
            checked, syntax_ok, syntax_err = validate_content_syntax(rel_str, new_content)
            diff_lines = list(
                difflib.unified_diff(
                    staged_before[rel_str].splitlines(),
                    new_content.splitlines(),
                    fromfile=f"before/{rel_str}",
                    tofile=f"after/{rel_str}",
                    lineterm="",
                )
            )
            diff_text = "\n".join(diff_lines)
            if not syntax_ok:
                results.append(
                    PatchFileResult(
                        rel_path=rel_str,
                        status="error",
                        reason=syntax_err,
                        unified_diff=diff_text,
                        syntax_checked=checked,
                    )
                )
                return PatchApplyReport(
                    ok=False,
                    dry_run=dry_run,
                    results=results,
                    error=f"Pre-write syntax check failed for {rel_str}: {syntax_err}",
                )
            results.append(
                PatchFileResult(
                    rel_path=rel_str,
                    status="valid" if dry_run else "applied",
                    unified_diff=diff_text,
                    syntax_checked=checked,
                )
            )

        # Step 3: Commit to disk only when dry_run=False and all files validated cleanly
        applied_files: list[str] = []
        if not dry_run:
            for rel_str, new_content in staged_after.items():
                abs_map[rel_str].write_text(new_content, encoding="utf-8")
                applied_files.append(rel_str)

        return PatchApplyReport(
            ok=True,
            dry_run=dry_run,
            applied_files=applied_files,
            results=results,
        )
