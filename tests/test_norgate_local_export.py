from __future__ import annotations

import ast
from datetime import date, datetime, timezone
import hashlib
import inspect
import json
import os
import pickle
from pathlib import Path
import subprocess
import sys
import textwrap
from types import SimpleNamespace

import pandas as pd
import pytest

import core.orchestration.norgate_local_export as module
import scripts.assemble_norgate_local_capture as capture_module
import scripts.compare_norgate_local_exports as compare_module
import scripts.export_norgate_local_sample as export_module
import scripts.export_norgate_local_universe as universe_export_module
import scripts.ingest_norgate_local_export as ingest_module
import scripts.ingest_norgate_local_universe as universe_ingest_module
from core.orchestration.norgate_local_export import (
    NorgateLocalExportAdapter,
    NorgateLocalExportSource,
    NorgateLocalStagingBatch,
    NorgateLocalUniverseCatalogEvidence,
    NorgateShardedCaptureManifest,
    assemble_norgate_sharded_capture_manifest,
    compare_norgate_same_vintage_exports,
    parse_norgate_local_export,
    parse_norgate_local_universe_catalog,
    stage_norgate_local_universe_catalog,
)
from scripts.export_norgate_local_sample import build_export, write_verified_export
from scripts.export_norgate_local_universe import build_universe_catalog


EXPORTED_AT = "2026-08-19T10:00:00.000000+00:00"
RETRIEVED_AT = "2026-08-19T10:05:00+00:00"


def export_payload(*, rows: list[dict] | None = None, **changes) -> bytes:
    body = rows if rows is not None else [
        {
            "asset_id": 101,
            "requested_symbol": "AAPL",
            "symbol": "AAPL",
            "security_name": "Apple Inc",
            "session_date": "2026-08-17",
            "open": 100.0,
            "high": 103.0,
            "low": 99.0,
            "close": 102.0,
            "volume": 1_000_000.0,
            "unadjusted_close": 102.0,
            "dividend": 0.0,
            "sp500_constituent": True,
        }
    ]
    requested_symbols = changes.pop("requested_symbols", ["AAPL"])
    payload = {
        "schema_version": "1.0",
        "export_contract": "NORGATE_LOCAL_EXPORT_V1",
        "provider_id": "NORGATE",
        "provider_dataset_id": "NORGATE_US_STOCKS_PLATINUM_LOCAL_V1",
        "norgatedata_package_version": "1.0.77",
        "database_name": "US Equities",
        "database_update_at": "2026-08-19T09:55:00.000000+00:00",
        "universe_selection_basis": "OPERATOR_SUPPLIED_SYMBOLS_UNQUALIFIED",
        "requested_symbols": requested_symbols,
        "requested_symbols_sha256": hashlib.sha256(
            json.dumps(requested_symbols, separators=(",", ":")).encode("utf-8")
        ).hexdigest(),
        "reused_symbols": [],
        "license_restricted_provider_data": True,
        "source_code_repository_storage_allowed": False,
        "exported_at": EXPORTED_AT,
        "requested_start": "2026-08-01",
        "requested_end": "2026-08-18",
        "frequency": "DAILY",
        "stock_price_adjustment": "NONE",
        "padding": "NONE",
        "membership_dataset": "S&P 500",
        "rows": body,
        **changes,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def source(payload: bytes | None = None) -> NorgateLocalExportSource:
    return NorgateLocalExportSource(
        retrieved_at=RETRIEVED_AT,
        payload_bytes=payload or export_payload(),
    )


def normalize(payload: bytes | None = None, *, decision_at: str = RETRIEVED_AT):
    return NorgateLocalExportAdapter().normalize(
        source=source(payload),
        decision_at=decision_at,
    )


def shard_payload(
    requested_symbol: str,
    *,
    asset_id: int,
    resolved_symbol: str | None = None,
    security_name: str | None = None,
    exported_at: str = EXPORTED_AT,
    **changes,
) -> bytes:
    symbol = resolved_symbol or requested_symbol
    row = {
        "asset_id": asset_id,
        "requested_symbol": requested_symbol,
        "symbol": symbol,
        "security_name": security_name
        or {
            "AAPL": "Apple Inc",
            "MSFT": "Microsoft Corp",
        }.get(requested_symbol, f"{requested_symbol} Incorporated"),
        "session_date": "2026-08-17",
        "open": 100.0,
        "high": 103.0,
        "low": 99.0,
        "close": 102.0,
        "volume": 1_000_000.0,
        "unadjusted_close": 102.0,
        "dividend": 0.0,
        "sp500_constituent": True,
    }
    return export_payload(
        rows=[row],
        requested_symbols=[requested_symbol],
        exported_at=exported_at,
        **changes,
    )


def universe_catalog_payload(
    *,
    entries: list[dict] | None = None,
    **changes,
) -> bytes:
    values = entries if entries is not None else [
        {"asset_id": 101, "symbol": "AAPL", "security_name": "Apple Inc"},
        {"asset_id": 202, "symbol": "MSFT", "security_name": "Microsoft Corp"},
    ]
    assets_by_symbol: dict[str, set[int]] = {}
    for entry in values:
        assets_by_symbol.setdefault(entry["symbol"], set()).add(entry["asset_id"])
    reused_symbols = sorted(
        symbol for symbol, asset_ids in assets_by_symbol.items() if len(asset_ids) > 1
    )
    entries_sha256 = hashlib.sha256(
        json.dumps(
            values,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    payload = {
        "schema_version": "1.0",
        "export_contract": "NORGATE_LOCAL_UNIVERSE_CATALOG_V1",
        "provider_id": "NORGATE",
        "provider_dataset_id": "NORGATE_US_STOCKS_PLATINUM_LOCAL_V1",
        "norgatedata_package_version": "1.0.77",
        "database_name": "US Equities",
        "database_update_at": "2026-08-19T09:55:00.000000+00:00",
        "watchlist_name": "S&P 500 Current & Past",
        "watchlist_semantics_basis": (
            "PROVIDER_NAMED_CURRENT_AND_PAST_WATCHLIST_UNQUALIFIED"
        ),
        "exported_at": EXPORTED_AT,
        "license_restricted_provider_data": True,
        "source_code_repository_storage_allowed": False,
        "entry_count": len(values),
        "entries_sha256": entries_sha256,
        "reused_symbols": reused_symbols,
        "entries": values,
        **changes,
    }
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()


def universe_catalog_evidence(
    *, entries: list[dict] | None = None, **changes
) -> NorgateLocalUniverseCatalogEvidence:
    return stage_norgate_local_universe_catalog(
        NorgateLocalExportSource(
            retrieved_at=RETRIEVED_AT,
            payload_bytes=universe_catalog_payload(entries=entries, **changes),
        )
    )


def test_provider_shaped_export_stages_bars_and_membership_fail_closed():
    original = export_payload()

    result = normalize(original)

    summary = result.as_dict()
    assert summary["provider_id"] == "NORGATE"
    assert summary["provider_dataset_id"] == "NORGATE_US_STOCKS_PLATINUM_LOCAL_V1"
    assert summary["roles"] == [
        "RAW_DAILY_SESSION_BARS",
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE",
    ]
    assert summary["source_evidence_kinds"] == [
        "RAW_DAILY_SESSION_BARS",
        "CURRENT_VINTAGE_UNQUALIFIED_UNIVERSE_MEMBERSHIP",
    ]
    assert summary["membership_evidence_admitted"] is False
    assert summary["record_counts"] == {
        "RAW_DAILY_SESSION_BARS": 1,
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE": 1,
    }
    assert summary["source_payload_sha256"] == hashlib.sha256(original).hexdigest()
    for flag in module.SAFETY_FLAG_NAMES:
        assert summary[flag] is False

    bar = result.observations_by_role["RAW_DAILY_SESSION_BARS"][0]
    member = result.observations_by_role[
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"
    ][0]
    assert bar["effective_at"] == "2026-08-17T23:59:59.999999+00:00"
    assert bar["available_at"] == "2026-08-19T10:05:00.000000+00:00"
    assert bar["retrieved_at"] == bar["available_at"]
    assert bar["observation_cutoff_at"] == bar["available_at"]
    assert bar["payload"]["permanent_security_id"] == "NORGATE-101"
    assert bar["payload"]["norgatedata_package_version"] == "1.0.77"
    assert bar["payload"]["database_name"] == "US Equities"
    assert bar["payload"]["license_restricted_provider_data"] is True
    assert bar["payload"]["source_code_repository_storage_allowed"] is False
    assert bar["payload"]["stock_price_adjustment"] == "NONE"
    assert bar["payload"]["padding"] == "NONE"
    assert bar["payload"]["effective_at_basis"] == (
        "SESSION_DATE_END_UTC_UNQUALIFIED"
    )
    assert bar["payload"]["historical_availability_basis"] == (
        "CURRENT_LOCAL_RECEIPT_ONLY_UNQUALIFIED"
    )
    assert member["payload"]["is_constituent"] is True
    assert member["payload"]["supplemental_evidence_only"] is True
    assert member["payload"]["source_evidence_kind"] == (
        "CURRENT_VINTAGE_UNQUALIFIED_UNIVERSE_MEMBERSHIP"
    )
    assert member["payload"]["ticker_history_basis"] == (
        "STATIC_EXPORT_SYMBOL_UNQUALIFIED"
    )


def test_historical_dates_are_not_invented_as_historical_availability():
    with pytest.raises(ValueError, match="available_at is after the decision"):
        normalize(decision_at="2026-08-19T10:04:59.999999+00:00")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"stock_price_adjustment": "TOTALRETURN"}, "adjustment"),
        ({"padding": "ALLMARKETDAYS"}, "padding"),
        ({"membership_dataset": "Nasdaq 100"}, "membership_dataset"),
        ({"provider_id": "OTHER"}, "provider_id"),
    ],
)
def test_unsupported_export_contract_assumptions_fail_closed(changes, message):
    with pytest.raises(ValueError, match=message):
        parse_norgate_local_export(export_payload(**changes))


def test_duplicate_asset_date_and_static_identity_drift_fail_closed():
    first = json.loads(export_payload())["rows"][0]
    with pytest.raises(ValueError, match="repeat an asset/date"):
        parse_norgate_local_export(export_payload(rows=[first, dict(first)]))

    second = dict(first, session_date="2026-08-18", symbol="AAPL-2025")
    with pytest.raises(ValueError, match="cannot change identity"):
        parse_norgate_local_export(export_payload(rows=[first, second]))


def test_adjustment_identity_ohlc_and_completed_session_checks_fail_closed():
    row = json.loads(export_payload())["rows"][0]
    with pytest.raises(ValueError, match="NONE-adjusted close"):
        parse_norgate_local_export(
            export_payload(rows=[dict(row, unadjusted_close=101.0)])
        )
    with pytest.raises(ValueError, match="OHLC values"):
        parse_norgate_local_export(export_payload(rows=[dict(row, high=98.0)]))
    with pytest.raises(ValueError, match="completed prior sessions"):
        normalize(
            export_payload(
                rows=[dict(row, session_date="2026-08-19")],
                requested_end="2026-08-19",
            )
        )


def test_export_timestamp_cannot_postdate_receipt():
    with pytest.raises(ValueError, match="cannot postdate local retrieval"):
        normalize(export_payload(exported_at="2026-08-19T10:06:00+00:00"))


def test_database_vintage_symbol_list_hash_and_order_fail_closed():
    with pytest.raises(ValueError, match="database update cannot postdate export"):
        parse_norgate_local_export(
            export_payload(database_update_at="2026-08-19T10:01:00+00:00")
        )
    with pytest.raises(ValueError, match="symbol hash"):
        parse_norgate_local_export(export_payload(requested_symbols_sha256="0" * 64))
    first = json.loads(export_payload())["rows"][0]
    second = dict(
        first,
        asset_id=99,
        requested_symbol="MSFT",
        symbol="MSFT",
        security_name="Microsoft Corp",
    )
    with pytest.raises(ValueError, match="ordered by asset_id"):
        parse_norgate_local_export(
            export_payload(rows=[first, second], requested_symbols=["AAPL", "MSFT"])
        )


def test_naive_timestamps_and_control_characters_fail_closed():
    with pytest.raises(ValueError, match="timezone-aware"):
        NorgateLocalExportSource(
            retrieved_at="2026-08-19T10:05:00",
            payload_bytes=export_payload(),
        )
    row = json.loads(export_payload())["rows"][0]
    with pytest.raises(ValueError, match="canonical text"):
        parse_norgate_local_export(
            export_payload(rows=[dict(row, security_name="Apple\nInc")])
        )


def test_strict_json_size_and_record_boundaries():
    duplicate = export_payload().replace(
        b'"provider_id":"NORGATE",',
        b'"provider_id":"NORGATE","provider_id":"NORGATE",',
    )
    with pytest.raises(ValueError, match="strict JSON"):
        parse_norgate_local_export(duplicate)
    with pytest.raises(ValueError, match="bounded nonempty bytes"):
        source(b"x" * (module.MAX_SOURCE_BYTES + 1))
    with pytest.raises(ValueError, match="bounded nonempty list"):
        parse_norgate_local_export(export_payload(rows=[]))


def test_staging_batch_authority_and_false_safety_flags_cannot_be_forged():
    required = {
        "decision_at": RETRIEVED_AT,
        "retrieved_at": RETRIEVED_AT,
        "observations_by_role": {},
        "source_payload_sha256": "0" * 64,
        "staging_sha256": "1" * 64,
    }
    with pytest.raises(PermissionError):
        NorgateLocalStagingBatch(**required)
    with pytest.raises(ValueError, match="safety flags were altered"):
        NorgateLocalStagingBatch(
            **required,
            performance_use_allowed=True,
            _authority=module._STAGING_AUTHORITY,
        )


def test_staged_payload_is_deeply_immutable_and_batch_cannot_be_pickled():
    result = normalize()
    payload = result.observations_by_role["RAW_DAILY_SESSION_BARS"][0]["payload"]
    with pytest.raises(TypeError):
        payload["close"] = 999.0
    with pytest.raises(TypeError, match="not pickleable"):
        pickle.dumps(result)


def test_false_membership_and_missing_membership_fail_closed_as_expected():
    row = json.loads(export_payload())["rows"][0]
    result = normalize(export_payload(rows=[dict(row, sp500_constituent=False)]))
    member = result.observations_by_role[
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"
    ][0]
    assert member["payload"]["is_constituent"] is False


class FakeNorgate:
    __version__ = "1.0.77"
    StockPriceAdjustmentType = SimpleNamespace(NONE="NONE")
    PaddingType = SimpleNamespace(NONE="NONE")

    def __init__(self) -> None:
        self.price_calls: list[tuple] = []
        self.membership_calls: list[tuple] = []

    def assetid(self, symbol):
        return 101

    def symbol(self, asset_id):
        return "AAPL"

    def security_name(self, symbol):
        return "Apple Inc"

    def last_database_update_time(self, database_name):
        return datetime(2026, 8, 19, 9, 55, tzinfo=timezone.utc)

    def price_timeseries(self, asset_id, **kwargs):
        self.price_calls.append((asset_id, kwargs))
        return pd.DataFrame(
            {
                "Open": [100.0],
                "High": [103.0],
                "Low": [99.0],
                "Close": [102.0],
                "Volume": [1_000_000],
                "Unadjusted Close": [102.0],
                "Dividend": [0.0],
            },
            index=pd.to_datetime(["2026-08-17"]),
        )

    def index_constituent_timeseries(self, asset_id, index_name, **kwargs):
        self.membership_calls.append((asset_id, index_name, kwargs))
        result = kwargs["pandas_dataframe"].copy()
        result["Index Constituent"] = [1]
        return result

    def watchlist(self, name):
        assert name == "S&P 500 Current & Past"
        return [
            {"symbol": "MSFT", "assetid": 202, "securityname": "Microsoft Corp"},
            {"symbol": "AAPL", "assetid": 101, "securityname": "Apple Inc"},
        ]


class MissingMembershipNorgate(FakeNorgate):
    def index_constituent_timeseries(self, asset_id, index_name, **kwargs):
        result = kwargs["pandas_dataframe"].copy()
        result["Index Constituent"] = [float("nan")]
        return result


class RenamedSymbolNorgate(FakeNorgate):
    def symbol(self, asset_id):
        return "META"


class ReusedSymbolNorgate(FakeNorgate):
    def assetid(self, symbol):
        return {"OLD": 101, "NEW": 202}[symbol]

    def symbol(self, asset_id):
        return "XYZ"

    def security_name(self, asset_id):
        return {101: "Old Issuer", 202: "New Issuer"}[asset_id]


def test_windows_export_builder_uses_stable_id_unadjusted_unpadded_local_calls():
    provider = FakeNorgate()

    payload = build_export(
        norgatedata=provider,
        symbols=["AAPL"],
        database_name="US Equities",
        start=date(2026, 8, 1),
        end=date(2026, 8, 18),
        exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
    )

    root = json.loads(payload)
    assert root["rows"][0]["asset_id"] == 101
    assert root["rows"][0]["requested_symbol"] == "AAPL"
    assert root["database_name"] == "US Equities"
    assert root["database_update_at"] == "2026-08-19T09:55:00.000000+00:00"
    assert root["rows"][0]["sp500_constituent"] is True
    assert provider.price_calls == [
        (
            101,
            {
                "stock_price_adjustment_setting": "NONE",
                "padding_setting": "NONE",
                "start_date": date(2026, 8, 1),
                "end_date": date(2026, 8, 18),
                "timeseriesformat": "pandas-dataframe",
                "interval": "D",
            },
        )
    ]
    assert len(provider.membership_calls) == 1
    asset_id, index_name, kwargs = provider.membership_calls[0]
    assert asset_id == 101
    assert index_name == "S&P 500"
    assert kwargs["padding_setting"] == "NONE"
    assert kwargs["timeseriesformat"] == "pandas-dataframe"
    assert list(kwargs["pandas_dataframe"].columns) == [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
        "Unadjusted Close",
        "Dividend",
    ]


def test_windows_import_guard_is_available_but_file_locking_fails_closed():
    missing = object()
    saved = sys.modules.pop("fcntl", missing)
    try:
        export_module._install_windows_fcntl_guard("Windows")
        guard = sys.modules["fcntl"]
        assert guard.LOCK_SH == 1
        assert guard.LOCK_EX == 2
        assert guard.LOCK_NB == 4
        assert guard.LOCK_UN == 8
        with pytest.raises(OSError, match="unavailable in the Windows extraction VM"):
            guard.flock(None, guard.LOCK_EX)
        for unsupported in ("lockf", "fcntl", "ioctl", "F_SETLK"):
            with pytest.raises(
                OSError, match="unavailable in the Windows extraction VM"
            ):
                getattr(guard, unsupported)
        assert not hasattr(guard, "__file__")
        assert inspect.getmodule(inspect.currentframe()) is sys.modules[__name__]
    finally:
        sys.modules.pop("fcntl", None)
        if saved is not missing:
            sys.modules["fcntl"] = saved


def test_windows_script_imports_when_posix_fcntl_is_unavailable():
    probe = textwrap.dedent(
        """
        import importlib.abc
        import importlib.util
        import platform
        import sys

        class BlockFcntl(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname == "fcntl":
                    raise ModuleNotFoundError("blocked fcntl")
                return None

        sys.modules.pop("fcntl", None)
        sys.meta_path.insert(0, BlockFcntl())
        platform.system = lambda: "Windows"
        spec = importlib.util.spec_from_file_location(
            "windows_import_probe",
            "scripts/export_norgate_local_sample.py",
        )
        if spec is None or spec.loader is None:
            raise AssertionError("could not load Windows export script")
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        module._norgate_contract()
        guard = sys.modules["fcntl"]
        try:
            guard.flock(None, guard.LOCK_EX)
        except OSError:
            pass
        else:
            raise AssertionError("Windows file locking did not fail closed")
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={
            key: os.environ[key]
            for key in (
                "HOME",
                "NUMBA_CACHE_DIR",
                "PATH",
                "SYSTEMROOT",
                "TEMP",
                "TMP",
                "TMPDIR",
                "WINDIR",
            )
            if key in os.environ
        },
    )
    assert completed.returncode == 0, completed.stderr


def test_export_records_requested_to_resolved_symbol_drift_and_missing_membership():
    payload = build_export(
        norgatedata=RenamedSymbolNorgate(),
        symbols=["FB"],
        database_name="US Equities",
        start=date(2026, 8, 1),
        end=date(2026, 8, 18),
        exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
    )
    row = json.loads(payload)["rows"][0]
    assert row["requested_symbol"] == "FB"
    assert row["symbol"] == "META"

    with pytest.raises(ValueError, match="membership value must be boolean"):
        build_export(
            norgatedata=MissingMembershipNorgate(),
            symbols=["AAPL"],
            database_name="US Equities",
            start=date(2026, 8, 1),
            end=date(2026, 8, 18),
            exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        )


def test_verified_export_write_is_exact_and_never_overwrites(tmp_path: Path):
    payload = export_payload()
    output = tmp_path / "norgate-export.json"
    digest = write_verified_export(output, payload)

    assert output.read_bytes() == payload
    assert digest == hashlib.sha256(payload).hexdigest()
    with pytest.raises(FileExistsError):
        write_verified_export(output, payload)


def test_export_records_ticker_reuse_across_permanent_asset_ids():
    payload = build_export(
        norgatedata=ReusedSymbolNorgate(),
        symbols=["OLD", "NEW"],
        database_name="US Equities",
        start=date(2026, 8, 1),
        end=date(2026, 8, 18),
        exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
    )

    root = json.loads(payload)
    assert root["reused_symbols"] == ["XYZ"]
    assert {row["asset_id"] for row in root["rows"]} == {101, 202}


def test_local_ingest_cli_stages_exact_synthetic_file(tmp_path: Path):
    sample = tmp_path / "norgate-export.json"
    sample.write_bytes(export_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ingest_norgate_local_export.py",
            "--export-file",
            str(sample),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["record_counts"]["RAW_DAILY_SESSION_BARS"] == 1
    assert summary["record_counts"][
        "POINT_IN_TIME_SUPPLEMENTAL_PROVIDER_EVIDENCE"
    ] == 1
    assert summary["performance_use_allowed"] is False
    assert summary["validation_accessed"] is False
    assert summary["test_accessed"] is False
    assert summary["broker_connection_allowed"] is False


def test_local_universe_builder_and_stager_preserve_stable_identity_without_authority():
    provider = FakeNorgate()
    payload = build_universe_catalog(
        norgatedata=provider,
        database_name="US Equities",
        exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
    )
    root = json.loads(payload)
    assert [entry["asset_id"] for entry in root["entries"]] == [101, 202]
    assert root["watchlist_name"] == "S&P 500 Current & Past"
    assert root["watchlist_semantics_basis"].endswith("_UNQUALIFIED")
    assert root["license_restricted_provider_data"] is True
    assert root["source_code_repository_storage_allowed"] is False

    entries = parse_norgate_local_universe_catalog(payload)
    assert tuple(entry["symbol"] for entry in entries) == ("AAPL", "MSFT")
    evidence = stage_norgate_local_universe_catalog(
        NorgateLocalExportSource(retrieved_at=RETRIEVED_AT, payload_bytes=payload)
    )
    summary = evidence.as_dict()
    assert summary["catalog_scope"] == (
        "CURRENT_DATABASE_VINTAGE_PROVIDER_NAMED_WATCHLIST_UNQUALIFIED"
    )
    assert summary["entry_count"] == 2
    assert summary["asset_id_min"] == 101
    assert summary["asset_id_max"] == 202
    assert summary["source_payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert summary["entries_sha256"] == root["entries_sha256"]
    assert summary["catalog_evidence_sha256"] == (
        "70a196719428ed0ff213fa43a8cd2733f4c0e0df16bedd663518204d2f0a1e17"
    )
    assert summary["license_restricted_provider_data"] is True
    assert summary["source_code_repository_storage_allowed"] is False
    for flag in module.UNIVERSE_CATALOG_SAFETY_FLAG_NAMES:
        assert summary[flag] is False
    with pytest.raises(TypeError):
        evidence.entries[0]["symbol"] = "CHANGED"
    with pytest.raises(TypeError, match="not pickleable"):
        pickle.dumps(evidence)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            universe_catalog_payload(watchlist_name="S&P 500"),
            "watchlist_name",
        ),
        (
            universe_catalog_payload(database_name="Different Equities"),
            "database_name",
        ),
        (
            universe_catalog_payload(entries_sha256="0" * 64),
            "entry hash",
        ),
        (
            universe_catalog_payload(
                entries=[
                    {"asset_id": 202, "symbol": "MSFT", "security_name": "Microsoft"},
                    {"asset_id": 101, "symbol": "AAPL", "security_name": "Apple"},
                ]
            ),
            "ordered unique asset IDs",
        ),
        (
            universe_catalog_payload(
                source_code_repository_storage_allowed=True
            ),
            "source control",
        ),
        (
            universe_catalog_payload(entry_count=True),
            "bounded counted list",
        ),
        (
            universe_catalog_payload(entries=[]),
            "bounded counted list",
        ),
        (
            universe_catalog_payload(unexpected_field="unsupported"),
            "top-level fields",
        ),
        (
            universe_catalog_payload(
                database_update_at="2026-08-19T10:00:01.000000+00:00"
            ),
            "cannot postdate",
        ),
    ],
)
def test_local_universe_parser_rejects_semantic_and_integrity_drift(
    payload, message
):
    with pytest.raises(ValueError, match=message):
        parse_norgate_local_universe_catalog(payload)


def test_local_universe_builder_rejects_duplicate_asset_ids_and_bad_clock():
    provider = FakeNorgate()
    provider.watchlist = lambda _name: [
        {"symbol": "AAPL", "assetid": 101, "securityname": "Apple Inc"},
        {"symbol": "AAPL2", "assetid": 101, "securityname": "Apple Inc"},
    ]
    with pytest.raises(ValueError, match="repeats a stable assetid"):
        build_universe_catalog(
            norgatedata=provider,
            database_name="US Equities",
            exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        build_universe_catalog(
            norgatedata=FakeNorgate(),
            database_name="US Equities",
            exported_at=datetime(2026, 8, 19, 10),
        )
    with pytest.raises(ValueError, match="database_name must be US Equities"):
        build_universe_catalog(
            norgatedata=FakeNorgate(),
            database_name="Different Equities",
            exported_at=datetime(2026, 8, 19, 10, tzinfo=timezone.utc),
        )


def test_local_universe_catalog_surfaces_cross_asset_symbol_reuse():
    entries = [
        {"asset_id": 101, "symbol": "XYZ", "security_name": "Old Issuer"},
        {"asset_id": 202, "symbol": "XYZ", "security_name": "New Issuer"},
    ]
    evidence = stage_norgate_local_universe_catalog(
        NorgateLocalExportSource(
            retrieved_at=RETRIEVED_AT,
            payload_bytes=universe_catalog_payload(entries=entries),
        )
    )

    assert evidence.reused_symbols == ("XYZ",)
    assert evidence.as_dict()["reused_symbol_count"] == 1
    assert evidence.historical_ticker_history_qualified is False
    assert evidence.security_master_admission_allowed is False


def test_local_universe_catalog_rejects_export_after_local_receipt():
    with pytest.raises(ValueError, match="cannot postdate local retrieval"):
        stage_norgate_local_universe_catalog(
            NorgateLocalExportSource(
                retrieved_at="2026-08-19T09:59:59.000000+00:00",
                payload_bytes=universe_catalog_payload(),
            )
        )


def test_local_universe_catalog_authority_flags_and_license_cannot_be_forged():
    required = {
        "retrieved_at": RETRIEVED_AT,
        "receipt_timestamp_basis": "CALLER_SUPPLIED_UNQUALIFIED",
        "exported_at": EXPORTED_AT,
        "database_name": "US Equities",
        "database_update_at": "2026-08-19T09:55:00.000000+00:00",
        "norgatedata_package_version": "1.0.77",
        "entries": ({"asset_id": 101, "symbol": "AAPL", "security_name": "Apple"},),
        "source_payload_sha256": "0" * 64,
        "entries_sha256": "1" * 64,
        "catalog_evidence_sha256": "2" * 64,
        "reused_symbols": (),
    }
    with pytest.raises(PermissionError):
        NorgateLocalUniverseCatalogEvidence(**required)
    with pytest.raises(ValueError, match="safety flags were altered"):
        NorgateLocalUniverseCatalogEvidence(
            **required,
            security_master_admission_allowed=True,
            _authority=module._UNIVERSE_CATALOG_AUTHORITY,
        )
    with pytest.raises(ValueError, match="license markings were altered"):
        NorgateLocalUniverseCatalogEvidence(
            **required,
            source_code_repository_storage_allowed=True,
            _authority=module._UNIVERSE_CATALOG_AUTHORITY,
        )


def test_local_universe_ingest_cli_stages_summary_only(tmp_path: Path):
    catalog = tmp_path / "norgate-universe.json"
    catalog.write_bytes(universe_catalog_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ingest_norgate_local_universe.py",
            "--catalog-file",
            str(catalog),
            "--expected-source-sha256",
            hashlib.sha256(catalog.read_bytes()).hexdigest(),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["entry_count"] == 2
    assert "entries" not in summary
    assert "reused_symbols" not in summary
    assert summary["reused_symbol_count"] == 0
    assert summary["provider_watchlist_semantics_qualified"] is False
    assert summary["provider_watchlist_completeness_proven"] is False
    assert summary["security_master_admission_allowed"] is False
    assert summary["performance_use_allowed"] is False
    assert summary["validation_accessed"] is False
    assert summary["test_accessed"] is False


def test_local_universe_ingest_cli_rejects_source_hash_mismatch(tmp_path: Path):
    catalog = tmp_path / "norgate-universe.json"
    catalog.write_bytes(universe_catalog_payload())

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/ingest_norgate_local_universe.py",
            "--catalog-file",
            str(catalog),
            "--expected-source-sha256",
            "0" * 64,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )

    assert completed.returncode == 1
    assert completed.stdout == ""
    assert "does not match expected source SHA-256" in completed.stderr


def test_local_universe_scripts_reject_provider_catalog_paths_inside_repository(
    tmp_path: Path, monkeypatch
):
    repository = tmp_path / "repository"
    repository.mkdir()
    provider_file = repository / "norgate-quarantine-catalog.json"
    monkeypatch.setattr(universe_export_module, "ROOT", repository)
    monkeypatch.setattr(universe_ingest_module, "ROOT", repository)
    monkeypatch.setattr(capture_module, "ROOT", repository)

    with pytest.raises(ValueError, match="written outside the repository"):
        universe_export_module._provider_file_outside_repository(provider_file)
    with pytest.raises(ValueError, match="read outside the repository"):
        universe_ingest_module._provider_file_outside_repository(provider_file)
    with pytest.raises(ValueError, match="outside the repository"):
        capture_module._bounded_file(provider_file, "provider file", 1_000_000)


def test_same_vintage_repeat_export_matches_only_after_excluding_observation_time():
    baseline = export_payload()
    repeat = export_payload(exported_at="2026-08-19T10:01:00.000000+00:00")
    assert set(json.loads(baseline)) == module._TOP_LEVEL_FIELDS

    result = compare_norgate_same_vintage_exports(baseline, repeat)

    summary = result.as_dict()
    invariant = json.loads(baseline)
    invariant.pop("exported_at")
    invariant_bytes = (
        json.dumps(
            invariant,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        + "\n"
    ).encode()
    assert summary["comparison_scope"] == (
        "SAME_DATABASE_VINTAGE_EXCLUDING_EXPORTED_AT_ONLY"
    )
    assert summary["same_vintage_invariant_match"] is True
    assert summary["baseline_source_payload_sha256"] == hashlib.sha256(
        baseline
    ).hexdigest()
    assert summary["repeat_source_payload_sha256"] == hashlib.sha256(
        repeat
    ).hexdigest()
    assert summary["invariant_payload_sha256"] == hashlib.sha256(
        invariant_bytes
    ).hexdigest()
    assert summary["requested_symbols"] == ["AAPL"]
    for flag in module.SAFETY_FLAG_NAMES:
        assert summary[flag] is False
    with pytest.raises(TypeError, match="not pickleable"):
        pickle.dumps(result)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_update_at", "2026-08-19T09:56:00.000000+00:00"),
        ("norgatedata_package_version", "1.0.78"),
        ("database_name", "Different Equities"),
    ],
)
def test_same_vintage_comparison_rejects_changed_invariant_metadata(field, value):
    with pytest.raises(ValueError, match=field):
        compare_norgate_same_vintage_exports(
            export_payload(),
            export_payload(
                exported_at="2026-08-19T10:01:00.000000+00:00",
                **{field: value},
            ),
        )


def test_same_vintage_comparison_requires_a_later_independent_export():
    with pytest.raises(ValueError, match="later independent observation"):
        compare_norgate_same_vintage_exports(export_payload(), export_payload())


def test_determinism_check_authority_and_false_flags_cannot_be_forged():
    required = {
        "baseline_source_payload_sha256": "0" * 64,
        "repeat_source_payload_sha256": "1" * 64,
        "invariant_payload_sha256": "2" * 64,
        "baseline_exported_at": EXPORTED_AT,
        "repeat_exported_at": "2026-08-19T10:01:00.000000+00:00",
        "database_update_at": "2026-08-19T09:55:00.000000+00:00",
        "requested_start": "2026-08-01",
        "requested_end": "2026-08-18",
        "requested_symbols": ("AAPL",),
    }
    with pytest.raises(PermissionError):
        module.NorgateSameVintageDeterminismCheck(**required)
    with pytest.raises(ValueError, match="safety flags were altered"):
        module.NorgateSameVintageDeterminismCheck(
            **required,
            performance_use_allowed=True,
            _authority=module._DETERMINISM_AUTHORITY,
        )


def test_same_vintage_comparison_rejects_changed_provider_row():
    changed = json.loads(export_payload())["rows"]
    changed[0]["close"] = 101.0
    changed[0]["unadjusted_close"] = 101.0
    with pytest.raises(ValueError, match="rows"):
        compare_norgate_same_vintage_exports(
            export_payload(),
            export_payload(
                rows=changed,
                exported_at="2026-08-19T10:01:00.000000+00:00",
            ),
        )


@pytest.mark.parametrize("payload", [b"", "not-bytes"])
def test_same_vintage_comparison_rejects_unbounded_or_nonbyte_inputs(payload):
    with pytest.raises(ValueError, match="bounded nonempty bytes"):
        compare_norgate_same_vintage_exports(payload, export_payload())


def test_same_vintage_comparison_cli_is_read_only_and_fail_closed(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    repeat = tmp_path / "repeat.json"
    baseline.write_bytes(export_payload())
    repeat.write_bytes(
        export_payload(exported_at="2026-08-19T10:01:00.000000+00:00")
    )

    completed = subprocess.run(
        [
            sys.executable,
            "scripts/compare_norgate_local_exports.py",
            "--baseline-file",
            str(baseline),
            "--repeat-file",
            str(repeat),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )

    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["same_vintage_invariant_match"] is True
    assert summary["performance_use_allowed"] is False
    assert summary["validation_accessed"] is False
    assert summary["test_accessed"] is False
    assert summary["broker_connection_allowed"] is False


def test_same_vintage_comparison_cli_rejects_drift_and_same_file(tmp_path: Path):
    baseline = tmp_path / "baseline.json"
    drifted = tmp_path / "drifted.json"
    baseline.write_bytes(export_payload())
    drifted.write_bytes(
        export_payload(
            exported_at="2026-08-19T10:01:00.000000+00:00",
            database_update_at="2026-08-19T09:56:00.000000+00:00",
        )
    )

    for repeat, expected in (
        (drifted, "invariant content changed"),
        (baseline, "distinct files"),
    ):
        completed = subprocess.run(
            [
                sys.executable,
                "scripts/compare_norgate_local_exports.py",
                "--baseline-file",
                str(baseline),
                "--repeat-file",
                str(repeat),
            ],
            cwd=Path(__file__).resolve().parents[1],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env={},
        )
        assert completed.returncode == 1
        assert completed.stdout == ""
        assert expected in completed.stderr


def test_sharded_capture_binds_exact_same_vintage_symbol_partition():
    aapl = shard_payload("AAPL", asset_id=101)
    msft = shard_payload(
        "MSFT",
        asset_id=202,
        exported_at="2026-08-19T10:01:00.000000+00:00",
    )

    catalog = universe_catalog_evidence()
    result = assemble_norgate_sharded_capture_manifest(
        [aapl, msft], catalog_evidence=catalog
    )

    summary = result.as_dict()
    assert summary["manifest_scope"] == (
        "CATALOG_BOUND_SAME_VINTAGE_SHARDED_QUARANTINE_CAPTURE_ONLY"
    )
    assert "requested_symbols" not in summary
    assert "aggregate_reused_symbols" not in summary
    assert summary["requested_symbol_count"] == 2
    assert summary["aggregate_reused_symbol_count"] == 0
    assert summary["requested_symbols_sha256"] == hashlib.sha256(
        b'["AAPL","MSFT"]'
    ).hexdigest()
    assert summary["catalog_source_payload_sha256"] == (
        catalog.source_payload_sha256
    )
    assert summary["catalog_entries_sha256"] == catalog.entries_sha256
    assert summary["catalog_evidence_sha256"] == catalog.catalog_evidence_sha256
    assert summary["catalog_entry_count"] == 2
    assert summary["catalog_exported_at"] == EXPORTED_AT
    assert summary["catalog_retrieved_at"] == (
        "2026-08-19T10:05:00.000000+00:00"
    )
    assert summary["catalog_receipt_timestamp_basis"] == (
        "CALLER_SUPPLIED_UNQUALIFIED"
    )
    assert [item["source_payload_sha256"] for item in summary["shards"]] == [
        hashlib.sha256(aapl).hexdigest(),
        hashlib.sha256(msft).hexdigest(),
    ]
    assert [item["ordinal"] for item in summary["shards"]] == [0, 1]
    assert summary["asset_count"] == 2
    assert summary["row_count"] == 2
    assert summary["license_restricted_provider_data"] is True
    assert summary["source_code_repository_storage_allowed"] is False
    assert summary["same_vintage_shard_contract_match"] is True
    assert summary["requested_symbol_partition_match"] is True
    assert summary["cross_shard_row_identity_unique"] is True
    assert summary["catalog_vintage_match"] is True
    assert summary["catalog_asset_identity_match"] is True
    for flag in module.SAFETY_FLAG_NAMES:
        assert summary[flag] is False
    repeated = assemble_norgate_sharded_capture_manifest(
        [aapl, msft], catalog_evidence=catalog
    )
    assert result.manifest_sha256 == (
        "deee00942ea6a06e8b3b6f6a87f3c97f8fb25f73c613c25c0ce377da3faff55c"
    )
    assert repeated.manifest_sha256 == result.manifest_sha256
    alternate_catalog = universe_catalog_evidence(
        exported_at="2026-08-19T09:59:00.000000+00:00"
    )
    rebound = assemble_norgate_sharded_capture_manifest(
        [aapl, msft], catalog_evidence=alternate_catalog
    )
    assert rebound.manifest_sha256 != result.manifest_sha256
    with pytest.raises(ValueError, match="catalog symbol partition"):
        assemble_norgate_sharded_capture_manifest(
            [msft, aapl], catalog_evidence=catalog
        )

    msft_root = json.loads(msft)
    second_row = dict(msft_root["rows"][0], session_date="2026-08-18")
    more_rows = export_payload(
        rows=[msft_root["rows"][0], second_row],
        requested_symbols=["MSFT"],
        exported_at="2026-08-19T10:01:00.000000+00:00",
    )
    expanded = assemble_norgate_sharded_capture_manifest(
        [aapl, more_rows], catalog_evidence=catalog
    )
    assert expanded.manifest_sha256 != result.manifest_sha256
    with pytest.raises(TypeError, match="not pickleable"):
        pickle.dumps(result)


@pytest.mark.parametrize(
    ("payloads", "catalog_entries", "message"),
    [
        (
            [
                shard_payload("MSFT", asset_id=202),
                shard_payload("AAPL", asset_id=101),
            ],
            None,
            "catalog symbol partition",
        ),
        (
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202),
            ],
            [
                {"asset_id": 101, "symbol": "AAPL", "security_name": "Apple Inc"},
                {
                    "asset_id": 202,
                    "symbol": "MSFT",
                    "security_name": "Microsoft Corp",
                },
                {
                    "asset_id": 303,
                    "symbol": "GOOG",
                    "security_name": "Alphabet Inc",
                },
            ],
            "catalog symbol partition",
        ),
        (
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload(
                    "AAPL",
                    asset_id=101,
                    exported_at="2026-08-19T10:01:00.000000+00:00",
                ),
            ],
            [
                {"asset_id": 101, "symbol": "AAPL", "security_name": "Apple Inc"}
            ],
            "asset/date identity",
        ),
    ],
)
def test_sharded_capture_rejects_partition_gap_order_and_overlap(
    payloads, catalog_entries, message
):
    with pytest.raises(ValueError, match=message):
        assemble_norgate_sharded_capture_manifest(
            payloads,
            catalog_evidence=universe_catalog_evidence(entries=catalog_entries),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_update_at", "2026-08-19T09:56:00.000000+00:00"),
        ("norgatedata_package_version", "1.0.78"),
        ("requested_end", "2026-08-17"),
        ("database_name", "Different Equities"),
    ],
)
def test_sharded_capture_rejects_mixed_contracts(field, value):
    with pytest.raises(ValueError, match="does not share the capture contract"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202, **{field: value}),
            ],
            catalog_evidence=universe_catalog_evidence(),
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database_update_at", "2026-08-19T09:54:00.000000+00:00"),
        ("norgatedata_package_version", "1.0.78"),
    ],
)
def test_sharded_capture_rejects_catalog_vintage_drift(field, value):
    with pytest.raises(ValueError, match="pinned catalog vintage"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202),
            ],
            catalog_evidence=universe_catalog_evidence(**{field: value}),
        )


def test_sharded_capture_rejects_cross_shard_row_and_asset_identity_drift():
    with pytest.raises(ValueError, match="repeats an asset/date identity"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=101),
            ],
            catalog_evidence=universe_catalog_evidence(),
        )

    with pytest.raises(ValueError, match="pinned catalog identity"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload(
                    "AAPL",
                    asset_id=101,
                    security_name="Wrong Apple Identity",
                ),
                shard_payload("MSFT", asset_id=202),
            ],
            catalog_evidence=universe_catalog_evidence(),
        )

    second_row = json.loads(shard_payload("MSFT", asset_id=101))["rows"][0]
    second_row["session_date"] = "2026-08-18"
    second_row["symbol"] = "AAPL"
    second_row["security_name"] = "AAPL Incorporated"
    drifted = export_payload(rows=[second_row], requested_symbols=["MSFT"])
    with pytest.raises(ValueError, match="changes a Norgate asset identity"):
        assemble_norgate_sharded_capture_manifest(
            [shard_payload("AAPL", asset_id=101), drifted],
            catalog_evidence=universe_catalog_evidence(),
        )


def test_sharded_capture_rejects_ambiguous_reused_catalog_symbols():
    catalog = universe_catalog_evidence(
        entries=[
            {"asset_id": 101, "symbol": "XYZ", "security_name": "Old Issuer"},
            {"asset_id": 202, "symbol": "XYZ", "security_name": "New Issuer"},
        ]
    )
    with pytest.raises(ValueError, match="unambiguous"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202),
            ],
            catalog_evidence=catalog,
        )


def test_sharded_capture_requires_authority_issued_catalog_evidence():
    with pytest.raises(ValueError, match="staged catalog evidence"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202),
            ],
            catalog_evidence=object(),
        )


def test_sharded_capture_enforces_aggregate_record_boundary(monkeypatch):
    monkeypatch.setattr(module, "MAX_CAPTURE_RECORDS", 1)
    with pytest.raises(ValueError, match="aggregate record boundary"):
        assemble_norgate_sharded_capture_manifest(
            [
                shard_payload("AAPL", asset_id=101),
                shard_payload("MSFT", asset_id=202),
            ],
            catalog_evidence=universe_catalog_evidence(),
        )


def test_sharded_capture_manifest_authority_and_flags_cannot_be_forged():
    required = {
        "manifest_sha256": "0" * 64,
        "catalog_source_payload_sha256": "a" * 64,
        "catalog_entries_sha256": "b" * 64,
        "catalog_evidence_sha256": "c" * 64,
        "catalog_entry_count": 2,
        "catalog_exported_at": EXPORTED_AT,
        "catalog_retrieved_at": "2026-08-19T10:05:00.000000+00:00",
        "catalog_receipt_timestamp_basis": "CALLER_SUPPLIED_UNQUALIFIED",
        "database_name": "US Equities",
        "database_update_at": "2026-08-19T09:55:00.000000+00:00",
        "norgatedata_package_version": "1.0.77",
        "requested_start": "2026-08-01",
        "requested_end": "2026-08-18",
        "requested_symbols": ("AAPL", "MSFT"),
        "requested_symbols_sha256": "1" * 64,
        "shard_source_payload_sha256": ("2" * 64, "3" * 64),
        "shard_requested_symbols_sha256": ("4" * 64, "5" * 64),
        "shard_exported_at": (EXPORTED_AT, EXPORTED_AT),
        "shard_symbol_counts": (1, 1),
        "shard_row_counts": (1, 1),
        "aggregate_reused_symbols": (),
        "asset_count": 2,
        "row_count": 2,
    }
    with pytest.raises(PermissionError):
        NorgateShardedCaptureManifest(**required)
    with pytest.raises(ValueError, match="safety flags were altered"):
        NorgateShardedCaptureManifest(
            **required,
            performance_use_allowed=True,
            _authority=module._CAPTURE_MANIFEST_AUTHORITY,
        )
    with pytest.raises(ValueError, match="assertions were altered"):
        NorgateShardedCaptureManifest(
            **required,
            same_vintage_shard_contract_match=False,
            _authority=module._CAPTURE_MANIFEST_AUTHORITY,
        )
    with pytest.raises(ValueError, match="assertions were altered"):
        NorgateShardedCaptureManifest(
            **required,
            catalog_asset_identity_match=False,
            _authority=module._CAPTURE_MANIFEST_AUTHORITY,
        )
    with pytest.raises(ValueError, match="license markings were altered"):
        NorgateShardedCaptureManifest(
            **required,
            license_restricted_provider_data=False,
            _authority=module._CAPTURE_MANIFEST_AUTHORITY,
        )
    inconsistent = dict(required, catalog_entry_count=3)
    with pytest.raises(ValueError, match="catalog-bound capture evidence"):
        NorgateShardedCaptureManifest(
            **inconsistent,
            _authority=module._CAPTURE_MANIFEST_AUTHORITY,
        )


def test_sharded_capture_cli_is_read_only_and_fail_closed(tmp_path: Path):
    first = tmp_path / "aapl.json"
    second = tmp_path / "msft.json"
    catalog = tmp_path / "catalog.json"
    first.write_bytes(shard_payload("AAPL", asset_id=101))
    second.write_bytes(shard_payload("MSFT", asset_id=202))
    catalog.write_bytes(universe_catalog_payload())
    catalog_sha256 = hashlib.sha256(catalog.read_bytes()).hexdigest()

    command = [
        sys.executable,
        "scripts/assemble_norgate_local_capture.py",
        "--export-file",
        str(first),
        "--export-file",
        str(second),
        "--catalog-file",
        str(catalog),
        "--expected-catalog-source-sha256",
        catalog_sha256,
    ]
    completed = subprocess.run(
        command,
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )
    assert completed.returncode == 0, completed.stderr
    summary = json.loads(completed.stdout)
    assert summary["requested_symbol_partition_match"] is True
    assert summary["catalog_vintage_match"] is True
    assert summary["catalog_asset_identity_match"] is True
    assert summary["catalog_entry_count"] == 2
    assert "requested_symbols" not in summary
    assert "aggregate_reused_symbols" not in summary
    assert summary["performance_use_allowed"] is False
    assert summary["validation_accessed"] is False
    assert summary["test_accessed"] is False
    assert summary["broker_connection_allowed"] is False

    rejected = subprocess.run(
        [
            sys.executable,
            "scripts/assemble_norgate_local_capture.py",
            "--export-file",
            str(first),
            "--export-file",
            str(first),
            "--catalog-file",
            str(catalog),
            "--expected-catalog-source-sha256",
            catalog_sha256,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )
    assert rejected.returncode == 1
    assert rejected.stdout == ""
    assert "distinct paths" in rejected.stderr

    substituted = subprocess.run(
        [
            sys.executable,
            "scripts/assemble_norgate_local_capture.py",
            "--export-file",
            str(first),
            "--export-file",
            str(second),
            "--catalog-file",
            str(catalog),
            "--expected-catalog-source-sha256",
            "0" * 64,
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env={},
    )
    assert substituted.returncode == 1
    assert substituted.stdout == ""
    assert "does not match expected source SHA-256" in substituted.stderr


def test_modules_have_no_network_broker_execution_or_order_surface():
    for inspected in (
        module,
        capture_module,
        compare_module,
        sys.modules[build_export.__module__],
        ingest_module,
        universe_export_module,
        universe_ingest_module,
    ):
        tree = ast.parse(inspect.getsource(inspected))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        text = inspect.getsource(inspected).lower()
        denied_roots = {
            "aiohttp",
            "ftplib",
            "http",
            "httpx",
            "requests",
            "socket",
            "urllib",
            "websockets",
        }
        assert not any(name.split(".")[0] in denied_roots for name in imports)
        assert not any("broker" in name or "execution" in name for name in imports)
        assert "submit_order" not in text
        assert "cancel_order" not in text
