from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
from pathlib import Path
import stat
import zipfile

import pytest

import core.orchestration.sharadar_quarantine as module
import scripts._sharadar_keychain as keychain
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
    inspect_ten_year_bulk_status,
    persist_bulk_capture,
    persist_probe,
    validate_probe_csv,
)


API_KEY = "synthetic-secret-key-that-must-not-leak"
UTC = timezone.utc
START = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def csv_payload(definition: SharadarProbeDefinition) -> bytes:
    values = {
        "table": "SF1",
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

    def iter_content(self, chunk_size):
        for offset in range(0, len(self.content), chunk_size):
            yield self.content[offset : offset + chunk_size]


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


def clocks():
    values = iter(START + timedelta(seconds=index) for index in range(20))
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
    blob.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash verification"):
        persist_probe(clean, probe)


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


def bulk_archive(table: str, *, extra_member: bool = False) -> bytes:
    stream = io.BytesIO()
    fields = sorted(BULK_REQUIRED_FIELDS[table])
    values = {name: "1" for name in fields}
    values.update(
        {
            "ticker": "AAPL",
            "dimension": "ARQ",
            "date": "2022-01-04",
            "calendardate": "2021-12-31",
            "reportperiod": "2021-12-25",
            "lastupdated": "2022-01-28",
            "firstpricedate": "1980-12-12",
            "lastpricedate": "2026-08-19",
        }
    )
    content = (
        ",".join(fields) + "\n" + ",".join(values[name] for name in fields) + "\n"
    ).encode()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{table}.csv", content)
        if extra_member:
            archive.writestr("unexpected.csv", content)
    return stream.getvalue()


class BulkAccess:
    def __init__(self, archives, *, redirect_host="downloads.s3.amazonaws.com"):
        self.archives = archives
        self.redirect_host = redirect_host
        self.calls = []

    def get(self, session, url, **kwargs):
        self.calls.append((session, url, kwargs))
        table = url.rsplit("/", 1)[-1]
        if kwargs["params"].get("status") == "True":
            payload = json.dumps(
                {
                    "table": table,
                    "name": f"{table}.csv.zip",
                    "size": len(self.archives[table]),
                    "sizeLabel": f"{len(self.archives[table])} B",
                    "modified": "2026-08-20T10:00:00+00:00",
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
    def __init__(self, archives, *, length_delta=0):
        self.archives = archives
        self.length_delta = length_delta
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        table = url.split("/licensed/", 1)[1].split(".csv.zip", 1)[0]
        payload = self.archives[table]
        response = Response(payload, content_type="application/zip")
        response.headers["Content-Length"] = str(len(payload) + self.length_delta)
        return response


def bulk_stack(
    *,
    extra_member=False,
    redirect_host="downloads.s3.amazonaws.com",
    length_delta=0,
):
    archives = {
        table: bulk_archive(table, extra_member=extra_member)
        for table in module.TEN_YEAR_TABLES
    }
    return (
        archives,
        BulkSession(archives, length_delta=length_delta),
        BulkAccess(archives, redirect_host=redirect_host),
    )


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
    assert API_KEY not in json.dumps([item.as_dict() for item in statuses])
    assert all(call[2]["params"]["api_key"] == API_KEY for call in access.calls)


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
    assert capture.redirect_host == "downloads.s3.amazonaws.com"
    assert "Signature" not in json.dumps(dict(record))
    assert API_KEY not in json.dumps(dict(record))
    assert record["dataset_admitted"] is False
    assert record["validation_opened"] is False
    archive = root / record["blob_relative_path"]
    assert stat.S_IMODE(archive.stat().st_mode) == 0o400
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == record["payload_sha256"]
    assert session.calls[0][1]["stream"] is True


@pytest.mark.parametrize(
    ("redirect_host", "length_delta", "extra_member", "message"),
    [
        ("127.0.0.1", 0, False, "redirect target"),
        ("downloads.s3.amazonaws.com", 1, False, "content length"),
        ("downloads.s3.amazonaws.com", 0, True, "exactly one"),
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


def test_bulk_status_dataclass_rejects_oversize_and_wrong_filename():
    required = {
        "table": "tickers",
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
        SharadarBulkStatus(
            **{**required, "size": module.MAX_COMPRESSED_BYTES["tickers"] + 1}
        )
