"""Blast-Radius Dependency Impact Guard (GREEN / YELLOW / RED, 100% Python stdlib, MIT).

Before modifying files, computes the reverse-import dependency graph across the repository:
  - Identifies direct and transitive dependent modules.
  - Identifies related test files (`tests/test_*.py`) that exercise the target(s).
  - Classifies change risk into 3 actionable tiers:
      * GREEN  : Isolated edit -> fast targeted tests.
      * YELLOW : Shared module (>= 3 dependents or >= 3 targets) -> mandatory snapshot + full tests.
      * RED    : Security/policy/billing/CI/deploy files OR wide blast radius (>= 8 dependents)
                 -> requires explicit human approval before applying edits.
"""

from __future__ import annotations

import ast
import os
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

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

_RED_PATH_MARKERS = (
    ".github/workflows/",
    "policy_guard",
    "cost_tracker",
    "license_audit",
    "security",
    "dockerfile",
    "wrangler",
    "pyproject.toml",
    ".env",
    "publish",
)


@dataclass
class ImpactReport:
    target_files: list[str]
    risk_level: str  # "GREEN" | "YELLOW" | "RED"
    requires_human_approval: bool
    direct_dependents: list[str] = field(default_factory=list)
    transitive_dependents: list[str] = field(default_factory=list)
    related_tests: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    recommended_actions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _py_module_name(rel_path: str) -> str:
    norm = rel_path.replace("\\", "/")
    if norm.endswith("/__init__.py"):
        norm = norm[: -len("/__init__.py")]
    elif norm.endswith(".py"):
        norm = norm[:-3]
    return norm.replace("/", ".")


def _extract_py_imports(py_file: Path) -> set[str]:
    try:
        tree = ast.parse(py_file.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, SyntaxError):
        return set()
    mods: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                mods.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods.add(node.module)
            for alias in node.names:
                mods.add(f"{node.module}.{alias.name}")
    return mods


def build_reverse_dependency_graph(repo_root: Path | str) -> dict[str, set[str]]:
    """Map each Python file relpath -> set of Python file relpaths that import it."""
    root = Path(repo_root).resolve()
    py_files: list[tuple[str, Path]] = []
    mod_to_rel: dict[str, str] = {}

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = Path(dirpath) / fname
            rel = str(fpath.relative_to(root)).replace("\\", "/")
            py_files.append((rel, fpath))
            mod = _py_module_name(rel)
            mod_to_rel[mod] = rel

    reverse: dict[str, set[str]] = {rel: set() for rel, _ in py_files}
    for rel, fpath in py_files:
        for imp in _extract_py_imports(fpath):
            target = mod_to_rel.get(imp)
            if target is None and "." in imp:
                parent = imp.rsplit(".", 1)[0]
                target = mod_to_rel.get(parent)
            if target and target != rel:
                reverse.setdefault(target, set()).add(rel)
    return reverse


def _is_test_file(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    name = Path(norm).name
    return norm.startswith("tests/") or name.startswith("test_") or name.endswith("_test.py")


def assess_impact(
    repo_root: Path | str,
    target_files: list[str],
) -> ImpactReport:
    """Compute blast radius, related tests, and GREEN/YELLOW/RED risk tier for `target_files`."""
    root = Path(repo_root).resolve()
    norm_targets: list[str] = []
    for raw in target_files:
        p = Path(raw)
        if p.is_absolute():
            try:
                rel = str(p.resolve().relative_to(root)).replace("\\", "/")
            except ValueError:
                rel = raw.replace("\\", "/")
        else:
            rel = raw.replace("\\", "/").lstrip("./")
        if rel and rel not in norm_targets:
            norm_targets.append(rel)

    reverse = build_reverse_dependency_graph(root)
    direct: set[str] = set()
    for t in norm_targets:
        direct |= reverse.get(t, set())
    direct -= set(norm_targets)

    # BFS for transitive dependents
    visited: set[str] = set(norm_targets) | set(direct)
    queue: deque[str] = deque(direct)
    transitive: set[str] = set()
    while queue:
        cur = queue.popleft()
        for dep in reverse.get(cur, set()):
            if dep not in visited:
                visited.add(dep)
                transitive.add(dep)
                queue.append(dep)

    all_deps = direct | transitive
    related_tests = sorted(f for f in (all_deps | set(norm_targets)) if _is_test_file(f))
    non_test_deps = sorted(f for f in all_deps if not _is_test_file(f))

    reasons: list[str] = []
    red_hits = [
        t for t in norm_targets if any(marker in t.lower() for marker in _RED_PATH_MARKERS)
    ]
    if red_hits:
        reasons.append(f"Touches critical/protected path(s): {', '.join(red_hits)}")
    if len(non_test_deps) >= 8:
        reasons.append(f"Wide blast radius: {len(non_test_deps)} dependent code modules")

    if red_hits or len(non_test_deps) >= 8:
        risk_level = "RED"
        requires_approval = True
        actions = [
            "STOP and request explicit human approval before applying changes.",
            "Capture WorkspaceSnapshot checkpoint prior to editing.",
            "Run full pytest suite + `vieforge audit` after applying.",
        ]
    elif len(non_test_deps) >= 3 or len(norm_targets) >= 3:
        risk_level = "YELLOW"
        requires_approval = False
        reasons.append(
            f"Shared impact: {len(non_test_deps)} dependent code modules across {len(norm_targets)} target(s)"
        )
        actions = [
            "Capture WorkspaceSnapshot checkpoint prior to editing.",
            "Run related tests and verify full regression suite.",
        ]
    else:
        risk_level = "GREEN"
        requires_approval = False
        reasons.append("Isolated change with low dependency fan-out.")
        actions = (
            [f"Run targeted tests: pytest {' '.join(related_tests[:5])}"]
            if related_tests
            else ["Run syntax check and unit tests for modified module."]
        )

    return ImpactReport(
        target_files=norm_targets,
        risk_level=risk_level,
        requires_human_approval=requires_approval,
        direct_dependents=sorted(direct),
        transitive_dependents=sorted(transitive),
        related_tests=related_tests,
        reasons=reasons,
        recommended_actions=actions,
    )
