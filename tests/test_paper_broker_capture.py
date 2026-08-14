from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from core.broker.paper_broker_capture import (
    CAPTURE_ATTESTOR,
    CAPTURE_KIND,
    CAPTURE_METHOD,
    PaperBrokerCaptureLedger,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT_ID = "paper-account-sensitive-123"
ACCOUNT_HASH = hashlib.sha256(ACCOUNT_ID.encode("utf-8")).hexdigest()
BASE_NOW = datetime.now(timezone.utc).replace(microsecond=0)


def moment(seconds):
    return (BASE_NOW + timedelta(seconds=seconds)).isoformat()


def payload(account_id=ACCOUNT_ID, **changes):
    value = {
        "id": account_id,
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1000.00",
        "buying_power": "2000.00",
        "equity": "1800.00",
    }
    value.update(changes)
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def ledger(tmp_path):
    return PaperBrokerCaptureLedger(tmp_path / "captures.jsonl", tmp_path / "blobs")


def admit(target, body=None, **changes):
    values = {
        "payload": payload() if body is None else body,
        "capture_kind": CAPTURE_KIND,
        "broker": "Alpaca",
        "broker_environment": "PAPER",
        "account_reference_sha256": ACCOUNT_HASH,
        "observed_at": moment(-30),
        "captured_at": moment(-20),
        "recorded_at": moment(-10),
        "capture_method": CAPTURE_METHOD,
        "capture_attested_by": CAPTURE_ATTESTOR,
    }
    values.update(changes)
    return target.admit(**values)


def rewrite(path, **changes):
    from core.broker import paper_broker_capture as module

    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


def test_retains_exact_bytes_without_claiming_broker_authenticity(tmp_path):
    target = ledger(tmp_path)
    body = payload()
    result = admit(target, body)
    assert result["status"] == "CAPTURE_BYTES_RETAINED_UNAUTHENTICATED"
    assert result["previous_hash"] == GENESIS_HASH
    assert result["payload_sha256"] == hashlib.sha256(body).hexdigest()
    assert result["account_reference_sha256"] == ACCOUNT_HASH
    assert ACCOUNT_ID not in target.path.read_text()
    assert result["capture_bytes_retained"] is True
    assert result["payload_hash_verified"] is True
    for field in (
        "transport_authenticated",
        "broker_signature_present",
        "broker_reconciliation_complete",
        "settlement_breakdown_available",
        "semantic_claim_validated",
        "credentials_accessed",
        "network_request_performed",
        "order_submitted",
        "paper_order_submission_allowed",
        "live_trading_enabled",
        "recommendation_provided",
        "human_review_eligible",
    ):
        assert result[field] is False
    blob = target.blob_directory / result["blob_relative_path"]
    assert blob.read_bytes() == body
    assert blob.stat().st_mode & 0o777 == 0o400
    assert target.blob_directory.stat().st_mode & 0o777 == 0o700
    assert blob.parent.stat().st_mode & 0o777 == 0o700
    assert target.path.stat().st_mode & 0o777 == 0o600
    assert target.verify() == [result]


def test_explicit_verified_read_returns_the_retained_bytes(tmp_path):
    target = ledger(tmp_path)
    result = admit(target)
    verified, body = target.read_verified(result["capture_evidence_id"])
    assert verified == result
    assert body == payload()
    with pytest.raises(ValueError, match="not found"):
        target.read_verified("PPCE-MISSING")


def test_identical_concurrent_admission_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: admit(target), range(2)))
    assert first == second
    assert len(target.verify()) == 1


def test_conflicting_same_observation_and_non_forward_time_fail_closed(tmp_path):
    target = ledger(tmp_path)
    first = admit(target)
    changed = payload(cash="999.00")
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        admit(target, changed)
    second = admit(
        target,
        observed_at=moment(-5),
        captured_at=moment(-4),
        recorded_at=moment(-3),
    )
    assert second["previous_hash"] == first["record_hash"]
    with pytest.raises(ValueError, match="move forward"):
        admit(
            target,
            observed_at=moment(-15),
            captured_at=moment(-14),
            recorded_at=moment(-13),
        )


@pytest.mark.parametrize(
    "body,account_hash,error",
    [
        (b"not-json", ACCOUNT_HASH, "JSON object"),
        (b"[]", ACCOUNT_HASH, "JSON object"),
        (json.dumps({"status": "ACTIVE"}).encode(), ACCOUNT_HASH, "account id"),
        (json.dumps({"id": 123}).encode(), ACCOUNT_HASH, "JSON string"),
        (json.dumps({"id": " padded "}).encode(), ACCOUNT_HASH, "JSON string"),
        (b'{"id":"first","id":"second"}', ACCOUNT_HASH, "duplicate JSON"),
        (b'{"id":"paper-account-sensitive-123","cash":NaN}', ACCOUNT_HASH, "non-standard"),
        (payload("different-account"), ACCOUNT_HASH, "does not match"),
        (payload(), "not-a-hash", "SHA-256"),
        (b"", ACCOUNT_HASH, "between 1 byte"),
    ],
)
def test_invalid_payload_or_account_identity_fails_before_write(
    tmp_path, body, account_hash, error
):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match=error):
        admit(target, body, account_reference_sha256=account_hash)
    assert target.records() == []


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"capture_kind": "ALPACA_LIVE_V2_ACCOUNT"}, "capture_kind"),
        ({"broker": "Other"}, "Alpaca PAPER"),
        ({"broker_environment": "LIVE"}, "Alpaca PAPER"),
        ({"capture_method": "AUTOMATIC_NETWORK"}, "capture_method"),
        ({"capture_attested_by": "sam@example.test"}, "non-identifying"),
        ({"observed_at": moment(-10), "captured_at": moment(-20)}, "chronological"),
        ({"captured_at": moment(-5), "recorded_at": moment(-10)}, "chronological"),
        ({"recorded_at": moment(600)}, "future"),
    ],
)
def test_environment_method_and_chronology_are_closed(tmp_path, changes, error):
    target = ledger(tmp_path)
    with pytest.raises(ValueError, match=error):
        admit(target, **changes)
    assert target.records() == []


def test_changed_missing_symlink_or_repermissioned_blob_fails_verify(tmp_path):
    for case in ("changed", "missing", "symlink", "permissions"):
        case_path = tmp_path / case
        target = ledger(case_path)
        result = admit(target)
        blob = target.blob_directory / result["blob_relative_path"]
        if case == "changed":
            blob.chmod(0o600)
            blob.write_bytes(payload(cash="1"))
            blob.chmod(0o400)
        elif case == "missing":
            blob.unlink()
        elif case == "symlink":
            blob.unlink()
            outside = case_path / "outside.json"
            outside.write_bytes(payload())
            blob.symlink_to(outside)
        else:
            blob.chmod(0o600)
        with pytest.raises(LedgerIntegrityError):
            target.verify()


def test_hardlinked_blob_fails_verification(tmp_path):
    target = ledger(tmp_path)
    result = admit(target)
    blob = target.blob_directory / result["blob_relative_path"]
    second_name = tmp_path / "second-name.blob"
    second_name.hardlink_to(blob)
    with pytest.raises(LedgerIntegrityError, match="invalid retained evidence"):
        target.verify()


def test_symlinked_or_insecure_ledger_and_lock_fail_closed(tmp_path):
    for case in ("ledger-symlink", "ledger-hardlink", "ledger-mode", "lock-symlink"):
        case_path = tmp_path / case
        case_path.mkdir(mode=0o700)
        target = ledger(case_path)
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
            admit(target)


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "AUTHENTICATED"},
        {"transport_authenticated": True},
        {"broker_signature_present": True},
        {"broker_reconciliation_complete": True},
        {"settlement_breakdown_available": True},
        {"semantic_claim_validated": True},
        {"credentials_accessed": True},
        {"network_request_performed": True},
        {"order_submitted": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"recommendation_provided": True},
        {"human_review_eligible": True},
        {"capture_bytes_retained": False},
        {"payload_hash_verified": False},
        {"capture_attested_by": "raw-account-id-or-email"},
        {"byte_length": 100.0},
        {"capture_bytes_retained": 1},
        {"transport_authenticated": 0},
        {"payload_sha256": "0" * 64},
        {"blob_relative_path": "sha256/00/" + "0" * 64 + ".blob"},
        {"unexpected": True},
    ],
)
def test_rehashed_metadata_cannot_expand_capture_authority(tmp_path, changes):
    target = ledger(tmp_path)
    admit(target)
    rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_incomplete_final_line_is_rejected(tmp_path):
    target = ledger(tmp_path)
    admit(target)
    target.path.write_bytes(target.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        target.records()


def test_oversized_retained_blob_is_rejected_before_read(tmp_path):
    from core.broker.paper_broker_capture import MAX_CAPTURE_BYTES

    target = ledger(tmp_path)
    result = admit(target)
    blob = target.blob_directory / result["blob_relative_path"]
    blob.chmod(0o600)
    with blob.open("wb") as destination:
        destination.truncate(MAX_CAPTURE_BYTES + 1)
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError, match="invalid retained evidence"):
        target.verify()
