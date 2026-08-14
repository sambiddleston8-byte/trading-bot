from __future__ import annotations

"""Exact long-position quantity/price evidence; no risk decision or order route."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping, Sequence

from core.broker.alpaca_paper import TICKER_PATTERN
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-position-quantity-evidence-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FIXED_FIELDS = {
    "quantity_price_identity_validated": True,
    "evidence_normalized": True,
    "source_payload_stored": False,
    "broker_reconciliation_complete": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "live_trading_enabled": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "quantity_snapshot_id",
        "record_type",
        "status",
        "risk_snapshot_id",
        "risk_snapshot_record_hash",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "observed_at",
        "recorded_at",
        "currency",
        "positions_source_payload_sha256",
        "positions",
        "current_position_gross_exposure_usd",
        "exact_fractions",
        "previous_hash",
        "record_hash",
    }
    | set(FIXED_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete position-quantity evidence append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _amount(value: Any, name: str, *, positive: bool) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an exact decimal string, not a float")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact finite decimal") from error
    if not decimal.is_finite() or decimal < 0 or (positive and decimal == 0):
        qualifier = "positive" if positive else "non-negative"
        raise ValueError(f"{name} must be a finite {qualifier} decimal")
    return Fraction(decimal)


def _fraction(material: Any, name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} denominator must be positive")
    return value


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return "0" if decimal == 0 else format(decimal.normalize(), "f")


def _ticker(value: Any) -> str:
    resolved = str(value or "").strip()
    if TICKER_PATTERN.fullmatch(resolved) is None:
        raise ValueError("ticker must match the Alpaca paper ticker format")
    return resolved


def _quantity_snapshot_id(
    risk_snapshot_id: str,
    risk_snapshot_record_hash: str,
    positions: Sequence[Mapping[str, str]],
) -> str:
    return "PPQTY-" + hashlib.sha256(
        _canonical_json(
            [risk_snapshot_id, risk_snapshot_record_hash, list(positions), POLICY_VERSION]
        ).encode("utf-8")
    ).hexdigest()[:32].upper()


class ProviderPaperPositionQuantityEvidenceLedger:
    """Append-only exact quantity evidence pinned to normalized risk positions."""

    def __init__(self, path: str | Path, risk_snapshot_ledger: Any) -> None:
        self.path = Path(path)
        self.risk_snapshot_ledger = risk_snapshot_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Position-quantity ledger has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank position-quantity line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid position-quantity JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Position-quantity line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record(
        self,
        *,
        risk_snapshot_id: str,
        risk_snapshot_record_hash: str,
        positions: Sequence[Mapping[str, Any]],
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        risk = self._pin(risk_snapshot_id, risk_snapshot_record_hash)
        result = self._build(
            risk, positions, _timestamp(recorded_at or datetime.now(timezone.utc))
        )
        return self._append(result, allow_existing=allow_existing)

    def _pin(self, identity: Any, record_hash: Any) -> Mapping[str, Any]:
        resolved, reasons = resolve_pinned_records(
            self.risk_snapshot_ledger.verify(),
            [identity],
            [record_hash],
            id_field="snapshot_id",
            label="paper risk snapshot",
        )
        if reasons or len(resolved) != 1:
            raise ValueError(
                reasons[0] if reasons else "Paper risk snapshot is not uniquely pinned"
            )
        return resolved[0]

    def _build(
        self,
        risk: Mapping[str, Any],
        position_inputs: Sequence[Mapping[str, Any]],
        recorded: datetime,
    ) -> dict[str, Any]:
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if not isinstance(position_inputs, (list, tuple)):
            raise ValueError("positions must be a sequence")

        risk_positions = risk.get("positions")
        risk_exact = risk.get("exact_fractions")
        if not isinstance(risk_positions, list) or not isinstance(risk_exact, Mapping):
            raise ValueError("Pinned risk position evidence is invalid")
        risk_position_fractions = risk_exact.get("positions")
        if not isinstance(risk_position_fractions, list) or len(risk_position_fractions) != len(
            risk_positions
        ):
            raise ValueError("Pinned risk position fractions are invalid")
        expected_values: dict[str, Fraction] = {}
        for raw, exact in zip(risk_positions, risk_position_fractions):
            ticker = _ticker(raw.get("ticker"))
            value = _fraction(exact.get("long_market_value_usd"), "risk market value")
            if value <= 0:
                raise ValueError("Pinned zero-value positions cannot prove positive quantity")
            expected_values[ticker] = value

        parsed: list[tuple[str, Fraction, Fraction, Fraction]] = []
        for entry in position_inputs:
            if not isinstance(entry, Mapping) or set(entry) != {
                "ticker",
                "long_quantity",
                "mark_price_usd",
            }:
                raise ValueError("Each quantity position must have exactly three fields")
            ticker = _ticker(entry.get("ticker"))
            quantity = _amount(entry.get("long_quantity"), "long_quantity", positive=True)
            mark_price = _amount(entry.get("mark_price_usd"), "mark_price_usd", positive=True)
            parsed.append((ticker, quantity, mark_price, quantity * mark_price))
        tickers = [ticker for ticker, _, _, _ in parsed]
        if len(tickers) != len(set(tickers)):
            raise ValueError("Quantity positions must have unique tickers")
        if set(tickers) != set(expected_values):
            raise ValueError("Quantity positions must exactly cover pinned risk positions")
        parsed.sort(key=lambda item: item[0])
        for ticker, _, _, market_value in parsed:
            if market_value != expected_values[ticker]:
                raise ValueError(
                    f"{ticker} quantity multiplied by mark price must equal pinned market value"
                )

        normalized = [
            {
                "ticker": ticker,
                "long_quantity": _decimal(quantity),
                "mark_price_usd": _decimal(mark_price),
                "long_market_value_usd": _decimal(market_value),
            }
            for ticker, quantity, mark_price, market_value in parsed
        ]
        exact_positions = [
            {
                "ticker": ticker,
                "long_quantity": _fraction_material(quantity),
                "mark_price_usd": _fraction_material(mark_price),
                "long_market_value_usd": _fraction_material(market_value),
            }
            for ticker, quantity, mark_price, market_value in parsed
        ]
        total = sum((value for _, _, _, value in parsed), Fraction(0))
        pinned_total = _fraction(
            risk_exact.get("current_position_gross_exposure_usd"),
            "pinned gross position exposure",
        )
        if total != pinned_total:
            raise ValueError("Quantity position total must equal pinned gross position exposure")

        identity = _quantity_snapshot_id(
            risk["snapshot_id"], risk["record_hash"], normalized
        )
        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "quantity_snapshot_id": identity,
            "record_type": "PROVIDER_PAPER_POSITION_QUANTITY_EVIDENCE",
            "status": "OBSERVED",
            "risk_snapshot_id": risk["snapshot_id"],
            "risk_snapshot_record_hash": risk["record_hash"],
            "broker": risk["broker"],
            "broker_environment": risk["broker_environment"],
            "account_reference_sha256": risk["account_reference_sha256"],
            "observed_at": risk["observed_at"],
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            "positions_source_payload_sha256": risk["positions_source_payload_sha256"],
            "positions": normalized,
            "current_position_gross_exposure_usd": _decimal(total),
            "exact_fractions": {
                "positions": exact_positions,
                "current_position_gross_exposure_usd": _fraction_material(total),
            },
            **FIXED_FIELDS,
        }

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        previous_time: dict[str, datetime] = {}
        seen: set[str] = set()
        risk_records = self.risk_snapshot_ledger.verify()
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Position-quantity record {index} has been modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("record field alphabet is invalid")
                resolved, reasons = resolve_pinned_records(
                    risk_records,
                    [record.get("risk_snapshot_id")],
                    [record.get("risk_snapshot_record_hash")],
                    id_field="snapshot_id",
                    label="paper risk snapshot",
                )
                if reasons or len(resolved) != 1:
                    raise ValueError(reasons[0] if reasons else "Pinned snapshot is unavailable")
                inputs = []
                raw_positions = record.get("positions")
                if not isinstance(raw_positions, list):
                    raise ValueError("positions are invalid")
                for item in raw_positions:
                    if not isinstance(item, Mapping) or set(item) != {
                        "ticker",
                        "long_quantity",
                        "mark_price_usd",
                        "long_market_value_usd",
                    }:
                        raise ValueError("position field alphabet is invalid")
                    inputs.append(
                        {
                            "ticker": item["ticker"],
                            "long_quantity": item["long_quantity"],
                            "mark_price_usd": item["mark_price_usd"],
                        }
                    )
                expected = self._build(
                    resolved[0], inputs, _timestamp(record.get("recorded_at"))
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("record does not match exact quantity evidence")
                account = record["account_reference_sha256"]
                observed = _timestamp(record["observed_at"])
                if account in previous_time and observed <= previous_time[account]:
                    raise ValueError("observed_at does not move forward")
                if record["quantity_snapshot_id"] in seen:
                    raise ValueError("quantity snapshot identity is duplicated")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Position-quantity record {index} violates its boundary."
                ) from error
            previous_time[account] = observed
            seen.add(record["quantity_snapshot_id"])
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["quantity_snapshot_id"] == result["quantity_snapshot_id"]
                ),
                None,
            )
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {
                    key: value for key, value in existing.items() if key not in ignored
                } == {key: value for key, value in result.items() if key not in ignored}:
                    return existing
                raise LedgerIntegrityError(
                    f"Quantity snapshot {result['quantity_snapshot_id']} already exists."
                )
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior["observed_at"]
            ):
                raise ValueError("observed_at must move forward for each account")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
