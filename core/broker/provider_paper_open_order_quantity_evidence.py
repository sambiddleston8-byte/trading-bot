from __future__ import annotations

"""Exact open-order quantity/price evidence; no risk decision or order route."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from core.broker.alpaca_paper import TICKER_PATTERN
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.pinned_support import resolve_pinned_records


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-open-order-quantity-evidence-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIDES = frozenset({"BUY", "SELL"})
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
        "order_quantity_snapshot_id",
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
        "open_orders_source_payload_sha256",
        "open_orders",
        "pending_buy_remaining_notional_usd",
        "pending_sell_remaining_notional_usd",
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
            raise OSError("Unable to complete open-order quantity evidence append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if SHA256_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _amount(value: Any, name: str) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be an exact decimal string, not a float")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an exact finite decimal") from error
    if not decimal.is_finite() or decimal <= 0:
        raise ValueError(f"{name} must be a finite positive decimal")
    return Fraction(decimal)


def _fraction(material: Any, name: str) -> Fraction:
    if not isinstance(material, Mapping) or set(material) != {"numerator", "denominator"}:
        raise ValueError(f"{name} exact fraction has an invalid field alphabet")
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (TypeError, ValueError, ZeroDivisionError) as error:
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


def _identity(
    risk_snapshot_id: str,
    risk_snapshot_record_hash: str,
    orders: Sequence[Mapping[str, str]],
) -> str:
    material = [risk_snapshot_id, risk_snapshot_record_hash, list(orders), POLICY_VERSION]
    return "PPOQTY-" + hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()


class ProviderPaperOpenOrderQuantityEvidenceLedger:
    """Append-only exact remaining-quantity evidence pinned to risk open orders."""

    def __init__(self, path: str | Path, risk_snapshot_ledger: Any) -> None:
        self.path = Path(path)
        self.risk_snapshot_ledger = risk_snapshot_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Open-order quantity ledger has an incomplete final line.")
        records: list[dict[str, Any]] = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank open-order quantity line at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid open-order quantity JSON at line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Open-order quantity line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record(
        self,
        *,
        risk_snapshot_id: str,
        risk_snapshot_record_hash: str,
        open_orders: Sequence[Mapping[str, Any]],
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        risk = self._pin(risk_snapshot_id, risk_snapshot_record_hash)
        result = self._build(
            risk, open_orders, _timestamp(recorded_at or datetime.now(timezone.utc))
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
        order_inputs: Sequence[Mapping[str, Any]],
        recorded: datetime,
    ) -> dict[str, Any]:
        observed = _timestamp(risk.get("observed_at"))
        if recorded < observed:
            raise ValueError("recorded_at cannot be before the pinned observation")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if not isinstance(order_inputs, (list, tuple)):
            raise ValueError("open_orders must be a sequence")

        risk_orders = risk.get("open_orders")
        risk_exact = risk.get("exact_fractions")
        if not isinstance(risk_orders, list) or not isinstance(risk_exact, Mapping):
            raise ValueError("Pinned risk open-order evidence is invalid")
        risk_order_fractions = risk_exact.get("open_orders")
        if not isinstance(risk_order_fractions, list) or len(risk_order_fractions) != len(
            risk_orders
        ):
            raise ValueError("Pinned risk open-order fractions are invalid")

        expected: dict[str, tuple[str, str, Fraction]] = {}
        for raw, exact in zip(risk_orders, risk_order_fractions):
            if not isinstance(raw, Mapping) or not isinstance(exact, Mapping):
                raise ValueError("Pinned risk open-order entries are invalid")
            order_hash = _sha256(raw.get("order_reference_sha256"), "order_reference_sha256")
            exact_hash = _sha256(exact.get("order_reference_sha256"), "exact order_reference_sha256")
            if exact_hash != order_hash or order_hash in expected:
                raise ValueError("Pinned risk open-order identity is invalid")
            ticker = _ticker(raw.get("ticker"))
            side = raw.get("side")
            if side not in SIDES:
                raise ValueError("Pinned risk open-order side is invalid")
            notional = _fraction(exact.get("remaining_notional_usd"), "risk remaining notional")
            if notional <= 0 or raw.get("remaining_notional_usd") != _decimal(notional):
                raise ValueError("Pinned risk remaining notional is invalid")
            expected[order_hash] = (ticker, side, notional)

        parsed: list[tuple[str, str, str, Fraction, Fraction, Fraction]] = []
        for entry in order_inputs:
            if not isinstance(entry, Mapping) or set(entry) != {
                "order_reference_sha256",
                "ticker",
                "side",
                "remaining_quantity",
                "risk_mark_price_usd",
            }:
                raise ValueError("Each open-order quantity must have exactly five fields")
            order_hash = _sha256(entry.get("order_reference_sha256"), "order_reference_sha256")
            ticker = _ticker(entry.get("ticker"))
            side = entry.get("side")
            if side not in SIDES:
                raise ValueError("side must be BUY or SELL")
            quantity = _amount(entry.get("remaining_quantity"), "remaining_quantity")
            price = _amount(entry.get("risk_mark_price_usd"), "risk_mark_price_usd")
            parsed.append((order_hash, ticker, side, quantity, price, quantity * price))

        hashes = [item[0] for item in parsed]
        if len(hashes) != len(set(hashes)):
            raise ValueError("Open-order quantities must have unique order references")
        if set(hashes) != set(expected):
            raise ValueError("Open-order quantities must exactly cover pinned risk open orders")
        parsed.sort(key=lambda item: (item[1], item[2], item[0]))
        for order_hash, ticker, side, _, _, notional in parsed:
            expected_ticker, expected_side, expected_notional = expected[order_hash]
            if ticker != expected_ticker or side != expected_side:
                raise ValueError("Open-order ticker and side must match pinned risk evidence")
            if notional != expected_notional:
                raise ValueError(
                    "Remaining quantity multiplied by risk mark price must equal pinned notional"
                )

        normalized = [
            {
                "order_reference_sha256": order_hash,
                "ticker": ticker,
                "side": side,
                "remaining_quantity": _decimal(quantity),
                "risk_mark_price_usd": _decimal(price),
                "remaining_notional_usd": _decimal(notional),
            }
            for order_hash, ticker, side, quantity, price, notional in parsed
        ]
        exact_orders = [
            {
                "order_reference_sha256": order_hash,
                "remaining_quantity": _fraction_material(quantity),
                "risk_mark_price_usd": _fraction_material(price),
                "remaining_notional_usd": _fraction_material(notional),
            }
            for order_hash, _, _, quantity, price, notional in parsed
        ]
        pending_buy = sum(
            (notional for _, _, side, _, _, notional in parsed if side == "BUY"),
            Fraction(0),
        )
        pending_sell = sum(
            (notional for _, _, side, _, _, notional in parsed if side == "SELL"),
            Fraction(0),
        )
        pinned_pending_buy = _fraction(
            risk_exact.get("pending_buy_exposure_usd"), "pinned pending buy exposure"
        )
        pinned_pending_sell = sum(
            (notional for _, side, notional in expected.values() if side == "SELL"),
            Fraction(0),
        )
        if pending_buy != pinned_pending_buy:
            raise ValueError("Pending BUY total must equal pinned pending BUY exposure")
        if pending_sell != pinned_pending_sell:
            raise ValueError("Pending SELL total must equal pinned pending SELL exposure")

        return {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "order_quantity_snapshot_id": _identity(
                risk["snapshot_id"], risk["record_hash"], normalized
            ),
            "record_type": "PROVIDER_PAPER_OPEN_ORDER_QUANTITY_EVIDENCE",
            "status": "OBSERVED",
            "risk_snapshot_id": risk["snapshot_id"],
            "risk_snapshot_record_hash": risk["record_hash"],
            "broker": risk["broker"],
            "broker_environment": risk["broker_environment"],
            "account_reference_sha256": risk["account_reference_sha256"],
            "observed_at": observed.isoformat(),
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            "open_orders_source_payload_sha256": risk["open_orders_source_payload_sha256"],
            "open_orders": normalized,
            "pending_buy_remaining_notional_usd": _decimal(pending_buy),
            "pending_sell_remaining_notional_usd": _decimal(pending_sell),
            "exact_fractions": {
                "open_orders": exact_orders,
                "pending_buy_remaining_notional_usd": _fraction_material(pending_buy),
                "pending_sell_remaining_notional_usd": _fraction_material(pending_sell),
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
                raise LedgerIntegrityError(f"Open-order quantity record {index} has been modified.")
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
                raw_orders = record.get("open_orders")
                if not isinstance(raw_orders, list):
                    raise ValueError("open_orders are invalid")
                inputs: list[dict[str, Any]] = []
                for item in raw_orders:
                    if not isinstance(item, Mapping) or set(item) != {
                        "order_reference_sha256",
                        "ticker",
                        "side",
                        "remaining_quantity",
                        "risk_mark_price_usd",
                        "remaining_notional_usd",
                    }:
                        raise ValueError("open-order field alphabet is invalid")
                    inputs.append(
                        {
                            "order_reference_sha256": item["order_reference_sha256"],
                            "ticker": item["ticker"],
                            "side": item["side"],
                            "remaining_quantity": item["remaining_quantity"],
                            "risk_mark_price_usd": item["risk_mark_price_usd"],
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
                    raise ValueError("record does not match exact open-order quantity evidence")
                account = _sha256(
                    record.get("account_reference_sha256"), "account_reference_sha256"
                )
                observed = _timestamp(record.get("observed_at"))
                if account in previous_time and observed <= previous_time[account]:
                    raise ValueError("observed_at does not move forward")
                identity = record.get("order_quantity_snapshot_id")
                if identity in seen:
                    raise ValueError("open-order quantity snapshot identity is duplicated")
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Open-order quantity record {index} violates its boundary."
                ) from error
            previous_time[account] = observed
            seen.add(identity)
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
                    if item["order_quantity_snapshot_id"]
                    == result["order_quantity_snapshot_id"]
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
                    f"Open-order quantity snapshot {result['order_quantity_snapshot_id']} already exists."
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
