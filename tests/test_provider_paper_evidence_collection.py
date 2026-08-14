from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json
import traceback

import pytest
import requests

from core.broker import (
    AlpacaPaperConfiguration,
    AlpacaPaperReadOnlyCollector,
    PaperEvidenceCollectionError,
    PaperReadOnlyCollectionBundleLedger,
)
from core.broker import provider_paper_evidence_collection as module
from core.data_sources.provider_configuration import ProviderConfiguration
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


ACCOUNT_ID = "synthetic-paper-account-123"
BASE = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=2)


def body(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


ACCOUNT = body(
    {
        "id": ACCOUNT_ID,
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1000",
        "buying_power": "900",
        "equity": "1500",
    }
)
POSITIONS = body([{"symbol": "AAPL", "side": "long", "qty": "2"}])
ORDERS = body(
    [{"id": "order-1", "symbol": "MSFT", "status": "partially_filled"}]
)


class Response:
    def __init__(self, url, content, status_code=200):
        self.url = url
        self.content = content
        self.status_code = status_code


class Session:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [])
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        outcome = self.outcomes.pop(0) if self.outcomes else Response(url, b"{}")
        if isinstance(outcome, Exception):
            raise outcome
        if outcome.url is None:
            outcome.url = url
        return outcome


class Clock:
    def __init__(self, *, seconds=(0, 1, 2, 3, 4), monotonic=(10.0, 14.0)):
        self.times = [BASE + timedelta(seconds=value) for value in seconds]
        self.monotonic_values = list(monotonic)

    def wall(self):
        return self.times.pop(0)

    def monotonic(self):
        return self.monotonic_values.pop(0)


def urls():
    base = "https://paper-api.alpaca.markets"
    return (
        f"{base}/v2/account",
        f"{base}/v2/positions",
        f"{base}/v2/orders?direction=asc&limit=500&nested=false&status=open",
        f"{base}/v2/account",
    )


def happy_session(*, closing=ACCOUNT):
    account_url, positions_url, orders_url, closing_url = urls()
    return Session(
        [
            Response(account_url, ACCOUNT),
            Response(positions_url, POSITIONS),
            Response(orders_url, ORDERS),
            Response(closing_url, closing),
        ]
    )


def ledger(tmp_path):
    return PaperReadOnlyCollectionBundleLedger(
        tmp_path / "collections.jsonl", tmp_path / "collection-blobs"
    )


def collector(tmp_path, session=None, clock=None, sleeper=None):
    clock = clock or Clock()
    return AlpacaPaperReadOnlyCollector(
        ledger=ledger(tmp_path),
        session=session or happy_session(),
        wall_clock=clock.wall,
        monotonic_clock=clock.monotonic,
        sleeper=sleeper or (lambda _: None),
    )


@pytest.fixture
def credentials(monkeypatch):
    monkeypatch.setenv("ALPACA_PAPER_API_KEY", "synthetic-key-secret")
    monkeypatch.setenv("ALPACA_PAPER_API_SECRET", "synthetic-api-secret")
    monkeypatch.setattr(
        ProviderConfiguration, "load_local_environment", classmethod(lambda cls: None)
    )


def test_missing_credentials_makes_no_call_or_write(monkeypatch, tmp_path):
    monkeypatch.delenv("ALPACA_PAPER_API_KEY", raising=False)
    monkeypatch.delenv("ALPACA_PAPER_API_SECRET", raising=False)
    monkeypatch.setattr(
        ProviderConfiguration, "load_local_environment", classmethod(lambda cls: None)
    )
    session = happy_session()
    target = collector(tmp_path, session=session)
    result = target.collect_and_admit()
    assert result["status"] == "NOT_CONFIGURED"
    assert session.calls == []
    assert target.ledger.records() == []
    assert not target.ledger.blob_directory.exists()


def test_happy_path_uses_four_fixed_gets_and_retains_three_private_blobs(
    credentials, tmp_path
):
    session = happy_session()
    target = collector(tmp_path, session=session)
    summary = target.collect_and_admit()
    assert summary == {
        "status": "RECORDED_LOCAL_ATTESTATION_ONLY",
        "broker": "Alpaca",
        "broker_environment": "PAPER",
        "collection_recorded": True,
        "bundle_id": summary["bundle_id"],
        "record_hash": summary["record_hash"],
        "credentials_returned": False,
        "source_payload_returned": False,
        "order_submitted": False,
        "live_trading_enabled": False,
    }
    assert [call[0] for call in session.calls] == list(urls())
    assert all(call[1]["timeout"] == 20 for call in session.calls)
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert all(set(call[1]["headers"]) == {
        "APCA-API-KEY-ID", "APCA-API-SECRET-KEY"
    } for call in session.calls)
    [record] = target.ledger.verify()
    assert record["status"] == "COLLECTION_BYTES_RETAINED_LOCAL_ATTESTATION_ONLY"
    assert record["previous_hash"] == GENESIS_HASH
    assert record["next_authority"] == "AUTHENTICATED_SEMANTIC_RECONCILIATION_REQUIRED"
    for name in module.TRUE_FIELDS:
        assert record[name] is True
    for name in module.FALSE_FIELDS:
        assert record[name] is False
    assert [part["part_kind"] for part in record["parts"]] == list(module.PART_KINDS)
    for part in record["parts"]:
        blob = target.ledger.blob_directory / part["blob_relative_path"]
        assert blob.stat().st_mode & 0o777 == 0o400
    assert len(list(target.ledger.blob_directory.rglob("*.blob"))) == 3
    assert target.ledger.path.stat().st_mode & 0o777 == 0o600


def test_explicit_verified_read_is_the_only_raw_payload_return(credentials, tmp_path):
    target = collector(tmp_path)
    summary = target.collect_and_admit()
    assert ACCOUNT_ID not in json.dumps(summary)
    record, payloads = target.ledger.read_verified(summary["bundle_id"])
    assert record["bundle_id"] == summary["bundle_id"]
    assert payloads == {
        "ACCOUNT_OPEN": ACCOUNT,
        "POSITIONS": POSITIONS,
        "OPEN_ORDERS": ORDERS,
        "ACCOUNT_CLOSE": ACCOUNT,
    }


def test_credentials_are_absent_from_summary_ledger_blobs_and_errors(credentials, tmp_path):
    target = collector(tmp_path)
    summary = target.collect_and_admit()
    material = json.dumps(summary).encode() + target.ledger.path.read_bytes()
    for blob in target.ledger.blob_directory.rglob("*.blob"):
        material += blob.read_bytes()
    assert b"synthetic-key-secret" not in material
    assert b"synthetic-api-secret" not in material
    bad = Session([Response(urls()[0], b"{}", 401)])
    with pytest.raises(PaperEvidenceCollectionError) as caught:
        collector(tmp_path / "bad", session=bad).collect_and_admit()
    assert "synthetic-key-secret" not in str(caught.value)
    assert "synthetic-api-secret" not in str(caught.value)
    assert caught.value.__cause__ is None
    formatted = "".join(traceback.format_exception(caught.value))
    assert "synthetic-key-secret" not in formatted
    assert "synthetic-api-secret" not in formatted


def test_explicit_session_and_paper_configuration_are_mandatory(tmp_path):
    with pytest.raises(ValueError, match="explicit read-only session"):
        AlpacaPaperReadOnlyCollector(ledger=ledger(tmp_path), session=None)
    with pytest.raises(ValueError, match="official paper"):
        AlpacaPaperReadOnlyCollector(
            ledger=ledger(tmp_path),
            session=Session(),
            configuration=AlpacaPaperConfiguration("https://api.alpaca.markets"),
        )


@pytest.mark.parametrize(
    "outcome,fragment",
    [
        (Response("https://paper-api.alpaca.markets/v2/account", ACCOUNT, 302), "redirect"),
        (Response("https://evil.test/v2/account", ACCOUNT), "final endpoint"),
        (Response("http://paper-api.alpaca.markets/v2/account", ACCOUNT), "final endpoint"),
    ],
)
def test_redirect_or_changed_final_endpoint_fails_before_write(
    credentials, tmp_path, outcome, fragment
):
    session = Session([outcome])
    target = collector(tmp_path, session=session)
    with pytest.raises(PaperEvidenceCollectionError, match=fragment):
        target.collect_and_admit()
    assert target.ledger.records() == []


def test_closing_account_identity_mismatch_leaves_no_evidence(credentials, tmp_path):
    changed = body(
        {"id": "other", "status": "ACTIVE", "currency": "USD", "cash": "1"}
    )
    target = collector(tmp_path, session=happy_session(closing=changed))
    with pytest.raises(PaperEvidenceCollectionError, match="identity changed"):
        target.collect_and_admit()
    assert target.ledger.records() == []
    assert not target.ledger.blob_directory.exists()


@pytest.mark.parametrize(
    "position_payload,fragment",
    [
        (body([{"symbol": "AAPL", "side": "short"}]), "long-only"),
        (body([{"symbol": "AAPL", "side": "long"}, {"symbol": "AAPL", "side": "long"}]), "unique"),
        (body([{"symbol": "bad ticker", "side": "long"}]), "unique valid"),
        (b'{"not":"an array"}', "JSON array"),
        (b'[{"symbol":"AAPL","symbol":"MSFT","side":"long"}]', "duplicate"),
    ],
)
def test_invalid_positions_fail_before_write(credentials, tmp_path, position_payload, fragment):
    account_url, positions_url, _, _ = urls()
    session = Session([Response(account_url, ACCOUNT), Response(positions_url, position_payload)])
    target = collector(tmp_path, session=session)
    with pytest.raises(ValueError, match=fragment):
        target.collect_and_admit()
    assert target.ledger.records() == []


@pytest.mark.parametrize(
    "orders_payload,fragment",
    [
        (body([{"id": "1", "symbol": "AAPL", "status": "filled"}]), "not open"),
        (body([{"id": "1", "symbol": "AAPL", "status": "new"}, {"id": "1", "symbol": "MSFT", "status": "new"}]), "unique"),
        (body([{"id": 1, "symbol": "AAPL", "status": "new"}]), "exact"),
        (body([{"id": str(i), "symbol": "AAPL", "status": "new"} for i in range(500)]), "ORDER_PAGE_INCOMPLETE"),
    ],
)
def test_invalid_or_incomplete_open_orders_fail_before_write(
    credentials, tmp_path, orders_payload, fragment
):
    account_url, positions_url, orders_url, _ = urls()
    session = Session(
        [Response(account_url, ACCOUNT), Response(positions_url, POSITIONS), Response(orders_url, orders_payload)]
    )
    target = collector(tmp_path, session=session)
    with pytest.raises(ValueError, match=fragment):
        target.collect_and_admit()
    assert target.ledger.records() == []


@pytest.mark.parametrize(
    "clock,fragment",
    [
        (Clock(seconds=(0, 1, 2, 3, 31)), "wall-clock"),
        (Clock(monotonic=(0.0, 31.0)), "monotonic"),
    ],
)
def test_collection_windows_fail_closed(credentials, tmp_path, clock, fragment):
    target = collector(tmp_path, clock=clock)
    with pytest.raises(PaperEvidenceCollectionError, match=fragment):
        target.collect_and_admit()
    assert target.ledger.records() == []


def test_retry_policy_is_narrow_and_secret_safe(credentials, tmp_path):
    slept = []
    account_url = urls()[0]
    session = Session(
        [requests.Timeout("synthetic"), Response(account_url, ACCOUNT)]
        + happy_session().outcomes[1:]
    )
    target = collector(tmp_path, session=session, sleeper=slept.append)
    assert target.collect_and_admit()["collection_recorded"] is True
    assert slept == [1.0]
    assert len(session.calls) == 5

    rejected = Session([Response(account_url, ACCOUNT, 401)])
    with pytest.raises(PaperEvidenceCollectionError, match="401"):
        collector(tmp_path / "rejected", session=rejected).collect_and_admit()
    assert len(rejected.calls) == 1


def direct_admit(target, *, start=0, account=ACCOUNT, positions=POSITIONS, orders=ORDERS):
    return target._admit(
        payloads={
            "ACCOUNT_OPEN": account,
            "POSITIONS": positions,
            "OPEN_ORDERS": orders,
            "ACCOUNT_CLOSE": account,
        },
        fetched_at={kind: BASE + timedelta(seconds=start + index + 1) for index, kind in enumerate(module.PART_KINDS)},
        collection_started_at=BASE + timedelta(seconds=start),
        collection_finished_at=BASE + timedelta(seconds=start + 4),
        recorded_at=BASE + timedelta(seconds=start + 5),
        monotonic_elapsed_microseconds=4_000_000,
    )


def test_identical_concurrent_admission_is_idempotent(tmp_path):
    target = ledger(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        first, second = list(executor.map(lambda _: direct_admit(target), range(2)))
    assert first == second
    assert len(target.verify()) == 1


def test_same_window_conflict_and_backward_collection_fail_before_append(tmp_path):
    target = ledger(tmp_path)
    first = direct_admit(target, start=10)
    with pytest.raises(ValueError, match="move forward"):
        direct_admit(target, start=0)
    changed_orders = body([{"id": "other", "symbol": "AAPL", "status": "new"}])
    with pytest.raises(LedgerIntegrityError, match="conflicts"):
        direct_admit(target, start=10, orders=changed_orders)
    assert target.verify() == [first]


def rewrite(path, **changes):
    record = json.loads(path.read_text())
    record.update(changes)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    path.write_text(json.dumps(record) + "\n")


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "AUTHENTICATED"},
        {"transport_authentication_evidence_retained": True},
        {"broker_origin_nonrepudiable": True},
        {"broker_reconciliation_complete": True},
        {"order_submitted": True},
        {"paper_order_submission_allowed": True},
        {"live_trading_enabled": True},
        {"collection_bytes_retained": 1},
        {"unexpected": True},
    ],
)
def test_rehashed_metadata_cannot_expand_authority(tmp_path, changes):
    target = ledger(tmp_path)
    direct_admit(target)
    rewrite(target.path, **changes)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


@pytest.mark.parametrize("case", ["path", "status", "length", "blob", "account"])
def test_rehashed_part_or_account_metadata_cannot_change_observation(tmp_path, case):
    target = ledger(tmp_path)
    direct_admit(target)
    record = json.loads(target.path.read_text())
    if case == "path":
        record["parts"][2]["request_path"] = "/v2/orders/submit"
    elif case == "status":
        record["parts"][0]["http_status"] = 201
    elif case == "length":
        record["parts"][0]["byte_length"] += 1
    elif case == "blob":
        record["parts"][0]["blob_relative_path"] = record["parts"][1][
            "blob_relative_path"
        ]
    else:
        record["account_reference_sha256"] = "0" * 64
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_blob_tamper_mode_symlink_and_hardlink_fail_closed(tmp_path):
    for case in ("bytes", "mode", "symlink", "hardlink"):
        case_path = tmp_path / case
        target = ledger(case_path)
        record = direct_admit(target)
        blob = target.blob_directory / record["parts"][0]["blob_relative_path"]
        if case == "bytes":
            blob.chmod(0o600)
            blob.write_bytes(b"changed")
            blob.chmod(0o400)
        elif case == "mode":
            blob.chmod(0o600)
        elif case == "symlink":
            blob.unlink()
            outside = case_path / "outside"
            outside.write_bytes(ACCOUNT)
            blob.symlink_to(outside)
        else:
            (case_path / "second-name").hardlink_to(blob)
        with pytest.raises(LedgerIntegrityError):
            target.verify()


def test_ledger_link_mode_and_incomplete_tail_fail_closed(tmp_path):
    for case in ("symlink", "hardlink", "mode"):
        case_path = tmp_path / case
        case_path.mkdir(mode=0o700)
        target = ledger(case_path)
        outside = case_path / "outside"
        outside.write_bytes(b"")
        outside.chmod(0o600)
        if case == "symlink":
            target.path.symlink_to(outside)
        elif case == "hardlink":
            target.path.hardlink_to(outside)
        else:
            target.path.write_bytes(b"")
            target.path.chmod(0o644)
        with pytest.raises(LedgerIntegrityError):
            direct_admit(target)

    incomplete = ledger(tmp_path / "incomplete")
    direct_admit(incomplete)
    incomplete.path.write_bytes(incomplete.path.read_bytes().rstrip(b"\n"))
    with pytest.raises(LedgerIntegrityError, match="incomplete final line"):
        incomplete.records()


def test_surface_contains_no_order_mutation_or_other_http_verbs():
    source = inspect.getsource(module.AlpacaPaperReadOnlyCollector).lower()
    assert ".post(" not in source
    assert ".put(" not in source
    assert ".patch(" not in source
    assert ".delete(" not in source
    for name in ("submit", "cancel", "replace", "liquidate", "close_position"):
        assert not hasattr(module.AlpacaPaperReadOnlyCollector, name)
    assert not hasattr(module.PaperReadOnlyCollectionBundleLedger, "admit")
