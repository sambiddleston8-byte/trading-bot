import json
from pathlib import Path

import pytest
import scripts.capture_massive_stage2 as capture_script

from core.orchestration.stage2_bounded_capture import (
    _validate, authorization_record, execute_capture, register_authorization, request_plan,
)


def bar_payload(symbol, start):
    import datetime
    stamp = int(datetime.datetime.fromisoformat(start + "T00:00:00+00:00").timestamp() * 1000)
    return json.dumps({"ticker": symbol, "adjusted": False, "status": "OK", "results": [
        {"o": 100, "h": 102, "l": 99, "c": 101, "v": 1000, "t": stamp}
    ]}).encode()


class Response:
    status_code = 200
    headers = {"content-type": "application/json"}
    def __init__(self, payload): self.content = payload


class Session:
    def __init__(self): self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        params = kwargs["params"]
        if "/v2/aggs/" in url:
            symbol = url.split("/ticker/")[1].split("/")[0]
            start = url.split("/day/")[1].split("/")[0]
            return Response(bar_payload(symbol, start))
        symbol = params["ticker"]
        return Response(json.dumps({"status": "OK", "results": []}).encode())


def test_authorization_is_exact_bounded_and_false_downstream():
    record = authorization_record()
    assert record["provider_use_authorized"] is True
    assert record["symbols"] == ["AAPL", "MSFT", "SPY"]
    assert record["capture_roles"] == ["TRAIN", "VALIDATION"]
    assert record["sealed_role"] == "UNTOUCHED_TEST"
    assert record["authorized_capture_window"]["end"] == "2025-04-30"
    assert all(record[name] is False for name in (
        "dataset_admission_allowed", "evaluation_allowed", "broker_connection_allowed",
        "orders_allowed", "live_trading_allowed",
    ))


def test_plan_is_exact_21_bar_and_6_corporate_action_requests():
    plan = request_plan()
    assert len(plan) == 27
    assert sum(item["dataset"] == "DAILY_BARS" for item in plan) == 21
    assert all(item["role"] != "UNTOUCHED_TEST" for item in plan)
    assert all(item["end"] <= "2025-04-30" for item in plan)


def test_offline_runner_quarantines_and_reports_without_admission(tmp_path: Path):
    session, waits = Session(), []
    report = execute_capture(repository_root=tmp_path, api_key="synthetic-key", session=session, sleeper=waits.append)
    assert report["status"] == "TRAIN_VALIDATION_CAPTURE_COMPLETE"
    assert report["request_count"] == 27
    assert report["dataset_admitted"] is False
    assert report["evaluation_allowed"] is False
    assert len(session.calls) == 27 and waits == [12.0] * 26
    assert all(call[1]["allow_redirects"] is False for call in session.calls)
    assert all(call[1]["stream"] is True for call in session.calls)
    assert all(call[1]["headers"]["Authorization"] == "Bearer synthetic-key" for call in session.calls)
    assert register_authorization(tmp_path) == authorization_record()
    serialized = json.dumps(report)
    assert "synthetic-key" not in serialized
    manifest = (tmp_path / "data/research/massive_campaign_v2_revision_2/stage2/quarantine/captures.jsonl").read_text()
    assert "synthetic-key" not in manifest
    assert len(manifest.splitlines()) == 27


def test_runner_rejects_redirect_errors_and_never_writes_report(tmp_path: Path):
    session = Session()
    def bad(url, **kwargs):
        response = Response(b"redirect"); response.status_code = 302
        return response
    session.get = bad
    with pytest.raises(RuntimeError, match="rejected"):
        execute_capture(repository_root=tmp_path, api_key="synthetic-key", session=session, sleeper=lambda _: None)
    assert not (tmp_path / "data/research/massive_campaign_v2_revision_2/stage2/completeness_report.json").exists()
    manifest = tmp_path / "data/research/massive_campaign_v2_revision_2/stage2/quarantine/captures.jsonl"
    assert not manifest.exists()


def test_bar_identity_adjustment_pagination_and_bounds_fail_closed(tmp_path: Path):
    session = Session()
    original = session.get
    def wrong(url, **kwargs):
        response = original(url, **kwargs)
        if "/v2/aggs/" in url:
            body = json.loads(response.content)
            body["ticker"] = "MSFT" if body["ticker"] == "AAPL" else "AAPL"
            body["next_url"] = "https://api.massive.com/page/2"
            response.content = json.dumps(body).encode()
        return response
    session.get = wrong
    with pytest.raises(ValueError, match="identity"):
        execute_capture(repository_root=tmp_path, api_key="synthetic-key", session=session, sleeper=lambda _: None)


@pytest.mark.parametrize("change", [
    {"adjusted": True},
    {"next_url": "https://api.massive.com/page/2"},
])
def test_bar_adjustment_and_pagination_each_fail_closed(change):
    request = next(item for item in request_plan() if item["dataset"] == "DAILY_BARS")
    root = json.loads(bar_payload(request["symbol"], request["start"])); root.update(change)
    with pytest.raises(ValueError): _validate(json.dumps(root).encode(), request)


def test_bar_outside_slice_fails_closed():
    request = next(item for item in request_plan() if item["dataset"] == "DAILY_BARS")
    with pytest.raises(ValueError, match="bounds"):
        _validate(bar_payload(request["symbol"], "2024-09-30"), request)


def test_populated_corporate_actions_validate_and_unsafe_variants_fail_closed():
    dividend_request = next(item for item in request_plan() if item["dataset"] == "DIVIDENDS")
    valid = {"id": "d1", "ticker": "AAPL", "cash_amount": 0.25, "currency": "USD",
        "declaration_date": "2025-01-02", "ex_dividend_date": "2025-01-10"}
    assert _validate(json.dumps({"status": "OK", "results": [valid]}).encode(), dividend_request)["record_count"] == 1
    for change in (
        {"ex_dividend_date": "2025-05-01"}, {"cash_amount": True},
        {"currency": "GBP"}, {"declaration_date": "2025-01-11"},
    ):
        row = {**valid, **change}
        with pytest.raises(ValueError): _validate(json.dumps({"status": "OK", "results": [row]}).encode(), dividend_request)
    with pytest.raises(ValueError): _validate(json.dumps({"status": "OK", "results": [valid, valid]}).encode(), dividend_request)
    with pytest.raises(ValueError): _validate(json.dumps({"status": "OK", "results": [valid], "next_url": "https://api.massive.com/page/2"}).encode(), dividend_request)
    split_request = next(item for item in request_plan() if item["dataset"] == "STOCK_SPLITS")
    split = {"id": "s1", "ticker": "AAPL", "execution_date": "2025-01-10", "split_from": 1, "split_to": 4}
    assert _validate(json.dumps({"status": "OK", "results": [split]}).encode(), split_request)["record_count"] == 1
    with pytest.raises(ValueError): _validate(json.dumps({"status": "OK", "results": [{**split, "split_to": True}]}).encode(), split_request)


def test_partial_failure_retains_attributed_manifest(tmp_path: Path):
    session, count = Session(), {"value": 0}
    original = session.get
    def second_fails(url, **kwargs):
        count["value"] += 1
        if count["value"] == 2:
            response = Response(b"failure"); response.status_code = 503
            return response
        return original(url, **kwargs)
    session.get = second_fails
    with pytest.raises(RuntimeError):
        execute_capture(repository_root=tmp_path, api_key="synthetic-key", session=session, sleeper=lambda _: None)
    manifest = tmp_path / "data/research/massive_campaign_v2_revision_2/stage2/quarantine/captures.jsonl"
    rows = [json.loads(line) for line in manifest.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["symbol"] == "AAPL" and rows[0]["dataset"] == "DAILY_BARS"


def test_cli_deletes_temporary_key_after_failure(tmp_path: Path, monkeypatch):
    key = tmp_path / "key"; key.write_text("synthetic-key\n"); key.chmod(0o600)
    monkeypatch.setattr(capture_script, "KEY_PATH", key)
    monkeypatch.setattr(capture_script, "execute_capture", lambda **_: (_ for _ in ()).throw(RuntimeError("synthetic")))
    assert capture_script.main() == 1
    assert not key.exists()
