from __future__ import annotations

"""Deterministic buffered production-universe reconstruction from a PIT master."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp
from core.portfolio.pit_security_master import SECURITY_ID_PATTERN, SHA256_PATTERN


POLICY_VERSION = "pit-sp500-buffered-liquidity-universe-v1"
MINIMUM_PRICE = Decimal("5")
MINIMUM_MEDIAN_DOLLAR_VOLUME = Decimal("20000000")
ENTRY_RANK = 100
RETENTION_RANK = 120
HARD_CEILING = 120


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _timestamp(value: Any, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value)).astimezone(timezone.utc)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _security_id(value: Any, name: str = "security_id") -> str:
    if not isinstance(value, str) or SECURITY_ID_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical permanent security identifier")
    return value


def _decimal(value: Any, name: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be a finite decimal")
    try:
        resolved = Decimal(str(value))
    except (InvalidOperation, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal") from error
    if not resolved.is_finite():
        raise ValueError(f"{name} must be a finite decimal")
    return resolved


def _format_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def reconstruct_buffered_universe(
    *,
    security_master_snapshot: Mapping[str, Any],
    market_observations: Sequence[Mapping[str, Any]],
    incumbent_security_ids: Sequence[str],
    benchmark_security_id: str,
    is_final_nyse_session_of_month: bool,
) -> dict[str, Any]:
    """Apply the frozen price, liquidity and 100/120 buffering rules.

    Missing or late market observations fail the whole reconstruction.  On a
    non-review session, no new member may enter, while price/liquidity failures
    and master removal still exit immediately.
    """

    if type(is_final_nyse_session_of_month) is not bool:
        raise TypeError("is_final_nyse_session_of_month must be boolean")
    if (
        security_master_snapshot.get("record_type")
        != "POINT_IN_TIME_SECURITY_MASTER_SNAPSHOT"
        or security_master_snapshot.get("universe") != "SP500"
        or security_master_snapshot.get("permanent_identity_used") is not True
        or security_master_snapshot.get("current_membership_used") is not False
    ):
        raise ValueError("a verified PIT SP500 security-master snapshot is required")
    decision_at = _timestamp(
        security_master_snapshot.get("effective_as_of"), "snapshot effective_as_of"
    )
    knowledge_cutoff = _timestamp(
        security_master_snapshot.get("known_as_of"), "snapshot known_as_of"
    )
    if knowledge_cutoff > decision_at:
        raise ValueError("knowledge cutoff cannot follow the effective decision cutoff")
    benchmark_id = _security_id(benchmark_security_id, "benchmark_security_id")
    members_raw = security_master_snapshot.get("members")
    if not isinstance(members_raw, list):
        raise ValueError("security-master snapshot members must be a list")
    members: dict[str, Mapping[str, Any]] = {}
    for member in members_raw:
        if not isinstance(member, Mapping):
            raise ValueError("every security-master member must be an object")
        identifier = _security_id(member.get("security_id"), "member security_id")
        if identifier in members:
            raise ValueError("security-master members repeat a permanent identifier")
        ticker = member.get("ticker")
        if not isinstance(ticker, str) or not ticker:
            raise ValueError("security-master member ticker is required")
        members[identifier] = member
    incumbents: list[str] = []
    seen_incumbents: set[str] = set()
    for value in incumbent_security_ids:
        identifier = _security_id(value, "incumbent_security_id")
        if identifier in seen_incumbents:
            raise ValueError("incumbent_security_ids must be unique")
        incumbents.append(identifier)
        seen_incumbents.add(identifier)
    if len(incumbents) > HARD_CEILING:
        raise ValueError("incumbent state exceeds the hard ceiling")

    exclusions_raw = security_master_snapshot.get("exclusions_retained")
    if not isinstance(exclusions_raw, list):
        raise ValueError("security-master exclusions_retained must be a list")
    exclusion_history: dict[str, list[dict[str, str]]] = {}
    for exclusion in exclusions_raw:
        if not isinstance(exclusion, Mapping):
            raise ValueError("every retained master exclusion must be an object")
        identifier = _security_id(
            exclusion.get("security_id"), "exclusion security_id"
        )
        exit_type = exclusion.get("exit_type")
        if exit_type not in {"INDEX_REMOVED", "DELISTED"}:
            raise ValueError("master exclusion exit_type is unsupported")
        exit_effective = _timestamp(
            exclusion.get("exit_effective_at"), "exit_effective_at"
        )
        if exit_effective > decision_at:
            raise ValueError("master exclusion is not effective at decision_at")
        exit_event_id = exclusion.get("exit_event_id")
        if not isinstance(exit_event_id, str) or not exit_event_id:
            raise ValueError("master exclusion exit_event_id is required")
        exit_event_hash = exclusion.get("exit_event_record_hash")
        if not isinstance(exit_event_hash, str) or SHA256_PATTERN.fullmatch(
            exit_event_hash
        ) is None:
            raise ValueError("master exclusion exit_event_record_hash is invalid")
        treatment = exclusion.get("terminal_outcome_treatment")
        if not isinstance(treatment, str) or not treatment:
            raise ValueError("master exclusion terminal outcome treatment is required")
        exclusion_history.setdefault(identifier, []).append(
            {
                "security_id": identifier,
                "reason": exit_type,
                "exit_effective_at": exit_effective.isoformat(),
                "exit_event_id": exit_event_id,
                "exit_event_record_hash": exit_event_hash,
                "terminal_outcome_treatment": treatment,
            }
        )
    latest_exclusion = {
        identifier: max(
            items,
            key=lambda item: (item["exit_effective_at"], item["exit_event_id"]),
        )
        for identifier, items in exclusion_history.items()
    }
    observations: dict[str, dict[str, Any]] = {}
    for observation in market_observations:
        if not isinstance(observation, Mapping):
            raise ValueError("every market observation must be an object")
        identifier = _security_id(observation.get("security_id"))
        if identifier in observations:
            raise ValueError("market observations repeat a permanent identifier")
        if identifier not in members:
            raise ValueError("market observation is outside the PIT master snapshot")
        effective = _timestamp(observation.get("effective_at"), "effective_at")
        available = _timestamp(observation.get("available_at"), "available_at")
        if effective > decision_at or available > decision_at:
            raise ValueError("market observation is not PIT-available at decision_at")
        price = _decimal(observation.get("price"), "price")
        median_dollar_volume = _decimal(
            observation.get("trailing_20_session_median_dollar_volume"),
            "trailing_20_session_median_dollar_volume",
        )
        if price <= 0 or median_dollar_volume < 0:
            raise ValueError("price must be positive and dollar volume nonnegative")
        observations[identifier] = {
            "security_id": identifier,
            "effective_at": effective.isoformat(),
            "available_at": available.isoformat(),
            "price": price,
            "median_dollar_volume": median_dollar_volume,
        }
    if set(observations) != set(members):
        raise ValueError("every PIT master member requires exactly one market observation")

    eligible: list[dict[str, Any]] = []
    floor_failures: dict[str, str] = {}
    for identifier, member in members.items():
        observation = observations[identifier]
        if identifier == benchmark_id:
            floor_failures[identifier] = "BENCHMARK_EXCLUDED_AS_ALPHA_ASSET"
        elif observation["price"] < MINIMUM_PRICE:
            floor_failures[identifier] = "PRICE_FLOOR_FAILED"
        elif observation["median_dollar_volume"] < MINIMUM_MEDIAN_DOLLAR_VOLUME:
            floor_failures[identifier] = "LIQUIDITY_FLOOR_FAILED"
        else:
            eligible.append(
                {
                    "security_id": identifier,
                    "ticker": member["ticker"],
                    "price": observation["price"],
                    "median_dollar_volume": observation["median_dollar_volume"],
                    "available_at": observation["available_at"],
                }
            )
    eligible.sort(
        key=lambda item: (-item["median_dollar_volume"], item["security_id"])
    )
    for rank, item in enumerate(eligible, start=1):
        item["liquidity_rank"] = rank

    if is_final_nyse_session_of_month:
        selected = [
            item
            for item in eligible
            if (
                item["liquidity_rank"] <= ENTRY_RANK
                or (
                    item["security_id"] in seen_incumbents
                    and item["liquidity_rank"] <= RETENTION_RANK
                )
            )
        ][:HARD_CEILING]
    else:
        selected = [
            item for item in eligible if item["security_id"] in seen_incumbents
        ]
    selected_ids = {item["security_id"] for item in selected}
    ranked_by_id = {item["security_id"]: item for item in eligible}
    exits: list[dict[str, str]] = []
    for identifier in incumbents:
        if identifier in selected_ids:
            continue
        if identifier not in members:
            verified_exit = latest_exclusion.get(identifier)
            if verified_exit is None:
                raise ValueError("incumbent outside the master lacks a verified master exit")
            exits.append(dict(verified_exit))
            continue
        elif identifier in floor_failures:
            reason = floor_failures[identifier]
        elif is_final_nyse_session_of_month:
            rank = ranked_by_id[identifier]["liquidity_rank"]
            reason = (
                "RETENTION_RANK_FAILED"
                if rank > RETENTION_RANK
                else "HARD_CEILING_FAILED"
            )
        else:
            reason = "INELIGIBLE_OUTSIDE_MONTHLY_REVIEW"
        exits.append({"security_id": identifier, "reason": reason})
    exits.sort(key=lambda item: item["security_id"])
    output_members = [
        {
            "security_id": item["security_id"],
            "ticker": item["ticker"],
            "liquidity_rank": item["liquidity_rank"],
            "price": _format_decimal(item["price"]),
            "trailing_20_session_median_dollar_volume": _format_decimal(
                item["median_dollar_volume"]
            ),
            "available_at": item["available_at"],
            "membership_status": (
                "RETAINED" if item["security_id"] in seen_incumbents else "ENTERED"
            ),
        }
        for item in selected
    ]
    material = {
        "policy_version": POLICY_VERSION,
        "security_master_snapshot_id": security_master_snapshot.get("snapshot_id"),
        "decision_at": decision_at.isoformat(),
        "is_final_nyse_session_of_month": is_final_nyse_session_of_month,
        "incumbent_security_ids": sorted(incumbents),
        "benchmark_security_id": benchmark_id,
        "members": output_members,
        "exits": exits,
    }
    return {
        "universe_selection_id": "UBUF-"
        + hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()[:32].upper(),
        "record_type": "PIT_BUFFERED_PRODUCTION_UNIVERSE_RECONSTRUCTION",
        "status": "RESEARCH_ONLY_NOT_ADMITTED",
        **material,
        "member_count": len(output_members),
        "entry_count": sum(
            item["membership_status"] == "ENTERED" for item in output_members
        ),
        "retained_count": sum(
            item["membership_status"] == "RETAINED" for item in output_members
        ),
        "exit_count": len(exits),
        "permanent_security_id_tie_break_used": True,
        "current_membership_used": False,
        "partition_admission_authorized": False,
        "performance_calculated": False,
        "performance_claim_allowed": False,
        "broker_submission_enabled": False,
        "live_trading_enabled": False,
    }
