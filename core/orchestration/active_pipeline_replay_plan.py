from __future__ import annotations

"""Immutable preregistration for a faithful replay of the active investment route."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "faithful-active-pipeline-replay-preregistration-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
MINIMUM_EVALUATION_SPAN = timedelta(days=90)
MINIMUM_NON_COMMISSION_COST_BPS = Decimal("1")
MINIMUM_PESSIMISTIC_COST_MULTIPLIER = Decimal("2")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
GIT_PATTERN = re.compile(r"^[0-9a-f]{40,64}$")
REQUIRED_COMPONENTS = {
    "investment_research_pipeline",
    "research_contract",
    "master_portfolio_decision_engine",
    "factor_lineage_policy",
    "portfolio_engine",
    "portfolio_construction_service",
}
REQUIRED_METRICS = {
    "COMPLETE_BENCHMARK_RELATIVE_RETURN",
    "MAXIMUM_DRAWDOWN",
    "TURNOVER",
    "HIT_RATE",
}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete active-pipeline replay-plan append")
        written += count


def _required(value: Any, name: str, maximum: int = 1000) -> str:
    resolved = str(value or "").strip()
    if not resolved:
        raise ValueError(f"{name} is required")
    if len(resolved) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")
    return resolved


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").lower()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _nonnegative_decimal(value: Any, name: str) -> str:
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a non-negative finite decimal") from error
    if not resolved.is_finite() or resolved < 0:
        raise ValueError(f"{name} must be a non-negative finite decimal")
    return "0" if resolved == 0 else format(resolved.normalize(), "f")


def _positive_decimal(value: Any, name: str) -> str:
    resolved = _nonnegative_decimal(value, name)
    if Decimal(resolved) <= 0:
        raise ValueError(f"{name} must be positive")
    return resolved


def _components(values: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    if not isinstance(values, Mapping) or set(values) != REQUIRED_COMPONENTS:
        raise ValueError("components must contain exactly the active pipeline stages")
    result: dict[str, dict[str, str]] = {}
    for name in sorted(REQUIRED_COMPONENTS):
        value = values[name]
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a component object")
        result[name] = {
            "version": _required(value.get("version"), f"{name}.version", 120),
            "source_sha256": _sha256(value.get("source_sha256"), f"{name}.source_sha256"),
        }
    return result


def _metrics(values: Mapping[str, Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    if not isinstance(values, Mapping) or set(values) != REQUIRED_METRICS:
        raise ValueError("acceptance_metrics must contain exactly the required metrics")
    result: dict[str, dict[str, str]] = {}
    for name in sorted(REQUIRED_METRICS):
        value = values[name]
        if not isinstance(value, Mapping):
            raise ValueError(f"{name} must be a metric object")
        result[name] = {
            "comparison": _required(value.get("comparison"), f"{name}.comparison", 20).upper(),
            "threshold": _nonnegative_decimal(value.get("threshold"), f"{name}.threshold"),
        }
        if result[name]["comparison"] not in {"AT_LEAST", "AT_MOST"}:
            raise ValueError(f"{name}.comparison must be AT_LEAST or AT_MOST")
    if result["COMPLETE_BENCHMARK_RELATIVE_RETURN"]["comparison"] != "AT_LEAST":
        raise ValueError("benchmark-relative return must use AT_LEAST")
    if result["HIT_RATE"]["comparison"] != "AT_LEAST":
        raise ValueError("hit rate must use AT_LEAST")
    if any(
        result[name]["comparison"] != "AT_MOST"
        for name in ("MAXIMUM_DRAWDOWN", "TURNOVER")
    ):
        raise ValueError("drawdown and turnover must use AT_MOST")
    return result


def _plan_id(registered_at: str, material: Mapping[str, Any]) -> str:
    return "REPLAY-" + hashlib.sha256(
        _canonical_json([registered_at, material, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


class ActivePipelineReplayPlanLedger:
    """Append-only plan only; it neither loads the sealed data nor runs a replay."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Replay-plan ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank replay-plan line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at replay-plan line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(f"Replay-plan line {line_number} is not an object.")
                records.append(record)
        return records

    def preregister(
        self,
        *,
        git_revision: str,
        components: Mapping[str, Mapping[str, Any]],
        dependency_lock_sha256: str,
        runner_sha256: str,
        sealed_evaluation_dataset_commitment_sha256: str,
        evaluation_not_before: str | datetime,
        evaluation_not_after: str | datetime,
        evaluation_data_access_not_before: str | datetime,
        universe: str,
        historical_universe_source_approval_required: bool,
        commission_bps_per_side: Any,
        bid_ask_half_spread_bps_per_side: Any,
        slippage_bps_per_side: Any,
        latency_bps_per_side: Any,
        market_impact_bps_per_side: Any,
        pessimistic_cost_multiplier: Any,
        acceptance_metrics: Mapping[str, Mapping[str, Any]],
        rejection_rule: str,
        registered_by: str,
        registered_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        registered = _timestamp(registered_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        start = _timestamp(evaluation_not_before)
        end = _timestamp(evaluation_not_after)
        access = _timestamp(evaluation_data_access_not_before)
        if not now - MAX_CLOCK_SKEW <= registered <= now + MAX_CLOCK_SKEW:
            raise ValueError("registered_at must match the actual append time")
        if end < start + MINIMUM_EVALUATION_SPAN:
            raise ValueError("evaluation window must span at least 90 days")
        if access < registered:
            raise ValueError("evaluation data access cannot predate preregistration")
        revision = str(git_revision or "").strip().lower()
        if not GIT_PATTERN.fullmatch(revision):
            raise ValueError("git_revision must be a full hexadecimal revision")
        resolved_universe = _required(universe, "universe", 30).upper()
        if resolved_universe not in {"SP500", "NASDAQ100", "SP500_AND_NASDAQ100"}:
            raise ValueError("universe is unsupported")
        if not historical_universe_source_approval_required:
            raise ValueError("historical universe source approval must remain required")
        cost_model = {
            "commission_bps_per_side": _nonnegative_decimal(
                commission_bps_per_side, "commission_bps_per_side"
            ),
            "bid_ask_half_spread_bps_per_side": _positive_decimal(
                bid_ask_half_spread_bps_per_side, "bid_ask_half_spread_bps_per_side"
            ),
            "slippage_bps_per_side": _positive_decimal(
                slippage_bps_per_side, "slippage_bps_per_side"
            ),
            "latency_bps_per_side": _positive_decimal(
                latency_bps_per_side, "latency_bps_per_side"
            ),
            "market_impact_bps_per_side": _positive_decimal(
                market_impact_bps_per_side, "market_impact_bps_per_side"
            ),
            "pessimistic_cost_multiplier": _positive_decimal(
                pessimistic_cost_multiplier, "pessimistic_cost_multiplier"
            ),
        }
        if any(
            Decimal(cost_model[field]) < MINIMUM_NON_COMMISSION_COST_BPS
            for field in (
                "bid_ask_half_spread_bps_per_side", "slippage_bps_per_side",
                "latency_bps_per_side", "market_impact_bps_per_side",
            )
        ):
            raise ValueError("every non-commission cost must be at least 1 basis point")
        if (
            Decimal(cost_model["pessimistic_cost_multiplier"])
            < MINIMUM_PESSIMISTIC_COST_MULTIPLIER
        ):
            raise ValueError("pessimistic_cost_multiplier must be at least 2")
        material = {
            "git_revision": revision,
            "components": _components(components),
            "dependency_lock_sha256": _sha256(dependency_lock_sha256, "dependency lock"),
            "runner_sha256": _sha256(runner_sha256, "runner"),
            "sealed_evaluation_dataset_commitment_sha256": _sha256(
                sealed_evaluation_dataset_commitment_sha256, "dataset commitment"
            ),
            "evaluation_not_before": start.isoformat(),
            "evaluation_not_after": end.isoformat(),
            "evaluation_data_access_not_before": access.isoformat(),
            "universe": resolved_universe,
            "cost_model": cost_model,
            "acceptance_metrics": _metrics(acceptance_metrics),
            "rejection_rule": _required(rejection_rule, "rejection_rule"),
        }
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "replay_plan_id": _plan_id(registered.isoformat(), material),
            "record_type": "FAITHFUL_ACTIVE_PIPELINE_REPLAY_PREREGISTRATION",
            "status": "PREREGISTERED_AWAITING_DATA_AND_SOURCE_APPROVAL",
            "simulation_only": True,
            "registered_at": registered.isoformat(),
            "registered_by": _required(registered_by, "registered_by", 100),
            **material,
            "active_route_only": True,
            "random_split_allowed": False,
            "evaluation_period_reuse_allowed": False,
            "metric_substitution_allowed": False,
            "optional_stopping_allowed": False,
            "point_in_time_inputs_required": True,
            "historical_universe_source_approval_required": True,
            "historical_universe_coverage_bound": False,
            "evaluation_dataset_opened": False,
            "replay_executed": False,
            "performance_claim_allowed": False,
            "paper_broker_submission_enabled": False,
            "broker_connection_allowed": False,
            "live_trading_enabled": False,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material_with_chain = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material_with_chain)
            ):
                raise LedgerIntegrityError(f"Replay-plan record {index} has been modified.")
            try:
                registered = _timestamp(record.get("registered_at"))
                start = _timestamp(record.get("evaluation_not_before"))
                end = _timestamp(record.get("evaluation_not_after"))
                access = _timestamp(record.get("evaluation_data_access_not_before"))
                revision = str(record.get("git_revision") or "").lower()
                if not GIT_PATTERN.fullmatch(revision):
                    raise ValueError("invalid revision")
                identity = {
                    "git_revision": revision,
                    "components": _components(record.get("components")),
                    "dependency_lock_sha256": _sha256(record.get("dependency_lock_sha256"), "lock"),
                    "runner_sha256": _sha256(record.get("runner_sha256"), "runner"),
                    "sealed_evaluation_dataset_commitment_sha256": _sha256(
                        record.get("sealed_evaluation_dataset_commitment_sha256"), "dataset"
                    ),
                    "evaluation_not_before": start.isoformat(),
                    "evaluation_not_after": end.isoformat(),
                    "evaluation_data_access_not_before": access.isoformat(),
                    "universe": _required(record.get("universe"), "universe", 30).upper(),
                    "cost_model": {
                        "commission_bps_per_side": _nonnegative_decimal(
                            record.get("cost_model", {}).get("commission_bps_per_side"), "commission"
                        ),
                        **{
                            field: _positive_decimal(record.get("cost_model", {}).get(field), field)
                            for field in (
                                "bid_ask_half_spread_bps_per_side", "slippage_bps_per_side",
                                "latency_bps_per_side", "market_impact_bps_per_side",
                                "pessimistic_cost_multiplier",
                            )
                        },
                    },
                    "acceptance_metrics": _metrics(record.get("acceptance_metrics")),
                    "rejection_rule": _required(record.get("rejection_rule"), "rejection_rule"),
                }
                _required(record.get("registered_by"), "registered_by", 100)
            except (AttributeError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(f"Replay-plan record {index} has invalid values.") from error
            fixed_false = (
                "random_split_allowed", "evaluation_period_reuse_allowed",
                "metric_substitution_allowed", "optional_stopping_allowed",
                "historical_universe_coverage_bound", "evaluation_dataset_opened",
                "replay_executed", "performance_claim_allowed",
                "paper_broker_submission_enabled", "broker_connection_allowed",
                "live_trading_enabled",
            )
            boundary = (
                record.get("schema_version") == SCHEMA_VERSION
                and record.get("policy_version") == POLICY_VERSION
                and record.get("replay_plan_id") == _plan_id(registered.isoformat(), identity)
                and record.get("replay_plan_id") not in seen
                and record.get("record_type") == "FAITHFUL_ACTIVE_PIPELINE_REPLAY_PREREGISTRATION"
                and record.get("status") == "PREREGISTERED_AWAITING_DATA_AND_SOURCE_APPROVAL"
                and record.get("simulation_only") is True
                and record.get("active_route_only") is True
                and record.get("point_in_time_inputs_required") is True
                and record.get("historical_universe_source_approval_required") is True
                and identity["universe"] in {"SP500", "NASDAQ100", "SP500_AND_NASDAQ100"}
                and end >= start + MINIMUM_EVALUATION_SPAN
                and access >= registered
                and all(
                    Decimal(identity["cost_model"][field]) >= MINIMUM_NON_COMMISSION_COST_BPS
                    for field in (
                        "bid_ask_half_spread_bps_per_side", "slippage_bps_per_side",
                        "latency_bps_per_side", "market_impact_bps_per_side",
                    )
                )
                and Decimal(identity["cost_model"]["pessimistic_cost_multiplier"])
                >= MINIMUM_PESSIMISTIC_COST_MULTIPLIER
                and registered <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and all(record.get(field) is False for field in fixed_false)
            )
            if not boundary:
                raise LedgerIntegrityError(f"Replay-plan record {index} violates its boundary.")
            seen.add(record["replay_plan_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["replay_plan_id"] == result["replay_plan_id"]),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError("Active-pipeline replay plan already exists.")
            if any(item["git_revision"] == result["git_revision"] for item in records):
                raise LedgerIntegrityError(
                    "This Git revision already has its single binding replay plan."
                )
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
