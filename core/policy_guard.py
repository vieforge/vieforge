"""Pluggable Role-Based Policy & Tool Execution Guard (100% Python stdlib, MIT Core).

Provides a clean, auditable policy enforcement boundary for teams and enterprise
deployments:
  - Role-based tool allowlists (`viewer`, `developer`, `operator`, `admin`)
  - Command prefix blocklist for shell/subprocess execution
  - Append-only JSONL policy audit log (`policy_audit.jsonl`)
  - Extension hook (`custom_authorizer`) for Enterprise SSO / SAML / OPA / RBAC add-ons
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from core.paths import default_data_dir

DEFAULT_ROLE_TOOLS: dict[str, set[str]] = {
    "viewer": {"preflight_env", "get_job_summary", "estimate_batch_dry_run"},
    "developer": {
        "preflight_env",
        "get_job_summary",
        "estimate_batch_dry_run",
        "run_local_batch",
        "run_trimmed_command",
    },
    "operator": {
        "preflight_env",
        "get_job_summary",
        "estimate_batch_dry_run",
        "run_local_batch",
        "run_trimmed_command",
    },
    "admin": {"*"},
}

DANGEROUS_COMMAND_TOKENS = (
    "rm -rf /",
    "format c:",
    "reg delete",
    "netsh ",  # noqa: VCM-010
    "sc config",  # noqa: VCM-010
    "mkfs.",
    "dd if=",
)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    role: str
    action: str
    reason: str


class PolicyGuard:
    """Deterministic local RBAC + command guardrail with optional Enterprise hook."""

    def __init__(
        self,
        *,
        role_tools: dict[str, set[str]] | None = None,
        audit_log_path: Path | str | None = None,
        custom_authorizer: Callable[[str, str, dict[str, Any]], bool] | None = None,
    ) -> None:
        self.role_tools = role_tools or DEFAULT_ROLE_TOOLS
        self.audit_log_path = Path(audit_log_path) if audit_log_path else (default_data_dir() / "policy_audit.jsonl")
        self.custom_authorizer = custom_authorizer

    def _record(self, decision: PolicyDecision, actor: str, metadata: dict[str, Any]) -> None:
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "actor": actor,
            "role": decision.role,
            "action": decision.action,
            "allowed": decision.allowed,
            "reason": decision.reason,
            "metadata": metadata,
        }
        self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.audit_log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def authorize(
        self,
        *,
        role: str,
        action: str,
        actor: str = "local-user",
        command: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> PolicyDecision:
        meta = metadata or {}
        norm_role = role.strip().lower()
        allowed_tools = self.role_tools.get(norm_role)

        if allowed_tools is None:
            dec = PolicyDecision(False, norm_role, action, f"Unknown role: {norm_role}")
        elif "*" not in allowed_tools and action not in allowed_tools:
            dec = PolicyDecision(False, norm_role, action, f"Role '{norm_role}' is not permitted to invoke '{action}'")
        elif command and any(tok in command.lower() for tok in DANGEROUS_COMMAND_TOKENS):
            dec = PolicyDecision(False, norm_role, action, "Blocked dangerous OS/destructive command pattern")
        elif self.custom_authorizer is not None and not self.custom_authorizer(norm_role, action, meta):
            dec = PolicyDecision(False, norm_role, action, "Denied by custom enterprise authorizer")
        else:
            dec = PolicyDecision(True, norm_role, action, "Allowed")

        self._record(dec, actor=actor, metadata=meta)
        return dec
