from __future__ import annotations

"""Pinned, read-only paper-risk evidence; no limit, approval, or order is produced."""

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
POLICY_VERSION = "provider-paper-risk-evidence-snapshot-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
COLLECTION_BUNDLE_WINDOW = timedelta(seconds=60)
MAX_PREVIOUS_CLOSE_AGE = timedelta(days=7)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SIDES = ("BUY", "SELL")
FIXED_FIELDS = {
    "long_only_usd_semantics": True,
    "evidence_normalized": True,
    # Honest boundary flags: this ledger only normalizes observed evidence. It
    # reconciles nothing, assesses no policy, enforces no limit, routes no
    # order, and enables no trading.
    "broker_reconciliation_complete": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "live_trading_enabled": False,
    "external_head_anchor_present": False,
    "cryptographic_authentication_present": False,
    "source_payloads_stored": False,
    "paper_account_confirmed": True,
}
RECORD_FIELDS = frozenset(
    {
        "schema_version", "policy_version", "snapshot_id", "record_type", "status",
        "account_snapshot_id", "account_snapshot_record_hash",
        "broker", "broker_environment", "account_reference_sha256",
        "account_snapshot_observed_at", "observed_at", "recorded_at", "currency",
        "previous_close_equity_usd", "current_account_equity_usd",
        "previous_close_observed_at", "previous_close_equity_source_payload_sha256",
        "positions", "open_orders",
        "positions_source_payload_sha256", "open_orders_source_payload_sha256",
        "current_position_gross_exposure_usd", "pending_buy_exposure_usd",
        "conservative_gross_exposure_usd", "daily_loss_usd",
        "exact_fractions",
        "previous_hash", "record_hash",
    }
    | set(FIXED_FIELDS)
)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        count = os.write(descriptor, payload[offset:])
        if count <= 0:
            raise OSError("Unable to complete provider-paper-risk-snapshot append")
        offset += count


def _timestamp(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _sha256(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if not SHA256_PATTERN.fullmatch(resolved):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return resolved


def _ticker(value: Any, name: str) -> str:
    resolved = str(value or "").strip()
    if TICKER_PATTERN.fullmatch(resolved) is None:
        raise ValueError(f"{name} must match the Alpaca paper ticker format")
    return resolved


def _amount(value: Any, name: str, *, allow_zero: bool) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise ValueError(f"{name} must be provided as a decimal string, not a float")
    try:
        decimal = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a finite decimal string") from error
    if not decimal.is_finite() or decimal < 0:
        raise ValueError(f"{name} must be a finite non-negative decimal string")
    if not allow_zero and decimal == 0:
        raise ValueError(f"{name} must be a positive decimal string")
    return Fraction(decimal)


def _decimal_string(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    return "0" if decimal == 0 else format(decimal.normalize(), "f")


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _fraction(material: Any, name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} exact fraction denominator must be positive")
    return value


def _parse_position(entry: Any) -> tuple[str, Fraction]:
    if not isinstance(entry, Mapping):
        raise ValueError("each position must be an object")
    ticker = _ticker(entry.get("ticker"), "position ticker")
    amount = _amount(entry.get("long_market_value_usd"), "long_market_value_usd", allow_zero=True)
    return ticker, amount


def _parse_order(entry: Any) -> tuple[str, str, str, Fraction]:
    if not isinstance(entry, Mapping):
        raise ValueError("each open order must be an object")
    order_hash = _sha256(entry.get("order_reference_sha256"), "order_reference_sha256")
    ticker = _ticker(entry.get("ticker"), "order ticker")
    side = entry.get("side")
    if side not in SIDES:
        raise ValueError("side must be BUY or SELL")
    amount = _amount(entry.get("remaining_notional_usd"), "remaining_notional_usd", allow_zero=False)
    return order_hash, ticker, side, amount


def _canonical_positions(positions: Any) -> list[tuple[str, Fraction]]:
    if not isinstance(positions, (list, tuple)):
        raise ValueError("positions must be a sequence")
    parsed = [_parse_position(entry) for entry in positions]
    tickers = [ticker for ticker, _ in parsed]
    if len(set(tickers)) != len(tickers):
        raise ValueError("positions must have unique tickers")
    return sorted(parsed, key=lambda item: item[0])


def _canonical_orders(open_orders: Any) -> list[tuple[str, str, str, Fraction]]:
    if not isinstance(open_orders, (list, tuple)):
        raise ValueError("open_orders must be a sequence")
    parsed = [_parse_order(entry) for entry in open_orders]
    hashes = [order_hash for order_hash, _, _, _ in parsed]
    if len(set(hashes)) != len(hashes):
        raise ValueError("open_orders must have unique order_reference_sha256")
    return sorted(parsed, key=lambda item: (item[1], item[2], item[0]))


def _snapshot_id(
    account_snapshot_id: Any,
    account_snapshot_record_hash: Any,
    observed_at: str,
    previous_close_equity_usd: str,
    previous_close_observed_at: str,
    previous_close_equity_source_payload_sha256: str,
    positions_canonical: list[list[str]],
    open_orders_canonical: list[list[str]],
    positions_source_payload_sha256: Any,
    open_orders_source_payload_sha256: Any,
) -> str:
    material = [
        "PROVIDER_PAPER_RISK_EVIDENCE",
        account_snapshot_id,
        account_snapshot_record_hash,
        observed_at,
        previous_close_equity_usd,
        previous_close_observed_at,
        previous_close_equity_source_payload_sha256,
        positions_canonical,
        open_orders_canonical,
        positions_source_payload_sha256,
        open_orders_source_payload_sha256,
        POLICY_VERSION,
    ]
    return "PPRES-" + hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()[:32].upper()


class ProviderPaperRiskSnapshotLedger:
    """Append-only, hash-chained normalized paper-risk evidence pinned to one
    verified :class:`PaperBrokerAccountSnapshotLedger` record."""

    def __init__(self, path: str | Path, account_snapshot_ledger: Any) -> None:
        self.path = Path(path)
        self.account_snapshot_ledger = account_snapshot_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Provider-paper-risk snapshot has an incomplete final line.")
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(f"Blank provider-paper-risk snapshot at {line_number}.")
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid provider-paper-risk snapshot JSON at {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Provider-paper-risk snapshot {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record(
        self,
        *,
        account_snapshot_id: str,
        account_snapshot_record_hash: str,
        observed_at: str | datetime,
        previous_close_equity_usd: Any,
        previous_close_observed_at: str | datetime,
        previous_close_equity_source_payload_sha256: str,
        positions: Sequence[Mapping[str, Any]],
        open_orders: Sequence[Mapping[str, Any]],
        positions_source_payload_sha256: str,
        open_orders_source_payload_sha256: str,
        paper_account_confirmed: bool,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        if paper_account_confirmed is not True:
            raise ValueError("An explicitly confirmed paper account is required")
        pinned_records = self.account_snapshot_ledger.verify()
        resolved, errors = resolve_pinned_records(
            pinned_records,
            [account_snapshot_id],
            [account_snapshot_record_hash],
            id_field="snapshot_id",
            label="account snapshot",
        )
        if errors:
            raise ValueError(errors[0])
        pinned = resolved[0]
        account_hash = pinned["account_reference_sha256"]
        account_observed = _timestamp(pinned["observed_at"])
        current_equity = _fraction(pinned["exact_fractions"]["equity"], "equity")

        observed = _timestamp(observed_at)
        recorded = _timestamp(recorded_at or datetime.now(timezone.utc))
        now = datetime.now(timezone.utc)
        if recorded > now + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        if observed > recorded:
            raise ValueError("observed_at cannot be after recorded_at")
        if observed < account_observed or observed > account_observed + COLLECTION_BUNDLE_WINDOW:
            raise ValueError(
                "observed_at must fall within the pinned account snapshot's collection bundle window"
            )

        previous_close_equity = _amount(
            previous_close_equity_usd, "previous_close_equity_usd", allow_zero=True
        )
        previous_close_observed = _timestamp(previous_close_observed_at)
        if not (
            account_observed - MAX_PREVIOUS_CLOSE_AGE
            <= previous_close_observed
            < account_observed
        ) or previous_close_observed.date() >= account_observed.date():
            raise ValueError(
                "previous_close_observed_at must identify an earlier UTC-date close within seven days"
            )
        previous_close_source_hash = _sha256(
            previous_close_equity_source_payload_sha256,
            "previous_close_equity_source_payload_sha256",
        )
        positions_source_hash = _sha256(positions_source_payload_sha256, "positions_source_payload_sha256")
        open_orders_source_hash = _sha256(
            open_orders_source_payload_sha256, "open_orders_source_payload_sha256"
        )
        canonical_positions = _canonical_positions(positions)
        canonical_orders = _canonical_orders(open_orders)

        current_position_gross_exposure = sum(
            (amount for _, amount in canonical_positions), Fraction(0)
        )
        pending_buy_exposure = sum(
            (amount for _, _, side, amount in canonical_orders if side == "BUY"), Fraction(0)
        )
        conservative_gross_exposure = current_position_gross_exposure + pending_buy_exposure
        daily_loss = previous_close_equity - current_equity
        if daily_loss < 0:
            daily_loss = Fraction(0)

        positions_material = [
            {"ticker": ticker, "long_market_value_usd": _decimal_string(amount)}
            for ticker, amount in canonical_positions
        ]
        positions_fraction_material = [
            {"ticker": ticker, "long_market_value_usd": _fraction_material(amount)}
            for ticker, amount in canonical_positions
        ]
        open_orders_material = [
            {
                "order_reference_sha256": order_hash,
                "ticker": ticker,
                "side": side,
                "remaining_notional_usd": _decimal_string(amount),
            }
            for order_hash, ticker, side, amount in canonical_orders
        ]
        open_orders_fraction_material = [
            {"order_reference_sha256": order_hash, "remaining_notional_usd": _fraction_material(amount)}
            for order_hash, _, _, amount in canonical_orders
        ]

        snapshot_id = _snapshot_id(
            pinned["snapshot_id"],
            pinned["record_hash"],
            observed.isoformat(),
            _decimal_string(previous_close_equity),
            previous_close_observed.isoformat(),
            previous_close_source_hash,
            [[ticker, _decimal_string(amount)] for ticker, amount in canonical_positions],
            [
                [order_hash, ticker, side, _decimal_string(amount)]
                for order_hash, ticker, side, amount in canonical_orders
            ],
            positions_source_hash,
            open_orders_source_hash,
        )

        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "snapshot_id": snapshot_id,
            "record_type": "PROVIDER_PAPER_RISK_EVIDENCE_SNAPSHOT",
            "status": "OBSERVED",
            "account_snapshot_id": pinned["snapshot_id"],
            "account_snapshot_record_hash": pinned["record_hash"],
            "broker": pinned["broker"],
            "broker_environment": pinned["broker_environment"],
            "account_reference_sha256": account_hash,
            "account_snapshot_observed_at": account_observed.isoformat(),
            "observed_at": observed.isoformat(),
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            "previous_close_equity_usd": _decimal_string(previous_close_equity),
            "previous_close_observed_at": previous_close_observed.isoformat(),
            "previous_close_equity_source_payload_sha256": previous_close_source_hash,
            "current_account_equity_usd": _decimal_string(current_equity),
            "positions": positions_material,
            "open_orders": open_orders_material,
            "positions_source_payload_sha256": positions_source_hash,
            "open_orders_source_payload_sha256": open_orders_source_hash,
            "current_position_gross_exposure_usd": _decimal_string(current_position_gross_exposure),
            "pending_buy_exposure_usd": _decimal_string(pending_buy_exposure),
            "conservative_gross_exposure_usd": _decimal_string(conservative_gross_exposure),
            "daily_loss_usd": _decimal_string(daily_loss),
            "exact_fractions": {
                "previous_close_equity_usd": _fraction_material(previous_close_equity),
                "current_account_equity_usd": _fraction_material(current_equity),
                "current_position_gross_exposure_usd": _fraction_material(current_position_gross_exposure),
                "pending_buy_exposure_usd": _fraction_material(pending_buy_exposure),
                "conservative_gross_exposure_usd": _fraction_material(conservative_gross_exposure),
                "daily_loss_usd": _fraction_material(daily_loss),
                "positions": positions_fraction_material,
                "open_orders": open_orders_fraction_material,
            },
            **FIXED_FIELDS,
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen_ids: set[str] = set()
        previous_time_by_account: dict[str, datetime] = {}
        records = self.records()
        pinned_by_id = {item["snapshot_id"]: item for item in self.account_snapshot_ledger.verify()}
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Provider-paper-risk snapshot {index} has been modified.")
            try:
                boundary = self._verify_record(record, pinned_by_id, previous_time_by_account, seen_ids)
            except (KeyError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Provider-paper-risk snapshot {index} violates its boundary."
                ) from error
            if not boundary:
                raise LedgerIntegrityError(f"Provider-paper-risk snapshot {index} violates its boundary.")
            previous_hash = record["record_hash"]
        return records

    def _verify_record(
        self,
        record: dict[str, Any],
        pinned_by_id: dict[str, dict[str, Any]],
        previous_time_by_account: dict[str, datetime],
        seen_ids: set[str],
    ) -> bool:
        if set(record) != RECORD_FIELDS:
            return False

        exact_fractions = record.get("exact_fractions")
        expected_exact_fields = {
            "previous_close_equity_usd",
            "current_account_equity_usd",
            "current_position_gross_exposure_usd",
            "pending_buy_exposure_usd",
            "conservative_gross_exposure_usd",
            "daily_loss_usd",
            "positions",
            "open_orders",
        }
        if not isinstance(exact_fractions, dict) or set(exact_fractions) != expected_exact_fields:
            return False

        raw_positions = record.get("positions")
        raw_position_fractions = exact_fractions.get("positions")
        if (
            not isinstance(raw_positions, list)
            or not isinstance(raw_position_fractions, list)
            or len(raw_positions) != len(raw_position_fractions)
        ):
            return False
        parsed_positions: list[tuple[str, Fraction]] = []
        for position_entry, fraction_entry in zip(raw_positions, raw_position_fractions):
            if (
                not isinstance(position_entry, Mapping)
                or set(position_entry) != {"ticker", "long_market_value_usd"}
            ):
                return False
            ticker = _ticker(position_entry.get("ticker"), "position ticker")
            if (
                not isinstance(fraction_entry, dict)
                or set(fraction_entry) != {"ticker", "long_market_value_usd"}
                or fraction_entry.get("ticker") != ticker
            ):
                return False
            amount = _fraction(fraction_entry.get("long_market_value_usd"), "long_market_value_usd")
            if (
                amount < 0
                or position_entry.get("long_market_value_usd") != _decimal_string(amount)
                or fraction_entry.get("long_market_value_usd") != _fraction_material(amount)
            ):
                return False
            parsed_positions.append((ticker, amount))
        tickers = [ticker for ticker, _ in parsed_positions]
        if len(set(tickers)) != len(tickers) or sorted(parsed_positions, key=lambda item: item[0]) != parsed_positions:
            return False

        raw_orders = record.get("open_orders")
        raw_order_fractions = exact_fractions.get("open_orders")
        if (
            not isinstance(raw_orders, list)
            or not isinstance(raw_order_fractions, list)
            or len(raw_orders) != len(raw_order_fractions)
        ):
            return False
        parsed_orders: list[tuple[str, str, str, Fraction]] = []
        for order_entry, fraction_entry in zip(raw_orders, raw_order_fractions):
            if (
                not isinstance(order_entry, Mapping)
                or set(order_entry)
                != {"order_reference_sha256", "ticker", "side", "remaining_notional_usd"}
            ):
                return False
            order_hash = _sha256(order_entry.get("order_reference_sha256"), "order_reference_sha256")
            if (
                not isinstance(fraction_entry, dict)
                or set(fraction_entry)
                != {"order_reference_sha256", "remaining_notional_usd"}
                or fraction_entry.get("order_reference_sha256") != order_hash
            ):
                return False
            ticker = _ticker(order_entry.get("ticker"), "order ticker")
            side = order_entry.get("side")
            if side not in SIDES:
                return False
            amount = _fraction(fraction_entry.get("remaining_notional_usd"), "remaining_notional_usd")
            if (
                amount <= 0
                or order_entry.get("remaining_notional_usd") != _decimal_string(amount)
                or fraction_entry.get("remaining_notional_usd") != _fraction_material(amount)
            ):
                return False
            parsed_orders.append((order_hash, ticker, side, amount))
        hashes = [order_hash for order_hash, _, _, _ in parsed_orders]
        if (
            len(set(hashes)) != len(hashes)
            or sorted(parsed_orders, key=lambda item: (item[1], item[2], item[0])) != parsed_orders
        ):
            return False

        previous_close_equity = _fraction(
            exact_fractions.get("previous_close_equity_usd"), "previous_close_equity_usd"
        )
        if previous_close_equity < 0 or record.get("previous_close_equity_usd") != _decimal_string(
            previous_close_equity
        ):
            return False
        current_equity = _fraction(
            exact_fractions.get("current_account_equity_usd"), "current_account_equity_usd"
        )
        if current_equity < 0 or record.get("current_account_equity_usd") != _decimal_string(current_equity):
            return False

        current_position_gross_exposure = sum((amount for _, amount in parsed_positions), Fraction(0))
        pending_buy_exposure = sum(
            (amount for _, _, side, amount in parsed_orders if side == "BUY"), Fraction(0)
        )
        conservative_gross_exposure = current_position_gross_exposure + pending_buy_exposure
        daily_loss = previous_close_equity - current_equity
        if daily_loss < 0:
            daily_loss = Fraction(0)
        derived = {
            "current_position_gross_exposure_usd": current_position_gross_exposure,
            "pending_buy_exposure_usd": pending_buy_exposure,
            "conservative_gross_exposure_usd": conservative_gross_exposure,
            "daily_loss_usd": daily_loss,
        }
        for name, value in derived.items():
            if record.get(name) != _decimal_string(value) or exact_fractions.get(name) != _fraction_material(value):
                return False

        pinned = pinned_by_id.get(record.get("account_snapshot_id"))
        if pinned is None or pinned.get("record_hash") != record.get("account_snapshot_record_hash"):
            return False
        if (
            record.get("broker") != pinned.get("broker")
            or record.get("broker_environment") != pinned.get("broker_environment")
            or record.get("account_reference_sha256") != pinned.get("account_reference_sha256")
        ):
            return False
        account_observed = _timestamp(pinned.get("observed_at"))
        if record.get("account_snapshot_observed_at") != account_observed.isoformat():
            return False
        pinned_equity = _fraction(pinned.get("exact_fractions", {}).get("equity"), "equity")
        if current_equity != pinned_equity:
            return False

        positions_source_hash = _sha256(
            record.get("positions_source_payload_sha256"), "positions_source_payload_sha256"
        )
        open_orders_source_hash = _sha256(
            record.get("open_orders_source_payload_sha256"), "open_orders_source_payload_sha256"
        )
        previous_close_source_hash = _sha256(
            record.get("previous_close_equity_source_payload_sha256"),
            "previous_close_equity_source_payload_sha256",
        )

        observed = _timestamp(record.get("observed_at"))
        recorded = _timestamp(record.get("recorded_at"))
        previous_close_observed = _timestamp(record.get("previous_close_observed_at"))
        if not (
            account_observed - MAX_PREVIOUS_CLOSE_AGE
            <= previous_close_observed
            < account_observed
        ) or previous_close_observed.date() >= account_observed.date():
            return False
        if not (account_observed <= observed <= account_observed + COLLECTION_BUNDLE_WINDOW):
            return False
        if not (observed <= recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW):
            return False
        account_hash = record.get("account_reference_sha256")
        prior = previous_time_by_account.get(account_hash)
        if prior is not None and observed <= prior:
            return False

        expected_id = _snapshot_id(
            record.get("account_snapshot_id"),
            record.get("account_snapshot_record_hash"),
            observed.isoformat(),
            _decimal_string(previous_close_equity),
            previous_close_observed.isoformat(),
            previous_close_source_hash,
            [[ticker, _decimal_string(amount)] for ticker, amount in parsed_positions],
            [
                [order_hash, ticker, side, _decimal_string(amount)]
                for order_hash, ticker, side, amount in parsed_orders
            ],
            positions_source_hash,
            open_orders_source_hash,
        )
        if record.get("snapshot_id") != expected_id or expected_id in seen_ids:
            return False

        boundary = (
            record.get("schema_version") == SCHEMA_VERSION
            and record.get("policy_version") == POLICY_VERSION
            and record.get("record_type") == "PROVIDER_PAPER_RISK_EVIDENCE_SNAPSHOT"
            and record.get("status") == "OBSERVED"
            and record.get("currency") == "USD"
            and all(record.get(field) == value for field, value in FIXED_FIELDS.items())
        )
        if not boundary:
            return False

        seen_ids.add(expected_id)
        previous_time_by_account[account_hash] = observed
        return True

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.path.with_suffix(self.path.suffix + ".lock"), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next((item for item in records if item["snapshot_id"] == result["snapshot_id"]), None)
            if existing:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                if allow_existing and {k: v for k, v in existing.items() if k not in ignored} == {
                    k: v for k, v in result.items() if k not in ignored
                }:
                    return existing
                raise LedgerIntegrityError(f"Provider-paper-risk snapshot {result['snapshot_id']} already exists.")
            prior_for_account = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"]
                    == result["account_reference_sha256"]
                ),
                None,
            )
            if prior_for_account is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior_for_account["observed_at"]
            ):
                raise ValueError(
                    "observed_at must move forward for each paper account"
                )
            material = {**result, "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH}
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
