from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest
import requests

import core.orchestration.sharadar_quarantine as module
import scripts._sharadar_keychain as keychain
import scripts.capture_sharadar_bulk as bulk_script
import scripts.capture_sharadar_connectivity as script
from core.data_sources.provider_access import ProviderAttemptMetadata, ProviderHTTPResult
from core.orchestration.sharadar_quarantine import (
    BULK_REQUIRED_FIELDS,
    PROBE_DEFINITIONS,
    QUARANTINE_RELATIVE_PATH,
    SharadarBulkStatus,
    SharadarCaptureError,
    SharadarFetchedProbe,
    SharadarProbeDefinition,
    SharadarSampleClient,
    execute_connectivity_capture,
    execute_ten_year_bulk_capture,
    ensure_foundation_baseline_observation,
    inspect_ten_year_bulk_status,
    load_verified_bulk_captures,
    load_verified_foundation_observations,
    persist_bulk_capture,
    persist_probe,
    validate_probe_csv,
)
from core.orchestration.sharadar_foundation import (
    COUNTERPARTY_NOT_PROVIDED,
    IDENTITY_AMBIGUOUS,
    IDENTITY_MISSING,
    IDENTITY_UNIQUE,
    MISSING_ABSENT_FROM_MASTER,
    MISSING_PRESENT_OUTSIDE_REQUIRED_TABLES,
    STATUS as FOUNDATION_PROFILE_STATUS,
    _identity_state,
    build_foundation_profile,
    persist_foundation_profile,
)
from core.orchestration.sharadar_vintages import (
    STATUS as VINTAGE_COMPARISON_STATUS,
    build_foundation_vintage_comparison,
    persist_foundation_vintage_comparison,
)


API_KEY = "synthetic-secret-key-that-must-not-leak"
UTC = timezone.utc
START = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def csv_payload(definition: SharadarProbeDefinition) -> bytes:
    values = {
        "table": "fundamentals",
        "permaticker": "199059",
        "ticker": "AAPL",
        "name": "Apple Inc",
        "exchange": "NASDAQ",
        "isdelisted": "N",
        "firstpricedate": "1980-12-12",
        "lastpricedate": "2026-08-19",
        "date": "2022-01-04",
        "open": "177.83",
        "high": "182.94",
        "low": "179.12",
        "close": "179.70",
        "volume": "99310438",
        "closeadj": "176.61",
        "closeunadj": "179.70",
        "dimension": "ARQ",
        "calendardate": "2021-12-31",
        "reportperiod": "2021-12-25",
        "lastupdated": "2022-01-28",
    }
    header = ",".join(definition.expected_fields)
    row = ",".join(values[name] for name in definition.expected_fields)
    return f"{header}\n{row}\n".encode()


class Response:
    def __init__(
        self,
        payload: bytes,
        *,
        status_code: int = 200,
        content_type: str = "text/csv; charset=utf-8",
    ) -> None:
        self.content = payload
        self.status_code = status_code
        self.headers = {"Content-Type": content_type}
        self.ok = status_code == 200
        self.closed = False

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]

    def close(self):
        self.closed = True


def metadata() -> ProviderAttemptMetadata:
    return ProviderAttemptMetadata(
        provider="Sharadar bounded connectivity",
        attempts=1,
        retry_count=0,
        retried_status_codes=(),
        total_wait_seconds=0.0,
        elapsed_seconds=0.01,
        circuit_state="CLOSED",
    )


class Access:
    def __init__(self, response_factory=None) -> None:
        self.calls: list[tuple] = []
        self.response_factory = response_factory or (
            lambda definition: Response(csv_payload(definition))
        )

    def get(self, session, url, **kwargs):
        self.calls.append((session, url, kwargs))
        table = url.rsplit("/", 1)[-1]
        definition = next(item for item in PROBE_DEFINITIONS if item.table == table)
        return ProviderHTTPResult(
            response=self.response_factory(definition),
            metadata=metadata(),
        )


def clocks(start=START):
    values = iter(start + timedelta(seconds=index) for index in range(20))
    return lambda: next(values)


def fetch(definition=PROBE_DEFINITIONS[0], *, access=None):
    return SharadarSampleClient(
        API_KEY,
        session=object(),
        access=access or Access(),
        clock=clocks(),
    ).fetch(definition)


def test_frozen_connectivity_plan_is_tiny_train_only_and_credential_free():
    assert [item.table for item in PROBE_DEFINITIONS] == [
        "tickers",
        "stocks",
        "fundamentals",
    ]
    assert all(int(item.query["limit"]) <= 5 for item in PROBE_DEFINITIONS)
    assert all("api_key" not in item.request_query_canonical for item in PROBE_DEFINITIONS)
    assert all(item.query.get("ticker") == "AAPL" for item in PROBE_DEFINITIONS)
    assert PROBE_DEFINITIONS[0].query["table"] == "fundamentals"
    assert PROBE_DEFINITIONS[1].query["to"] == "2022-01-07"
    assert PROBE_DEFINITIONS[2].query["dimension"] == "ARQ"


def test_client_sends_key_only_in_provider_required_params_and_returns_no_secret():
    access = Access()
    result = SharadarSampleClient(
        API_KEY,
        session=object(),
        access=access,
        clock=clocks(),
    ).fetch(PROBE_DEFINITIONS[2])

    _, url, kwargs = access.calls[0]
    assert url == "https://api.sharadar.com/v1.0/data/fundamentals"
    assert kwargs["params"]["api_key"] == API_KEY
    assert kwargs["allow_redirects"] is False
    assert kwargs["headers"] == {"Accept": "text/csv"}
    serialized = json.dumps(result.as_record(), sort_keys=True)
    assert API_KEY not in serialized
    assert "api_key" not in serialized
    assert result.row_count == 1
    assert result.payload_sha256 == hashlib.sha256(result.payload_bytes).hexdigest()
    assert result.as_record()["dataset_admitted"] is False
    assert result.as_record()["validation_opened"] is False
    assert result.as_record()["test_opened"] is False


def test_exact_csv_shapes_pass_without_granting_semantic_authority():
    for definition in PROBE_DEFINITIONS:
        rows, header_sha = validate_probe_csv(csv_payload(definition), definition)
        assert rows == 1
        assert len(header_sha) == 64


def test_probe_accepts_provider_native_column_order_and_hashes_observed_header():
    definition = PROBE_DEFINITIONS[0]
    payload = csv_payload(definition)
    lines = payload.decode().splitlines()
    fields = lines[0].split(",")
    values = lines[1].split(",")
    reordered = (
        ",".join(reversed(fields))
        + "\n"
        + ",".join(reversed(values))
        + "\n"
    ).encode()

    rows, header_sha = validate_probe_csv(reordered, definition)

    assert rows == 1
    assert header_sha == hashlib.sha256(
        ",".join(reversed(fields)).encode()
    ).hexdigest()


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda definition, payload: payload.replace(b"AAPL", b"MSFT"), "ticker"),
        (
            lambda definition, payload: payload.replace(b"ARQ", b"MRQ")
            if definition.table == "fundamentals"
            else b"bad,header\n1,2\n",
            "as-reported|schema",
        ),
        (lambda definition, payload: payload + payload.split(b"\n", 1)[1] * 30, "bounded"),
    ],
)
def test_provider_shape_mismatches_fail_closed(mutator, message):
    definition = PROBE_DEFINITIONS[2]
    with pytest.raises(ValueError, match=message):
        validate_probe_csv(mutator(definition, csv_payload(definition)), definition)


@pytest.mark.parametrize(
    ("status", "content_type", "message"),
    [
        (302, "text/csv", "redirect"),
        (401, "text/csv", "rejected"),
        (200, "application/json", "media type"),
    ],
)
def test_http_redirect_error_and_wrong_media_type_are_secret_free(
    status, content_type, message
):
    access = Access(
        lambda definition: Response(
            csv_payload(definition), status_code=status, content_type=content_type
        )
    )
    with pytest.raises(SharadarCaptureError, match=message) as caught:
        fetch(access=access)
    assert API_KEY not in str(caught.value)


def test_forged_definition_and_result_are_rejected():
    original = PROBE_DEFINITIONS[0]
    forged = SharadarProbeDefinition(
        table=original.table,
        role=original.role,
        query=dict(original.query),
        expected_fields=original.expected_fields,
    )
    with pytest.raises(ValueError, match="frozen"):
        SharadarSampleClient(
            API_KEY,
            session=object(),
            access=Access(),
            clock=clocks(),
        ).fetch(forged)
    required = {
        "table": "tickers",
        "role": "SECURITY_MASTER_CONNECTIVITY",
        "request_uri": "https://api.sharadar.com/v1.0/data/tickers",
        "request_query_canonical": "fields=x",
        "requested_at": START.isoformat(),
        "retrieved_at": START.isoformat(),
        "response_status_code": 200,
        "response_headers_sha256": "0" * 64,
        "media_type": "text/csv",
        "payload_bytes": b"x",
        "payload_sha256": hashlib.sha256(b"x").hexdigest(),
        "byte_length": 1,
        "row_count": 1,
        "csv_header_sha256": "1" * 64,
        "provider_access": metadata().as_dict(),
    }
    with pytest.raises(PermissionError):
        SharadarFetchedProbe(**required)


def test_persistence_is_owner_only_content_addressed_hash_chained_and_secret_free(
    tmp_path: Path,
):
    root = tmp_path / "quarantine"
    first = persist_probe(root, fetch(PROBE_DEFINITIONS[0]))
    second = persist_probe(root, fetch(PROBE_DEFINITIONS[1]))

    blob = root / first["blob_relative_path"]
    ledger = root / "captures.jsonl"
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "blobs").stat().st_mode) == 0o700
    assert stat.S_IMODE(blob.stat().st_mode) == 0o400
    assert stat.S_IMODE(ledger.stat().st_mode) == 0o600
    rows = [json.loads(line) for line in ledger.read_text().splitlines()]
    assert rows[0]["previous_hash"] == module.GENESIS_HASH
    assert rows[1]["previous_hash"] == rows[0]["record_hash"]
    assert second["record_hash"] == rows[1]["record_hash"]
    assert API_KEY not in ledger.read_text()
    assert all(row["quarantine_only"] is True for row in rows)
    assert all(row["performance_authorized"] is False for row in rows)


def test_tampered_ledger_or_blob_is_rejected(tmp_path: Path):
    root = tmp_path / "quarantine"
    probe = fetch()
    record = persist_probe(root, probe)
    ledger = root / "captures.jsonl"
    ledger.write_bytes(ledger.read_bytes().replace(b'"row_count":1', b'"row_count":2'))
    with pytest.raises(ValueError, match="hash"):
        persist_probe(root, probe)

    clean = tmp_path / "clean"
    record = persist_probe(clean, probe)
    blob = clean / record["blob_relative_path"]
    blob.chmod(0o600)
    with pytest.raises(ValueError, match="unsafe"):
        persist_probe(clean, probe)
    blob.write_bytes(b"tampered")
    blob.chmod(0o400)
    with pytest.raises(ValueError, match="hash verification"):
        persist_probe(clean, probe)


def test_repeat_probe_capture_records_new_observation_time_without_rewriting_blob(
    tmp_path: Path,
):
    root = tmp_path / "quarantine"

    def fetched_at(moment: datetime) -> SharadarFetchedProbe:
        values = iter((moment, moment + timedelta(seconds=1)))
        return SharadarSampleClient(
            API_KEY,
            session=object(),
            access=Access(),
            clock=lambda: next(values),
        ).fetch(PROBE_DEFINITIONS[0])

    first = persist_probe(root, fetched_at(START))
    second = persist_probe(root, fetched_at(START + timedelta(days=12)))

    assert first["payload_sha256"] == second["payload_sha256"]
    assert first["requested_at"] != second["requested_at"]
    assert first["record_hash"] != second["record_hash"]
    assert len((root / "captures.jsonl").read_text().splitlines()) == 2
    assert len(list((root / "blobs").glob("*.csv"))) == 1


def test_execute_runs_exactly_three_probes_and_stays_out_of_source_control(tmp_path: Path):
    access = Access()
    records = execute_connectivity_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=object(),
        access=access,
        clock=clocks(),
    )
    assert len(records) == len(access.calls) == 3
    assert [record["table"] for record in records] == [
        "tickers",
        "stocks",
        "fundamentals",
    ]
    assert all(record["dataset_admitted"] is False for record in records)
    assert all(record["license_restricted"] is True for record in records)
    assert (tmp_path / module.QUARANTINE_RELATIVE_PATH / "captures.jsonl").exists()


def test_cli_uses_hidden_prompt_and_prints_only_bounded_summary(monkeypatch, capsys):
    observed = {}
    monkeypatch.setattr(script, "load_key", lambda: API_KEY)

    def execute(**kwargs):
        observed.update(kwargs)
        return (
            {
                "table": "tickers",
                "role": "SECURITY_MASTER_CONNECTIVITY",
                "row_count": 1,
                "byte_length": 100,
                "payload_sha256": "a" * 64,
                "quarantine_only": True,
                "dataset_admitted": False,
            },
        )

    monkeypatch.setattr(script, "execute_connectivity_capture", execute)
    assert script.main() == 0
    output = capsys.readouterr()
    assert observed["api_key"] == API_KEY
    assert API_KEY not in output.out + output.err
    assert '"dataset_admitted": false' in output.out


def test_keychain_store_prompts_without_putting_secret_in_argv(monkeypatch):
    calls = []

    class Result:
        returncode = 0

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return Result()

    monkeypatch.setattr(keychain.getpass, "getuser", lambda: "sam")
    keychain.store_interactively(runner=runner)
    command, kwargs = calls[0]
    assert command[-1] == "-w"
    assert API_KEY not in command
    assert command[:2] == ["/usr/bin/security", "add-generic-password"]
    assert kwargs == {"check": False}


def test_keychain_load_keeps_secret_out_of_errors_and_validates_output(monkeypatch):
    class Result:
        returncode = 0
        stdout = (API_KEY + "\n").encode()

    monkeypatch.setattr(keychain.getpass, "getuser", lambda: "sam")
    observed = {}

    def runner(command, **kwargs):
        observed["command"] = command
        observed["kwargs"] = kwargs
        return Result()

    assert keychain.load(runner=runner) == API_KEY
    assert API_KEY not in observed["command"]
    Result.stdout = b"bad\nkey\n"
    with pytest.raises(RuntimeError, match="invalid") as caught:
        keychain.load(runner=runner)
    assert API_KEY not in str(caught.value)


def bulk_archive(
    table: str,
    *,
    empty: bool = False,
    extra_member: bool = False,
    symlink_member: bool = False,
    bomb_member: bool = False,
    encrypted_member: bool = False,
    row_overrides: dict[str, str] | None = None,
    additional_rows: list[dict[str, str]] | None = None,
) -> bytes:
    stream = io.BytesIO()
    fields = sorted(BULK_REQUIRED_FIELDS[table])
    values = {name: "1" for name in fields}
    values.update(
        {
            "ticker": "AAPL",
            "table": "SEP",
            "permaticker": "199059",
            "isdelisted": "N",
            "dimension": "ARQ",
            "date": "2022-01-04",
            "datekey": "2022-01-04",
            "calendardate": "2021-12-31",
            "reportperiod": "2021-12-25",
            "lastupdated": "2022-01-28",
            "firstpricedate": "1980-12-12",
            "lastpricedate": "2026-08-19",
        }
    )
    values.update(row_overrides or {})
    rows = [] if empty else [values]
    for overrides in additional_rows or ():
        rows.append({**values, **overrides})
    content = (
        ",".join(fields)
        + "\n"
        + "".join(",".join(row[name] for name in fields) + "\n" for row in rows)
    ).encode()
    if bomb_member:
        content += b"0" * (2 * 1024 * 1024)
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if symlink_member:
            member = zipfile.ZipInfo(f"{table}.csv")
            member.create_system = 3
            member.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(member, content)
        else:
            archive.writestr(f"{table}.csv", content)
        if extra_member:
            archive.writestr("unexpected.csv", content)
    payload = bytearray(stream.getvalue())
    if encrypted_member:
        for signature, flag_offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
            position = payload.find(signature)
            assert position >= 0
            flags = int.from_bytes(
                payload[position + flag_offset : position + flag_offset + 2],
                "little",
            )
            payload[position + flag_offset : position + flag_offset + 2] = (
                flags | 1
            ).to_bytes(2, "little")
    return bytes(payload)


class BulkAccess:
    def __init__(
        self,
        archives,
        *,
        redirect_host=module.OBSERVED_BULK_HOST,
        status_delta=0,
        status_extra=None,
        modified="2026-08-20T10:00:00.000Z",
    ):
        self.archives = archives
        self.redirect_host = redirect_host
        self.status_delta = status_delta
        self.status_extra = status_extra or {}
        self.modified = modified
        self.calls = []

    def get(self, session, url, **kwargs):
        self.calls.append((session, url, kwargs))
        table = url.rsplit("/", 1)[-1]
        if kwargs["params"].get("status") == "True":
            history = module.FOUNDATION_HISTORY[table]
            name = (
                f"{table}.csv.zip"
                if history == "full"
                else f"{table}-10Y.csv.zip"
            )
            selected_file = {
                "available": True,
                "history": history,
                "historyLabel": (
                    "Full History" if history == "full" else "10 Years"
                ),
                "key": f"bulk-sharadar/{name}",
                "name": name,
                "size": len(self.archives[table]) + self.status_delta,
                "sizeLabel": f"{len(self.archives[table])} B",
                "modified": self.modified,
            }
            files = [selected_file]
            if history == "10y":
                files = [
                    {
                        **selected_file,
                        "history": "5y",
                        "historyLabel": "5 Years",
                        "name": f"{table}-5Y.csv.zip",
                        "key": f"bulk-sharadar/{table}-5Y.csv.zip",
                        "size": max(1, len(self.archives[table]) // 2),
                    },
                    selected_file,
                    {
                        **selected_file,
                        "history": "full",
                        "historyLabel": "Full History",
                        "name": f"{table}.csv.zip",
                        "key": f"bulk-sharadar/{table}.csv.zip",
                        "size": len(self.archives[table]) * 2,
                    },
                ]
            payload = json.dumps(
                {
                    "table": table,
                    "files": files,
                    **self.status_extra,
                },
                separators=(",", ":"),
            ).encode()
            response = Response(payload, content_type="application/json")
        else:
            response = Response(b"", status_code=302)
            response.headers = {
                "Location": (
                    f"https://{self.redirect_host}/licensed/{table}.csv.zip"
                    "?X-Amz-Signature=secret-signed-url"
                )
            }
        return ProviderHTTPResult(response=response, metadata=metadata())


class BulkSession:
    def __init__(self, archives, *, length_delta=0, omit_length=False, stream_error=False):
        self.archives = archives
        self.length_delta = length_delta
        self.omit_length = omit_length
        self.stream_error = stream_error
        self.calls = []
        self.responses = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        table = url.split("/licensed/", 1)[1].split(".csv.zip", 1)[0]
        payload = self.archives[table]
        response = Response(payload, content_type="application/zip")
        if not self.omit_length:
            response.headers["Content-Length"] = str(len(payload) + self.length_delta)
        if self.stream_error:
            response.iter_content = lambda chunk_size: (_ for _ in ()).throw(
                requests.exceptions.ChunkedEncodingError(
                    "signed-url-secret-must-not-leak"
                )
            )
        self.responses.append(response)
        return response


def bulk_stack(
    *,
    extra_member=False,
    redirect_host=module.OBSERVED_BULK_HOST,
    length_delta=0,
    status_delta=0,
    status_extra=None,
    omit_length=False,
    stream_error=False,
    symlink_member=False,
    bomb_member=False,
    encrypted_member=False,
    row_overrides=None,
    additional_rows=None,
    modified="2026-08-20T10:00:00.000Z",
    empty_tables=(),
):
    archives = {
        table: bulk_archive(
            table,
            empty=table in empty_tables,
            extra_member=extra_member,
            symlink_member=symlink_member,
            bomb_member=bomb_member,
            encrypted_member=encrypted_member,
            row_overrides=(row_overrides or {}).get(table),
            additional_rows=(additional_rows or {}).get(table),
        )
        for table in module.TEN_YEAR_TABLES
    }
    return (
        archives,
        BulkSession(
            archives,
            length_delta=length_delta,
            omit_length=omit_length,
            stream_error=stream_error,
        ),
        BulkAccess(
            archives,
            redirect_host=redirect_host,
            status_delta=status_delta,
            status_extra=status_extra,
            modified=modified,
        ),
    )


def test_fundamentals_bulk_schema_uses_observed_archive_datekey():
    required = BULK_REQUIRED_FIELDS["fundamentals"]

    assert "datekey" in required
    assert "date" not in required


def test_bulk_status_is_exact_bounded_and_credential_free():
    archives, session, access = bulk_stack()
    statuses = inspect_ten_year_bulk_status(
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    assert [item.table for item in statuses] == list(module.TEN_YEAR_TABLES)
    assert all(item.size == len(archives[item.table]) for item in statuses)
    assert [item.history for item in statuses] == [
        "full",
        "10y",
        "10y",
        "10y",
        "10y",
    ]
    assert statuses[0].name == "tickers.csv.zip"
    assert all(item.modified.endswith("Z") for item in statuses)
    assert API_KEY not in json.dumps([item.as_dict() for item in statuses])
    assert all(call[2]["params"]["api_key"] == API_KEY for call in access.calls)


def test_bulk_status_size_is_advisory_even_above_ten_year_download_ceiling():
    archives, session, access = bulk_stack()
    access.status_delta = (
        module.MAX_COMPRESSED_BYTES["stocks"] + 1 - len(archives["stocks"])
    )
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    status = client.fetch_bulk_status("stocks")

    assert status.size == module.MAX_COMPRESSED_BYTES["stocks"] + 1
    assert status.as_dict()["status_size_is_advisory"] is True
    assert status.as_dict()["status_modified_is_opaque"] is True


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ([], "files are unsupported"),
        (
            [
                {
                    "available": False,
                    "history": "full",
                    "name": "tickers.csv.zip",
                    "size": 100,
                    "modified": "2026-08-20T03:12:24.321Z",
                }
            ],
            "foundation file is unavailable",
        ),
        (
            [
                {
                    "available": True,
                    "history": "full",
                    "name": "tickers.csv.zip",
                    "size": 100,
                    "modified": "2026-08-20T03:12:24.321Z",
                },
                {
                    "available": True,
                    "history": "full",
                    "name": "tickers-copy.csv.zip",
                    "size": 100,
                    "modified": "2026-08-20T03:12:24.321Z",
                },
            ],
            "foundation file is unavailable",
        ),
    ],
)
def test_bulk_status_envelope_requires_one_available_foundation_file(files, message):
    _, session, access = bulk_stack(status_extra={"files": files})
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    with pytest.raises(ValueError, match=message):
        client.fetch_bulk_status("tickers")


def test_bulk_download_validates_redirect_size_zip_schema_and_persists_hash_chain(
    tmp_path: Path,
):
    archives, session, access = bulk_stack()
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    root = tmp_path / "quarantine"
    status = client.fetch_bulk_status("stocks")
    capture = client.download_ten_year_bulk(status=status, quarantine_root=root)
    record = persist_bulk_capture(root, capture)

    assert capture.byte_length == len(archives["stocks"])
    assert capture.archive_member == "stocks.csv"
    assert capture.redirect_host == module.OBSERVED_BULK_HOST
    assert "Signature" not in json.dumps(dict(record))
    assert API_KEY not in json.dumps(dict(record))
    assert record["dataset_admitted"] is False
    assert record["validation_opened"] is False
    assert record["status_size_is_advisory"] is True
    assert record["archive_member_declared_bytes"] > 0
    assert record["license_restricted"] is True
    archive = root / record["blob_relative_path"]
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == record["payload_sha256"]
    assert session.calls[0][1]["stream"] is True


@pytest.mark.parametrize(
    ("redirect_host", "length_delta", "extra_member", "message"),
    [
        ("127.0.0.1", 0, False, "redirect target"),
        (module.OBSERVED_BULK_HOST, 1, False, "declaration"),
        (module.OBSERVED_BULK_HOST, 0, True, "exactly one"),
    ],
)
def test_bulk_download_rejects_unsafe_redirect_changed_size_and_archive_shape(
    tmp_path, redirect_host, length_delta, extra_member, message
):
    _, session, access = bulk_stack(
        extra_member=extra_member,
        redirect_host=redirect_host,
        length_delta=length_delta,
    )
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("tickers")
    with pytest.raises((SharadarCaptureError, ValueError), match=message) as caught:
        client.download_ten_year_bulk(status=status, quarantine_root=tmp_path / "q")
    assert API_KEY not in str(caught.value)
    assert not list((tmp_path / "q").glob("*.partial"))


def test_bulk_stream_rejects_more_bytes_than_content_length(tmp_path):
    _, session, access = bulk_stack(length_delta=-1)
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("tickers")

    with pytest.raises(SharadarCaptureError, match="exceeded its declared size"):
        client.download_ten_year_bulk(status=status, quarantine_root=tmp_path / "q")
    assert not list((tmp_path / "q").glob("*.partial"))


@pytest.mark.parametrize(
    "option", ["symlink_member", "bomb_member", "encrypted_member"]
)
def test_bulk_archive_rejects_symlink_and_extreme_compression_ratio(tmp_path, option):
    _, session, access = bulk_stack(**{option: True})
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("tickers")

    with pytest.raises(ValueError, match="archive member is unsafe"):
        client.download_ten_year_bulk(status=status, quarantine_root=tmp_path / "q")


def test_end_to_end_bulk_capture_is_five_table_quarantine_only(tmp_path):
    _, session, access = bulk_stack()
    records = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    assert [record["table"] for record in records] == list(module.TEN_YEAR_TABLES)
    assert all(record["quarantine_only"] is True for record in records)
    assert all(record["performance_authorized"] is False for record in records)
    root = tmp_path / QUARANTINE_RELATIVE_PATH
    assert len((root / "bulk_captures.jsonl").read_text().splitlines()) == 5
    assert API_KEY not in (root / "bulk_captures.jsonl").read_text()
    download_queries = [
        call[2]["params"]
        for call in access.calls
        if call[2]["params"].get("status") != "True"
    ]
    assert [query["years"] for query in download_queries] == [
        "full",
        "10",
        "10",
        "10",
        "10",
    ]
    assert records[0]["status_history"] == "full"
    assert [record["history"] for record in records] == [
        "full",
        "10y",
        "10y",
        "10y",
        "10y",
    ]


def test_verified_bulk_loader_rechecks_exact_five_table_foundation(tmp_path):
    _, session, access = bulk_stack()
    captured = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    loaded = load_verified_bulk_captures(tmp_path)

    assert [record["record_hash"] for record in loaded] == [
        record["record_hash"] for record in captured
    ]
    assert all(record["dataset_admitted"] is False for record in loaded)


def test_foundation_observations_reobserve_unchanged_capture_set(tmp_path):
    _, first_session, first_access = bulk_stack()
    first_records = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=first_session,
        access=first_access,
        clock=clocks(),
    )
    _, second_session, second_access = bulk_stack()
    second_records = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=second_session,
        access=second_access,
        clock=clocks(START + timedelta(days=1)),
    )

    observations = load_verified_foundation_observations(tmp_path)

    assert len(observations) == 2
    assert observations[0]["origin"] == "CAPTURE_RUN"
    assert observations[0]["downloaded_by_table"] == {
        table: True for table in sorted(module.TEN_YEAR_TABLES)
    }
    assert observations[1]["downloaded_by_table"] == {
        table: False for table in sorted(module.TEN_YEAR_TABLES)
    }
    assert observations[0]["capture_record_hashes"] == observations[1][
        "capture_record_hashes"
    ]
    assert [record["record_hash"] for record in first_records] == [
        record["record_hash"] for record in second_records
    ]
    assert second_session.calls == []
    assert all(item[2]["params"].get("status") == "True" for item in second_access.calls)
    assert observations[1]["dataset_admitted"] is False


def test_cross_vintage_exact_repeat_is_measurement_not_qualification(tmp_path):
    for start in (START, START + timedelta(days=1)):
        _, session, access = bulk_stack()
        execute_ten_year_bulk_capture(
            repository_root=tmp_path,
            api_key=API_KEY,
            session=session,
            access=access,
            clock=clocks(start),
        )
    baseline, candidate = load_verified_foundation_observations(tmp_path)

    comparison = build_foundation_vintage_comparison(
        tmp_path,
        baseline_observation_hash=baseline["record_hash"],
        candidate_observation_hash=candidate["record_hash"],
        synthetic_fixture=True,
    )

    assert comparison["status"] == VINTAGE_COMPARISON_STATUS
    assert comparison["every_table_reobserved_later"] is True
    assert comparison["sha256_canonical_row_multisets_compared"] is True
    assert comparison["historical_row_churn_count"] == 0
    assert comparison["undated_ticker_master_churn_count"] == 0
    assert all(
        details["identical_rows"] == 1
        and details["removed_rows"] == 0
        and details["added_rows"] == 0
        for details in comparison["tables"].values()
    )
    assert comparison["historical_availability_qualified"] is False
    assert comparison["dataset_admitted"] is False
    assert comparison["performance_claim_allowed"] is False


def test_cross_vintage_comparison_counts_historical_and_undated_churn(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    _, later_session, later_access = bulk_stack(
        modified="2026-08-21T10:00:00.000Z",
        row_overrides={
            "stocks": {
                "close": "2",
                "high": "2",
                "closeadj": "2",
                "closeunadj": "2",
                "lastupdated": "2022-02-01",
            }
        },
        additional_rows={
            "stocks": [{"date": "2022-01-05"}],
            "tickers": [{"ticker": "MSFT", "permaticker": "2"}],
        },
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=later_session,
        access=later_access,
        clock=clocks(START + timedelta(days=1)),
    )
    baseline, candidate = load_verified_foundation_observations(tmp_path)

    comparison = persist_foundation_vintage_comparison(
        tmp_path,
        baseline_observation_hash=baseline["record_hash"],
        candidate_observation_hash=candidate["record_hash"],
    )

    stocks = comparison["tables"]["stocks"]
    assert stocks["baseline_rows"] == 1
    assert stocks["candidate_rows"] == 2
    assert stocks["identical_rows"] == 0
    assert stocks["removed_rows"] == 1
    assert stocks["added_rows"] == 2
    assert stocks["added_rows_at_or_before_baseline_max_observed_date"] == 1
    assert stocks["added_rows_after_baseline_max_observed_date"] == 1
    assert stocks["historical_row_churn_observed"] is True
    assert comparison["historical_row_churn_count"] == 2
    assert comparison["historical_row_churn_count_basis"] == (
        "REMOVED_PLUS_ADDED_AT_OR_BEFORE_BASELINE_MAX_LITERAL_DATE"
    )
    assert comparison["undated_ticker_master_churn_count"] == 1
    serialized = json.dumps(comparison, sort_keys=True)
    assert "AAPL" not in serialized
    assert "MSFT" not in serialized
    target = (
        tmp_path
        / QUARANTINE_RELATIVE_PATH
        / f"foundation-vintage-{comparison['comparison_sha256']}.json"
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o400
    with pytest.raises(ValueError, match="must be later"):
        build_foundation_vintage_comparison(
            tmp_path,
            baseline_observation_hash=candidate["record_hash"],
            candidate_observation_hash=baseline["record_hash"],
            synthetic_fixture=True,
        )


def test_existing_capture_can_seed_one_honest_baseline_observation(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    observation_ledger = (
        tmp_path / QUARANTINE_RELATIVE_PATH / "foundation_observations.jsonl"
    )
    observation_ledger.unlink()

    baseline = ensure_foundation_baseline_observation(tmp_path)
    repeated = ensure_foundation_baseline_observation(tmp_path)

    assert baseline["origin"] == "CAPTURE_RECORD_BASELINE"
    assert baseline["record_hash"] == repeated["record_hash"]
    assert baseline["historical_availability_qualified"] is False
    assert len(observation_ledger.read_text().splitlines()) == 1


def test_cross_vintage_comparison_rejects_empty_foundation_table(tmp_path):
    for start in (START, START + timedelta(days=1)):
        _, session, access = bulk_stack(empty_tables=("actions",))
        execute_ten_year_bulk_capture(
            repository_root=tmp_path,
            api_key=API_KEY,
            session=session,
            access=access,
            clock=clocks(start),
        )
    baseline, candidate = load_verified_foundation_observations(tmp_path)

    with pytest.raises(ValueError, match="nonempty foundation tables"):
        build_foundation_vintage_comparison(
            tmp_path,
            baseline_observation_hash=baseline["record_hash"],
            candidate_observation_hash=candidate["record_hash"],
            synthetic_fixture=True,
        )


def test_foundation_profile_streams_every_row_and_withholds_authority(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = build_foundation_profile(tmp_path, synthetic_fixture=True)

    assert profile["status"] == FOUNDATION_PROFILE_STATUS
    assert profile["schema_version"] == "1.2"
    assert profile["policy_version"] == "sharadar-foundation-structural-profile-v3"
    assert profile["synthetic_fixture"] is True
    assert profile["archive_integrity_verified"] is True
    assert profile["every_row_stream_parsed"] is True
    assert profile["tables"]["stocks"]["row_count"] == 1
    assert profile["tables"]["fundamentals"]["dimension_counts"] == {"ARQ": 1}
    assert profile["tables"]["stocks"]["identity_state_counts"] == {"UNIQUE": 1}
    assert profile["tables"]["fundamentals"]["identity_state_counts"] == {
        "MISSING": 1
    }
    assert profile["tables"]["tickers"]["table_counts"] == {"SEP": 1}
    assert profile["tables"]["tickers"]["ticker_reuse_groups"] == 0
    assert profile["tables"]["tickers"]["permaticker_alias_groups"] == 0
    assert profile["tables"]["tickers"]["ticker_only_join_safe"] is True
    assert profile["tables"]["tickers"]["ticker_only_join_safe_by_table"] == {
        "SEP": True
    }
    assert (
        profile["tables"]["tickers"]["observed_tradable_ticker_only_join_safe"]
        is False
    )
    assert profile["tables"]["tickers"]["observed_tradable_master_tables"] == [
        "SEP"
    ]
    assert profile["tables"]["tickers"]["unobserved_tradable_master_tables"] == [
        "SF1",
        "SFP",
    ]
    assert profile["observed_stock_date_span_days"] == 0
    assert profile["structural_identity_missing_count"] == 1
    assert profile["structural_identity_missing_disposition_counts"] == {
        MISSING_PRESENT_OUTSIDE_REQUIRED_TABLES: 1
    }
    assert profile["tables"]["fundamentals"][
        "missing_identity_disposition_counts"
    ] == {MISSING_PRESENT_OUTSIDE_REQUIRED_TABLES: 1}
    assert profile["tables"]["fundamentals"][
        "rows_by_missing_identity_disposition"
    ] == {MISSING_PRESENT_OUTSIDE_REQUIRED_TABLES: 1}
    assert profile["structural_identity_ambiguous_count"] == 0
    assert profile["structural_identity_gap_count"] == 1
    assert profile["structural_identity_gap_count_basis"] == (
        "SUM_OF_DEPENDENT_TABLE_UNIQUE_TICKER_REFERENCES"
    )
    assert profile["structural_identity_join_ready"] is False
    assert profile["dataset_admitted"] is False
    assert profile["performance_claim_allowed"] is False
    assert profile["validation_access_authorized"] is False
    assert profile["test_access_authorized"] is False
    repeated = build_foundation_profile(tmp_path, synthetic_fixture=True)
    assert repeated["profile_sha256"] == profile["profile_sha256"]


def test_identity_classifier_never_guesses_across_permatickers():
    master = {
        ("SEP", "UNIQUE"): {"1"},
        ("SF1", "UNIQUE"): {"1"},
        ("SEP", "REUSED"): {"2", "3"},
    }

    assert _identity_state(master, "ABSENT", ("SEP", "SF1")) == IDENTITY_MISSING
    assert _identity_state(master, "UNIQUE", ("SEP", "SF1")) == IDENTITY_UNIQUE
    assert _identity_state(master, "REUSED", ("SEP",)) == IDENTITY_AMBIGUOUS


def test_duplicate_master_permatickers_stay_ambiguous_end_to_end(tmp_path):
    _, session, access = bulk_stack(
        additional_rows={
            "tickers": [
                {"table": "SEP", "ticker": "AAPL", "permaticker": "600001"}
            ]
        }
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = build_foundation_profile(tmp_path, synthetic_fixture=True)

    assert profile["tables"]["stocks"]["identity_state_counts"] == {
        "AMBIGUOUS": 1
    }
    assert profile["tables"]["stocks"]["tickers_ambiguous_sep_identity"] == 1
    tickers = profile["tables"]["tickers"]
    assert tickers["ticker_reuse_groups"] == 1
    assert tickers["ticker_reuse_group_counts_by_table"] == {"SEP": 1}
    assert tickers["max_permatickers_per_table_ticker"] == 2
    assert tickers["ticker_only_join_safe"] is False
    assert tickers["ticker_only_join_safe_by_table"] == {"SEP": False}
    assert tickers["observed_tradable_ticker_only_join_safe"] is False
    assert profile["structural_identity_ambiguous_count"] == 3
    assert profile["structural_identity_join_ready"] is False


def test_master_profiles_permaticker_ticker_aliases_end_to_end(tmp_path):
    _, session, access = bulk_stack(
        additional_rows={
            "tickers": [
                {"table": "SEP", "ticker": "AAPL.NEW", "permaticker": "199059"}
            ]
        }
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = build_foundation_profile(tmp_path, synthetic_fixture=True)

    tickers = profile["tables"]["tickers"]
    assert tickers["unique_table_tickers"] == 2
    assert tickers["unique_table_permatickers"] == 1
    assert tickers["permaticker_alias_groups"] == 1
    assert tickers["permaticker_alias_group_counts_by_table"] == {"SEP": 1}
    assert tickers["max_tickers_per_table_permaticker"] == 2
    assert tickers["ticker_reuse_groups"] == 0
    assert tickers["ticker_only_join_safe"] is True
    assert tickers["ticker_only_join_safe_by_table"] == {"SEP": True}
    assert tickers["observed_tradable_ticker_only_join_safe"] is False


def test_all_expected_tradable_master_tables_must_be_observed_for_safe_rollup(
    tmp_path,
):
    _, session, access = bulk_stack(
        additional_rows={
            "tickers": [
                {"table": "SF1", "ticker": "AAPL", "permaticker": "199059"},
                {"table": "SFP", "ticker": "AAPL", "permaticker": "199059"},
            ]
        }
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    tickers = build_foundation_profile(tmp_path, synthetic_fixture=True)["tables"][
        "tickers"
    ]

    assert tickers["observed_tradable_master_tables"] == ["SEP", "SF1", "SFP"]
    assert tickers["unobserved_tradable_master_tables"] == []
    assert tickers["ticker_only_join_safe_by_table"] == {
        "SEP": True,
        "SF1": True,
        "SFP": True,
    }
    assert tickers["observed_tradable_ticker_only_join_safe"] is True


def test_foundation_profile_classifies_unmapped_action_counterparty_without_mapping_it(
    tmp_path,
):
    _, session, access = bulk_stack(
        row_overrides={
            "actions": {
                "ticker": "PRIVATE_TARGET",
                "action": "acquisitionof",
                "contraticker": "AAPL",
            }
        }
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = build_foundation_profile(tmp_path, synthetic_fixture=True)

    actions = profile["tables"]["actions"]
    assert actions["primary_identity_state_counts"] == {"MISSING": 1}
    assert actions["unresolved_primary_action_counts"] == {"acquisitionof": 1}
    assert actions["unresolved_primary_counterparty_state_counts"] == {
        "UNIQUE": 1
    }
    assert actions["missing_primary_disposition_counts"] == {
        MISSING_ABSENT_FROM_MASTER: 1
    }
    assert actions["missing_primary_rows_by_disposition"] == {
        MISSING_ABSENT_FROM_MASTER: 1
    }
    assert actions["structural_identity_join_ready"] is False
    assert profile["structural_identity_missing_count"] == 2
    assert profile["structural_identity_missing_disposition_counts"] == {
        MISSING_ABSENT_FROM_MASTER: 1,
        MISSING_PRESENT_OUTSIDE_REQUIRED_TABLES: 1,
    }
    assert profile["cross_table_identity_complete"] is False


def test_unresolved_action_without_counterparty_is_not_a_missing_identity(tmp_path):
    _, session, access = bulk_stack(
        row_overrides={
            "actions": {
                "ticker": "PRIVATE_TARGET",
                "action": "delisted",
                "contraticker": "",
            }
        }
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = build_foundation_profile(tmp_path, synthetic_fixture=True)

    actions = profile["tables"]["actions"]
    assert actions["primary_identity_state_counts"] == {"MISSING": 1}
    assert actions["unresolved_primary_counterparty_state_counts"] == {
        COUNTERPARTY_NOT_PROVIDED: 1
    }


def test_real_profile_persistence_is_content_addressed_owner_only(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    profile = persist_foundation_profile(tmp_path)
    repeated = persist_foundation_profile(tmp_path)

    assert repeated == profile
    target = (
        tmp_path
        / QUARANTINE_RELATIVE_PATH
        / f"foundation-profile-{profile['profile_sha256']}.json"
    )
    assert stat.S_IMODE(target.stat().st_mode) == 0o400
    assert json.loads(target.read_text())["profile_sha256"] == profile["profile_sha256"]


@pytest.mark.parametrize(
    ("row_overrides", "message"),
    [
        ({"fundamentals": {"dimension": "BAD"}}, "dimension"),
        ({"stocks": {"high": "0"}}, "OHLCV"),
        ({"stocks": {"close": "nan"}}, "numerics"),
        ({"stocks": {"high": "inf"}}, "numerics"),
        ({"stocks": {"closeadj": "Infinity"}}, "numerics"),
        ({"fundamentals": {"datekey": "2020-01-01"}}, "predates"),
        ({"actions": {"contraticker": " BAD "}}, "contra ticker"),
    ],
)
def test_foundation_profile_rejects_semantically_invalid_rows(
    tmp_path, row_overrides, message
):
    _, session, access = bulk_stack(row_overrides=row_overrides)
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )

    with pytest.raises(ValueError, match=message):
        build_foundation_profile(tmp_path, synthetic_fixture=True)


def _rewrite_bulk_ledger(ledger: Path, field: str, value):
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    records[-1][field] = value
    previous_hash = module.GENESIS_HASH
    rewritten = []
    for record in records:
        material = {key: item for key, item in record.items() if key != "record_hash"}
        material["previous_hash"] = previous_hash
        record_hash = hashlib.sha256(module._canonical_json(material)).hexdigest()
        rewritten.append({**material, "record_hash": record_hash})
        previous_hash = record_hash
    ledger.write_bytes(b"".join(module._canonical_json(record) + b"\n" for record in rewritten))
    ledger.chmod(0o600)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("dataset_admitted", True),
        ("csv_header_sha256", "0" * 64),
        ("history", "full"),
    ],
)
def test_verified_bulk_loader_rejects_rechained_unsafe_records(
    tmp_path, field, value
):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    ledger = tmp_path / QUARANTINE_RELATIVE_PATH / "bulk_captures.jsonl"
    _rewrite_bulk_ledger(ledger, field, value)

    with pytest.raises(ValueError):
        load_verified_bulk_captures(tmp_path)


def test_verified_bulk_loader_rejects_loose_mode_and_corrupt_bytes(tmp_path):
    _, session, access = bulk_stack()
    records = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    root = tmp_path / QUARANTINE_RELATIVE_PATH
    archive = root / records[0]["blob_relative_path"]
    archive.chmod(0o444)
    with pytest.raises(ValueError, match="archive failed verification"):
        load_verified_bulk_captures(tmp_path)

    archive.chmod(0o600)
    payload = bytearray(archive.read_bytes())
    payload[len(payload) // 2] ^= 0x01
    archive.write_bytes(payload)
    archive.chmod(0o400)
    with pytest.raises(ValueError, match="archive failed verification"):
        load_verified_bulk_captures(tmp_path)


def test_verified_bulk_loader_selects_newest_capture_per_table(tmp_path):
    _, session, access = bulk_stack()
    first = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    _, refreshed_session, refreshed_access = bulk_stack(
        status_extra={"revision": 2},
        row_overrides={"stocks": {"close": "2", "high": "2"}},
    )
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=refreshed_session,
        access=refreshed_access,
        clock=clocks(),
    )

    loaded = load_verified_bulk_captures(tmp_path)
    ledger = tmp_path / QUARANTINE_RELATIVE_PATH / "bulk_captures.jsonl"

    assert len(ledger.read_text().splitlines()) == 10
    assert all(
        loaded[index]["record_hash"] != first[index]["record_hash"]
        for index in range(len(module.TEN_YEAR_TABLES))
    )
    assert loaded[1]["payload_sha256"] != first[1]["payload_sha256"]


def test_status_size_is_advisory_and_actual_download_length_is_authoritative(tmp_path):
    archives, session, access = bulk_stack(status_delta=123, status_extra={"rows": 1})
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("stocks")
    capture = client.download_ten_year_bulk(
        status=status,
        quarantine_root=tmp_path / "q",
    )

    assert status.size == len(archives["stocks"]) + 123
    assert capture.byte_length == len(archives["stocks"])
    assert capture.status.size != capture.byte_length


def test_bulk_requires_content_length_before_writing(tmp_path):
    _, session, access = bulk_stack(omit_length=True)
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("tickers")

    with pytest.raises(SharadarCaptureError, match="content length is required"):
        client.download_ten_year_bulk(status=status, quarantine_root=tmp_path / "q")
    assert not list((tmp_path / "q").glob("*.partial"))


def test_bulk_stream_errors_are_sanitized_and_response_is_closed(tmp_path):
    _, session, access = bulk_stack(stream_error=True)
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    status = client.fetch_bulk_status("tickers")

    with pytest.raises(SharadarCaptureError, match="stream could not be completed") as caught:
        client.download_ten_year_bulk(status=status, quarantine_root=tmp_path / "q")
    assert "signed-url-secret" not in str(caught.value)
    assert session.responses[-1].closed is True
    assert not list((tmp_path / "q").glob("*.partial"))


@pytest.mark.parametrize(
    "value",
    [
        f"http://{module.OBSERVED_BULK_HOST}/x.zip",
        f"https://user@{module.OBSERVED_BULK_HOST}/x.zip",
        f"https://{module.OBSERVED_BULK_HOST}:8443/x.zip",
        "https://downloads.s3.amazonaws.com/x.zip",
        "https://distribution.cloudfront.net/x.zip",
        "https://evilamazonaws.com/x.zip",
        f"https://{module.OBSERVED_BULK_HOST}.evil.example/x.zip",
    ],
)
def test_redirect_boundary_rejects_downgrade_userinfo_port_and_near_misses(value):
    with pytest.raises(SharadarCaptureError, match="redirect target"):
        SharadarSampleClient._safe_redirect(
            value,
            base_url="https://api.sharadar.com/v1.0/data/stocks",
        )


def test_redirect_boundary_resolves_relative_provider_location():
    resolved, host, path_hash = SharadarSampleClient._safe_redirect(
        "/licensed/stocks.csv.zip?signature=secret",
        base_url="https://api.sharadar.com/v1.0/data/stocks",
    )
    assert resolved.startswith("https://api.sharadar.com/licensed/")
    assert host == "api.sharadar.com"
    assert len(path_hash) == 64
    assert "secret" not in path_hash


def test_bulk_capture_resume_skips_verified_same_status_archives(tmp_path):
    _, session, access = bulk_stack()
    arguments = {
        "repository_root": tmp_path,
        "api_key": API_KEY,
        "session": session,
        "access": access,
        "clock": lambda: START,
    }
    first = execute_ten_year_bulk_capture(**arguments)
    second = execute_ten_year_bulk_capture(**arguments)

    assert [dict(record) for record in second] == [dict(record) for record in first]
    assert len(session.calls) == 5
    assert len(access.calls) == 15
    ledger = tmp_path / QUARANTINE_RELATIVE_PATH / "bulk_captures.jsonl"
    assert len(ledger.read_text().splitlines()) == 5


def test_concurrent_bulk_persistence_serializes_and_deduplicates_ledger(tmp_path):
    _, session, access = bulk_stack()
    client = SharadarSampleClient(
        API_KEY,
        session=session,
        access=access,
        clock=clocks(),
    )
    root = tmp_path / "q"
    status = client.fetch_bulk_status("tickers")
    capture = client.download_ten_year_bulk(status=status, quarantine_root=root)

    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda _: persist_bulk_capture(root, capture), range(2)))

    assert records[0]["record_hash"] == records[1]["record_hash"]
    assert len((root / "bulk_captures.jsonl").read_text().splitlines()) == 1


def test_bulk_ledger_non_object_fails_closed(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=lambda: START,
    )
    ledger = tmp_path / QUARANTINE_RELATIVE_PATH / "bulk_captures.jsonl"
    ledger.write_text("5\n")
    ledger.chmod(0o600)

    with pytest.raises(ValueError, match="objects"):
        execute_ten_year_bulk_capture(
            repository_root=tmp_path,
            api_key=API_KEY,
            session=session,
            access=access,
            clock=lambda: START,
        )


def test_bulk_ledger_hash_tamper_fails_closed(tmp_path):
    _, session, access = bulk_stack()
    execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=lambda: START,
    )
    ledger = tmp_path / QUARANTINE_RELATIVE_PATH / "bulk_captures.jsonl"
    records = [json.loads(line) for line in ledger.read_text().splitlines()]
    records[0]["byte_length"] += 1
    ledger.write_text(
        "\n".join(json.dumps(record, sort_keys=True) for record in records) + "\n"
    )
    ledger.chmod(0o600)

    with pytest.raises(ValueError, match="hash"):
        execute_ten_year_bulk_capture(
            repository_root=tmp_path,
            api_key=API_KEY,
            session=session,
            access=access,
            clock=lambda: START,
        )


def test_missing_resumable_archive_has_sanitized_recovery_failure(tmp_path):
    _, session, access = bulk_stack()
    records = execute_ten_year_bulk_capture(
        repository_root=tmp_path,
        api_key=API_KEY,
        session=session,
        access=access,
        clock=lambda: START,
    )
    root = tmp_path / QUARANTINE_RELATIVE_PATH
    (root / records[0]["blob_relative_path"]).unlink()

    with pytest.raises(ValueError, match="missing; explicit quarantine recovery") as caught:
        execute_ten_year_bulk_capture(
            repository_root=tmp_path,
            api_key=API_KEY,
            session=session,
            access=access,
            clock=lambda: START,
        )
    assert str(root) not in str(caught.value)


def test_private_directory_rejects_symlink_without_chmodding_target(tmp_path):
    victim = tmp_path / "victim"
    victim.mkdir(mode=0o755)
    root = tmp_path / "quarantine"
    root.symlink_to(victim, target_is_directory=True)

    with pytest.raises(ValueError, match="owner-only"):
        persist_probe(root, fetch())
    assert stat.S_IMODE(victim.stat().st_mode) == 0o755


def test_bulk_cli_status_is_explicitly_advisory_and_secret_free(monkeypatch, capsys):
    archives, _, access = bulk_stack()
    statuses = tuple(
        SharadarSampleClient(
            API_KEY,
            session=object(),
            access=access,
            clock=lambda: START,
        ).fetch_bulk_status(table)
        for table in module.TEN_YEAR_TABLES
    )
    monkeypatch.setattr(bulk_script, "load_key", lambda: API_KEY)
    monkeypatch.setattr(
        bulk_script,
        "inspect_ten_year_bulk_status",
        lambda **kwargs: statuses,
    )

    assert bulk_script.main(["--status"]) == 0
    output = capsys.readouterr().out
    assert API_KEY not in output
    assert "X-Amz" not in output
    assert '"disk_preflight_authoritative": false' in output
    assert '"actual_download_requires_content_length_and_double_space_margin": true' in output


def test_bulk_status_dataclass_rejects_unbounded_size_and_wrong_filename():
    required = {
        "table": "tickers",
        "history": "full",
        "name": "tickers.csv.zip",
        "size": 100,
        "modified": "2026-08-20T10:00:00+00:00",
        "payload_sha256": "a" * 64,
        "requested_at": START.isoformat(),
        "retrieved_at": START.isoformat(),
        "provider_access": metadata().as_dict(),
    }
    with pytest.raises(ValueError, match="filename"):
        SharadarBulkStatus(**{**required, "name": "wrong.zip"})
    with pytest.raises(ValueError, match="size"):
        SharadarBulkStatus(**{**required, "size": 2**63})
    with pytest.raises(ValueError, match="history"):
        SharadarBulkStatus(**{**required, "history": "10y"})
