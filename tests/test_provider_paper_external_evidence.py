from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json

import pytest

from core.broker import (
    PaperReadOnlyCollectionBundleLedger,
    ProviderPaperBundleNormalizationLedger,
    ProviderPaperExternalEvidenceResolutionLedger,
)
from core.broker import provider_paper_external_evidence as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT_ID = "synthetic-paper-account"
BASE = datetime(2025, 8, 14, 12, 0, tzinfo=timezone.utc)
PART_KINDS = ("ACCOUNT_OPEN", "POSITIONS", "OPEN_ORDERS", "ACCOUNT_CLOSE")


def body(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def account(**changes):
    value = {
        "id": ACCOUNT_ID,
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1000",
        "buying_power": "1000",
        "equity": "1500",
        "last_equity": "1490.25",
    }
    value.update(changes)
    return body(value)


def positions():
    return body(
        [
            {
                "symbol": "AAPL",
                "asset_class": "us_equity",
                "side": "long",
                "qty": "5",
                "current_price": "100",
                "market_value": "500",
            }
        ]
    )


def orders():
    return body([])


def admit(source, *, start=0, opening=None, closing=None):
    opening = opening or account()
    closing = closing or opening
    payloads = {
        "ACCOUNT_OPEN": opening,
        "POSITIONS": positions(),
        "OPEN_ORDERS": orders(),
        "ACCOUNT_CLOSE": closing,
    }
    fetched = {
        kind: BASE + timedelta(seconds=start + index + 1)
        for index, kind in enumerate(PART_KINDS)
    }
    return source._admit(
        payloads=payloads,
        fetched_at=fetched,
        collection_started_at=BASE + timedelta(seconds=start),
        collection_finished_at=BASE + timedelta(seconds=start + 5),
        recorded_at=BASE + timedelta(seconds=start + 5),
        monotonic_elapsed_microseconds=5_000_000,
    )


def build(tmp_path, *, opening=None, closing=None):
    source = PaperReadOnlyCollectionBundleLedger(
        tmp_path / "collections.jsonl", tmp_path / "collection-blobs"
    )
    bundle = admit(source, opening=opening, closing=closing)
    normalizations = ProviderPaperBundleNormalizationLedger(
        tmp_path / "normalizations.jsonl", source
    )
    normalization = normalizations.record(
        collection_bundle_id=bundle["bundle_id"],
        recorded_at=BASE + timedelta(seconds=6),
    )
    target = ProviderPaperExternalEvidenceResolutionLedger(
        tmp_path / "external-evidence.jsonl", normalizations
    )
    return source, bundle, normalization, target


def resolve(target, normalization, seconds=7):
    return target.record(
        normalization_id=normalization["normalization_id"],
        recorded_at=BASE + timedelta(seconds=seconds),
    )


def test_documented_previous_close_value_stays_unresolved_without_exact_date(tmp_path):
    _, bundle, normalization, target = build(tmp_path)
    result = resolve(target, normalization)
    assert result["status"] == "EXTERNAL_EVIDENCE_UNRESOLVED"
    assert result["previous_close_status"] == (
        "PROVIDER_VALUE_SUPPORTED_EFFECTIVE_TIME_UNRESOLVED"
    )
    assert result["previous_close_available"] is False
    assert result["previous_close_equity_usd"] is None
    assert result["previous_close_source_field"] == "last_equity"
    assert result["previous_close_effective_at"] is None
    assert result["previous_close_reason_codes"] == [module.PREVIOUS_CLOSE_REASON]
    assert result["exact_fractions"] == {}
    assert result["settlement_status"] == "UNRESOLVED_PROVIDER_SEMANTICS"
    assert result["account_binding_status"] == (
        "UPSTREAM_LOCAL_CREDENTIAL_PRESENCE_ATTESTATION_ONLY"
    )
    assert result["official_account_last_equity_semantics_applied"] is False
    assert result["settlement_reason_codes"] == [module.SETTLEMENT_REASON]
    assert result["account_binding_reason_codes"] == [module.BINDING_REASON]
    assert result["positions_orders_account_binding_proven"] is False
    assert result["cryptographic_authentication_present"] is False
    assert result["account_snapshot_adoption_eligible"] is False
    assert result["risk_snapshot_adoption_eligible"] is False
    assert result["collection_bundle_record_hash"] == bundle["record_hash"]
    assert result["previous_hash"] == GENESIS_HASH
    assert target.verify() == [result]


def test_caller_added_effective_date_fields_cannot_create_previous_close_evidence(
    tmp_path,
):
    opening = account(
        previous_close="2025-08-13T16:00:00-04:00",
        balance_asof="2025-08-13",
    )
    _, _, normalization, target = build(tmp_path, opening=opening)
    result = resolve(target, normalization)
    assert result["previous_close_available"] is False
    assert result["previous_close_effective_at"] is None
    assert result["previous_close_source_field"] == "last_equity"
    assert result["previous_close_reason_codes"] == [module.PREVIOUS_CLOSE_REASON]


@pytest.mark.parametrize(
    "opening,closing,status,reason",
    [
        (
            account(last_equity=None),
            account(last_equity=None),
            "UNRESOLVED_SOURCE_FIELD_MISSING",
            "LAST_EQUITY_MISSING",
        ),
        (
            account(),
            account(last_equity="1491"),
            "UNRESOLVED_BRACKET_VALUES_DISAGREE",
            "PREVIOUS_CLOSE_BRACKET_MISMATCH",
        ),
    ],
)
def test_unproven_previous_close_is_recorded_as_unresolved(
    tmp_path, opening, closing, status, reason
):
    _, _, normalization, target = build(tmp_path, opening=opening, closing=closing)
    result = resolve(target, normalization)
    assert result["status"] == "EXTERNAL_EVIDENCE_UNRESOLVED"
    assert result["previous_close_status"] == status
    assert result["previous_close_available"] is False
    assert result["previous_close_equity_usd"] is None
    assert result["previous_close_effective_at"] is None
    assert result["previous_close_reason_codes"] == [reason]
    assert result["official_account_last_equity_semantics_applied"] is False
    assert "PREVIOUS_CLOSE_EVIDENCE_UNRESOLVED" in result[
        "downstream_blocking_reasons"
    ]
    assert result["exact_fractions"] == {}


def test_cash_like_fields_are_never_relabelled_as_settlement(tmp_path):
    opening = account(
        settled_cash="999",
        unsettled_cash="1",
        cash_withdrawable="900",
        non_marginable_buying_power="950",
    )
    _, _, normalization, target = build(tmp_path, opening=opening)
    result = resolve(target, normalization)
    material = json.dumps(result, sort_keys=True)
    assert result["settlement_breakdown_available"] is False
    assert result["settled_cash_derived_from_account_cash"] is False
    assert '"settled_cash"' not in material
    assert '"unsettled_cash"' not in material


def test_position_and_order_arrays_never_become_account_binding_proof(tmp_path):
    _, _, normalization, target = build(tmp_path)
    result = resolve(target, normalization)
    assert result["upstream_local_collector_attests_credentials_present"] is True
    assert result["provider_endpoints_documented_as_account_scoped"] is True
    assert result["positions_orders_account_id_present"] is False
    assert result["positions_orders_account_binding_proven"] is False
    assert "POSITIONS_ORDERS_ACCOUNT_BINDING_UNRESOLVED" in result[
        "downstream_blocking_reasons"
    ]


def test_blocked_normalization_cannot_be_assessed(tmp_path):
    opening = account(equity="1000")
    _, _, normalization, target = build(tmp_path, opening=opening)
    assert normalization["normalization_succeeded"] is False
    with pytest.raises(ValueError, match="successful normalization"):
        resolve(target, normalization)
    assert target.records() == []


def test_idempotence_and_conflict_controls(tmp_path):
    _, _, normalization, target = build(tmp_path)
    first = resolve(target, normalization)
    assert resolve(target, normalization, seconds=8) == first
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        target.record(
            normalization_id=normalization["normalization_id"],
            recorded_at=BASE + timedelta(seconds=8),
            allow_existing=False,
        )


def test_concurrent_identical_writes_collapse_to_one_record(tmp_path):
    _, _, normalization, target = build(tmp_path)
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda _: resolve(target, normalization), range(16))
        )
    assert len({item["record_hash"] for item in results}) == 1
    assert target.verify() == [results[0]]


def test_account_observations_cannot_be_resolved_backward(tmp_path):
    source = PaperReadOnlyCollectionBundleLedger(
        tmp_path / "collections.jsonl", tmp_path / "collection-blobs"
    )
    older = admit(source, start=0)
    newer = admit(source, start=20)
    normalizations = ProviderPaperBundleNormalizationLedger(
        tmp_path / "normalizations.jsonl", source
    )
    older_normalization = normalizations.record(
        collection_bundle_id=older["bundle_id"],
        recorded_at=BASE + timedelta(seconds=30),
    )
    newer_normalization = normalizations.record(
        collection_bundle_id=newer["bundle_id"],
        recorded_at=BASE + timedelta(seconds=31),
    )
    target = ProviderPaperExternalEvidenceResolutionLedger(
        tmp_path / "external-evidence.jsonl", normalizations
    )
    resolve(target, newer_normalization, seconds=32)
    with pytest.raises(ValueError, match="move forward"):
        resolve(target, older_normalization, seconds=33)


def test_source_tampering_invalidates_resolution(tmp_path):
    source, bundle, normalization, target = build(tmp_path)
    resolve(target, normalization)
    part = next(item for item in bundle["parts"] if item["part_kind"] == "ACCOUNT_OPEN")
    blob = source.blob_directory / part["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(account(last_equity="1"))
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_tampered_resolution_flags_fail_closed(tmp_path):
    _, _, normalization, target = build(tmp_path)
    resolve(target, normalization)
    record = json.loads(target.path.read_text())
    record["settlement_breakdown_available"] = True
    target.path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError, match="modified"):
        target.verify()


def test_rehashed_resolution_cannot_expand_authority(tmp_path):
    _, _, normalization, target = build(tmp_path)
    resolve(target, normalization)
    record = json.loads(target.path.read_text())
    record["paper_order_submission_allowed"] = True
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()


def test_rehashed_resolution_field_alphabet_cannot_change(tmp_path):
    _, _, normalization, target = build(tmp_path)
    resolve(target, normalization)
    record = json.loads(target.path.read_text())
    record["unexpected_authority"] = False
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
    with pytest.raises(LedgerIntegrityError, match="boundary"):
        target.verify()


@pytest.mark.parametrize("value", [True, 1.25, "-1", "1e19", "NaN"])
def test_invalid_last_equity_never_becomes_evidence(tmp_path, value):
    opening = account(last_equity=value)
    _, _, normalization, target = build(tmp_path, opening=opening)
    result = resolve(target, normalization)
    assert result["previous_close_status"] == "UNRESOLVED_SOURCE_FIELD_INVALID"
    assert result["previous_close_equity_usd"] is None
    assert result["exact_fractions"] == {}


def test_no_network_or_order_mutation_surface():
    names = set(dir(ProviderPaperExternalEvidenceResolutionLedger))
    assert not names & {
        "submit_order",
        "replace_order",
        "cancel_order",
        "close_position",
        "collect",
        "request",
        "get",
        "post",
        "patch",
        "delete",
    }
