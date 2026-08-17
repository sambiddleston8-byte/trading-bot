from __future__ import annotations

"""Append-only audit evidence for completed guardrailed simulation runs."""

from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from decimal import ROUND_FLOOR
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.guardrailed_backtest import (
    AUTHENTICATED_REPLAY_ROLES,
    ENGINE_POLICY_VERSION,
    BacktestResult,
    CompletedTrade,
    ExecutionRecord,
    PortfolioStateTrace,
    SizingDecisionTrace,
)
from core.orchestration.authenticated_execution_profile import (
    resolve_authenticated_execution_profile,
)
from core.orchestration.replay_backtest_inputs import load_authenticated_backtest_inputs


_LEGACY_ENGINE_POLICY_VERSION = "causal-single-instrument-guardrailed-backtest-v2"
_POSITION_CAP_ENGINE_POLICY_VERSION = "causal-single-instrument-guardrailed-backtest-v3"
_PORTFOLIO_ENGINE_POLICY_VERSION = "causal-portfolio-guardrailed-backtest-v4"
_SUPPORTED_ENGINE_POLICY_VERSIONS = frozenset(
    {
        _LEGACY_ENGINE_POLICY_VERSION,
        _POSITION_CAP_ENGINE_POLICY_VERSION,
        _PORTFOLIO_ENGINE_POLICY_VERSION,
    }
)
if ENGINE_POLICY_VERSION != _PORTFOLIO_ENGINE_POLICY_VERSION:
    raise RuntimeError(
        "replay audit must be updated explicitly for the current engine policy"
    )


SCHEMA_VERSION = "1.3"
POLICY_VERSION = "authenticated-replay-run-audit-v4"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
FIXED_FALSE = (
    "performance_claim_allowed",
    "paper_trade_promotion_allowed",
    "broker_connection_allowed",
    "orders_submitted",
    "live_trading_enabled",
)
METADATA_FIELDS = {
    "schema_version", "policy_version", "replay_run_id", "record_type", "status",
    "recorded_at", "recorded_by", "git_revision", "previous_hash", "record_hash",
}
STRATEGY_BINDING_FIELDS = {
    "replay_plan_id", "replay_plan_record_hash", "dataset_commitment_sha256",
    "replay_plan_git_revision", "replay_plan_evaluation_start",
    "replay_plan_evaluation_end",
    "strategy_specification_id", "strategy_specification_record_hash",
    "strategy_entrypoint", "strategy_source_sha256",
}
EXECUTION_BINDING_FIELDS = {
    "execution_profile_version", "replay_execution_policy_record_id",
    "replay_execution_policy_record_hash", "execution_policy_id",
    "execution_policy_version",
}


def _canonical_json(value: Any) -> str:
    def default(item: Any) -> Any:
        if isinstance(item, Decimal):
            return "0" if item == 0 else format(item.normalize(), "f")
        if isinstance(item, datetime):
            return item.astimezone(timezone.utc).isoformat(timespec="microseconds")
        raise TypeError(f"unsupported replay audit value: {type(item)!r}")

    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=default
    )


def _record_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _time(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)


def _required(value: Any, name: str, maximum: int = 200) -> str:
    resolved = str(value or "").strip()
    if not resolved or len(resolved) > maximum:
        raise ValueError(f"{name} is required and must not exceed {maximum} characters")
    return resolved


def _hash(value: Any, name: str) -> str:
    resolved = _required(value, name, 64).lower()
    if not SHA256.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _git_revision(value: Any) -> str:
    resolved = _required(value, "git_revision", 64).lower()
    if not GIT_REVISION.fullmatch(resolved):
        raise ValueError("git_revision must be a lowercase 40- or 64-character commit ID")
    return resolved


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("replay-run audit append made no progress")
        offset += count


def _result_payload(result: BacktestResult) -> dict[str, Any]:
    if (
        result.evaluation_start is None
        or result.evaluation_end is None
        or result.evaluation_start >= result.evaluation_end
    ):
        raise ValueError("result requires an exact completed evaluation window")
    if not result.portfolio_states or len(result.sizing_decisions) != len(result.executions):
        raise ValueError("result lacks complete sizing, portfolio-state or execution traces")
    if any(getattr(result, field) is not False for field in FIXED_FALSE):
        raise ValueError("result exceeds the simulation-only authority boundary")
    if result.no_lookahead_contract_enforced is not True:
        raise ValueError("result must enforce the no-lookahead contract")
    if result.mechanical_simulation_only is not True:
        raise ValueError("result must be labelled mechanical simulation only")
    if tuple(sorted(result.evidence_role_hashes)) != result.evidence_role_hashes:
        raise ValueError("authenticated evidence role hashes must be canonical")
    payload = {
        "strategy_version": _required(result.strategy_version, "strategy_version"),
        "strategy_entrypoint": _required(
            result.strategy_entrypoint, "strategy_entrypoint", 300
        ),
        "strategy_source_sha256": _hash(
            result.strategy_source_sha256, "strategy_source_sha256"
        ),
        "parameter_hash": _hash(result.parameter_hash, "parameter_hash"),
        "source_id": _required(result.source_id, "source_id"),
        "source_content_sha256": _hash(
            result.source_content_sha256, "source_content_sha256"
        ),
        "validation_receipt_sha256": _hash(
            result.validation_receipt_sha256, "validation_receipt_sha256"
        ),
        "evidence_role_hashes": [list(item) for item in result.evidence_role_hashes],
        "engine_policy_version": _required(
            result.engine_policy_version, "engine_policy_version"
        ),
        "engine_config_sha256": _hash(
            result.engine_config_sha256, "engine_config_sha256"
        ),
        "engine_config_canonical_json": _required(
            result.engine_config_canonical_json,
            "engine_config_canonical_json",
            100_000,
        ),
        "fee_schedule_id": _required(result.fee_schedule_id, "fee_schedule_id"),
        "execution_scenario": _required(
            result.execution_scenario, "execution_scenario"
        ),
        "evaluation_start": result.evaluation_start,
        "evaluation_end": result.evaluation_end,
        "starting_equity": result.starting_equity,
        "ending_equity": result.ending_equity,
        "total_return": result.total_return,
        "maximum_drawdown": result.maximum_drawdown,
        "executions": [asdict(item) for item in result.executions],
        "completed_trades": [asdict(item) for item in result.completed_trades],
        "equity_curve": [list(item) for item in result.equity_curve],
        "sizing_decisions": [asdict(item) for item in result.sizing_decisions],
        "portfolio_states": [asdict(item) for item in result.portfolio_states],
        "executive_intents": [asdict(item) for item in result.executive_intents],
        "cash_reservations": [asdict(item) for item in result.cash_reservations],
        "no_lookahead_contract_enforced": True,
        "mechanical_simulation_only": True,
        **{field: False for field in FIXED_FALSE},
    }
    return json.loads(_canonical_json(payload))


class ReplayRunAuditLedger:
    """Store one immutable record per deterministic run identity."""

    def __init__(
        self,
        path: str | Path,
        *,
        admission_ledger: Any,
        content_ledger: Any,
        strategy_ledger: Any,
        execution_policy_ledger: Any,
    ) -> None:
        self.path = Path(path)
        self.admission_ledger = admission_ledger
        self.content_ledger = content_ledger
        self.strategy_ledger = strategy_ledger
        self.execution_policy_ledger = execution_policy_ledger

    def _verify_authenticated_parent(self, payload: Mapping[str, Any]) -> dict[str, str]:
        inputs = load_authenticated_backtest_inputs(
            admission_ledger=self.admission_ledger,
            content_ledger=self.content_ledger,
            admission_id=payload["source_id"],
        )
        attestation = inputs.data_attestation
        expected_roles = [list(item) for item in attestation.evidence_role_hashes]
        specifications = [
            item for item in self.strategy_ledger.verify()
            if item.get("replay_plan_id") == inputs.replay_plan_id
        ]
        if len(specifications) != 1:
            raise ValueError("replay plan requires exactly one verified strategy specification")
        specification = specifications[0]
        policies = [
            item for item in self.execution_policy_ledger.verify()
            if item.get("replay_plan_id") == inputs.replay_plan_id
        ]
        if len(policies) != 1:
            raise ValueError("replay plan requires exactly one verified execution policy")
        policy = policies[0]
        profile = resolve_authenticated_execution_profile(
            policy, payload["execution_scenario"]
        )
        binding = {
            "replay_plan_id": inputs.replay_plan_id,
            "replay_plan_record_hash": inputs.replay_plan_record_hash,
            "dataset_commitment_sha256": inputs.dataset_commitment_sha256,
            "replay_plan_git_revision": specification["replay_plan_git_revision"],
            "replay_plan_evaluation_start": specification[
                "replay_plan_evaluation_start"
            ],
            "replay_plan_evaluation_end": specification["replay_plan_evaluation_end"],
            "strategy_specification_id": specification["strategy_specification_id"],
            "strategy_specification_record_hash": specification["record_hash"],
            "strategy_entrypoint": specification["strategy_entrypoint"],
            "strategy_source_sha256": specification["strategy_source_sha256"],
            "execution_profile_version": profile.profile_version,
            "replay_execution_policy_record_id": (
                profile.replay_execution_policy_record_id
            ),
            "replay_execution_policy_record_hash": (
                profile.replay_execution_policy_record_hash
            ),
            "execution_policy_id": profile.execution_policy_id,
            "execution_policy_version": profile.execution_policy_version,
        }
        if (
            inputs.admission_id != payload["source_id"]
            or attestation.source_content_sha256 != payload["source_content_sha256"]
            or attestation.validation_receipt_sha256
            != payload["validation_receipt_sha256"]
            or expected_roles != payload["evidence_role_hashes"]
            or inputs.broker_connection_allowed is not False
            or inputs.orders_submitted is not False
            or inputs.live_trading_enabled is not False
            or specification["replay_plan_record_hash"]
            != inputs.replay_plan_record_hash
            or specification["strategy_version"] != payload["strategy_version"]
            or specification["parameter_hash"] != payload["parameter_hash"]
            or specification["replay_plan_evaluation_start"]
            != payload["evaluation_start"]
            or specification["replay_plan_evaluation_end"]
            != payload["evaluation_end"]
            or profile.replay_plan_record_hash != inputs.replay_plan_record_hash
            or profile.engine_config_canonical_json
            != payload["engine_config_canonical_json"]
            or profile.engine_config_sha256 != payload["engine_config_sha256"]
            or profile.fee_schedule.schedule_id != payload["fee_schedule_id"]
            or profile.scenario != payload["execution_scenario"]
            or any(
                field in payload and payload[field] != expected
                for field, expected in binding.items()
            )
        ):
            raise ValueError("replay result no longer matches authenticated input evidence")
        return binding

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Replay-run audit ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank replay-run audit line at {line_number}."
                    )
                try:
                    value = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid replay-run audit JSON at {line_number}."
                    ) from error
                if not isinstance(value, dict):
                    raise LedgerIntegrityError(
                        f"Replay-run audit line {line_number} is not an object."
                    )
                records.append(value)
        return records

    @staticmethod
    def _identity(payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            "source_id": payload["source_id"],
            "source_content_sha256": payload["source_content_sha256"],
            "validation_receipt_sha256": payload["validation_receipt_sha256"],
            **{field: payload[field] for field in sorted(STRATEGY_BINDING_FIELDS)},
            **{field: payload[field] for field in sorted(EXECUTION_BINDING_FIELDS)},
            "strategy_version": payload["strategy_version"],
            "parameter_hash": payload["parameter_hash"],
            "engine_policy_version": payload["engine_policy_version"],
            "engine_config_sha256": payload["engine_config_sha256"],
            "fee_schedule_id": payload["fee_schedule_id"],
            "execution_scenario": payload["execution_scenario"],
            "evaluation_start": payload["evaluation_start"],
            "evaluation_end": payload["evaluation_end"],
            "git_revision": payload["git_revision"],
        }

    def append(
        self,
        *,
        result: BacktestResult,
        git_revision: str,
        recorded_by: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        payload = _result_payload(result)
        payload = {**payload, **self._verify_authenticated_parent(payload)}
        git = _git_revision(git_revision)
        if git != payload["replay_plan_git_revision"]:
            raise ValueError("run Git revision must match the preregistered replay plan")
        recorded = _time(recorded_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if not now - MAX_CLOCK_SKEW <= recorded <= now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at must match the actual append time")
        if recorded < _time(payload["evaluation_end"]):
            raise ValueError("run cannot be recorded before its evaluation window ends")
        identity = self._identity({**payload, "git_revision": git})
        replay_run_id = "REPLAY-RUN-" + hashlib.sha256(
            _canonical_json([identity, POLICY_VERSION]).encode("utf-8")
        ).hexdigest()[:32].upper()
        record = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "replay_run_id": replay_run_id,
            "record_type": "IMMUTABLE_AUTHENTICATED_REPLAY_RUN_AUDIT",
            "status": "MECHANICAL_SIMULATION_RECORDED_NOT_A_TRACK_RECORD",
            "recorded_at": recorded.isoformat(timespec="microseconds"),
            "recorded_by": _required(recorded_by, "recorded_by", 100),
            "git_revision": git,
            **payload,
        }
        # Validate the candidate's arithmetic and cross-trace relationships before
        # making the append durable.  The placeholder chain fields are ignored by
        # this semantic validation and are replaced atomically below.
        _result_payload_from_record({
            **record,
            "previous_hash": GENESIS_HASH,
            "record_hash": GENESIS_HASH,
        })
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_APPEND, 0o600)
        try:
            with os.fdopen(descriptor, "a+", encoding="utf-8", closefd=False) as target:
                fcntl.flock(target.fileno(), fcntl.LOCK_EX)
                existing = self.verify()
                same_id = [
                    item for item in existing if item.get("replay_run_id") == replay_run_id
                ]
                if same_id:
                    comparable = {
                        key: value
                        for key, value in same_id[0].items()
                        if key not in {"recorded_at", "previous_hash", "record_hash"}
                    }
                    retry = {
                        key: value for key, value in record.items() if key != "recorded_at"
                    }
                    if allow_existing and comparable == retry:
                        return same_id[0]
                    raise LedgerIntegrityError(
                        "replay run identity already exists with different audit content"
                    )
                material = {
                    **record,
                    "previous_hash": (
                        existing[-1]["record_hash"] if existing else GENESIS_HASH
                    ),
                }
                complete = {**material, "record_hash": _record_hash(material)}
                _write_all(descriptor, (_canonical_json(complete) + "\n").encode("utf-8"))
                os.fsync(descriptor)
                return complete
        finally:
            os.close(descriptor)

    def verify(self) -> list[dict[str, Any]]:
        previous = GENESIS_HASH
        seen_ids: set[str] = set()
        seen_identity: set[str] = set()
        records = self.records()
        for index, record in enumerate(records, start=1):
            try:
                material = {
                    key: value for key, value in record.items() if key != "record_hash"
                }
                payload = _result_payload_from_record(record)
                self._verify_authenticated_parent(payload)
                identity = self._identity(record)
                identity_hash = _record_hash(identity)
                expected_id = "REPLAY-RUN-" + hashlib.sha256(
                    _canonical_json([identity, POLICY_VERSION]).encode("utf-8")
                ).hexdigest()[:32].upper()
                recorded = _time(record["recorded_at"])
                if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
                    raise ValueError("future record")
                boundary = (
                    record.get("schema_version") == SCHEMA_VERSION
                    and record.get("policy_version") == POLICY_VERSION
                    and record.get("record_type")
                    == "IMMUTABLE_AUTHENTICATED_REPLAY_RUN_AUDIT"
                    and record.get("status")
                    == "MECHANICAL_SIMULATION_RECORDED_NOT_A_TRACK_RECORD"
                    and record.get("replay_run_id") == expected_id
                    and expected_id not in seen_ids
                    and identity_hash not in seen_identity
                    and record.get("previous_hash") == previous
                    and record.get("record_hash") == _record_hash(material)
                    and GIT_REVISION.fullmatch(str(record.get("git_revision") or "")) is not None
                    and record.get("git_revision")
                    == record.get("replay_plan_git_revision")
                    and recorded >= _time(record["evaluation_end"])
                    and bool(_required(record.get("recorded_by"), "recorded_by", 100))
                    and all(record.get(field) is False for field in FIXED_FALSE)
                )
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Replay-run audit {index} is invalid."
                ) from error
            if not boundary:
                raise LedgerIntegrityError(
                    f"Replay-run audit {index} violates its immutable boundary."
                )
            seen_ids.add(expected_id)
            seen_identity.add(identity_hash)
            previous = record["record_hash"]
        return records

    def require_paired_scenarios(
        self,
        *,
        replay_plan_id: str,
        strategy_specification_id: str,
    ) -> dict[str, dict[str, Any]]:
        """Return the required pair or fail; this makes no promotion claim."""
        plan = _required(replay_plan_id, "replay_plan_id")
        strategy = _required(
            strategy_specification_id, "strategy_specification_id"
        )
        matches = [
            item for item in self.verify()
            if item["replay_plan_id"] == plan
            and item["strategy_specification_id"] == strategy
        ]
        by_scenario = {item["execution_scenario"]: item for item in matches}
        if len(matches) != 2 or set(by_scenario) != {"BASE", "PESSIMISTIC"}:
            raise ValueError(
                "audited replay is incomplete until BASE and PESSIMISTIC both exist"
            )
        common = (
            "source_id", "source_content_sha256", "validation_receipt_sha256",
            "replay_plan_id", "replay_plan_record_hash", "dataset_commitment_sha256",
            "strategy_specification_id", "strategy_specification_record_hash",
            "strategy_entrypoint", "strategy_source_sha256", "strategy_version",
            "parameter_hash", "evaluation_start", "evaluation_end", "git_revision",
            "replay_execution_policy_record_id", "replay_execution_policy_record_hash",
            "execution_policy_id", "execution_policy_version",
        )
        if any(
            by_scenario["BASE"][field] != by_scenario["PESSIMISTIC"][field]
            for field in common
        ):
            raise LedgerIntegrityError(
                "BASE and PESSIMISTIC records do not share one authenticated identity"
            )
        return by_scenario


def _result_payload_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    required = {
        "strategy_version", "parameter_hash", "source_id", "source_content_sha256",
        "validation_receipt_sha256", "evidence_role_hashes", "engine_policy_version",
        "engine_config_sha256", "engine_config_canonical_json",
        "fee_schedule_id", "execution_scenario",
        "evaluation_start", "evaluation_end", "starting_equity", "ending_equity",
        "total_return", "maximum_drawdown", "executions", "completed_trades",
        "equity_curve", "sizing_decisions", "portfolio_states",
        "executive_intents", "cash_reservations",
        "no_lookahead_contract_enforced", "mechanical_simulation_only", *FIXED_FALSE,
        *STRATEGY_BINDING_FIELDS,
        *EXECUTION_BINDING_FIELDS,
    }
    if set(record) != required | METADATA_FIELDS:
        raise ValueError("replay-run audit has missing or unsupported fields")
    if not required.issubset(record):
        raise ValueError("replay-run audit is missing result fields")
    if (
        not record["portfolio_states"]
        or len(record["executions"]) != len(record["sizing_decisions"])
    ):
        raise ValueError("replay-run audit lacks complete traces")
    if record["no_lookahead_contract_enforced"] is not True:
        raise ValueError("no-lookahead boundary changed")
    if record["mechanical_simulation_only"] is not True:
        raise ValueError("simulation label changed")
    if _time(record["evaluation_start"]) >= _time(record["evaluation_end"]):
        raise ValueError("evaluation window changed")
    for name in (
        "parameter_hash", "source_content_sha256", "validation_receipt_sha256",
        "engine_config_sha256", "replay_plan_record_hash",
        "dataset_commitment_sha256", "strategy_specification_record_hash",
        "strategy_source_sha256",
        "replay_execution_policy_record_hash",
    ):
        _hash(record[name], name)
    engine_config_canonical = _required(
        record["engine_config_canonical_json"], "engine_config_canonical_json", 100_000
    )
    if hashlib.sha256(engine_config_canonical.encode("utf-8")).hexdigest() != record[
        "engine_config_sha256"
    ]:
        raise ValueError("engine configuration hash no longer matches its exact content")
    try:
        engine_config_value = json.loads(engine_config_canonical)
    except json.JSONDecodeError as error:
        raise ValueError("engine configuration is not canonical JSON") from error
    if _canonical_json(engine_config_value) != engine_config_canonical:
        raise ValueError("engine configuration JSON is not canonical")
    _validate_engine_execution_economics(record, engine_config_value)
    _git_revision(record["replay_plan_git_revision"])
    for name in (
        "replay_plan_id", "strategy_specification_id", "strategy_entrypoint",
        "execution_profile_version", "replay_execution_policy_record_id",
        "execution_policy_id", "execution_policy_version",
    ):
        _required(record[name], name, 300)
    _time(record["replay_plan_evaluation_start"])
    _time(record["replay_plan_evaluation_end"])
    role_hashes = record["evidence_role_hashes"]
    if (
        not isinstance(role_hashes, list)
        or role_hashes != sorted(role_hashes)
        or {item[0] for item in role_hashes} != AUTHENTICATED_REPLAY_ROLES
        or len(role_hashes) != len(AUTHENTICATED_REPLAY_ROLES)
        or any(
            not isinstance(item, list)
            or len(item) != 2
            or SHA256.fullmatch(str(item[1])) is None
            for item in role_hashes
        )
    ):
        raise ValueError("authenticated evidence role hashes changed")
    starting = _finite_decimal(record["starting_equity"], "starting_equity", positive=True)
    ending = _finite_decimal(record["ending_equity"], "ending_equity", non_negative=True)
    total_return = _finite_decimal(record["total_return"], "total_return")
    maximum_drawdown = _finite_decimal(
        record["maximum_drawdown"], "maximum_drawdown", non_negative=True
    )
    if total_return != ending / starting - Decimal("1"):
        raise ValueError("total return no longer reconciles to equity")
    if maximum_drawdown > Decimal("1"):
        raise ValueError("maximum drawdown exceeds 100%")

    executions = record["executions"]
    sizing = record["sizing_decisions"]
    states = record["portfolio_states"]
    completed = record["completed_trades"]
    curve = record["equity_curve"]
    intents = record["executive_intents"]
    reservations = record["cash_reservations"]
    if not all(
        isinstance(value, list)
        for value in (
            executions, sizing, states, completed, curve, intents, reservations
        )
    ):
        raise ValueError("replay-run trace collections changed type")
    for item in executions:
        _validate_execution(item)
    for item in sizing:
        _validate_sizing(item, engine_config_value)
    _match_executions_and_sizing(executions, sizing, engine_config_value)
    for item in completed:
        _validate_completed_trade(item)
    _match_completed_trades_and_exits(completed, executions)
    prior_curve_time = None
    curve_values = [starting]
    for item in curve:
        if not isinstance(item, list) or len(item) != 2:
            raise ValueError("equity curve row is malformed")
        moment = _time(item[0])
        value = _finite_decimal(item[1], "equity curve value", non_negative=True)
        if prior_curve_time is not None and moment <= prior_curve_time:
            raise ValueError("equity curve is not strictly chronological")
        prior_curve_time = moment
        curve_values.append(value)
    curve_values.append(ending)
    if _drawdown(curve_values) != maximum_drawdown:
        raise ValueError("maximum drawdown no longer reconciles to the equity curve")
    sequences = [item.get("sequence") for item in record["portfolio_states"]]
    if sequences != list(range(1, len(sequences) + 1)):
        raise ValueError("portfolio-state sequence changed")
    prior_state_time = None
    for item in states:
        _validate_state(item)
        moment = _time(item["as_of_at"])
        if prior_state_time is not None and moment < prior_state_time:
            raise ValueError("portfolio-state sequence is not chronological")
        prior_state_time = moment
    if _finite_decimal(states[-1]["equity"], "final state equity", non_negative=True) != ending:
        raise ValueError("final portfolio state does not reconcile to ending equity")
    intent_sequences = [item.get("sequence") for item in intents]
    if intent_sequences != list(range(1, len(intent_sequences) + 1)):
        raise ValueError("Executive intent sequence changed")
    intent_keys: set[tuple[str, str, str]] = set()
    for item in intents:
        if set(item) != {
            "sequence", "decision_at", "symbol", "intent_sha256",
            "risk_envelope_sha256", "action", "current_weight",
            "target_weight", "reason_codes",
        }:
            raise ValueError("Executive intent trace structure changed")
        decision_at = _time(item["decision_at"])
        symbol = _required(item["symbol"], "Executive intent symbol", 32)
        intent_sha256 = _hash(item["intent_sha256"], "intent_sha256")
        _hash(item["risk_envelope_sha256"], "risk_envelope_sha256")
        if item["action"] not in {"CASH", "HOLD", "ENTER_LONG", "REDUCE", "EXIT"}:
            raise ValueError("Executive intent action changed")
        current_weight = _finite_decimal(
            item["current_weight"], "Executive current_weight", non_negative=True
        )
        target_weight = _finite_decimal(
            item["target_weight"], "Executive target_weight", non_negative=True
        )
        if current_weight > Decimal("1") or target_weight > Decimal("1"):
            raise ValueError("Executive intent weight exceeds 100%")
        if not isinstance(item["reason_codes"], list) or not item["reason_codes"]:
            raise ValueError("Executive intent reasons changed")
        key = (decision_at.isoformat(), symbol, intent_sha256)
        if key in intent_keys:
            raise ValueError("Executive intent trace is duplicated")
        intent_keys.add(key)
    prior_reservation_key: tuple[int, str] | None = None
    reservation_keys: set[tuple[str, str, str, str]] = set()
    for item in reservations:
        if set(item) != {
            "batch_sequence", "decision_at", "execution_at", "intent_sha256",
            "symbol", "requested_cash", "reserved_cash", "consumed_cash",
            "released_cash", "status",
        }:
            raise ValueError("cash reservation trace structure changed")
        batch = item["batch_sequence"]
        symbol = _required(item["symbol"], "cash reservation symbol", 32)
        if type(batch) is not int or batch < 1:
            raise ValueError("cash reservation batch sequence changed")
        order_key = (batch, symbol)
        if prior_reservation_key is not None and order_key <= prior_reservation_key:
            raise ValueError("cash reservations are not canonically ordered")
        prior_reservation_key = order_key
        decision_at = _time(item["decision_at"])
        execution_at = _time(item["execution_at"])
        if execution_at <= decision_at:
            raise ValueError("cash reservation does not execute after its decision")
        intent_sha256 = _hash(item["intent_sha256"], "reservation intent_sha256")
        requested = _finite_decimal(
            item["requested_cash"], "requested_cash", non_negative=True
        )
        reserved = _finite_decimal(
            item["reserved_cash"], "reserved_cash", non_negative=True
        )
        consumed = _finite_decimal(
            item["consumed_cash"], "consumed_cash", non_negative=True
        )
        released = _finite_decimal(
            item["released_cash"], "released_cash", non_negative=True
        )
        if reserved > requested or consumed + released != reserved:
            raise ValueError("cash reservation no longer reconciles")
        if item["status"] not in {"FILLED", "PARTIAL", "REJECTED"}:
            raise ValueError("cash reservation status changed")
        if (decision_at.isoformat(), symbol, intent_sha256) not in intent_keys:
            raise ValueError("cash reservation lacks its Executive intent trace")
        reservation_key = (
            decision_at.isoformat(), execution_at.isoformat(), symbol, intent_sha256
        )
        if reservation_key in reservation_keys:
            raise ValueError("cash reservation trace is duplicated")
        reservation_keys.add(reservation_key)
    return {key: record[key] for key in required}


def _finite_decimal(
    value: Any, name: str, *, positive: bool = False, non_negative: bool = False,
) -> Decimal:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a canonical decimal string")
    try:
        resolved = Decimal(value)
    except Exception as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    canonical = "0" if resolved == 0 else format(resolved.normalize(), "f")
    if (
        not resolved.is_finite()
        or canonical != value
        or (positive and resolved <= 0)
        or (non_negative and resolved < 0)
    ):
        raise ValueError(f"{name} is outside its canonical decimal boundary")
    return resolved


def _validate_engine_execution_economics(
    record: Mapping[str, Any], engine: Any,
) -> None:
    if not isinstance(engine, Mapping) or set(engine) != {
        "engine_policy_version", "config", "fee_schedule"
    }:
        raise ValueError("engine configuration has an unsupported structure")
    config = engine["config"]
    fee_schedule = engine["fee_schedule"]
    if not isinstance(config, Mapping) or not isinstance(fee_schedule, Mapping):
        raise ValueError("engine configuration sections are malformed")
    if (
        engine["engine_policy_version"] != record["engine_policy_version"]
        or config.get("execution_scenario") != record["execution_scenario"]
        or fee_schedule.get("schedule_id") != record["fee_schedule_id"]
        or _finite_decimal(config.get("initial_cash"), "initial_cash", positive=True)
        != _finite_decimal(record["starting_equity"], "starting_equity", positive=True)
    ):
        raise ValueError("result identity does not match its engine configuration")
    if engine["engine_policy_version"] not in _SUPPORTED_ENGINE_POLICY_VERSIONS:
        raise ValueError("engine policy version is unsupported by replay audit")
    tiers = fee_schedule.get("tiers")
    if not isinstance(tiers, list) or len(tiers) != 1 or not isinstance(tiers[0], Mapping):
        raise ValueError("authenticated replay requires one exact commission tier")
    tier = tiers[0]
    if set(tier) != {
        "prior_monthly_notional_below", "variable_bps", "minimum_fee"
    } or tier["prior_monthly_notional_below"] is not None:
        raise ValueError("authenticated replay commission tier changed")
    commission = _finite_decimal(tier["variable_bps"], "commission_bps", non_negative=True)
    minimum = _finite_decimal(tier["minimum_fee"], "minimum_fee", non_negative=True)
    spread = _finite_decimal(
        config.get("bid_ask_half_spread_bps"), "bid_ask_half_spread_bps",
        non_negative=True,
    )
    slippage = _finite_decimal(
        config.get("baseline_slippage_bps"), "baseline_slippage_bps",
        non_negative=True,
    )
    latency = _finite_decimal(
        config.get("latency_adverse_bps"), "latency_adverse_bps",
        non_negative=True,
    )
    maximum_impact = _finite_decimal(
        config.get("liquidity_impact_bps_at_max_participation"),
        "liquidity_impact_bps_at_max_participation", non_negative=True,
    )
    maximum_participation = _finite_decimal(
        config.get("maximum_lagged_volume_participation"),
        "maximum_lagged_volume_participation", positive=True,
    )
    if engine["engine_policy_version"] in {
        _POSITION_CAP_ENGINE_POLICY_VERSION,
        _PORTFOLIO_ENGINE_POLICY_VERSION,
    }:
        maximum_position_fraction = _finite_decimal(
            config.get("maximum_position_fraction"),
            "maximum_position_fraction",
            positive=True,
        )
        if maximum_position_fraction > Decimal("1"):
            raise ValueError("maximum position fraction exceeds 100%")
    for item in record["executions"]:
        filled = _finite_decimal(item["filled_quantity"], "filled_quantity")
        price = _finite_decimal(item["execution_price"], "execution_price", positive=True)
        fee = _finite_decimal(item["fee"], "fee", non_negative=True)
        if item["action"] == "TERMINAL_SETTLEMENT":
            terminal_costs = (
                "bid_ask_half_spread_bps",
                "baseline_slippage_bps",
                "latency_adverse_bps",
                "liquidity_impact_bps",
                "total_adverse_execution_bps",
            )
            if fee != 0 or any(
                _finite_decimal(item[field], field, non_negative=True) != 0
                for field in terminal_costs
            ):
                raise ValueError(
                    "terminal settlement cannot carry commission or execution costs"
                )
            continue
        expected_fee = max(minimum, filled * price * commission / Decimal("10000")) if filled else Decimal("0")
        if fee != expected_fee:
            raise ValueError("execution commission no longer matches the policy-derived tier")
        reference = _finite_decimal(item["reference_price"], "reference_price", positive=True)
        liquidity = _finite_decimal(
            item["lagged_liquidity_notional"], "lagged_liquidity_notional",
            non_negative=True,
        )
        participation = filled * reference / liquidity if liquidity > 0 else Decimal("0")
        normalized = min(Decimal("1"), participation / maximum_participation)
        expected_impact = maximum_impact * normalized * normalized
        impact = _finite_decimal(item["liquidity_impact_bps"], "liquidity_impact_bps")
        total = spread + slippage + latency + expected_impact
        direction = Decimal("1") if item["action"] == "BUY" else Decimal("-1")
        expected_price = reference * (Decimal("1") + direction * total / Decimal("10000"))
        if (
            item["bid_ask_half_spread_bps"] != _canonical_decimal(spread)
            or item["baseline_slippage_bps"] != _canonical_decimal(slippage)
            or item["latency_adverse_bps"] != _canonical_decimal(latency)
            or impact != expected_impact
            or _finite_decimal(
                item["total_adverse_execution_bps"], "total_adverse_execution_bps"
            ) != total
            or price != expected_price
            or filled * reference > liquidity * maximum_participation
        ):
            raise ValueError("execution economics no longer match the policy-derived engine")


def _exact_fields(item: Any, expected: set[str], name: str) -> Mapping[str, Any]:
    if not isinstance(item, Mapping) or set(item) != expected:
        raise ValueError(f"{name} has missing or unsupported fields")
    return item


def _validate_execution(value: Any) -> None:
    item = _exact_fields(value, set(ExecutionRecord.__dataclass_fields__), "execution")
    action = item["action"]
    if action not in {"BUY", "SELL", "TERMINAL_SETTLEMENT"}:
        raise ValueError("execution action changed")
    signal = _time(item["signal_at"]); executed = _time(item["executed_at"])
    if signal > executed:
        raise ValueError("execution predates its signal")
    reference = _finite_decimal(item["reference_price"], "reference_price", positive=True)
    price = _finite_decimal(item["execution_price"], "execution_price", positive=True)
    requested = _finite_decimal(item["requested_quantity"], "requested_quantity", non_negative=True)
    filled = _finite_decimal(item["filled_quantity"], "filled_quantity", non_negative=True)
    fee = _finite_decimal(item["fee"], "fee", non_negative=True)
    if filled > requested:
        raise ValueError("execution fill exceeds request")
    for name in (
        "lagged_liquidity_notional", "bid_ask_half_spread_bps",
        "baseline_slippage_bps", "latency_adverse_bps", "liquidity_impact_bps",
        "total_adverse_execution_bps",
    ):
        _finite_decimal(item[name], name, non_negative=True)
    total_bps = sum(
        (_finite_decimal(item[name], name, non_negative=True) for name in (
            "bid_ask_half_spread_bps", "baseline_slippage_bps",
            "latency_adverse_bps", "liquidity_impact_bps"
        )), Decimal("0")
    )
    if total_bps != _finite_decimal(
        item["total_adverse_execution_bps"], "total_adverse_execution_bps"
    ):
        raise ValueError("execution cost components no longer reconcile")
    status = item["status"]
    if status == "REJECTED" and filled != 0:
        raise ValueError("rejected execution has a fill")
    if status == "FILLED" and (filled <= 0 or filled != requested):
        raise ValueError("filled execution does not match its request")
    if status in {"PARTIALLY_FILLED", "PARTIALLY_FILLED_CANCELED"} and not 0 < filled < requested:
        raise ValueError("partial execution is not partial")
    if status not in {"REJECTED", "FILLED", "PARTIALLY_FILLED", "PARTIALLY_FILLED_CANCELED"}:
        raise ValueError("execution status changed")
    if action == "BUY" and price < reference:
        raise ValueError("buy execution is not adverse")
    if action == "SELL" and price > reference:
        raise ValueError("sell execution is not adverse")
    if action == "TERMINAL_SETTLEMENT" and (price != reference or fee != 0):
        raise ValueError("terminal settlement economics changed")


def _validate_sizing(value: Any, engine: Mapping[str, Any]) -> None:
    item = _exact_fields(value, set(SizingDecisionTrace.__dataclass_fields__), "sizing trace")
    if item["action"] not in {"BUY", "SELL", "TERMINAL_SETTLEMENT"}:
        raise ValueError("sizing action changed")
    if _time(item["signal_at"]) > _time(item["evaluated_at"]):
        raise ValueError("sizing decision predates its signal")
    for name in (
        "portfolio_equity_before", "settled_cash_before", "unsettled_cash_before",
        "position_quantity_before", "open_risk_before", "liquidity_notional",
        "liquidity_quantity_limit", "requested_quantity", "filled_quantity",
    ):
        _finite_decimal(item[name], name, non_negative=True)
    requested = _finite_decimal(item["requested_quantity"], "requested_quantity")
    filled = _finite_decimal(item["filled_quantity"], "filled_quantity")
    if filled > requested:
        raise ValueError("sizing fill exceeds request")
    for name in (
        "risk_per_share", "risk_budget", "risk_quantity_limit",
        "cash_quantity_limit", "stop_price_after",
    ):
        if item[name] is not None:
            _finite_decimal(item[name], name, non_negative=True)
    constraints = item["limiting_constraints"]
    allowed = {
        "RISK_BUDGET", "LIQUIDITY_CAP", "CASH_AND_FEES", "POSITION_QUANTITY",
        "POSITION_FRACTION_CAP",
        "UNIVERSE_INELIGIBLE_AT_EXECUTION", "NO_POSITIVE_ATR_RISK_DISTANCE",
        "MANDATORY_TERMINAL_OUTCOME",
        "EXECUTIVE_TARGET_WEIGHT", "HARD_POSITION_FRACTION_MAXIMUM",
        "STOP_ALREADY_BREACHED_AT_EXECUTION",
        "HARD_POSITION_OPEN_RISK_MAXIMUM", "HARD_AGGREGATE_OPEN_RISK",
        "SHARED_CASH_RESERVATION",
    }
    if (
        not isinstance(constraints, list)
        or not constraints
        or len(set(constraints)) != len(constraints)
        or set(constraints) - allowed
    ):
        raise ValueError("sizing constraints changed")
    action = item["action"]
    liquidity_limit = _finite_decimal(
        item["liquidity_quantity_limit"], "liquidity_quantity_limit"
    )
    zero_atr_rejection = (
        action == "BUY"
        and constraints == ["NO_POSITIVE_ATR_RISK_DISTANCE"]
    )
    universe_rejection = (
        action == "BUY"
        and constraints == ["UNIVERSE_INELIGIBLE_AT_EXECUTION"]
    )
    if zero_atr_rejection:
        if (
            item["risk_per_share"] != "0"
            or item["risk_budget"] != "0"
            or item["risk_quantity_limit"] != "0"
            or item["cash_quantity_limit"] != "0"
            or requested != 0
            or filled != 0
            or liquidity_limit != 0
            or item["stop_price_after"] is not None
        ):
            raise ValueError("zero-ATR rejected buy sizing changed")
    elif universe_rejection:
        if (
            item["risk_budget"] is not None
            or item["risk_quantity_limit"] != "0"
            or item["cash_quantity_limit"] != "0"
            or requested != 0
            or filled != 0
            or liquidity_limit != 0
            or item["stop_price_after"] is not None
        ):
            raise ValueError("universe-ineligible rejected buy sizing changed")
    elif action == "BUY" and item["risk_budget"] is not None:
        portfolio_buy = (
            engine.get("engine_policy_version") == _PORTFOLIO_ENGINE_POLICY_VERSION
            and item["reason"] == "EXECUTIVE_PORTFOLIO_TARGET"
        )
        risk_per_share = _finite_decimal(
            item["risk_per_share"], "risk_per_share",
            non_negative=portfolio_buy, positive=not portfolio_buy,
        )
        risk_budget = _finite_decimal(item["risk_budget"], "risk_budget", non_negative=True)
        risk_limit = _finite_decimal(item["risk_quantity_limit"], "risk_quantity_limit")
        cash_limit = _finite_decimal(item["cash_quantity_limit"], "cash_quantity_limit")
        if risk_per_share > 0 and risk_limit != (
            risk_budget / risk_per_share
        ).to_integral_value(rounding=ROUND_FLOOR):
            raise ValueError("risk quantity no longer reconciles to budget and ATR distance")
        if risk_per_share == 0 and risk_limit != 0:
            raise ValueError("zero risk distance has a positive risk quantity limit")
        position_cap_active = engine.get("engine_policy_version") in {
            _POSITION_CAP_ENGINE_POLICY_VERSION,
            _PORTFOLIO_ENGINE_POLICY_VERSION,
        }
        if portfolio_buy:
            if filled != min(requested, risk_limit, liquidity_limit, cash_limit):
                raise ValueError("portfolio buy sizing limits no longer reconcile")
        elif position_cap_active:
            if requested > risk_limit:
                raise ValueError("buy request exceeds its risk quantity limit")
            if requested < risk_limit and "POSITION_FRACTION_CAP" not in constraints:
                raise ValueError("reduced buy request lacks its position-fraction constraint")
        elif requested != risk_limit:
            raise ValueError("legacy buy request no longer matches its risk limit")
        if not portfolio_buy and filled != min(requested, liquidity_limit, cash_limit):
            raise ValueError("buy sizing limits no longer reconcile")
    elif action == "BUY" and requested > 0:
        raise ValueError("positive buy request lacks its complete risk sizing inputs")
    elif action == "SELL":
        position_quantity = _finite_decimal(
            item["position_quantity_before"], "position_quantity_before"
        )
        if requested != position_quantity or filled != min(requested, liquidity_limit):
            raise ValueError("sell sizing limits no longer reconcile")
    elif action == "TERMINAL_SETTLEMENT":
        if requested != filled or constraints != ["MANDATORY_TERMINAL_OUTCOME"]:
            raise ValueError("terminal sizing trace changed")


def _match_executions_and_sizing(
    executions: list[Mapping[str, Any]], sizing: list[Mapping[str, Any]],
    engine: Mapping[str, Any],
) -> None:
    def key(item: Mapping[str, Any], sizing_item: bool) -> tuple[Any, ...]:
        return (
            item["symbol"], item["action"], item["reason"], item["signal_at"],
            item["evaluated_at"] if sizing_item else item["executed_at"],
            item["requested_quantity"], item["filled_quantity"],
        )
    if sorted(key(item, False) for item in executions) != sorted(
        key(item, True) for item in sizing
    ):
        raise ValueError("execution and sizing traces no longer reconcile one-to-one")
    execution_by_key = {key(item, False): item for item in executions}
    for trace in sizing:
        execution = execution_by_key[key(trace, True)]
        if (
            trace["action"] == "BUY"
            and engine.get("engine_policy_version") in {
                _POSITION_CAP_ENGINE_POLICY_VERSION,
                _PORTFOLIO_ENGINE_POLICY_VERSION,
            }
        ):
            portfolio_buy = (
                engine.get("engine_policy_version")
                == _PORTFOLIO_ENGINE_POLICY_VERSION
                and trace["reason"] == "EXECUTIVE_PORTFOLIO_TARGET"
            )
            config = engine["config"]
            fraction = _finite_decimal(
                config.get("maximum_position_fraction"),
                "maximum_position_fraction",
                positive=True,
            )
            equity = _finite_decimal(
                trace["portfolio_equity_before"],
                "portfolio_equity_before",
                positive=True,
            )
            reference = _finite_decimal(
                execution["reference_price"], "reference_price", positive=True
            )
            maximum_cost_bps = sum(
                (
                    _finite_decimal(config.get(name), name, non_negative=True)
                    for name in (
                        "bid_ask_half_spread_bps",
                        "baseline_slippage_bps",
                        "latency_adverse_bps",
                        "liquidity_impact_bps_at_max_participation",
                    )
                ),
                Decimal("0"),
            )
            maximum_price = reference * (
                Decimal("1") + maximum_cost_bps / Decimal("10000")
            )
            expected_position_limit = (
                equity * fraction / maximum_price
            ).to_integral_value(rounding=ROUND_FLOOR)
            risk_limit = _finite_decimal(
                trace["risk_quantity_limit"], "risk_quantity_limit", non_negative=True
            )
            requested = _finite_decimal(
                trace["requested_quantity"], "requested_quantity", non_negative=True
            )
            if (
                not portfolio_buy
                and requested != min(risk_limit, expected_position_limit)
            ):
                raise ValueError("buy request no longer reconciles to its position-fraction cap")
            filled = _finite_decimal(
                trace["filled_quantity"], "filled_quantity", non_negative=True
            )
            execution_price = _finite_decimal(
                execution["execution_price"], "execution_price", positive=True
            )
            if filled * execution_price > equity * fraction:
                raise ValueError("filled buy notional exceeds the maximum position fraction")
        if trace["filled_quantity"] != "0" and trace["action"] == "BUY":
            risk_per_share = _finite_decimal(
                trace["risk_per_share"], "risk_per_share", positive=True
            )
            if (
                engine.get("engine_policy_version")
                == _PORTFOLIO_ENGINE_POLICY_VERSION
                and trace["reason"] == "EXECUTIVE_PORTFOLIO_TARGET"
            ):
                config = engine["config"]
                reference = _finite_decimal(
                    execution["reference_price"], "reference_price", positive=True
                )
                maximum_cost_bps = sum(
                    (
                        _finite_decimal(config.get(name), name, non_negative=True)
                        for name in (
                            "bid_ask_half_spread_bps",
                            "baseline_slippage_bps",
                            "latency_adverse_bps",
                            "liquidity_impact_bps_at_max_participation",
                        )
                    ),
                    Decimal("0"),
                )
                expected_stop = reference * (
                    Decimal("1") + maximum_cost_bps / Decimal("10000")
                ) - risk_per_share
            else:
                expected_stop = _finite_decimal(
                    execution["execution_price"], "execution_price"
                ) - risk_per_share
            if _finite_decimal(trace["stop_price_after"], "stop_price_after") != expected_stop:
                raise ValueError("post-fill stop no longer reconciles to execution and ATR risk")


def _validate_completed_trade(value: Any) -> None:
    item = _exact_fields(value, set(CompletedTrade.__dataclass_fields__), "completed trade")
    if _time(item["opened_at"]) > _time(item["closed_at"]):
        raise ValueError("completed trade closes before opening")
    cost = _finite_decimal(item["entry_total_cost"], "entry_total_cost", positive=True)
    proceeds = _finite_decimal(item["exit_net_proceeds"], "exit_net_proceeds", non_negative=True)
    return_rate = _finite_decimal(item["return_rate"], "return_rate")
    if return_rate != proceeds / cost - Decimal("1"):
        raise ValueError("completed trade return no longer reconciles")


def _match_completed_trades_and_exits(
    completed: list[Mapping[str, Any]], executions: list[Mapping[str, Any]],
) -> None:
    windows = [
        (_time(item["opened_at"]), _time(item["closed_at"]), item["symbol"])
        for item in completed
    ]
    if windows != sorted(windows) or any(
        left[1] >= right[0] and left[2] == right[2]
        for left, right in zip(windows, windows[1:])
    ):
        raise ValueError("completed round-trip windows overlap or changed order")
    buy_entries: dict[tuple[str, str], Decimal] = {}
    filled_exits: list[Mapping[str, Any]] = []
    for item in executions:
        filled = _finite_decimal(item["filled_quantity"], "filled_quantity")
        if filled == 0:
            continue
        if item["action"] == "BUY":
            price = _finite_decimal(item["execution_price"], "execution_price")
            fee = _finite_decimal(item["fee"], "fee")
            key = (item["symbol"], item["executed_at"])
            if key in buy_entries:
                raise ValueError("multiple filled entries share an ambiguous lot identity")
            buy_entries[key] = filled * price + fee
        elif item["action"] in {"SELL", "TERMINAL_SETTLEMENT"}:
            filled_exits.append(item)

    completed_by_entry: dict[tuple[str, str], Mapping[str, Any]] = {}
    assigned_exit_ids: set[int] = set()
    for item in completed:
        key = (item["symbol"], item["opened_at"])
        if key not in buy_entries:
            raise ValueError("completed trade has no matching filled entry")
        if key in completed_by_entry:
            raise ValueError("filled entry has multiple completed round-trip records")
        cost = _finite_decimal(item["entry_total_cost"], "entry_total_cost")
        if cost != buy_entries[key]:
            raise ValueError("completed trade cost no longer matches its filled entry")
        opened = _time(item["opened_at"])
        closed = _time(item["closed_at"])
        matched = [
            (index, exit_item)
            for index, exit_item in enumerate(filled_exits)
            if index not in assigned_exit_ids
            and exit_item["symbol"] == item["symbol"]
            and opened <= _time(exit_item["executed_at"]) <= closed
        ]
        if not matched or max(_time(exit_item["executed_at"]) for _, exit_item in matched) != closed:
            raise ValueError("completed round trip lacks its final filled exit")
        proceeds = sum(
            (
                _finite_decimal(exit_item["filled_quantity"], "filled_quantity")
                * _finite_decimal(exit_item["execution_price"], "execution_price")
                - _finite_decimal(exit_item["fee"], "fee")
                for _, exit_item in matched
            ),
            Decimal("0"),
        )
        if proceeds != _finite_decimal(item["exit_net_proceeds"], "exit_net_proceeds"):
            raise ValueError("completed round-trip proceeds no longer match its filled exits")
        final_exit = max(matched, key=lambda value: _time(value[1]["executed_at"]))[1]
        if item["exit_reason"] != final_exit["reason"]:
            raise ValueError("completed round-trip reason no longer matches its final exit")
        assigned_exit_ids.update(index for index, _ in matched)
        completed_by_entry[key] = item
    if set(completed_by_entry) != set(buy_entries) or len(assigned_exit_ids) != len(filled_exits):
        raise ValueError("filled entries and exits do not reconcile to completed round trips")


def _canonical_decimal(value: Decimal) -> str:
    return "0" if value == 0 else format(value.normalize(), "f")


def _validate_state(value: Any) -> None:
    item = _exact_fields(value, set(PortfolioStateTrace.__dataclass_fields__), "portfolio state")
    _time(item["as_of_at"])
    for name in (
        "settled_cash", "unsettled_cash", "equity", "position_quantity",
        "position_cost_basis",
    ):
        _finite_decimal(item[name], name, non_negative=True)
    quantity = _finite_decimal(item["position_quantity"], "position_quantity")
    for name in ("average_entry_price", "stop_price", "mark_price"):
        if item[name] is not None:
            _finite_decimal(item[name], name, positive=True)
    if quantity == 0 and any(
        item[name] is not None for name in ("average_entry_price", "stop_price")
    ):
        raise ValueError("flat portfolio state retains position economics")
    if quantity > 0 and any(
        item[name] is None for name in ("average_entry_price", "stop_price")
    ):
        raise ValueError("open portfolio state lacks position economics")
    settled = _finite_decimal(item["settled_cash"], "settled_cash")
    unsettled = _finite_decimal(item["unsettled_cash"], "unsettled_cash")
    equity = _finite_decimal(item["equity"], "equity")
    mark = (
        _finite_decimal(item["mark_price"], "mark_price", positive=True)
        if item["mark_price"] is not None else Decimal("0")
    )
    if equity != settled + unsettled + quantity * mark:
        raise ValueError("portfolio-state equity no longer reconciles to cash and marked position")


def _drawdown(values: list[Decimal]) -> Decimal:
    peak = values[0]; worst = Decimal("0")
    for value in values:
        peak = max(peak, value)
        if peak > 0:
            worst = max(worst, Decimal("1") - value / peak)
    return worst
