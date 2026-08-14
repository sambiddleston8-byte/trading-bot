from __future__ import annotations

"""Derive engine inputs from verified replay artifacts, never caller assertions."""

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.data_quality.authenticated_source_content import AuthenticatedSourceContentLedger
from core.decision_ledger import canonical_timestamp
from core.guardrailed_backtest import (
    AUTHENTICATED_REPLAY_ROLES,
    CorporateAction,
    MarketBar,
    ReplayDataAttestation,
    TerminalOutcome,
    UniverseEvent,
)
from core.orchestration.authenticated_replay_artifact import (
    load_authenticated_backtest_artifact_bundle,
)
from core.orchestration.replay_dataset_admission import ReplayDatasetAdmissionLedger


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "authenticated-guardrailed-backtest-inputs-v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _time(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _final_active(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    chains: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        chains.setdefault((row["ticker"], row["event_id"]), []).append(row)
    return sorted(
        (
            max(chain, key=lambda item: item["version"])
            for chain in chains.values()
            if max(chain, key=lambda item: item["version"])["record_status"] == "ACTIVE"
        ),
        key=lambda item: (
            item["ticker"], item["effective_at"], item["available_at"], item["event_id"]
        ),
    )


def _reject_late_actual_state_revisions(
    rows: Sequence[Mapping[str, Any]], label: str,
) -> None:
    chains: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        chains.setdefault((row["ticker"], row["event_id"]), []).append(row)
    for chain in chains.values():
        ordered = sorted(chain, key=lambda item: item["version"])
        for current, successor in zip(ordered, ordered[1:]):
            if (
                current["record_status"] == "ACTIVE"
                and _time(successor["available_at"], "available_at")
                > _time(current["effective_at"], "effective_at")
            ):
                raise ValueError(
                    f"{label} was revised after its prior version took effect"
                )


@dataclass(frozen=True)
class AuthenticatedBacktestInputs:
    schema_version: str
    policy_version: str
    admission_id: str
    validation_receipt_sha256: str
    bars: tuple[MarketBar, ...]
    universe_events: tuple[UniverseEvent, ...]
    terminal_outcomes: tuple[TerminalOutcome, ...]
    corporate_actions: tuple[CorporateAction, ...]
    data_attestation: ReplayDataAttestation
    prices_are_unadjusted: bool = True
    broker_connection_allowed: bool = False
    orders_submitted: bool = False
    live_trading_enabled: bool = False

    def engine_inputs(self) -> dict[str, Any]:
        return {
            "bars": self.bars,
            "universe_events": self.universe_events,
            "terminal_outcomes": self.terminal_outcomes,
            "corporate_actions": self.corporate_actions,
            "prices_are_unadjusted": self.prices_are_unadjusted,
        }


def load_authenticated_backtest_inputs(
    *,
    admission_ledger: ReplayDatasetAdmissionLedger,
    content_ledger: AuthenticatedSourceContentLedger,
    admission_id: str,
) -> AuthenticatedBacktestInputs:
    """Validate a one-instrument bundle and return immutable offline engine inputs."""
    bundle = load_authenticated_backtest_artifact_bundle(
        admission_ledger=admission_ledger,
        content_ledger=content_ledger,
        admission_id=admission_id,
    )
    if len(bundle["tickers"]) != 1:
        raise ValueError(
            "authenticated adapter supports one instrument until portfolio-wide batching exists"
        )
    symbol = bundle["tickers"][0]
    artifacts = bundle["artifacts"]
    if set(artifacts) != AUTHENTICATED_REPLAY_ROLES:
        raise ValueError("authenticated bundle does not contain the exact engine evidence roles")
    if any(
        artifact["source_bytes_authenticated"] is not True
        or artifact["artifact_structure_validated"] is not True
        for artifact in artifacts.values()
    ):
        raise ValueError("every engine input role must pass authenticated structural validation")

    bars = tuple(sorted((
        MarketBar(
            symbol=row["ticker"],
            open_at=_time(row["session_opens_at"], "session_opens_at"),
            close_at=_time(row["session_closes_at"], "session_closes_at"),
            available_at=_time(row["available_at"], "available_at"),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        for row in artifacts["RAW_DAILY_SESSION_BARS"]["rows"]
    ), key=lambda item: (item.open_at, item.close_at, item.symbol)))
    if not bars:
        raise ValueError("authenticated daily bars are required")
    if len({(bar.symbol, bar.open_at, bar.close_at) for bar in bars}) != len(bars):
        raise ValueError("authenticated daily sessions must be unique")

    calendar = artifacts["MARKET_CALENDARS_AND_HALTS"]["rows"]
    if any(row["status"] == "HALTED" for row in calendar):
        raise ValueError("halted sessions require a dedicated intraday execution model")
    if any(
        _time(row["available_at"], "available_at")
        > _time(row["starts_at"], "starts_at")
        for row in calendar
    ):
        raise ValueError("market calendar state must be known by the start of its interval")
    expected_sessions = {
        (
            _time(row["starts_at"], "starts_at"),
            _time(row["ends_at"], "ends_at"),
        )
        for row in calendar
        if row["ticker"] == symbol and row["status"] == "OPEN"
    }
    actual_sessions = {(bar.open_at, bar.close_at) for bar in bars}
    if actual_sessions != expected_sessions:
        raise ValueError("daily bars must exactly cover every authenticated open session")
    if any(left.available_at >= right.open_at for left, right in zip(bars, bars[1:])):
        raise ValueError("a completed bar was not available before the next session open")

    membership = artifacts["UNIVERSE_MEMBERSHIP"]
    _reject_late_actual_state_revisions(membership["rows"], "universe membership")
    resolved_membership = membership["resolved_membership_states"][symbol]
    if resolved_membership["membership_incoherent"] is not False:
        raise ValueError("historical universe membership is incoherent")
    initial_membership = resolved_membership["initial_state"]
    membership_events: list[UniverseEvent] = []
    if initial_membership["state"] == "MEMBER":
        membership_events.append(
            UniverseEvent(
                symbol,
                "ADD",
                _time(initial_membership["as_of_at"], "as_of_at"),
                _time(initial_membership["available_at"], "available_at"),
                initial_membership["source_row_locator"],
            )
        )
    membership_events.extend(
        UniverseEvent(
            row["ticker"],
            row["membership_action"],
            _time(row["effective_at"], "effective_at"),
            _time(row["available_at"], "available_at"),
            row["source_row_locator"],
        )
        for row in resolved_membership["canonical_events"]
    )
    if not any(event.action == "ADD" for event in membership_events):
        raise ValueError("instrument never has authenticated historical universe entry evidence")

    terminal_artifact = artifacts["DELISTING_OUTCOMES"]
    _reject_late_actual_state_revisions(terminal_artifact["rows"], "terminal outcome")
    terminal_state = terminal_artifact["resolved_ticker_states"][symbol]
    if terminal_state["initial_state"]["state"] != "ACTIVE":
        raise ValueError("coverage cannot begin after the instrument is already terminal")
    if terminal_state["actual_state_ambiguous"] is not False:
        raise ValueError("ambiguous terminal history cannot support a backtest")
    terminal_rows = _final_active(terminal_artifact["rows"])
    terminal_outcomes: list[TerminalOutcome] = []
    for row in terminal_rows:
        if row["terminal_type"] not in {"DELISTED", "BANKRUPT", "ACQUIRED", "MERGED"}:
            raise ValueError("terminal type requires an explicit supported economic model")
        settlement = row["settlement"]
        if settlement["recovery_basis"] == "UNKNOWN":
            raise ValueError("terminal recovery basis must be known")
        terminal_outcomes.append(
            TerminalOutcome(
                row["ticker"],
                row["terminal_type"],
                _time(row["effective_at"], "effective_at"),
                _time(row["available_at"], "available_at"),
                Decimal(settlement["recovery_per_share"]),
                _time(settlement["cash_settled_at"], "cash_settled_at"),
                row["source_row_locator"],
            )
        )
    if terminal_outcomes:
        terminal_at = terminal_outcomes[0].effective_at
        if any(bar.open_at < terminal_at < bar.close_at for bar in bars):
            raise ValueError("terminal outcome cannot take effect inside an aggregate daily bar")
        if any(bar.open_at >= terminal_at for bar in bars):
            raise ValueError("daily bars cannot continue after an authenticated terminal outcome")

    corporate_artifact = artifacts["CORPORATE_ACTIONS"]
    _reject_late_actual_state_revisions(corporate_artifact["rows"], "corporate action")
    corporate_actions: list[CorporateAction] = []
    for row in _final_active(corporate_artifact["rows"]):
        economics = row["economics"]
        effective_at = _time(row["effective_at"], "effective_at")
        if any(bar.open_at < effective_at < bar.close_at for bar in bars):
            raise ValueError(
                "corporate action cannot take effect inside an aggregate daily bar"
            )
        common = {
            "symbol": row["ticker"],
            "action_type": row["event_type"],
            "effective_at": effective_at,
            "available_at": _time(row["available_at"], "available_at"),
            "source_locator": row["source_row_locator"],
        }
        if row["event_type"] == "SPLIT":
            corporate_actions.append(
                CorporateAction(**common, split_ratio=Decimal(economics["split_ratio"]))
            )
        elif row["event_type"] == "CASH_DIVIDEND":
            corporate_actions.append(
                CorporateAction(
                    **common,
                    cash_per_share=Decimal(economics["cash_per_share"]),
                    cash_paid_at=_time(economics["cash_paid_at"], "cash_paid_at"),
                )
            )
        else:
            raise ValueError("corporate action requires an explicit supported economic model")

    role_hashes = tuple(sorted(
        (role, artifact["source_input_sha256"])
        for role, artifact in artifacts.items()
    ))
    source_material = {
        "admission_id": bundle["admission_id"],
        "admission_record_hash": bundle["admission_record_hash"],
        "replay_plan_id": bundle["replay_plan_id"],
        "replay_plan_record_hash": bundle["replay_plan_record_hash"],
        "dataset_commitment_sha256": bundle["dataset_commitment_sha256"],
        "role_hashes": role_hashes,
    }
    source_hash = _hash(source_material)
    receipt_material = {
        "schema_version": SCHEMA_VERSION,
        "policy_version": POLICY_VERSION,
        "source_content_sha256": source_hash,
        "symbol": symbol,
        "session_count": len(bars),
        "membership_event_count": len(membership_events),
        "terminal_outcome_count": len(terminal_outcomes),
        "corporate_action_count": len(corporate_actions),
        "calendar_sessions_exactly_reconciled": True,
        "next_open_availability_validated": True,
        "historical_membership_validated": True,
        "terminal_economics_validated": True,
        "corporate_economics_validated": True,
    }
    receipt_hash = _hash(receipt_material)
    data_attestation = ReplayDataAttestation._from_authenticated_artifacts(
        source_id=bundle["admission_id"],
        source_content_sha256=source_hash,
        validation_receipt_sha256=receipt_hash,
        derivation_policy_version=POLICY_VERSION,
        evidence_role_hashes=role_hashes,
    )
    return AuthenticatedBacktestInputs(
        schema_version=SCHEMA_VERSION,
        policy_version=POLICY_VERSION,
        admission_id=bundle["admission_id"],
        validation_receipt_sha256=receipt_hash,
        bars=bars,
        universe_events=tuple(membership_events),
        terminal_outcomes=tuple(terminal_outcomes),
        corporate_actions=tuple(corporate_actions),
        data_attestation=data_attestation,
    )
