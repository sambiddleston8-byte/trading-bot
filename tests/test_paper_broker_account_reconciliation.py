from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from core.broker import (
    PaperBrokerAccountReconciliationLedger,
    PaperBrokerAccountSnapshotLedger,
    PaperBrokerCaptureLedger,
)
from core.broker import paper_broker_account_reconciliation as module
from core.broker.paper_broker_capture import (
    CAPTURE_ATTESTOR,
    CAPTURE_KIND,
    CAPTURE_METHOD,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT_ID = "paper-account-sensitive-123"
ACCOUNT_HASH = hashlib.sha256(ACCOUNT_ID.encode("utf-8")).hexdigest()
BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def moment(seconds):
    return (BASE_NOW + timedelta(seconds=seconds)).isoformat()


def account_object(**changes):
    value = {
        "id": ACCOUNT_ID,
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1000.00",
        "buying_power": "900.00",
        "equity": "1500.00",
    }
    value.update(changes)
    return value


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def canonical_hash(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def ledgers(tmp_path):
    return (
        PaperBrokerCaptureLedger(tmp_path / "captures.jsonl", tmp_path / "blobs"),
        PaperBrokerAccountSnapshotLedger(tmp_path / "snapshots.jsonl"),
    )


def admit_capture(captures, raw, *, offset=-40, **changes):
    values = {
        "payload": raw,
        "capture_kind": CAPTURE_KIND,
        "broker": "Alpaca",
        "broker_environment": "PAPER",
        "account_reference_sha256": ACCOUNT_HASH,
        "observed_at": moment(offset),
        "captured_at": moment(offset + 2),
        "recorded_at": moment(offset + 4),
        "capture_method": CAPTURE_METHOD,
        "capture_attested_by": CAPTURE_ATTESTOR,
    }
    values.update(changes)
    return captures.admit(**values)


def record_snapshot(snapshots, value, *, offset=-40, **changes):
    values = {
        "broker": "Alpaca",
        "account_reference_sha256": ACCOUNT_HASH,
        "observed_at": moment(offset),
        "recorded_at": moment(offset + 4),
        "cash": "1000.00",
        "settled_cash": "900",
        "unsettled_cash": "100",
        "buying_power": "900.00",
        "equity": "1500.00",
        "source_payload_sha256": canonical_hash(value),
        "paper_account_confirmed": True,
    }
    values.update(changes)
    return snapshots.record(**values)


def build(tmp_path, *, value=None, raw=None, offset=-40, snapshot_changes=None):
    value = account_object() if value is None else value
    captures, snapshots = ledgers(tmp_path)
    capture = admit_capture(
        captures, canonical_bytes(value) if raw is None else raw, offset=offset
    )
    snapshot = record_snapshot(
        snapshots, value, offset=offset, **(snapshot_changes or {})
    )
    target = PaperBrokerAccountReconciliationLedger(
        tmp_path / "reconciliations.jsonl", captures, snapshots
    )
    return target, capture, snapshot


def reconcile(target, capture, snapshot, *, offset=-30, **changes):
    values = {
        "capture_evidence_id": capture["capture_evidence_id"],
        "capture_record_hash": capture["record_hash"],
        "snapshot_id": snapshot["snapshot_id"],
        "snapshot_record_hash": snapshot["record_hash"],
        "reconciled_at": moment(offset),
        "recorded_at": moment(offset + 1),
    }
    values.update(changes)
    return target.reconcile(**values)


def rewrite(path, remove=(), **changes):
    record = json.loads(path.read_text().splitlines()[-1])
    record.update(changes)
    for field in remove:
        record.pop(field, None)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_reconciles_five_account_fields_without_claiming_authentication(tmp_path):
    target, capture, snapshot = build(tmp_path)
    result = reconcile(target, capture, snapshot)
    assert result["status"] == "ACCOUNT_FIELDS_RECONCILED_UNAUTHENTICATED"
    assert result["record_type"] == "PAPER_BROKER_ACCOUNT_FIELD_RECONCILIATION"
    assert result["previous_hash"] == GENESIS_HASH
    assert result["capture_evidence_id"] == capture["capture_evidence_id"]
    assert result["capture_record_hash"] == capture["record_hash"]
    assert result["capture_payload_sha256"] == capture["payload_sha256"]
    assert result["snapshot_id"] == snapshot["snapshot_id"]
    assert result["snapshot_record_hash"] == snapshot["record_hash"]
    assert result["snapshot_source_payload_sha256"] == snapshot["source_payload_sha256"]
    assert result["captured_object_canonical_sha256"] == snapshot["source_payload_sha256"]
    assert result["observed_at"] == capture["observed_at"] == snapshot["observed_at"]
    assert result["next_authority"] == "AUTHENTICATED_BROKER_RECONCILIATION_REQUIRED"
    for field in module.TRUE_FIELDS:
        assert result[field] is True
    for field in (
        "transport_authenticated",
        "broker_origin_authenticated",
        "settlement_breakdown_reconciled",
        "positions_reconciled",
        "open_orders_reconciled",
        "previous_close_reconciled",
        "broker_reconciliation_complete",
        "cryptographic_authentication_present",
        "risk_policy_assessed",
        "risk_limits_enforced",
        "order_route_exists",
        "credentials_accessed",
        "network_request_performed",
        "order_submitted",
        "paper_order_submission_allowed",
        "recommendation_provided",
        "human_review_eligible",
        "live_trading_enabled",
        "source_payload_copied",
    ):
        assert result[field] is False
    assert set(result) == module.RECORD_FIELDS
    assert target.verify() == [result]
    assert target.path.stat().st_mode & 0o777 == 0o600


def test_no_account_identifier_or_amount_is_stored(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    text = target.path.read_text()
    assert ACCOUNT_ID not in text
    for amount in ("1000.00", "900.00", "1500.00"):
        assert amount not in text
    for field in ('"cash"', '"buying_power"', '"equity"', '"settled_cash"', '"id"'):
        assert field not in text


def test_caller_cannot_supply_success_booleans(tmp_path):
    target, capture, snapshot = build(tmp_path)
    with pytest.raises(TypeError):
        reconcile(target, capture, snapshot, cash_matched=True)


def test_raw_bytes_hash_differs_from_the_canonical_object_hash(tmp_path):
    value = account_object()
    raw = json.dumps(value, indent=2).encode("utf-8")
    assert raw != canonical_bytes(value)
    target, capture, snapshot = build(tmp_path, value=value, raw=raw)
    result = reconcile(target, capture, snapshot)
    assert result["capture_payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["capture_payload_sha256"] != result["captured_object_canonical_sha256"]
    assert result["captured_object_canonical_sha256"] == canonical_hash(value)
    assert target.verify() == [result]


def test_identical_concurrent_reconciliation_is_idempotent(tmp_path):
    target, capture, snapshot = build(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(
            executor.map(lambda _: reconcile(target, capture, snapshot), range(2))
        )
    assert first == second
    assert len(target.verify()) == 1


def test_conflicting_reuse_of_the_same_sources_fails_closed(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        reconcile(target, capture, snapshot, offset=-20)
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        reconcile(target, capture, snapshot, allow_existing=False)


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"status": "INACTIVE"}, "status must be the exact string ACTIVE"),
        ({"status": " ACTIVE "}, "status must be the exact string ACTIVE"),
        ({"status": 1}, "status must be the exact string ACTIVE"),
        ({"currency": "EUR"}, "currency must be the exact string USD"),
        ({"cash": 1000.0}, "captured cash"),
        ({"cash": True}, "captured cash"),
        ({"cash": "-1"}, "captured cash"),
        ({"cash": "NaN"}, "captured cash"),
        ({"cash": " 1000.00 "}, "captured cash"),
        ({"cash": "1001.00"}, "Captured cash does not match"),
        ({"buying_power": "1.00"}, "Captured buying_power does not match"),
        ({"equity": "2.00"}, "Captured equity does not match"),
    ],
)
def test_every_account_field_mismatch_fails_closed(tmp_path, changes, error):
    value = account_object(**changes)
    target, capture, snapshot = build(tmp_path, value=value)
    with pytest.raises(ValueError, match=error):
        reconcile(target, capture, snapshot)
    assert target.records() == []


def test_missing_captured_field_fails_closed(tmp_path):
    value = account_object()
    value.pop("equity")
    target, capture, snapshot = build(tmp_path, value=value)
    with pytest.raises(ValueError, match="captured equity"):
        reconcile(target, capture, snapshot)
    assert target.records() == []


def test_canonical_hash_must_match_the_snapshot_source_hash(tmp_path):
    target, capture, snapshot = build(
        tmp_path, snapshot_changes={"source_payload_sha256": "c" * 64}
    )
    with pytest.raises(ValueError, match="does not match the snapshot source hash"):
        reconcile(target, capture, snapshot)
    assert target.records() == []


def test_cash_must_equal_the_snapshot_total_cash(tmp_path):
    target, capture, snapshot = build(tmp_path)
    verified, payload = target.capture_ledger.read_verified(
        capture["capture_evidence_id"]
    )
    fractions = dict(snapshot["exact_fractions"])
    fractions["unsettled_cash"] = {"numerator": "1", "denominator": "1"}
    tampered = {**snapshot, "exact_fractions": fractions}
    with pytest.raises(ValueError, match="total cash"):
        target._build(
            verified,
            payload,
            tampered,
            module._timestamp(moment(-30)),
            module._timestamp(moment(-29)),
        )


@pytest.mark.parametrize(
    "snapshot_changes,error",
    [
        ({"account_reference_sha256": "f" * 64}, "account references do not match"),
        ({"observed_at": moment(-39)}, "observed_at must be identical"),
    ],
)
def test_account_identity_and_observation_must_match(
    tmp_path, snapshot_changes, error
):
    target, capture, snapshot = build(tmp_path, snapshot_changes=snapshot_changes)
    with pytest.raises(ValueError, match=error):
        reconcile(target, capture, snapshot)
    assert target.records() == []


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"reconciled_at": moment(-38)}, "must follow every pinned source"),
        (
            {"reconciled_at": moment(-20), "recorded_at": moment(-25)},
            "cannot exceed recorded_at",
        ),
        (
            {"reconciled_at": moment(600), "recorded_at": moment(900)},
            "cannot be in the future",
        ),
    ],
)
def test_forward_time_conflicts_fail_closed(tmp_path, changes, error):
    target, capture, snapshot = build(tmp_path)
    with pytest.raises(ValueError, match=error):
        reconcile(target, capture, snapshot, **changes)
    assert target.records() == []


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"capture_evidence_id": "PPCE-MISSING"}, "missing"),
        ({"snapshot_id": "PBAS-MISSING"}, "missing"),
        ({"capture_record_hash": "0" * 64}, "hash has changed"),
        ({"snapshot_record_hash": "0" * 64}, "hash has changed"),
        ({"capture_evidence_id": ""}, "capture_evidence_id"),
        ({"snapshot_id": "  "}, "snapshot_id"),
        ({"capture_record_hash": "not-a-hash"}, "SHA-256"),
        ({"snapshot_record_hash": None}, "SHA-256"),
    ],
)
def test_unexpected_or_missing_source_links_fail_closed(tmp_path, changes, error):
    target, capture, snapshot = build(tmp_path)
    with pytest.raises(ValueError, match=error):
        reconcile(target, capture, snapshot, **changes)
    assert target.records() == []


def test_observations_move_forward_for_each_account(tmp_path):
    value = account_object()
    captures, snapshots = ledgers(tmp_path)
    first_capture = admit_capture(captures, canonical_bytes(value), offset=-40)
    first_snapshot = record_snapshot(snapshots, value, offset=-40)
    second_capture = admit_capture(captures, canonical_bytes(value), offset=-20)
    second_snapshot = record_snapshot(snapshots, value, offset=-20)
    target = PaperBrokerAccountReconciliationLedger(
        tmp_path / "reconciliations.jsonl", captures, snapshots
    )
    second = reconcile(target, second_capture, second_snapshot, offset=-10)
    assert second["previous_hash"] == GENESIS_HASH
    with pytest.raises(ValueError, match="move forward"):
        reconcile(target, first_capture, first_snapshot, offset=-30)
    assert target.verify() == [second]


@pytest.mark.parametrize(
    "payload,error",
    [
        (b"not-json", "UTF-8 JSON object"),
        (b"[]", "UTF-8 JSON object"),
        (b"\xff\xfe", "UTF-8 JSON object"),
        (b'{"cash":"1","cash":"2"}', "duplicate JSON keys"),
        (b'{"cash":NaN}', "non-standard JSON constant"),
        (b'{"cash":Infinity}', "non-standard JSON constant"),
    ],
)
def test_malformed_or_ambiguous_captured_json_is_rejected(payload, error):
    with pytest.raises(ValueError, match=error):
        module._payload_object(payload)


def test_tampered_capture_blob_or_snapshot_fails_verification(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    blob = target.capture_ledger.blob_directory / capture["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(canonical_bytes(account_object(cash="1.00")))
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_tampered_snapshot_source_fails_verification(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    rewrite(target.snapshot_ledger.path, cash="1")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


@pytest.mark.parametrize(
    "changes,remove",
    [
        ({"status": "AUTHENTICATED"}, ()),
        ({"transport_authenticated": True}, ()),
        ({"broker_origin_authenticated": True}, ()),
        ({"settlement_breakdown_reconciled": True}, ()),
        ({"positions_reconciled": True}, ()),
        ({"open_orders_reconciled": True}, ()),
        ({"previous_close_reconciled": True}, ()),
        ({"broker_reconciliation_complete": True}, ()),
        ({"cryptographic_authentication_present": True}, ()),
        ({"risk_policy_assessed": True}, ()),
        ({"risk_limits_enforced": True}, ()),
        ({"order_route_exists": True}, ()),
        ({"credentials_accessed": True}, ()),
        ({"network_request_performed": True}, ()),
        ({"order_submitted": True}, ()),
        ({"paper_order_submission_allowed": True}, ()),
        ({"recommendation_provided": True}, ()),
        ({"human_review_eligible": True}, ()),
        ({"live_trading_enabled": True}, ()),
        ({"source_payload_copied": True}, ()),
        ({"capture_bytes_locally_verified": False}, ()),
        ({"snapshot_record_verified": False}, ()),
        ({"cash_matched": 1}, ()),
        ({"equity_matched": 1}, ()),
        ({"transport_authenticated": 0}, ()),
        ({"account_reference_sha256": "f" * 64}, ()),
        ({"capture_payload_sha256": "0" * 64}, ()),
        ({"captured_object_canonical_sha256": "0" * 64}, ()),
        ({"snapshot_source_payload_sha256": "0" * 64}, ()),
        ({"capture_record_hash": "0" * 64}, ()),
        ({"reconciliation_id": "PBARC-" + "0" * 32}, ()),
        ({"policy_version": "other"}, ()),
        ({"next_authority": "NONE_REQUIRED"}, ()),
        ({"observed_at": moment(-1)}, ()),
        ({"unexpected": True}, ()),
        ({}, ("next_authority",)),
        ({}, ("cash_matched",)),
    ],
)
def test_rehashed_records_cannot_expand_authority(tmp_path, changes, remove):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    rewrite(target.path, remove=remove, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_broken_hash_chain_is_detected(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    record = json.loads(target.path.read_text())
    record["previous_hash"] = "1" * 64
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError, match="was modified"):
        target.verify()


def test_incomplete_final_line_is_rejected(tmp_path):
    target, capture, snapshot = build(tmp_path)
    reconcile(target, capture, snapshot)
    target.path.write_bytes(target.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.records()


def test_symlinked_hardlinked_or_repermissioned_ledger_fails_closed(tmp_path):
    for case in ("ledger-symlink", "ledger-hardlink", "ledger-mode", "lock-symlink"):
        case_path = tmp_path / case
        case_path.mkdir(mode=0o700)
        target, capture, snapshot = build(case_path)
        outside = case_path / "outside"
        outside.write_bytes(b"")
        outside.chmod(0o600)
        if case == "ledger-symlink":
            target.path.symlink_to(outside)
        elif case == "ledger-hardlink":
            target.path.hardlink_to(outside)
        elif case == "ledger-mode":
            target.path.write_bytes(b"")
            target.path.chmod(0o644)
        else:
            target.path.with_suffix(".jsonl.lock").symlink_to(outside)
        with pytest.raises(LedgerIntegrityError):
            reconcile(target, capture, snapshot)
