# Contributing to TierForge Harness

Thank you for helping make local-first AI agent orchestration faster, safer, and more accessible!

## Non-Negotiable Engineering Rules
All pull requests must pass automated CI (`pytest`, `ruff check`, `core/license_audit.py`, and `scripts/audit_step.py`) and strictly respect these 4 rules:

1. **100% Python Stdlib in `core/`:** Do not introduce mandatory third-party runtime dependencies in `core/`. Optional extras (like `mcp`) belong in `pyproject.toml` under `[project.optional-dependencies]`.
2. **Clean-Room MIT Licensing:** Never embed, vendor, or depend on `BUSL-1.1` (e.g., standard `rtk-engine`/`rtk-proxy`), `AGPL-3.0`, or `CC-BY-NC` code or weights.
3. **Zero OS-Mutation Policy:** Never add scripts that edit Windows Registry, OS services, system pagefiles, or network stacks.
4. **Cross-Platform & Domain-Agnostic:** Code must work cleanly on Windows, macOS, and Linux with zero hardcoded personal paths or private business logic.

## Local Development Workflow

```bash
# Install dev tools (pytest + ruff)
uv sync --extra dev

# Run tests, linter, and automated step audit
uv run --extra dev ruff check .
uv run --extra dev pytest -v
uv run --extra dev python scripts/audit_step.py --step pr_check
```

## Documentation & Localization
- Default documentation is in international English (`README.md`).
- Vietnamese localization is maintained in `README.vi.md`; additional community translations (`README.<lang>.md`) are warmly welcomed.
