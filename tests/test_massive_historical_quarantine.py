from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import inspect
import json

import pytest

import core.orchestration.historical_quarantine_preregistration as prereg_module
import core.orchestration.massive_historical_quarantine as module
import scripts.fetch_massive_quarantine as fetch_script
from core.data_sources.provider_access import ProviderAttemptMetadata
from core.decision_ledger import LedgerIntegrityError
from core.orchestration.historical_quarantine_preregistration import (
    HistoricalQuarantinePreregistrationLedger,
)
from core.orchestration.massive_historical_adapter import MassiveFetchedPayload
from core.orchestration.massive_historical_quarantine import (
    MassiveHistoricalQuarantineFetcher,
    MassiveHistoricalQuarantineStore,
    QuarantinedHistoricalPayload,
    planned_request_slices,
)
from core.research.vectorbt_pilot import VectorBTPilotAdapter


UTC = timezone.utc


def preregistration_definition():
    now = datetime.now(UTC)
    return {
        "registered_by": "SAM_AND_PAT_LOCAL_RESEARCH",
        "acquisition_start": "2025-08-01",
        "acquisition_end": "2026-07-31",
        "splits": [
            {"role": "TRAIN", "start": "2025-08-01", "end": "2026-02-28"},
            {"role": "VALIDATION", "start": "2026-03-01", "end": "2026-04-30"},
            {"role": "UNTOUCHED_TEST", "start": "2026-05-01", "end": "2026-07-31"},
        ],
        "strategy_entrypoint": "research.baseline:MovingAverageCross",
        "strategy_source_path": "research/baseline.py",
        "strategy_version": "baseline-grid-v1",
        "parameter_space": {"fast": [10, 20], "slow": [50, 100]},
        "evaluation_protocol": {
            "primary_metric": "TOTAL_RETURN",
            "optimization_direction": "MAXIMIZE",
            "tie_break_metrics": [
                {"metric": "MAXIMUM_DRAWDOWN", "direction": "MINIMIZE"}
            ],
            "success_thresholds": {"minimum_total_return": "0.00"},
            "warmup_observations": 100,
            "purge_observations": 1,
            "embargo_observations": 1,
            "maximum_untouched_test_evaluations": 1,
            "execution_policy_version": "synthetic-pilot-policy-v1",
            "execution_policy_sha256": "d" * 64,
            "selection_rule_version": "single-primary-metric-v1",
        },
        "entitlement_metadata": {
            "plan_name": "STOCKS_BASIC_FREE",
            "terms_uri": "https://massive.com/stocks",
            "terms_retrieved_at": (now - timedelta(minutes=1)).isoformat(),
            "terms_payload_sha256": "c" * 64,
            "asserted_request_limit_per_minute": 5,
            "asserted_incremental_cost_usd": "0.00",
        },
    }


def preregister(tmp_path):
    source = tmp_path / "research" / "baseline.py"
    source.parent.mkdir(exist_ok=True)
    source.write_text("class MovingAverageCross:\n    pass\n")
    ledger = HistoricalQuarantinePreregistrationLedger(
        tmp_path / "prereg.jsonl",
        repository_root=tmp_path,
        git_revision_resolver=lambda _: "a" * 40,
        worktree_clean_resolver=lambda _: True,
    )
    record = ledger.preregister(**preregistration_definition())
    return ledger, record


def store(tmp_path):
    ledger, plan = preregister(tmp_path)
    target = MassiveHistoricalQuarantineStore(
        tmp_path / "quarantine",
        preregistration_ledger=ledger,
        admitted_store_roots=[tmp_path / "admitted-source-blobs"],
    )
    return target, plan


def access_metadata():
    return ProviderAttemptMetadata(
        provider="Massive historical sample",
        attempts=1,
        retry_count=0,
        retried_status_codes=(),
        total_wait_seconds=0.0,
        elapsed_seconds=0.01,
        circuit_state="CLOSED",
    ).as_dict()


def response_headers_sha256():
    return hashlib.sha256(
        b'{"content-type":"application/json; charset=utf-8"}'
    ).hexdigest()


def payload(symbol="AAPL", marker="one"):
    return json.dumps(
        {
            "adjusted": False,
            "request_id": f"synthetic-{marker}",
            "results": [
                {"o": 100, "h": 102, "l": 99, "c": 101, "v": 10, "t": 1_754_995_200_000}
            ],
            "resultsCount": 1,
            "status": "OK",
            "ticker": symbol,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def capture(target, plan, *, raw=None, **overrides):
    requested = datetime.now(UTC)
    values = {
        "preregistration_id": plan["preregistration_id"],
        "symbol": "AAPL",
        "request_start": "2025-08-01",
        "request_end": "2025-08-31",
        "request_uri": "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/2025-08-01/2025-08-31",
        "request_query_canonical": "adjusted=false&limit=120&sort=asc",
        "requested_at": requested,
        "retrieved_at": requested,
        "response_status_code": 200,
        "response_headers_sha256": response_headers_sha256(),
        "payload": raw or payload(),
        "provider_access": access_metadata(),
    }
    values.update(overrides)
    return target.capture_response(**values)


def test_retains_exact_raw_bytes_owner_only_and_rehashes_every_read(tmp_path):
    target, plan = store(tmp_path)
    raw = payload()

    record = capture(target, plan, raw=raw)

    assert record["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert record["raw_response_bytes_retained"] is True
    assert record["quarantine_only"] is True
    assert all(record[name] is False for name in module.FIXED_FALSE)
    assert "content_evidence_id" not in record
    assert "admission_id" not in record
    blob = target.blob_directory / record["blob_relative_path"]
    assert blob.read_bytes() == raw
    assert blob.stat().st_mode & 0o777 == 0o400
    assert target.root.stat().st_mode & 0o777 == 0o700
    assert target.blob_directory.stat().st_mode & 0o777 == 0o700
    verified = target.read_verified(record["quarantine_capture_id"])
    assert type(verified) is QuarantinedHistoricalPayload
    assert verified.payload_bytes == raw
    assert verified.engine_input_ready is False


def test_quarantine_and_admitted_roots_must_be_disjoint(tmp_path):
    ledger, _ = preregister(tmp_path)
    with pytest.raises(ValueError, match="must be disjoint"):
        MassiveHistoricalQuarantineStore(
            tmp_path / "shared",
            preregistration_ledger=ledger,
            admitted_store_roots=[tmp_path / "shared"],
        )
    with pytest.raises(ValueError, match="must be disjoint"):
        MassiveHistoricalQuarantineStore(
            tmp_path / "parent" / "quarantine",
            preregistration_ledger=ledger,
            admitted_store_roots=[tmp_path / "parent"],
        )


def test_request_must_match_preregistered_basket_window_uri_and_chronology(tmp_path):
    target, plan = store(tmp_path)
    with pytest.raises(ValueError, match="outside the preregistered basket"):
        capture(target, plan, symbol="TSLA")
    with pytest.raises(ValueError, match="acquisition window"):
        capture(target, plan, request_end="2025-09-01")
    with pytest.raises(ValueError, match="exact preregistered request slice"):
        capture(
            target,
            plan,
            request_end="2025-08-30",
            request_uri=(
                "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/"
                "2025-08-01/2025-08-30"
            ),
            request_query_canonical="adjusted=false&limit=120&sort=asc",
        )
    with pytest.raises(ValueError, match="credential-free preregistration"):
        capture(
            target,
            plan,
            request_uri=(
                "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/"
                "2025-08-01/2025-08-31?apiKey=secret"
            ),
        )
    with pytest.raises(ValueError, match="request query"):
        capture(
            target,
            plan,
            request_query_canonical="adjusted=true&limit=120&sort=asc",
        )
    with pytest.raises(ValueError, match="HTTP 200"):
        capture(target, plan, response_status_code=500)
    with pytest.raises(ValueError, match="response_headers_sha256"):
        capture(target, plan, response_headers_sha256="not-a-digest")
    registered = datetime.fromisoformat(plan["registered_at"])
    with pytest.raises(ValueError, match="chronology"):
        capture(
            target,
            plan,
            requested_at=registered - timedelta(microseconds=1),
            retrieved_at=registered,
        )
    assert target.records() == []


def test_duplicate_slice_is_idempotent_only_for_identical_raw_payload(tmp_path):
    target, plan = store(tmp_path)
    first = capture(target, plan)

    assert capture(target, plan) == first
    with pytest.raises(LedgerIntegrityError, match="already captured"):
        capture(target, plan, raw=payload(marker="changed"))


@pytest.mark.parametrize(
    "change",
    [
        {"source_bytes_authenticated": True},
        {"dataset_admission_eligible": True},
        {"replay_data_attestation_issued": True},
        {"synthetic_pilot_attestation_issued": True},
        {"engine_input_ready": True},
        {"untouched_test_opened": True},
        {"live_trading_enabled": True},
    ],
)
def test_rehashed_metadata_tampering_cannot_expand_quarantine_authority(tmp_path, change):
    target, plan = store(tmp_path)
    record = capture(target, plan)
    record.update(change)
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.ledger_path.chmod(0o600)
    target.ledger_path.write_text(json.dumps(record, separators=(",", ":")) + "\n")

    with pytest.raises(LedgerIntegrityError, match="violates its boundary"):
        target.verify()


def test_changed_missing_or_symlinked_blob_fails_verification(tmp_path):
    target, plan = store(tmp_path)
    record = capture(target, plan)
    blob = target.blob_directory / record["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"changed")
    with pytest.raises(LedgerIntegrityError):
        target.verify()

    blob.unlink()
    outside = tmp_path / "outside.json"
    outside.write_bytes(payload())
    blob.symlink_to(outside)
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_unreferenced_blob_fails_closed(tmp_path):
    target, plan = store(tmp_path)
    capture(target, plan)
    orphan_hash = hashlib.sha256(b"orphan").hexdigest()
    shard = target.blob_directory / "sha256" / orphan_hash[:2]
    shard.mkdir(mode=0o700)
    orphan = shard / f"{orphan_hash}.blob"
    orphan.write_bytes(b"orphan")
    orphan.chmod(0o400)

    with pytest.raises(LedgerIntegrityError, match="unreferenced blobs"):
        target.verify()


def test_failed_blob_write_is_removed_for_safe_retry(tmp_path, monkeypatch):
    target, plan = store(tmp_path)
    raw = payload()
    expected_hash = hashlib.sha256(raw).hexdigest()
    original_write_all = module._write_all

    def fail_write(descriptor, value):
        module.os.write(descriptor, value[:3])
        raise OSError("synthetic short write")

    monkeypatch.setattr(module, "_write_all", fail_write)
    with pytest.raises(OSError, match="synthetic short write"):
        capture(target, plan, raw=raw)
    monkeypatch.setattr(module, "_write_all", original_write_all)

    assert not (
        target.blob_directory
        / "sha256"
        / expected_hash[:2]
        / f"{expected_hash}.blob"
    ).exists()
    assert target.records() == []

def test_planned_slices_cover_exact_year_for_all_three_tickers(tmp_path):
    _, plan = preregister(tmp_path)
    slices = planned_request_slices(plan)

    assert len(slices) == 36
    assert slices[0] == ("AAPL", "TRAIN", "2025-08-01", "2025-08-31")
    assert slices[6] == ("AAPL", "TRAIN", "2026-02-03", "2026-02-28")
    assert slices[7] == ("AAPL", "VALIDATION", "2026-03-01", "2026-03-31")
    assert slices[9] == ("AAPL", "UNTOUCHED_TEST", "2026-05-01", "2026-05-31")
    assert slices[11] == ("AAPL", "UNTOUCHED_TEST", "2026-07-02", "2026-07-31")
    assert slices[12][0] == "MSFT"
    assert slices[24][0] == "SPY"


def test_untouched_test_bytes_are_retained_but_cannot_be_opened(tmp_path):
    target, plan = store(tmp_path)
    requested = datetime.now(UTC)
    record = capture(
        target,
        plan,
        request_start="2026-05-01",
        request_end="2026-05-31",
        request_uri=(
            "https://api.massive.com/v2/aggs/ticker/AAPL/range/1/day/"
            "2026-05-01/2026-05-31"
        ),
        requested_at=requested,
        retrieved_at=requested,
    )

    assert record["split_role"] == "UNTOUCHED_TEST"
    assert target.verify() == [record]
    with pytest.raises(ValueError, match="cannot be opened"):
        target.read_verified(record["quarantine_capture_id"])


class FakeClient:
    def __init__(self):
        self.calls = []

    def fetch_daily_bars(self, *, symbol, start, end):
        self.calls.append((symbol, start, end))
        now = datetime.now(UTC).isoformat()
        return MassiveFetchedPayload(
            payload_bytes=payload(symbol, marker=f"{start}-{end}"),
            request_uri=(
                f"https://api.massive.com/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}"
            ),
            request_query_canonical="adjusted=false&limit=120&sort=asc",
            requested_at=now,
            retrieved_at=now,
            response_status_code=200,
            response_headers_sha256=response_headers_sha256(),
            media_type="application/json",
            provider_access=access_metadata(),
        )


def test_fetcher_uses_only_missing_slices_and_never_calls_a_broker(tmp_path):
    target, plan = store(tmp_path)
    client = FakeClient()
    fetcher = MassiveHistoricalQuarantineFetcher(store=target, client=client)

    result = fetcher.fetch(plan["preregistration_id"])

    assert result["expected_request_count"] == 36
    assert result["captured_request_count"] == 36
    assert result["missing_request_count"] == 0
    assert result["complete"] is True
    assert result["dataset_admitted"] is False
    assert result["engine_input_ready"] is False
    assert result["replay_executed"] is False
    assert len(client.calls) == 36
    fetcher.fetch(plan["preregistration_id"])
    assert len(client.calls) == 36


def test_quarantined_payload_is_not_a_vectorbt_bar_or_attestation(tmp_path):
    target, plan = store(tmp_path)
    record = capture(target, plan)
    quarantined = target.read_verified(record["quarantine_capture_id"])

    with pytest.raises(ValueError, match="only SyntheticPilotBar"):
        VectorBTPilotAdapter._require_bar(quarantined)
    assert quarantined.replay_data_attestation_issued is False
    assert quarantined.synthetic_pilot_attestation_issued is False


def test_quarantine_module_has_no_admission_guardrail_vectorbt_or_broker_import():
    tree = ast.parse(inspect.getsource(module))
    imports = {
        imported
        for node in ast.walk(tree)
        for imported in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }

    assert not any("replay_dataset_admission" in name for name in imports)
    assert not any("guardrailed_backtest" in name for name in imports)
    assert not any("vectorbt" in name for name in imports)
    assert not any("broker" in name for name in imports)
    assert module.MassiveHistoricalSampleClient.ACCESS_POLICY.minimum_interval_seconds == 12.0

    preregistration_tree = ast.parse(inspect.getsource(prereg_module))
    preregistration_imports = {
        imported
        for node in ast.walk(preregistration_tree)
        for imported in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    assert not any("guardrailed_backtest" in name for name in preregistration_imports)


def test_fetch_key_file_must_be_owner_only_and_not_a_symlink(tmp_path, monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    key = tmp_path / "massive-key.txt"
    key.write_text("synthetic-test-key\n", encoding="ascii")
    key.chmod(0o600)

    assert fetch_script._api_key(key) == "synthetic-test-key"

    key.chmod(0o644)
    with pytest.raises(ValueError, match="owner-only"):
        fetch_script._api_key(key)
    key.chmod(0o600)
    link = tmp_path / "key-link.txt"
    link.symlink_to(key)
    with pytest.raises(OSError):
        fetch_script._api_key(link)


def test_fetch_environment_key_is_canonical(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "synthetic-test-key")
    assert fetch_script._api_key(None) == "synthetic-test-key"

    monkeypatch.setenv("MASSIVE_API_KEY", " synthetic-test-key")
    with pytest.raises(ValueError, match="invalid format"):
        fetch_script._api_key(None)
