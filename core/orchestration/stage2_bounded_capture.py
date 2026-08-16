"""Bounded Revision-2 TRAIN/VALIDATION capture into owner-only quarantine."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import stat
import time
from typing import Any, Callable, Mapping

import requests

from core.orchestration.massive_historical_adapter import parse_massive_unadjusted_daily_bars
from core.research.campaign_v2_revision_2_registered_chain import PREREGISTRATION_ID, PROPOSAL_SHA256


SYMBOLS = ("AAPL", "MSFT", "SPY")
SPLITS = (
    ("TRAIN", "2024-10-01", "2025-02-28"),
    ("VALIDATION", "2025-03-01", "2025-04-30"),
)
DATASETS = ("DAILY_BARS", "DIVIDENDS", "STOCK_SPLITS")
BASE_URL = "https://api.massive.com"
AUTHORIZATION_ID = "CV2R2-BOUNDED-CAPTURE-20260816"
AUTHORIZATION_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/stage2/operator_authorization.json")
QUARANTINE_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/stage2/quarantine")
REPORT_RELATIVE_PATH = Path("data/research/massive_campaign_v2_revision_2/stage2/completeness_report.json")


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def authorization_record() -> dict[str, Any]:
    material = {
        "schema_version": "1.0", "authorization_id": AUTHORIZATION_ID,
        "operator": "Sam", "authorization_basis": "EXPLICIT_OPERATOR_APPROVAL_IN_CODEX_TASK",
        "proposal_sha256": PROPOSAL_SHA256, "preregistration_id": PREREGISTRATION_ID,
        "symbols": list(SYMBOLS),
        "registered_campaign_window": {"start": "2024-10-01", "end": "2025-07-31"},
        "authorized_capture_window": {"start": "2024-10-01", "end": "2025-04-30"},
        "capture_roles": ["TRAIN", "VALIDATION"], "sealed_role": "UNTOUCHED_TEST",
        "authorized_request_plan_sha256": hashlib.sha256(_canonical(request_plan())).hexdigest(),
        "datasets": list(DATASETS), "unadjusted_daily_bars": True,
        "quarantine_only": True, "provider_use_authorized": True,
        "dataset_admission_allowed": False, "evaluation_allowed": False,
        "broker_connection_allowed": False, "orders_allowed": False, "live_trading_allowed": False,
    }
    return {**material, "record_sha256": hashlib.sha256(_canonical(material)).hexdigest()}


def register_authorization(repository_root: Path) -> dict[str, Any]:
    target = repository_root / AUTHORIZATION_RELATIVE_PATH
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    encoded = _canonical(authorization_record()) + b"\n"
    if target.exists():
        if target.read_bytes() != encoded:
            raise ValueError("operator authorization record conflicts")
        return authorization_record()
    descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, encoded); os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return authorization_record()


def request_plan() -> tuple[dict[str, Any], ...]:
    if SPLITS != (("TRAIN", "2024-10-01", "2025-02-28"), ("VALIDATION", "2025-03-01", "2025-04-30")):
        raise ValueError("capture splits differ from the exact authorization")
    plan: list[dict[str, Any]] = []
    for symbol in SYMBOLS:
        for role, start, end in SPLITS:
            cursor, finish = date.fromisoformat(start), date.fromisoformat(end)
            while cursor <= finish:
                through = min(finish, cursor + timedelta(days=30))
                plan.append({"dataset": "DAILY_BARS", "symbol": symbol, "role": role,
                    "start": cursor.isoformat(), "end": through.isoformat(),
                    "path": f"/v2/aggs/ticker/{symbol}/range/1/day/{cursor.isoformat()}/{through.isoformat()}",
                    "params": {"adjusted": "false", "sort": "asc", "limit": "120"}})
                cursor = through + timedelta(days=1)
        for dataset, path, field in (
            ("DIVIDENDS", "/stocks/v1/dividends", "ex_dividend_date"),
            ("STOCK_SPLITS", "/stocks/v1/splits", "execution_date"),
        ):
            plan.append({"dataset": dataset, "symbol": symbol, "role": "TRAIN_VALIDATION",
                "start": "2024-10-01", "end": "2025-04-30", "path": path,
                "params": {"ticker": symbol, f"{field}.gte": "2024-10-01", f"{field}.lte": "2025-04-30", "sort": f"{field}.asc", "limit": "5000"}})
    if any(item["start"] < "2024-10-01" or item["end"] > "2025-04-30" for item in plan):
        raise ValueError("request plan escapes the authorized capture window")
    return tuple(plan)


def _validate(payload: bytes, request: Mapping[str, Any]) -> dict[str, Any]:
    if len(payload) > 5 * 1024 * 1024:
        raise ValueError("provider payload exceeds 5 MiB")
    if request["dataset"] == "DAILY_BARS":
        root = json.loads(payload)
        if (not isinstance(root, dict) or root.get("ticker") != request["symbol"]
                or root.get("adjusted") is not False or root.get("status") != "OK"
                or root.get("next_url") is not None):
            raise ValueError("daily-bar response identity, adjustment, status or pagination failed")
        bars = parse_massive_unadjusted_daily_bars(payload)
        dates = [item["window_start"].date().isoformat() for item in bars]
        if not dates or dates != sorted(set(dates)) or any(not request["start"] <= value <= request["end"] for value in dates):
            raise ValueError("daily-bar chronology or request bounds failed")
        return {"record_count": len(bars), "first_date": dates[0], "last_date": dates[-1]}
    root = json.loads(payload)
    if not isinstance(root, dict) or root.get("status") != "OK" or root.get("next_url"):
        raise ValueError("corporate-action response is invalid or paginated")
    rows = root.get("results")
    if not isinstance(rows, list) or len(rows) > 5000:
        raise ValueError("corporate-action results are invalid")
    date_field = "ex_dividend_date" if request["dataset"] == "DIVIDENDS" else "execution_date"
    dates, identities = [], set()
    for row in rows:
        if not isinstance(row, dict) or row.get("ticker") != request["symbol"] or not isinstance(row.get("id"), str):
            raise ValueError("corporate-action schema or symbol failed")
        identity = row["id"]
        if identity in identities:
            raise ValueError("corporate-action identity is duplicated")
        identities.add(identity)
        event_date = date.fromisoformat(row[date_field]).isoformat()
        if not request["start"] <= event_date <= request["end"]:
            raise ValueError("corporate action is outside request bounds")
        if request["dataset"] == "DIVIDENDS":
            if row.get("currency") != "USD" or isinstance(row.get("cash_amount"), bool) or not isinstance(row.get("cash_amount"), (int, float)) or row["cash_amount"] <= 0:
                raise ValueError("dividend economics failed")
            declaration = row.get("declaration_date")
            if declaration is not None and date.fromisoformat(declaration) > date.fromisoformat(event_date):
                raise ValueError("dividend chronology failed")
        else:
            if any(isinstance(row.get(name), bool) or not isinstance(row.get(name), (int, float)) or row[name] <= 0 for name in ("split_from", "split_to")):
                raise ValueError("split economics failed")
        dates.append(event_date)
    if dates != sorted(dates):
        raise ValueError("corporate actions are not chronological")
    return {"record_count": len(rows), "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
        "point_in_time_availability": "UNRESOLVED_PROVIDER_RESPONSE_HAS_NO_REPORTED_AT"}


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(descriptor, payload[offset:])
        if written <= 0: raise OSError("quarantine write made no progress")
        offset += written


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    details = path.lstat()
    if path.is_symlink() or not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid() or stat.S_IMODE(details.st_mode) != 0o700:
        raise ValueError("quarantine directory is unsafe")


def _response_bytes(response: Any) -> bytes:
    if hasattr(response, "iter_content"):
        chunks, total = [], 0
        for chunk in response.iter_content(64 * 1024):
            if not isinstance(chunk, bytes): raise ValueError("provider response chunk is invalid")
            total += len(chunk)
            if total > 5 * 1024 * 1024: raise ValueError("provider payload exceeds 5 MiB")
            chunks.append(chunk)
        return b"".join(chunks)
    return response.content


def execute_capture(*, repository_root: Path, api_key: str, session: Any | None = None,
                    sleeper: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    authorization = register_authorization(repository_root)
    if authorization["provider_use_authorized"] is not True:
        raise ValueError("provider use is not authorized")
    if not api_key or api_key.strip() != api_key:
        raise ValueError("API key is invalid")
    target = repository_root / QUARANTINE_RELATIVE_PATH
    _secure_directory(target)
    manifest_path = target / "captures.jsonl"
    http = session or requests.Session()
    captures = []
    existing: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    if manifest_path.exists():
        for line in manifest_path.read_bytes().splitlines():
            row = json.loads(line)
            identity = tuple(row[name] for name in ("dataset", "symbol", "role", "start", "end"))
            if identity in existing: raise ValueError("quarantine manifest repeats a request identity")
            blob = target / f"{row['payload_sha256']}.json"
            if not blob.exists() or hashlib.sha256(blob.read_bytes()).hexdigest() != row["payload_sha256"]:
                raise ValueError("existing manifest blob failed verification")
            existing[identity] = row
    for index, request in enumerate(request_plan()):
        identity = tuple(request[name] for name in ("dataset", "symbol", "role", "start", "end"))
        if identity in existing:
            captures.append(existing[identity]); continue
        if index: sleeper(12.0)
        response = http.get(BASE_URL + request["path"], params=request["params"],
            headers={"Authorization": f"Bearer {api_key}"}, timeout=30, allow_redirects=False, stream=True)
        if response.status_code != 200 or response.headers.get("content-type", "").split(";", 1)[0].lower() != "application/json":
            raise RuntimeError(f"Massive rejected bounded {request['dataset']} request")
        payload = _response_bytes(response)
        validation = _validate(payload, request)
        digest = hashlib.sha256(payload).hexdigest()
        blob = target / f"{digest}.json"
        if blob.exists():
            if hashlib.sha256(blob.read_bytes()).hexdigest() != digest:
                raise ValueError("existing quarantine blob failed hash verification")
        else:
            descriptor = os.open(blob, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try: _write_all(descriptor, payload); os.fsync(descriptor)
            finally: os.close(descriptor)
        capture = {**{k: request[k] for k in ("dataset", "symbol", "role", "start", "end", "path")},
            **validation, "payload_sha256": digest, "byte_length": len(payload), "quarantine_only": True}
        captures.append(capture)
        descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try: _write_all(descriptor, _canonical(capture) + b"\n"); os.fsync(descriptor)
        finally: os.close(descriptor)
    report = {"authorization_id": AUTHORIZATION_ID, "authorization_record_sha256": authorization["record_sha256"],
        "status": "TRAIN_VALIDATION_CAPTURE_COMPLETE", "request_count": len(captures), "captures": captures,
        "by_role": {role: {symbol: sum(item["record_count"] for item in captures if item["dataset"] == "DAILY_BARS" and item["role"] == role and item["symbol"] == symbol) for symbol in SYMBOLS} for role, _, _ in SPLITS},
        "corporate_actions": {dataset: {symbol: sum(item["record_count"] for item in captures if item["dataset"] == dataset and item["symbol"] == symbol) for symbol in SYMBOLS} for dataset in ("DIVIDENDS", "STOCK_SPLITS")},
        "corporate_action_point_in_time_availability": "UNRESOLVED", "dataset_admitted": False, "evaluation_allowed": False}
    report_path = repository_root / REPORT_RELATIVE_PATH
    descriptor = os.open(report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try: _write_all(descriptor, _canonical(report) + b"\n"); os.fsync(descriptor)
    finally: os.close(descriptor)
    return report
