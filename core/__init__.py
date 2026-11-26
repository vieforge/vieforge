"""TierForge core orchestration modules (100% Python stdlib, MIT clean-room)."""

from core.cdp_bridge import (
    BrowserCandidate,
    CDPEndpoint,
    ThreeTierRouter,
    detect_installed_browsers,
    discover_cdp_endpoints,
    find_free_port,
    get_default_profile_dir,
    launch_managed_browser,
    resolve_cdp_url,
)
from core.concurrency import (
    CrossProcessLock,
    OllamaLeaseManager,
    atomic_write_json,
    exclusive_gpu_lock,
)
from core.cost_tracker import (
    BudgetExceededError,
    CostEvent,
    CostTracker,
    DryRunEstimate,
    SavingsReceipt,
    compute_provider_cost_usd,
    load_budget_limits,
    load_pricing,
)
from core.extension_bridge import (
    BridgeJob,
    ConnectedBrowser,
    ExtensionBridgeClient,
    ExtensionBridgeServer,
    load_or_create_bridge_token,
)
from core.license_audit import LicenseAuditReport, LicenseFinding, run_license_audit
from core.llm_router import LLMResponse, LLMRouter
from core.output_trimmer import TrimResult, compact_job_summary, trim_output
from core.pipeline_runner import (
    GateBlockedError,
    GateStatus,
    JobWorkspace,
    assert_gate_approved,
    check_retry_cap,
    run_profiled_stage,
)
from core.qa_logger import QALogger, load_qa_codes
from core.state_db import ItemRecord, StateDB, compute_input_hash, scoped_job_id
from core.step_auditor import (
    StepAuditFinding,
    StepTelemetry,
    export_step_3judge_audit,
    run_step_telemetry,
)
from core.subprocess_runner import SubprocessResult, VRAMInsufficientError, run_isolated
from core.vram_manager import (
    GPUStatus,
    VRAMPhaseRecord,
    flush,
    free_comfyui_vram,
    free_http_gpu_server,
    query_gpu_status,
    recommend_local_model,
    unload_ollama_model,
    vram_phase_profiler,
    wait_for_free_vram,
)

__version__ = "0.2.0"

__all__ = [
    "__version__",
    "BrowserCandidate",
    "CDPEndpoint",
    "ThreeTierRouter",
    "detect_installed_browsers",
    "discover_cdp_endpoints",
    "find_free_port",
    "get_default_profile_dir",
    "launch_managed_browser",
    "resolve_cdp_url",
    "CrossProcessLock",
    "OllamaLeaseManager",
    "atomic_write_json",
    "exclusive_gpu_lock",
    "BudgetExceededError",
    "CostEvent",
    "CostTracker",
    "DryRunEstimate",
    "SavingsReceipt",
    "compute_provider_cost_usd",
    "load_budget_limits",
    "load_pricing",
    "BridgeJob",
    "ConnectedBrowser",
    "ExtensionBridgeClient",
    "ExtensionBridgeServer",
    "load_or_create_bridge_token",
    "LicenseAuditReport",
    "LicenseFinding",
    "run_license_audit",
    "LLMResponse",
    "LLMRouter",
    "TrimResult",
    "compact_job_summary",
    "trim_output",
    "GateBlockedError",
    "GateStatus",
    "JobWorkspace",
    "assert_gate_approved",
    "check_retry_cap",
    "run_profiled_stage",
    "QALogger",
    "load_qa_codes",
    "ItemRecord",
    "StateDB",
    "compute_input_hash",
    "scoped_job_id",
    "StepAuditFinding",
    "StepTelemetry",
    "export_step_3judge_audit",
    "run_step_telemetry",
    "SubprocessResult",
    "VRAMInsufficientError",
    "run_isolated",
    "GPUStatus",
    "VRAMPhaseRecord",
    "flush",
    "free_comfyui_vram",
    "free_http_gpu_server",
    "query_gpu_status",
    "recommend_local_model",
    "unload_ollama_model",
    "vram_phase_profiler",
    "wait_for_free_vram",
]
