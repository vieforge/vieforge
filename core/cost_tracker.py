"""Job & Daily Cost Guard with pricing.json, budget_limits.json, --dry-run & Savings Receipt.

100% Python stdlib, cross-platform, commercial-safe (MIT).
Supports both TierForge `models` pricing (`1m_tokens` / `item`) and generic `providers` pricing
(`per_second` / `per_image` / `flat`).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from core.concurrency import CrossProcessLock, atomic_write_json
from core.paths import default_data_dir, resolve_asset

ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PRICING_PATH = resolve_asset("config/pricing.json")
EXAMPLE_PRICING_PATH = resolve_asset("config/pricing.example.json")
DEFAULT_BUDGET_LIMITS_PATH = resolve_asset("config/budget_limits.json")
DEFAULT_DAILY_SPEND_PATH = default_data_dir() / "daily_spend.json"


class BudgetExceededError(RuntimeError):
    """Raised when an API call would breach MAX_JOB_USD or DAILY_USD_CAP."""


def load_pricing(pricing_path: Path | None = None) -> dict[str, Any]:
    if pricing_path is not None:
        return json.loads(pricing_path.read_text(encoding="utf-8"))
    env_path = os.getenv("TIERFORGE_PRICING_CONFIG")
    if env_path:
        p = Path(env_path)
        if not p.is_absolute():
            p = ROOT_DIR / p
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    if DEFAULT_PRICING_PATH.exists():
        return json.loads(DEFAULT_PRICING_PATH.read_text(encoding="utf-8"))
    if EXAMPLE_PRICING_PATH.exists():
        return json.loads(EXAMPLE_PRICING_PATH.read_text(encoding="utf-8"))
    return {"currency": "USD", "models": {}, "providers": {}}


def load_budget_limits(limits_path: Path | None = None) -> dict[str, Any]:
    if limits_path is not None:
        p = limits_path
    else:
        env_path = os.getenv("TIERFORGE_BUDGET_CONFIG")
        p = (ROOT_DIR / env_path) if env_path and not Path(env_path).is_absolute() else (
            Path(env_path) if env_path else DEFAULT_BUDGET_LIMITS_PATH
        )
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {
        "max_job_usd": 5.0,
        "daily_usd_cap": 20.0,
        "max_retry_per_item": 2,
        "require_dry_run_for_paid": True,
        "require_approval_above_usd": 1.0,
    }


def compute_provider_cost_usd(
    provider: str,
    pricing: dict[str, Any],
    duration_sec: float = 5.0,
    count: int = 1,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> float:
    # 1. TierForge `models` schema
    model_entry = pricing.get("models", {}).get(provider)
    if model_entry:
        unit = model_entry.get("billing_unit", "item")
        if unit == "1m_tokens":
            in_cost = (input_tokens / 1_000_000.0) * float(model_entry.get("input_usd_per_1m", 0.0))
            out_cost = (output_tokens / 1_000_000.0) * float(model_entry.get("output_usd_per_1m", 0.0))
            return round(in_cost + out_cost, 6)
        return round(float(model_entry.get("usd_per_item", 0.0)) * count, 4)

    # 2. Generic `providers` schema
    entry = pricing.get("providers", {}).get(provider)
    if not entry:
        return 0.0
    unit = entry.get("unit", "flat")
    price = float(entry.get("price_usd", 0.0))
    if unit == "per_second":
        return round(price * duration_sec * count, 4)
    if unit == "per_image":
        return round(price * count, 4)
    return round(price * count, 4)


@dataclass
class DryRunEstimate:
    provider: str
    item_count: int
    estimated_base_usd: float
    retry_buffer_ratio: float
    estimated_worst_case_usd: float
    max_job_usd: float
    daily_usd_cap: float
    today_spend_usd: float
    within_budget: bool
    requires_approval: bool
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SavingsReceipt:
    """Compares actual TierForge spend vs. an equivalent Cloud-only baseline."""

    job_id: str
    items_processed: int
    actual_usd: float
    cloud_baseline_provider: str
    cloud_baseline_usd: float
    saved_usd: float
    saved_pct: float
    raw_log_chars: int = 0
    trimmed_log_chars: int = 0
    log_reduction_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def format_summary(self) -> str:
        lines = [
            f"=== TierForge Savings Receipt ({self.job_id}) ===",
            f"Items processed : {self.items_processed}",
            f"Actual cost     : ${self.actual_usd:.4f} USD",
            f"Cloud baseline  : ${self.cloud_baseline_usd:.4f} USD ({self.cloud_baseline_provider})",
            f"Estimated saved : ${self.saved_usd:.4f} USD ({self.saved_pct:.1f}%)",
        ]
        if self.raw_log_chars > 0:
            lines.append(
                f"Context trimmed : {self.raw_log_chars} -> {self.trimmed_log_chars} chars "
                f"(-{self.log_reduction_pct:.1f}%)"
            )
        return "\n".join(lines)


@dataclass
class CostEvent:
    stage: str
    provider: str
    item_id: int | str | None
    cost_usd: float
    retries: int = 0
    duration_sec: float = 0.0
    notes: str = ""
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class CostTracker:
    """Two-tier budget cap (MAX_JOB_USD + DAILY_USD_CAP) backed by config/pricing.json."""

    def __init__(
        self,
        job_id: str,
        max_job_usd: float | None = None,
        daily_usd_cap: float | None = None,
        pricing_path: Path | None = None,
        daily_spend_path: Path | None = None,
        budget_limits_path: Path | None = None,
    ) -> None:
        self.job_id = job_id
        self.pricing = load_pricing(pricing_path)
        self.limits = load_budget_limits(budget_limits_path)
        self.retry_buffer_ratio = float(self.pricing.get("retry_buffer_ratio", 0.25))
        self.max_retry_per_item = int(
            os.getenv(
                "TIERFORGE_MAX_RETRY_PER_ITEM",
                str(self.limits.get("max_retry_per_item", 2)),
            )
        )
        self.require_approval_above_usd = float(
            self.limits.get("require_approval_above_usd", 1.0)
        )
        env_job = os.getenv("TIERFORGE_MAX_JOB_USD") or os.getenv("MAX_JOB_USD")
        self.max_job_usd = (
            max_job_usd
            if max_job_usd is not None
            else float(env_job if env_job is not None else self.limits.get("max_job_usd", 5.0))
        )
        env_day = os.getenv("TIERFORGE_DAILY_USD_CAP") or os.getenv("DAILY_USD_CAP")
        self.daily_usd_cap = (
            daily_usd_cap
            if daily_usd_cap is not None
            else float(env_day if env_day is not None else self.limits.get("daily_usd_cap", 20.0))
        )
        self.daily_spend_path = daily_spend_path or DEFAULT_DAILY_SPEND_PATH
        self.events: list[CostEvent] = []

    def _today_key(self) -> str:
        return date.today().isoformat()

    def get_today_spend_usd(self) -> float:
        if not self.daily_spend_path.exists():
            return 0.0
        data = json.loads(self.daily_spend_path.read_text(encoding="utf-8"))
        return float(data.get(self._today_key(), 0.0))

    def _add_today_spend_usd(self, delta_usd: float) -> None:
        if delta_usd <= 0:
            return
        self.daily_spend_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.daily_spend_path.with_suffix(self.daily_spend_path.suffix + ".lock")
        with CrossProcessLock(lock_path, timeout_sec=10.0):
            data = (
                json.loads(self.daily_spend_path.read_text(encoding="utf-8"))
                if self.daily_spend_path.exists()
                else {}
            )
            key = self._today_key()
            data[key] = round(float(data.get(key, 0.0)) + delta_usd, 4)
            atomic_write_json(self.daily_spend_path, data)

    @property
    def total_usd(self) -> float:
        return round(sum(e.cost_usd for e in self.events), 4)

    @property
    def total_api_calls(self) -> int:
        return sum(1 for e in self.events if e.cost_usd > 0)

    @property
    def total_retries(self) -> int:
        return sum(e.retries for e in self.events)

    def estimate_dry_run(
        self,
        provider: str,
        item_count: int,
        avg_input_tokens: int = 0,
        avg_output_tokens: int = 0,
        avg_duration_sec: float = 5.0,
    ) -> DryRunEstimate:
        """Estimate job cost before spending and evaluate budget & approval gates."""
        per_item = compute_provider_cost_usd(
            provider=provider,
            pricing=self.pricing,
            duration_sec=avg_duration_sec,
            count=1,
            input_tokens=avg_input_tokens,
            output_tokens=avg_output_tokens,
        )
        base = round(per_item * item_count, 4)
        worst = round(base * (1.0 + self.retry_buffer_ratio), 4)
        today = self.get_today_spend_usd()

        within = True
        reason = "Within job and daily budget limits."
        if worst > self.max_job_usd:
            within = False
            reason = f"Worst-case ${worst:.4f} exceeds MAX_JOB_USD (${self.max_job_usd:.2f})."
        elif today + worst > self.daily_usd_cap:
            within = False
            reason = (
                f"Projected daily spend ${today + worst:.4f} exceeds "
                f"DAILY_USD_CAP (${self.daily_usd_cap:.2f})."
            )

        requires_approval = base > self.require_approval_above_usd
        return DryRunEstimate(
            provider=provider,
            item_count=item_count,
            estimated_base_usd=base,
            retry_buffer_ratio=self.retry_buffer_ratio,
            estimated_worst_case_usd=worst,
            max_job_usd=self.max_job_usd,
            daily_usd_cap=self.daily_usd_cap,
            today_spend_usd=today,
            within_budget=within,
            requires_approval=requires_approval,
            reason=reason,
        )

    def build_savings_receipt(
        self,
        items_processed: int,
        cloud_baseline_provider: str = "external/text-frontier",
        avg_input_tokens: int = 1500,
        avg_output_tokens: int = 500,
        raw_log_chars: int = 0,
        trimmed_log_chars: int = 0,
    ) -> SavingsReceipt:
        """Compute a transparent Savings Receipt comparing actual cost vs. a Cloud baseline."""
        per_item_cloud = compute_provider_cost_usd(
            provider=cloud_baseline_provider,
            pricing=self.pricing,
            count=1,
            input_tokens=avg_input_tokens,
            output_tokens=avg_output_tokens,
        )
        baseline_usd = round(per_item_cloud * items_processed, 4)
        actual = self.total_usd
        saved = round(max(0.0, baseline_usd - actual), 4)
        saved_pct = round((saved / baseline_usd) * 100.0, 1) if baseline_usd > 0 else 0.0
        log_pct = (
            round(((raw_log_chars - trimmed_log_chars) / raw_log_chars) * 100.0, 1)
            if raw_log_chars > 0 and trimmed_log_chars <= raw_log_chars
            else 0.0
        )
        return SavingsReceipt(
            job_id=self.job_id,
            items_processed=items_processed,
            actual_usd=actual,
            cloud_baseline_provider=cloud_baseline_provider,
            cloud_baseline_usd=baseline_usd,
            saved_usd=saved,
            saved_pct=saved_pct,
            raw_log_chars=raw_log_chars,
            trimmed_log_chars=trimmed_log_chars,
            log_reduction_pct=log_pct,
        )

    def check_budget_before_spend(self, upcoming_cost_usd: float) -> None:
        projected_job = self.total_usd + upcoming_cost_usd
        if projected_job > self.max_job_usd:
            raise BudgetExceededError(
                f"MAX_JOB_USD BREACHED: projected ${projected_job:.3f} > ${self.max_job_usd:.2f}"
            )
        projected_day = self.get_today_spend_usd() + upcoming_cost_usd
        if projected_day > self.daily_usd_cap:
            raise BudgetExceededError(
                f"DAILY_USD_CAP BREACHED: projected daily ${projected_day:.3f} > ${self.daily_usd_cap:.2f}"
            )

    def record(
        self,
        stage: str,
        provider: str,
        cost_usd: float,
        item_id: int | str | None = None,
        retries: int = 0,
        duration_sec: float = 0.0,
        notes: str = "",
    ) -> None:
        if cost_usd > 0:
            self.check_budget_before_spend(cost_usd)
            self._add_today_spend_usd(cost_usd)
        self.events.append(
            CostEvent(
                stage=stage,
                provider=provider,
                item_id=item_id,
                cost_usd=round(cost_usd, 4),
                retries=retries,
                duration_sec=round(duration_sec, 2),
                notes=notes,
            )
        )

    def build_report(self) -> dict[str, Any]:
        by_stage: dict[str, float] = {}
        for e in self.events:
            by_stage[e.stage] = round(by_stage.get(e.stage, 0.0) + e.cost_usd, 4)
        return {
            "job_id": self.job_id,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "max_job_usd": self.max_job_usd,
            "daily_usd_cap": self.daily_usd_cap,
            "today_total_spend_usd": self.get_today_spend_usd(),
            "total_usd": self.total_usd,
            "total_api_calls": self.total_api_calls,
            "total_retries": self.total_retries,
            "cost_by_stage_usd": by_stage,
            "events": [asdict(e) for e in self.events],
        }

    def save_report(self, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(self.build_report(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return output_path
