from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
import hashlib

import pytest

import core.orchestration.historical_role_cutoff as module
from core.orchestration.historical_role_cutoff import (
    HistoricalRoleCutoffBatch,
    REQUIRED_OBSERVATION_FIELDS,
    ROLE_SCHEMAS,
    enforce_historical_role_cutoffs,
    normalized_payload_sha256,
    verify_role_schema_registry,
)
from core.orchestration.replay_input_coverage import declared_artifact_roles


DECISION_AT = "2022-04-02T09:30:00+00:00"


def observation(
    role: str,
    record_id: str,
    *,
    effective_at: str = "2022-04-01T16:00:00+00:00",
    available_at: str = "2022-04-01T16:00:01+00:00",
    retrieved_at: str = "2022-04-03T12:00:00+00:00",
    cutoff_at: str = DECISION_AT,
    payload: dict | None = None,
) -> dict:
    resolved_payload = (
        {"subject_id": "SYN", "value": "100.00"} if payload is None else payload
    )
    return {
        "schema_version": "1.0",
        "role": role,
        "provider_id": "SYNTHETIC_PROVIDER",
        "provider_dataset_id": f"SYNTHETIC_{role}",
        "provider_record_id": record_id,
        "effective_at": effective_at,
        "available_at": available_at,
        "retrieved_at": retrieved_at,
        "observation_cutoff_at": cutoff_at,
        "source_payload_sha256": hashlib.sha256(
            f"raw:{role}:{record_id}".encode()
        ).hexdigest(),
        "normalized_payload_sha256": normalized_payload_sha256(resolved_payload),
        "payload": resolved_payload,
    }


def market_regime_observations() -> dict[str, list[dict]]:
    return {
        "MARKET_CALENDARS_AND_HALTS": [
            observation("MARKET_CALENDARS_AND_HALTS", "calendar:1")
        ],
        "TOTAL_RETURN_PRICES": [
            observation("TOTAL_RETURN_PRICES", "price:1")
        ],
    }


class FakeInvocation:
    def __init__(self, now: str = DECISION_AT) -> None:
        self._now = now
        self.validated = False

    def require_valid(self):
        self.validated = True
        return self

    def now(self) -> str:
        return self._now


def test_role_schemas_exactly_cover_the_active_pipeline_artifact_roles():
    verify_role_schema_registry()

    assert set(ROLE_SCHEMAS) == declared_artifact_roles()
    assert all(
        schema.required_observation_fields == REQUIRED_OBSERVATION_FIELDS
        and schema.cutoff_field == "available_at"
        and schema.effective_at_policy
        in {module.EFFECTIVE_BY_CUTOFF, module.KNOWN_FUTURE_EFFECTIVE_ALLOWED}
        and schema.provider_payload_semantics_qualified is False
        for schema in ROLE_SCHEMAS.values()
    )


def test_builds_deterministic_immutable_cutoff_batch_with_safety_false():
    first = enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        observations_by_role=market_regime_observations(),
    )
    second = enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=datetime.fromisoformat(DECISION_AT).astimezone(timezone.utc),
        observations_by_role=market_regime_observations(),
    )

    assert first.batch_sha256 == second.batch_sha256
    assert first.observation_cutoffs_validated is True
    assert first.normalized_payload_hashes_validated is True
    assert first.provider_payload_semantics_qualified is False
    assert first.source_bytes_authenticated is False
    assert first.observation_selection_validated is False
    assert first.role_coverage_validated is False
    assert first.dataset_commitment_bound is False
    assert first.engine_input_ready is False
    assert first.replay_executed is False
    assert first.broker_connection_allowed is False
    assert first.orders_submitted is False
    assert first.live_trading_enabled is False
    with pytest.raises(TypeError):
        first.observations_by_role["TOTAL_RETURN_PRICES"] = ()
    with pytest.raises(TypeError):
        first.observations_by_role["TOTAL_RETURN_PRICES"][0]["provider_id"] = "OTHER"


def test_strictly_rejects_observation_available_after_decision_timestamp():
    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"][0]["available_at"] = (
        "2022-04-02T09:30:00.000001+00:00"
    )

    with pytest.raises(ValueError, match="available_at is after the decision"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )


def test_availability_exactly_at_decision_is_included_by_policy():
    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"][0]["available_at"] = DECISION_AT

    result = enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        observations_by_role=values,
    )

    assert (
        result.as_dict()["observations_by_role"]["TOTAL_RETURN_PRICES"][0][
            "available_at"
        ]
        == "2022-04-02T09:30:00.000000+00:00"
    )


def test_retrieval_after_decision_is_provenance_not_lookahead():
    result = enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        observations_by_role=market_regime_observations(),
    )

    record = result.as_dict()["observations_by_role"]["TOTAL_RETURN_PRICES"][0]
    assert record["available_at"] < result.decision_at < record["retrieved_at"]


def test_rejects_mixed_cutoffs_and_retrieval_before_availability():
    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"][0]["observation_cutoff_at"] = (
        "2022-04-02T09:29:59+00:00"
    )
    with pytest.raises(ValueError, match="cutoff must equal"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )

    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"][0]["retrieved_at"] = (
        "2022-04-01T15:59:59+00:00"
    )
    with pytest.raises(ValueError, match="cannot predate available_at"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )


def test_rejects_payload_hash_mismatch_and_nonfinite_payload():
    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"][0]["payload"]["value"] = "changed"
    with pytest.raises(ValueError, match="hash does not match"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )

    with pytest.raises(ValueError, match="finite canonical JSON"):
        normalized_payload_sha256({"value": float("nan")})


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda row: row.pop("provider_id"), "missing or unsupported"),
        (lambda row: row.update(provider_dataset_id=""), "canonical text"),
        (lambda row: row.update(source_payload_sha256="A" * 64), "lowercase"),
        (lambda row: row.update(extra_field=True), "missing or unsupported"),
    ],
)
def test_requires_exact_provider_identity_and_hash_envelope(mutation, message):
    values = market_regime_observations()
    mutation(values["TOTAL_RETURN_PRICES"][0])

    with pytest.raises(ValueError, match=message):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )


def test_requires_exact_engine_role_set_and_canonical_unique_provider_records():
    values = market_regime_observations()
    values.pop("MARKET_CALENDARS_AND_HALTS")
    with pytest.raises(ValueError, match="cover exactly"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )

    values = market_regime_observations()
    values["TOTAL_RETURN_PRICES"] = [
        observation("TOTAL_RETURN_PRICES", "price:2"),
        observation("TOTAL_RETURN_PRICES", "price:1"),
    ]
    with pytest.raises(ValueError, match="canonically ordered"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )

    values["TOTAL_RETURN_PRICES"] = [
        observation("TOTAL_RETURN_PRICES", "price:1"),
        observation("TOTAL_RETURN_PRICES", "price:1"),
    ]
    with pytest.raises(ValueError, match="repeat a provider record"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=values,
        )


def test_effective_time_is_retained_without_inventing_a_global_ordering_rule():
    future_event = observation(
        "POINT_IN_TIME_CORPORATE_EVENTS",
        "event:1",
        effective_at="2022-04-08T13:00:00+00:00",
        available_at="2022-04-01T12:00:00+00:00",
        payload={"subject_id": "SYN", "event_type": "SYNTHETIC_EVENT"},
    )

    result = enforce_historical_role_cutoffs(
        engine_key="catalyst",
        decision_at=DECISION_AT,
        observations_by_role={"POINT_IN_TIME_CORPORATE_EVENTS": [future_event]},
    )

    record = result.as_dict()["observations_by_role"][
        "POINT_IN_TIME_CORPORATE_EVENTS"
    ][0]
    assert record["available_at"] < result.decision_at < record["effective_at"]
    assert result.provider_payload_semantics_qualified is False


@pytest.mark.parametrize("role", ["TOTAL_RETURN_PRICES", "RAW_DAILY_SESSION_BARS"])
def test_realized_market_observations_cannot_be_future_effective(role):
    engine = "market_regime" if role == "TOTAL_RETURN_PRICES" else "market_signals"
    values = {
        required_role: [observation(required_role, f"{required_role}:1")]
        for required_role in module.engine_input_roles()[engine]
    }
    values[role][0]["effective_at"] = "2022-04-03T16:00:00+00:00"

    with pytest.raises(ValueError, match=f"{role} effective_at is after"):
        enforce_historical_role_cutoffs(
            engine_key=engine,
            decision_at=DECISION_AT,
            observations_by_role=values,
        )


def test_dense_roles_require_observations_but_sparse_event_roles_may_be_empty():
    with pytest.raises(ValueError, match="dense role TOTAL_RETURN_PRICES"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role={
                "MARKET_CALENDARS_AND_HALTS": [
                    observation("MARKET_CALENDARS_AND_HALTS", "calendar:1")
                ],
                "TOTAL_RETURN_PRICES": [],
            },
        )

    sparse = enforce_historical_role_cutoffs(
        engine_key="catalyst",
        decision_at=DECISION_AT,
        observations_by_role={"POINT_IN_TIME_CORPORATE_EVENTS": []},
    )
    assert sparse.as_dict()["observations_by_role"] == {
        "POINT_IN_TIME_CORPORATE_EVENTS": []
    }
    assert sparse.role_coverage_validated is False


def test_sealed_invocation_must_match_the_decision_clock():
    invocation = FakeInvocation()
    enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        observations_by_role=market_regime_observations(),
        invocation=invocation,
    )
    assert invocation.validated is True

    with pytest.raises(ValueError, match="invocation clock"):
        enforce_historical_role_cutoffs(
            engine_key="market_regime",
            decision_at=DECISION_AT,
            observations_by_role=market_regime_observations(),
            invocation=FakeInvocation("2022-04-02T09:31:00+00:00"),
        )


def test_batch_cannot_be_forged_or_promoted_by_dataclass_replace():
    issued = enforce_historical_role_cutoffs(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        observations_by_role=market_regime_observations(),
    )
    fields = {
        name: getattr(issued, name)
        for name in HistoricalRoleCutoffBatch.__dataclass_fields__
        if name != "_authority"
    }
    with pytest.raises(PermissionError, match="enforce_historical_role_cutoffs"):
        HistoricalRoleCutoffBatch(**fields)
    with pytest.raises(PermissionError, match="enforce_historical_role_cutoffs"):
        replace(issued, engine_input_ready=True)
    with pytest.raises(ValueError, match="safety flags"):
        replace(
            issued,
            engine_input_ready=True,
            _authority=module._BATCH_AUTHORITY,
        )
