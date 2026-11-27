"""TierForge core orchestration modules (100% Python stdlib, MIT clean-room).

Uses PEP 562 lazy attribute loading (`__getattr__`) so CLI startup stays fast and
running `python -m core.<submodule>` emits zero `runpy` RuntimeWarnings.
"""

from __future__ import annotations

import importlib
from typing import Any

__version__ = "0.4.2"

_EXPORTS: dict[str, tuple[str, str]] = {
    "BrowserCandidate": ("core.cdp_bridge", "BrowserCandidate"),
    "CDPEndpoint": ("core.cdp_bridge", "CDPEndpoint"),
    "ThreeTierRouter": ("core.cdp_bridge", "ThreeTierRouter"),
    "detect_installed_browsers": ("core.cdp_bridge", "detect_installed_browsers"),
    "discover_cdp_endpoints": ("core.cdp_bridge", "discover_cdp_endpoints"),
    "find_free_port": ("core.cdp_bridge", "find_free_port"),
    "get_default_profile_dir": ("core.cdp_bridge", "get_default_profile_dir"),
    "launch_managed_browser": ("core.cdp_bridge", "launch_managed_browser"),
    "resolve_cdp_url": ("core.cdp_bridge", "resolve_cdp_url"),
    "CrossProcessLock": ("core.concurrency", "CrossProcessLock"),
    "OllamaLeaseManager": ("core.concurrency", "OllamaLeaseManager"),
    "atomic_write_json": ("core.concurrency", "atomic_write_json"),
    "exclusive_gpu_lock": ("core.concurrency", "exclusive_gpu_lock"),
    "BudgetExceededError": ("core.cost_tracker", "BudgetExceededError"),
    "CostEvent": ("core.cost_tracker", "CostEvent"),
    "CostTracker": ("core.cost_tracker", "CostTracker"),
    "DryRunEstimate": ("core.cost_tracker", "DryRunEstimate"),
    "SavingsReceipt": ("core.cost_tracker", "SavingsReceipt"),
    "compute_provider_cost_usd": ("core.cost_tracker", "compute_provider_cost_usd"),
    "load_budget_limits": ("core.cost_tracker", "load_budget_limits"),
    "load_pricing": ("core.cost_tracker", "load_pricing"),
    "BridgeJob": ("core.extension_bridge", "BridgeJob"),
    "ConnectedBrowser": ("core.extension_bridge", "ConnectedBrowser"),
    "ExtensionBridgeClient": ("core.extension_bridge", "ExtensionBridgeClient"),
    "ExtensionBridgeServer": ("core.extension_bridge", "ExtensionBridgeServer"),
    "load_or_create_bridge_token": ("core.extension_bridge", "load_or_create_bridge_token"),
    "LicenseAuditReport": ("core.license_audit", "LicenseAuditReport"),
    "LicenseFinding": ("core.license_audit", "LicenseFinding"),
    "run_license_audit": ("core.license_audit", "run_license_audit"),
    "LLMResponse": ("core.llm_router", "LLMResponse"),
    "LLMRouter": ("core.llm_router", "LLMRouter"),
    "PROVIDER_PRESETS": ("core.llm_router", "PROVIDER_PRESETS"),
    "ProviderPreset": ("core.llm_router", "ProviderPreset"),
    "discover_local_llm": ("core.llm_router", "discover_local_llm"),
    "list_providers": ("core.llm_router", "list_providers"),
    "resolve_provider_preset": ("core.llm_router", "resolve_provider_preset"),
    "TrimResult": ("core.output_trimmer", "TrimResult"),
    "compact_job_summary": ("core.output_trimmer", "compact_job_summary"),
    "redact_secrets_text": ("core.output_trimmer", "redact_secrets_text"),
    "trim_output": ("core.output_trimmer", "trim_output"),
    "default_data_dir": ("core.paths", "default_data_dir"),
    "is_source_checkout": ("core.paths", "is_source_checkout"),
    "resolve_asset": ("core.paths", "resolve_asset"),
    "GateBlockedError": ("core.pipeline_runner", "GateBlockedError"),
    "GateStatus": ("core.pipeline_runner", "GateStatus"),
    "JobWorkspace": ("core.pipeline_runner", "JobWorkspace"),
    "assert_gate_approved": ("core.pipeline_runner", "assert_gate_approved"),
    "check_retry_cap": ("core.pipeline_runner", "check_retry_cap"),
    "run_profiled_stage": ("core.pipeline_runner", "run_profiled_stage"),
    "QALogger": ("core.qa_logger", "QALogger"),
    "emit_genai_span": ("core.qa_logger", "emit_genai_span"),
    "PolicyDecision": ("core.policy_guard", "PolicyDecision"),
    "PolicyGuard": ("core.policy_guard", "PolicyGuard"),
    "load_qa_codes": ("core.qa_logger", "load_qa_codes"),
    "ItemRecord": ("core.state_db", "ItemRecord"),
    "StateDB": ("core.state_db", "StateDB"),
    "compute_input_hash": ("core.state_db", "compute_input_hash"),
    "scoped_job_id": ("core.state_db", "scoped_job_id"),
    "StepAuditFinding": ("core.step_auditor", "StepAuditFinding"),
    "StepTelemetry": ("core.step_auditor", "StepTelemetry"),
    "export_step_3judge_audit": ("core.step_auditor", "export_step_3judge_audit"),
    "run_step_telemetry": ("core.step_auditor", "run_step_telemetry"),
    "SubprocessResult": ("core.subprocess_runner", "SubprocessResult"),
    "VRAMInsufficientError": ("core.subprocess_runner", "VRAMInsufficientError"),
    "run_isolated": ("core.subprocess_runner", "run_isolated"),
    "GPUStatus": ("core.vram_manager", "GPUStatus"),
    "VRAMPhaseRecord": ("core.vram_manager", "VRAMPhaseRecord"),
    "flush": ("core.vram_manager", "flush"),
    "free_comfyui_vram": ("core.vram_manager", "free_comfyui_vram"),
    "free_http_gpu_server": ("core.vram_manager", "free_http_gpu_server"),
    "query_gpu_status": ("core.vram_manager", "query_gpu_status"),
    "recommend_local_model": ("core.vram_manager", "recommend_local_model"),
    "unload_ollama_model": ("core.vram_manager", "unload_ollama_model"),
    "vram_phase_profiler": ("core.vram_manager", "vram_phase_profiler"),
    "wait_for_free_vram": ("core.vram_manager", "wait_for_free_vram"),
}

__all__ = ["__version__", *_EXPORTS.keys()]


def __getattr__(name: str) -> Any:
    target = _EXPORTS.get(name)
    if target is None:
        raise AttributeError(f"module 'core' has no attribute {name!r}")
    mod_name, attr_name = target
    mod = importlib.import_module(mod_name)
    val = getattr(mod, attr_name)
    globals()[name] = val
    return val


def __dir__() -> list[str]:
    return sorted(set(globals().keys()) | set(__all__))
