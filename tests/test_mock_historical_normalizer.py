from __future__ import annotations

import ast
import csv
import hashlib
import inspect
import io
import json

import pytest

import core.orchestration.mock_historical_normalizer as module
from core.orchestration.historical_role_cutoff import normalized_payload_sha256
from core.orchestration.mock_historical_normalizer import (
    MockHistoricalNormalizerAdapter,
    MockHistoricalSource,
)


DECISION_AT = "2022-04-02T09:30:00+00:00"
RETRIEVED_AT = "2022-04-03T12:00:00+00:00"


def record(
    identifier: str,
    *,
    effective_at: str = "2022-04-01T16:00:00+00:00",
    available_at: str = "2022-04-01T16:00:01+00:00",
    payload: dict | None = None,
) -> dict:
    return {
        "provider_record_id": identifier,
        "effective_at": effective_at,
        "available_at": available_at,
        "payload": payload or {"subject_id": "SYN", "value": "100.00"},
    }


def json_bytes(records: list[dict]) -> bytes:
    return json.dumps(
        {"schema_version": "1.0", "records": records},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def csv_bytes(records: list[dict]) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(
        ["provider_record_id", "effective_at", "available_at", "payload_json"]
    )
    for item in records:
        writer.writerow(
            [
                item["provider_record_id"],
                item["effective_at"],
                item["available_at"],
                json.dumps(item["payload"], sort_keys=True, separators=(",", ":")),
            ]
        )
    return output.getvalue().encode()


def source(
    role: str,
    records: list[dict],
    *,
    transport_format: str = "JSON",
    retrieved_at: str = RETRIEVED_AT,
) -> MockHistoricalSource:
    payload = json_bytes(records) if transport_format == "JSON" else csv_bytes(records)
    return MockHistoricalSource(
        role=role,
        transport_format=transport_format,
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id=f"SYNTHETIC_{role}",
        retrieved_at=retrieved_at,
        payload_bytes=payload,
    )


def market_regime_sources(*, transport_format: str = "JSON") -> dict:
    return {
        "MARKET_CALENDARS_AND_HALTS": source(
            "MARKET_CALENDARS_AND_HALTS",
            [record("calendar:1", payload={"status": "OPEN", "subject_id": "SYN"})],
            transport_format=transport_format,
        ),
        "TOTAL_RETURN_PRICES": source(
            "TOTAL_RETURN_PRICES",
            [record("price:1")],
            transport_format=transport_format,
        ),
    }


def normalize(sources: dict):
    return MockHistoricalNormalizerAdapter().normalize_for_engine(
        engine_key="market_regime",
        decision_at=DECISION_AT,
        sources_by_role=sources,
    )


@pytest.mark.parametrize("transport_format", ["JSON", "CSV"])
def test_normalizes_exact_json_and_csv_schemas_into_canonical_cutoff_batch(
    transport_format,
):
    sources = market_regime_sources(transport_format=transport_format)

    result = normalize(sources)

    values = result.as_dict()["observations_by_role"]
    assert set(values) == {"MARKET_CALENDARS_AND_HALTS", "TOTAL_RETURN_PRICES"}
    price = values["TOTAL_RETURN_PRICES"][0]
    assert price["schema_version"] == "1.0"
    assert price["provider_id"] == "SYNTHETIC_CLOUD_PROVIDER"
    assert price["provider_dataset_id"] == "SYNTHETIC_TOTAL_RETURN_PRICES"
    assert price["provider_record_id"] == "price:1"
    assert price["effective_at"] == "2022-04-01T16:00:00.000000+00:00"
    assert price["available_at"] == "2022-04-01T16:00:01.000000+00:00"
    assert price["retrieved_at"] == "2022-04-03T12:00:00.000000+00:00"
    assert price["observation_cutoff_at"] == (
        "2022-04-02T09:30:00.000000+00:00"
    )
    assert price["source_payload_sha256"] == hashlib.sha256(
        sources["TOTAL_RETURN_PRICES"].payload_bytes
    ).hexdigest()
    assert price["normalized_payload_sha256"] == normalized_payload_sha256(
        price["payload"]
    )
    assert result.observation_cutoffs_validated is True
    assert result.provider_payload_semantics_qualified is False
    assert result.source_bytes_authenticated is False
    assert result.engine_input_ready is False
    assert result.broker_connection_allowed is False
    assert result.orders_submitted is False
    assert result.live_trading_enabled is False


@pytest.mark.parametrize("transport_format", ["JSON", "CSV"])
def test_rejects_record_available_after_the_decision_cutoff(transport_format):
    sources = market_regime_sources(transport_format=transport_format)
    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:late", available_at="2022-04-02T09:30:00.000001+00:00")],
        transport_format=transport_format,
    )

    with pytest.raises(ValueError, match="available_at is after the decision"):
        normalize(sources)


@pytest.mark.parametrize("transport_format", ["JSON", "CSV"])
def test_rejects_future_effective_realized_price(transport_format):
    sources = market_regime_sources(transport_format=transport_format)
    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:future", effective_at="2022-04-03T16:00:00+00:00")],
        transport_format=transport_format,
    )

    with pytest.raises(ValueError, match="effective_at is after the decision"):
        normalize(sources)


def test_known_future_corporate_event_is_retained_only_when_already_available():
    event = source(
        "POINT_IN_TIME_CORPORATE_EVENTS",
        [
            record(
                "event:1",
                effective_at="2022-04-08T13:00:00+00:00",
                available_at="2022-04-01T12:00:00+00:00",
                payload={"subject_id": "SYN", "event_type": "SYNTHETIC_EVENT"},
            )
        ],
    )

    result = MockHistoricalNormalizerAdapter().normalize_for_engine(
        engine_key="catalyst",
        decision_at=DECISION_AT,
        sources_by_role={"POINT_IN_TIME_CORPORATE_EVENTS": event},
    )

    value = result.as_dict()["observations_by_role"][
        "POINT_IN_TIME_CORPORATE_EVENTS"
    ][0]
    assert value["available_at"] < result.decision_at < value["effective_at"]

    late_event = source(
        "POINT_IN_TIME_CORPORATE_EVENTS",
        [
            record(
                "event:late",
                effective_at="2022-04-08T13:00:00+00:00",
                available_at="2022-04-02T09:30:00.000001+00:00",
                payload={"subject_id": "SYN", "event_type": "SYNTHETIC_EVENT"},
            )
        ],
    )
    with pytest.raises(ValueError, match="available_at is after the decision"):
        MockHistoricalNormalizerAdapter().normalize_for_engine(
            engine_key="catalyst",
            decision_at=DECISION_AT,
            sources_by_role={"POINT_IN_TIME_CORPORATE_EVENTS": late_event},
        )


def test_source_retrieval_cannot_predate_record_availability():
    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:1", available_at="2022-04-03T13:00:00+00:00")],
        retrieved_at="2022-04-03T12:00:00+00:00",
    )

    with pytest.raises(ValueError, match="retrieved_at cannot predate"):
        normalize(sources)


def test_records_are_sorted_and_duplicate_provider_ids_fail_closed():
    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:2"), record("price:1")],
    )
    result = normalize(sources)
    assert [
        item["provider_record_id"]
        for item in result.as_dict()["observations_by_role"]["TOTAL_RETURN_PRICES"]
    ] == ["price:1", "price:2"]

    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:1"), record("price:1")],
    )
    with pytest.raises(ValueError, match="repeats a provider_record_id"):
        normalize(sources)


def test_json_duplicate_fields_nonfinite_values_and_extra_fields_fail_closed():
    duplicate = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="JSON",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=(
            b'{"schema_version":"1.0","schema_version":"1.0","records":[]}'
        ),
    )
    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = duplicate
    with pytest.raises(ValueError):
        normalize(sources)

    nonfinite = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="JSON",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=(
            b'{"schema_version":"1.0","records":[{"provider_record_id":"p",'
            b'"effective_at":"2022-04-01T16:00:00Z",'
            b'"available_at":"2022-04-01T16:00:01Z",'
            b'"payload":{"value":NaN}}]}'
        ),
    )
    sources["TOTAL_RETURN_PRICES"] = nonfinite
    with pytest.raises(ValueError, match="strict JSON with unique fields"):
        normalize(sources)

    extra = record("price:1")
    extra["invented"] = True
    sources["TOTAL_RETURN_PRICES"] = source("TOTAL_RETURN_PRICES", [extra])
    with pytest.raises(ValueError, match="missing or unsupported fields"):
        normalize(sources)


def test_csv_requires_exact_header_and_json_object_payload():
    malformed = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="CSV",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=b"available_at,effective_at,provider_record_id,payload_json\n",
    )
    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = malformed
    with pytest.raises(ValueError, match="canonical field order"):
        normalize(sources)

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(module.CSV_FIELDS)
    writer.writerow(
        ["price:1", "2022-04-01T16:00:00Z", "2022-04-01T16:00:01Z", "[]"]
    )
    sources["TOTAL_RETURN_PRICES"] = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="CSV",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=output.getvalue().encode(),
    )
    with pytest.raises(ValueError, match="payload must be a JSON object"):
        normalize(sources)


def test_source_size_encoding_nul_and_naive_timestamps_fail_closed():
    with pytest.raises(ValueError, match="bounded nonempty"):
        MockHistoricalSource(
            role="TOTAL_RETURN_PRICES",
            transport_format="JSON",
            provider_id="SYNTHETIC_CLOUD_PROVIDER",
            provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
            retrieved_at=RETRIEVED_AT,
            payload_bytes=b"x" * (module.MAX_SOURCE_BYTES + 1),
        )

    sources = market_regime_sources()
    for invalid, message in ((b"\xff", "UTF-8"), (b"{}\x00", "NUL")):
        sources["TOTAL_RETURN_PRICES"] = MockHistoricalSource(
            role="TOTAL_RETURN_PRICES",
            transport_format="JSON",
            provider_id="SYNTHETIC_CLOUD_PROVIDER",
            provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
            retrieved_at=RETRIEVED_AT,
            payload_bytes=invalid,
        )
        with pytest.raises(ValueError, match=message):
            normalize(sources)

    sources["TOTAL_RETURN_PRICES"] = source(
        "TOTAL_RETURN_PRICES",
        [record("price:naive", effective_at="2022-04-01T16:00:00")],
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        normalize(sources)


def test_record_limit_and_deep_json_fail_with_controlled_errors():
    too_many = json_bytes([record(f"price:{index}") for index in range(10_001)])
    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="JSON",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=too_many,
    )
    with pytest.raises(ValueError, match="record limit"):
        normalize(sources)

    deeply_nested = (
        b'{"schema_version":"1.0","records":['
        + b"[" * 1_500
        + b"]" * 1_500
        + b"]}"
    )
    sources["TOTAL_RETURN_PRICES"] = MockHistoricalSource(
        role="TOTAL_RETURN_PRICES",
        transport_format="JSON",
        provider_id="SYNTHETIC_CLOUD_PROVIDER",
        provider_dataset_id="SYNTHETIC_TOTAL_RETURN_PRICES",
        retrieved_at=RETRIEVED_AT,
        payload_bytes=deeply_nested,
    )
    with pytest.raises(ValueError):
        normalize(sources)


def test_json_csv_equivalence_and_raw_source_tampering_are_explicit():
    json_sources = market_regime_sources(transport_format="JSON")
    csv_sources = market_regime_sources(transport_format="CSV")
    json_price = normalize(json_sources).as_dict()["observations_by_role"][
        "TOTAL_RETURN_PRICES"
    ][0]
    csv_price = normalize(csv_sources).as_dict()["observations_by_role"][
        "TOTAL_RETURN_PRICES"
    ][0]
    json_source_hash = json_price.pop("source_payload_sha256")
    csv_source_hash = csv_price.pop("source_payload_sha256")
    assert json_price == csv_price
    assert json_source_hash != csv_source_hash

    original = json_sources["TOTAL_RETURN_PRICES"]
    changed = MockHistoricalSource(
        role=original.role,
        transport_format=original.transport_format,
        provider_id=original.provider_id,
        provider_dataset_id=original.provider_dataset_id,
        retrieved_at=original.retrieved_at,
        payload_bytes=original.payload_bytes + b"\n",
    )
    changed_sources = dict(json_sources)
    changed_sources["TOTAL_RETURN_PRICES"] = changed
    changed_price = normalize(changed_sources).as_dict()["observations_by_role"][
        "TOTAL_RETURN_PRICES"
    ][0]
    assert changed_price["payload"] == json_price["payload"]
    assert changed_price["source_payload_sha256"] != json_source_hash


def test_mock_boundary_rejects_real_provider_identity_and_role_mismatch():
    with pytest.raises(ValueError, match="synthetic provider identities only"):
        MockHistoricalSource(
            role="TOTAL_RETURN_PRICES",
            transport_format="JSON",
            provider_id="REAL_PROVIDER",
            provider_dataset_id="REAL_DATASET",
            retrieved_at=RETRIEVED_AT,
            payload_bytes=json_bytes([]),
        )

    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = source(
        "RAW_DAILY_SESSION_BARS", [record("bar:1")]
    )
    with pytest.raises(ValueError, match="source role does not match"):
        normalize(sources)


def test_exact_engine_role_coverage_and_dense_empty_source_are_enforced():
    sources = market_regime_sources()
    sources.pop("MARKET_CALENDARS_AND_HALTS")
    with pytest.raises(ValueError, match="cover exactly"):
        normalize(sources)

    sources = market_regime_sources()
    sources["TOTAL_RETURN_PRICES"] = source("TOTAL_RETURN_PRICES", [])
    with pytest.raises(ValueError, match="dense role TOTAL_RETURN_PRICES"):
        normalize(sources)


def test_module_has_no_network_provider_broker_or_execution_import():
    tree = ast.parse(inspect.getsource(module))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)

    forbidden = (
        "aiohttp",
        "boto3",
        "broker",
        "databento",
        "execution",
        "http",
        "polygon",
        "provider",
        "requests",
        "socket",
        "ssl",
        "subprocess",
        "urllib",
    )
    assert not [
        name for name in imported if any(word in name.lower() for word in forbidden)
    ]
    forbidden_calls = {"open", "read_bytes", "read_text", "urlopen", "request"}
    called = {
        node.func.id
        if isinstance(node.func, ast.Name)
        else node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, (ast.Name, ast.Attribute))
    }
    assert not called.intersection(forbidden_calls)
