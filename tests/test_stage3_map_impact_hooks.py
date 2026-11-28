"""Unit & integration tests for Stage 3 (v0.7.0) Repo Map, Impact Guard, Sandbox & Hooks.

Covers:
  - core/repo_map.py (AST/regex extraction, hub ranking, token budget cap, SQLite mtime cache)
  - core/impact_guard.py (reverse dependency graph, related tests, GREEN/YELLOW/RED risk tiers)
  - core/subprocess_runner.py (secret env stripping & sandbox_root traversal guard)
  - core/hooks_engine.py (pre_command, post_edit, pre_commit secret/doc-drift checks, git/claude install)
  - scripts/repo_map.py, scripts/hooks.py CLI & scripts/mcp_server.py::tool_workspace_guard
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from core.hooks_engine import HooksEngine
from core.impact_guard import assess_impact
from core.repo_map import build_repo_map
from core.subprocess_runner import run_isolated, sanitize_child_env
from scripts import cli
from scripts.mcp_server import tool_workspace_guard

# ---------------------------------------------------------------------------
# 1. Token-Budgeted Repo Map & SQLite Cache
# ---------------------------------------------------------------------------


def test_repo_map_ranks_hub_modules_and_uses_sqlite_cache(tmp_path: Path) -> None:
    (tmp_path / "base.py").write_text(
        "class Engine:\n    def start(self):\n        pass\n\ndef core_helper(x, y):\n    return x + y\n",
        encoding="utf-8",
    )
    for name in ("svc_a", "svc_b", "svc_c"):
        (tmp_path / f"{name}.py").write_text(
            f"import base\n\ndef run_{name}():\n    return base.core_helper(1, 2)\n",
            encoding="utf-8",
        )
    (tmp_path / "web.ts").write_text(
        "export function renderCard(id: string) { return id; }\n",
        encoding="utf-8",
    )

    rep1 = build_repo_map(tmp_path, token_budget=800, use_cache=True)
    assert rep1.total_files == 5
    assert rep1.cache_hits == 0
    assert rep1.files[0].rel_path == "base.py"
    assert rep1.files[0].inbound_refs == 3
    assert "def core_helper(x, y)" in rep1.markdown
    assert "renderCard" in rep1.markdown

    # Second run hits SQLite mtime cache for all unchanged files
    rep2 = build_repo_map(tmp_path, token_budget=800, use_cache=True)
    assert rep2.cache_hits == 5
    assert rep2.estimated_tokens <= 820


# ---------------------------------------------------------------------------
# 2. Blast-Radius Impact Guard (GREEN / YELLOW / RED)
# ---------------------------------------------------------------------------


def test_impact_guard_classifies_green_yellow_and_red(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "pkg" / "leaf.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests" / "test_leaf.py").write_text("from pkg import leaf\n", encoding="utf-8")

    (tmp_path / "pkg" / "shared.py").write_text("X = 1\n", encoding="utf-8")
    for i in range(4):
        (tmp_path / "pkg" / f"user_{i}.py").write_text("from pkg import shared\n", encoding="utf-8")

    (tmp_path / "pkg" / "policy_guard.py").write_text("SAFE = True\n", encoding="utf-8")

    green = assess_impact(tmp_path, ["pkg/leaf.py"])
    assert green.risk_level == "GREEN"
    assert green.requires_human_approval is False
    assert "tests/test_leaf.py" in green.related_tests

    yellow = assess_impact(tmp_path, ["pkg/shared.py"])
    assert yellow.risk_level == "YELLOW"
    assert len(yellow.direct_dependents) == 4

    red = assess_impact(tmp_path, ["pkg/policy_guard.py"])
    assert red.risk_level == "RED"
    assert red.requires_human_approval is True


# ---------------------------------------------------------------------------
# 3. Subprocess Secret Env Stripping & Sandbox Boundary Enforcement
# ---------------------------------------------------------------------------


def test_subprocess_secret_env_sanitization_and_sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cleaned = sanitize_child_env(
        {
            "PATH": "/usr/bin",
            "OPENAI_API_KEY": "sk-proj-topsecret1234567890",
            "GITHUB_TOKEN": "ghp_secret1234567890abcdef",
            "SAFE_FLAG": "yes",
        },
        explicit_overrides={"EXPLICIT_API_KEY": "allowed_when_explicit"},
        strip_secrets=True,
    )
    assert "OPENAI_API_KEY" not in cleaned
    assert "GITHUB_TOKEN" not in cleaned
    assert cleaned["SAFE_FLAG"] == "yes"
    assert cleaned["EXPLICIT_API_KEY"] == "allowed_when_explicit"

    repo = tmp_path / "sandbox_proj"
    repo.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    # Escaping cwd must raise PermissionError
    with pytest.raises(PermissionError, match="Sandbox violation"):
        run_isolated([sys.executable, "-c", "print('hi')"], cwd=outside, sandbox_root=repo)

    # Escaping argument must raise PermissionError
    with pytest.raises(PermissionError, match="Sandbox violation"):
        run_isolated([sys.executable, "../outside/run.py"], cwd=repo, sandbox_root=repo)

    # Inside sandbox succeeds and strips inherited secret env
    monkeypatch.setenv("TEST_LEAK_API_KEY", "sk-proj-shouldnotpass12345")
    res = run_isolated(
        [sys.executable, "-c", "import os; print(os.getenv('TEST_LEAK_API_KEY', 'STRIPPED'))"],
        cwd=repo,
        sandbox_root=repo,
        strip_secret_env=True,
    )
    assert res.returncode == 0
    assert "STRIPPED" in res.stdout_trimmed


# ---------------------------------------------------------------------------
# 4. Hooks Engine, CLI (`vieforge map` / `vieforge hooks`) & MCP `workspace_guard`
# ---------------------------------------------------------------------------


def test_hooks_engine_pre_command_post_edit_pre_commit_and_install(tmp_path: Path) -> None:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    engine = HooksEngine(tmp_path)
    cfg_path = engine.init_config()
    assert cfg_path.is_file()

    # pre_command blocks destructive command and allows pytest
    blocked = engine.run_pre_command("rm -rf /")
    assert blocked.passed is False
    allowed = engine.run_pre_command("pytest -q")
    assert allowed.passed is True

    # post_edit flags Python syntax error
    broken = tmp_path / "broken.py"
    broken.write_text("def oops(:\n    pass\n", encoding="utf-8")
    post_bad = engine.run_post_edit(["broken.py"])
    assert post_bad.passed is False

    # pre_commit flags hardcoded secret and stale doc marker
    leaked = tmp_path / "cfg.py"
    leaked.write_text("KEY = 'sk-proj-supersecretkey1234567890'\n", encoding="utf-8")
    stale_doc = tmp_path / "NOTES.md"
    stale_doc.write_text("<!--measure: score=100 @2020-01-01-->\n", encoding="utf-8")
    pre_bad = engine.run_pre_commit(["cfg.py", "NOTES.md"])
    assert pre_bad.passed is False
    by_name = {c.name: c.passed for c in pre_bad.checks}
    assert by_name["secret_scan"] is False
    assert by_name["doc_drift"] is False

    # Install Git pre-commit hook & Claude Code snippet
    inst = engine.install_git_pre_commit(dry_run=False)
    assert Path(inst["hook_path"]).is_file()
    claude_cfg = engine.generate_claudecode_hooks_snippet()
    assert "PreToolUse" in claude_cfg["hooks"]


def test_cli_map_hooks_and_mcp_workspace_guard(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "alpha.py").write_text("def hello(name):\n    return name\n", encoding="utf-8")

    assert cli.main(["map", "--repo", str(tmp_path), "--budget", "600", "--json"]) == 0
    map_out = json.loads(capsys.readouterr().out)
    assert map_out["included_files"] == 1

    assert cli.main(["map", "--repo", str(tmp_path), "--impact", "alpha.py", "--json"]) == 0
    imp_out = json.loads(capsys.readouterr().out)
    assert imp_out["risk_level"] == "GREEN"

    assert cli.main(["hooks", "--repo", str(tmp_path), "--json", "run", "post_edit", "--file", "alpha.py"]) == 0
    hook_out = json.loads(capsys.readouterr().out)
    assert hook_out["passed"] is True

    # Read-only MCP tool_workspace_guard
    mcp_map = json.loads(tool_workspace_guard("map", repo_root=str(tmp_path), token_budget=600))
    assert mcp_map["action"] == "map"
    assert "alpha.py" in mcp_map["markdown"]

    mcp_imp = json.loads(
        tool_workspace_guard("impact", repo_root=str(tmp_path), targets_json='["alpha.py"]')
    )
    assert mcp_imp["risk_level"] == "GREEN"
