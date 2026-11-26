## Summary
Briefly explain what this PR changes and why.

## Verification Checklist
- [ ] `pytest -v` passes with zero failures
- [ ] `ruff check .` passes with zero warnings
- [ ] `python -m core.license_audit` passes (`core/` remains 100% Python stdlib, MIT clean-room)
- [ ] `python scripts/audit_step.py --step pr_check` passes (`local_score == 100.0`)
- [ ] Zero OS-Mutation: no scripts modify OS services, Registry, network stack, or pagefiles
- [ ] No private workspace data, secrets, or hardcoded user paths committed
