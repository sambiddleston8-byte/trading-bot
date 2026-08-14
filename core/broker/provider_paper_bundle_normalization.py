from __future__ import annotations

"""Exact local normalization of a verified PAPER collection; no broker action."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from core.broker.alpaca_paper import TICKER_PATTERN
from core.broker.paper_broker_capture import (
    MAX_LEDGER_BYTES,
    _check_regular_descriptor,
    _fsync_directory,
    _no_follow,
    _payload_object,
    _private_directory,
    _read_private_file,
    _write_all,
)
from core.broker.provider_paper_evidence_collection import (
    OPEN_ORDER_STATUSES,
    PART_KINDS,
    PaperReadOnlyCollectionBundleLedger,
    _strict_array,
)
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp


SCHEMA_VERSION = "1.0"
POLICY_VERSION = "provider-paper-bundle-normalization-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
BASE_DOWNSTREAM_BLOCKERS = (
    "POSITIONS_ORDERS_ACCOUNT_BINDING_UNPROVEN",
    "PREVIOUS_CLOSE_EVIDENCE_REQUIRED",
    "SETTLEMENT_BREAKDOWN_EVIDENCE_REQUIRED",
)
TRUE_FIELDS = {
    "collection_bundle_verified": True,
    "source_payload_hashes_verified": True,
    "source_payloads_stored_upstream": True,
    "local_collector_attestation_only": True,
}
FALSE_FIELDS = {
    "broker_authenticity_proven": False,
    "broker_origin_nonrepudiable": False,
    "cryptographic_authentication_present": False,
    "settlement_breakdown_available": False,
    "previous_close_available": False,
    "broker_reconciliation_complete": False,
    "risk_policy_assessed": False,
    "risk_limits_enforced": False,
    "downstream_ledger_written": False,
    "account_snapshot_adoption_eligible": False,
    "risk_snapshot_adoption_eligible": False,
    "source_payloads_copied": False,
    "order_route_exists": False,
    "paper_order_submission_allowed": False,
    "order_submitted": False,
    "recommendation_provided": False,
    "live_trading_enabled": False,
    "external_head_anchor_present": False,
}
BOOLEAN_FIELDS = {**TRUE_FIELDS, **FALSE_FIELDS}
RECORD_FIELDS = frozenset(
    {
        "schema_version",
        "policy_version",
        "normalization_id",
        "record_type",
        "status",
        "normalization_succeeded",
        "collection_bundle_id",
        "collection_bundle_record_hash",
        "broker",
        "broker_environment",
        "account_reference_sha256",
        "account_open_payload_sha256",
        "account_close_payload_sha256",
        "positions_source_payload_sha256",
        "open_orders_source_payload_sha256",
        "observed_at",
        "recorded_at",
        "currency",
        "normalization_errors",
        "downstream_blocking_reasons",
        "account_values",
        "positions",
        "open_orders",
        "candidate_position_gross_exposure_usd",
        "candidate_pending_buy_notional_usd",
        "candidate_pending_sell_notional_usd",
        "exact_fractions",
        "next_authority",
        "previous_hash",
        "record_hash",
    }
    | set(BOOLEAN_FIELDS)
)


class PaperBundleNormalizationError(ValueError):
    """One stable fail-closed semantic reason code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _timestamp(value: Any) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


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


def _decimal_string(value: Fraction) -> str:
    if value == 0:
        return "0"
    denominator = value.denominator
    powers_of_two = 0
    powers_of_five = 0
    while denominator % 2 == 0:
        denominator //= 2
        powers_of_two += 1
    while denominator % 5 == 0:
        denominator //= 5
        powers_of_five += 1
    if denominator != 1:
        raise PaperBundleNormalizationError("NON_TERMINATING_DECIMAL")
    scale = max(powers_of_two, powers_of_five)
    scaled = abs(value.numerator)
    scaled *= 2 ** (scale - powers_of_two)
    scaled *= 5 ** (scale - powers_of_five)
    digits = str(scaled)
    if scale:
        digits = digits.zfill(scale + 1)
        whole = digits[:-scale]
        fraction = digits[-scale:].rstrip("0")
        rendered = whole if not fraction else f"{whole}.{fraction}"
    else:
        rendered = digits
    return f"-{rendered}" if value < 0 else rendered


def _amount(value: Any, name: str, *, positive: bool = False) -> Fraction:
    if isinstance(value, (bool, float)) or not isinstance(value, str):
        raise PaperBundleNormalizationError("NON_DECIMAL_STRING")
    if not value or value != value.strip():
        raise PaperBundleNormalizationError("NON_DECIMAL_STRING")
    try:
        decimal = Decimal(value)
    except (InvalidOperation, TypeError, ValueError) as error:
        raise PaperBundleNormalizationError("NON_DECIMAL_STRING") from error
    if not decimal.is_finite() or decimal < 0 or (positive and decimal == 0):
        raise PaperBundleNormalizationError("NON_DECIMAL_STRING")
    decimal_parts = decimal.as_tuple()
    if len(decimal_parts.digits) > 30 or not -18 <= decimal_parts.exponent <= 18:
        raise PaperBundleNormalizationError("NON_DECIMAL_STRING")
    return Fraction(decimal)


def _exact_string(value: Any, reason: str, maximum: int = 200) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > maximum
    ):
        raise PaperBundleNormalizationError(reason)
    return value


def _ticker(value: Any) -> str:
    resolved = _exact_string(value, "MISSING_FIELD", 15)
    if TICKER_PATTERN.fullmatch(resolved) is None:
        raise PaperBundleNormalizationError("UNSUPPORTED_SYMBOL")
    return resolved


def _part_hashes(bundle: Mapping[str, Any]) -> dict[str, str]:
    return {part["part_kind"]: part["payload_sha256"] for part in bundle["parts"]}


def _normalization_id(material: list[Any]) -> str:
    return "PPBN-" + hashlib.sha256(
        _canonical_json([*material, POLICY_VERSION]).encode("utf-8")
    ).hexdigest()[:32].upper()


def _normalize_payloads(payloads: Mapping[str, bytes]) -> dict[str, Any]:
    opening = _payload_object(payloads["ACCOUNT_OPEN"])
    closing = _payload_object(payloads["ACCOUNT_CLOSE"])
    for account in (opening, closing):
        if (
            account.get("status") != "ACTIVE"
            or not isinstance(account.get("status"), str)
            or account.get("currency") != "USD"
            or not isinstance(account.get("currency"), str)
        ):
            raise PaperBundleNormalizationError("ACCOUNT_STATUS_CURRENCY_UNSUPPORTED")
    account_values: dict[str, Fraction] = {}
    for field in ("cash", "buying_power", "equity"):
        opened = _amount(opening.get(field), field)
        closed = _amount(closing.get(field), field)
        if opened != closed:
            raise PaperBundleNormalizationError("ACCOUNT_VALUE_DRIFT")
        account_values[field] = opened
    if account_values["equity"] < account_values["cash"]:
        raise PaperBundleNormalizationError("ACCOUNT_VALUE_INCONSISTENT")

    positions: list[dict[str, Any]] = []
    position_fractions: list[dict[str, Any]] = []
    quantities: dict[str, Fraction] = {}
    for entry in _strict_array(payloads["POSITIONS"], "positions"):
        if not isinstance(entry, Mapping):
            raise PaperBundleNormalizationError("MISSING_FIELD")
        if entry.get("asset_class") != "us_equity" or not isinstance(
            entry.get("asset_class"), str
        ):
            raise PaperBundleNormalizationError("UNSUPPORTED_ASSET_CLASS")
        if entry.get("side") != "long" or not isinstance(entry.get("side"), str):
            raise PaperBundleNormalizationError("SHORT_POSITION_UNSUPPORTED")
        ticker = _ticker(entry.get("symbol"))
        if ticker in quantities:
            raise PaperBundleNormalizationError("DUPLICATE_POSITION")
        quantity = _amount(entry.get("qty"), "qty", positive=True)
        price = _amount(entry.get("current_price"), "current_price", positive=True)
        market_value = _amount(entry.get("market_value"), "market_value", positive=True)
        if quantity * price != market_value:
            raise PaperBundleNormalizationError("QUANTITY_PRICE_IDENTITY_MISMATCH")
        quantities[ticker] = quantity
        positions.append(
            {
                "ticker": ticker,
                "quantity": _decimal_string(quantity),
                "current_price_usd": _decimal_string(price),
                "long_market_value_usd": _decimal_string(market_value),
            }
        )
        position_fractions.append(
            {
                "ticker": ticker,
                "quantity": _fraction_material(quantity),
                "current_price_usd": _fraction_material(price),
                "long_market_value_usd": _fraction_material(market_value),
            }
        )
    positions.sort(key=lambda item: item["ticker"])
    position_fractions.sort(key=lambda item: item["ticker"])

    open_orders: list[dict[str, Any]] = []
    order_fractions: list[dict[str, Any]] = []
    order_hashes: set[str] = set()
    sell_quantities: dict[str, Fraction] = {}
    pending_buy = Fraction(0)
    pending_sell = Fraction(0)
    extra_blockers: set[str] = set()
    for entry in _strict_array(payloads["OPEN_ORDERS"], "open-orders"):
        if not isinstance(entry, Mapping):
            raise PaperBundleNormalizationError("MISSING_FIELD")
        if entry.get("asset_class") != "us_equity" or not isinstance(
            entry.get("asset_class"), str
        ):
            raise PaperBundleNormalizationError("UNSUPPORTED_ASSET_CLASS")
        if entry.get("order_class") not in ("", "simple", None) or entry.get("legs") not in (
            None,
            [],
        ):
            raise PaperBundleNormalizationError("COMPLEX_ORDER_UNSUPPORTED")
        identity = _exact_string(entry.get("id"), "MISSING_FIELD")
        order_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        if order_hash in order_hashes:
            raise PaperBundleNormalizationError("DUPLICATE_ORDER")
        order_hashes.add(order_hash)
        ticker = _ticker(entry.get("symbol"))
        side_value = entry.get("side")
        if side_value not in ("buy", "sell") or not isinstance(side_value, str):
            raise PaperBundleNormalizationError("UNSUPPORTED_ORDER_SIDE")
        side = side_value.upper()
        status_value = entry.get("status")
        if not isinstance(status_value, str) or status_value not in OPEN_ORDER_STATUSES:
            raise PaperBundleNormalizationError("ORDER_STATUS_NOT_OPEN")
        order_type = entry.get("type")
        if not isinstance(order_type, str):
            raise PaperBundleNormalizationError("MISSING_FIELD")
        if order_type == "market":
            raise PaperBundleNormalizationError("MARKET_ORDER_NO_EXACT_PRICE")
        if order_type == "stop":
            raise PaperBundleNormalizationError("STOP_ORDER_TRIGGER_NOT_FILL_PRICE")
        if order_type not in ("limit", "stop_limit"):
            raise PaperBundleNormalizationError("UNSUPPORTED_ORDER_TYPE")
        quantity_value = entry.get("qty")
        notional_value = entry.get("notional")
        if (quantity_value is None) == (notional_value is None):
            raise PaperBundleNormalizationError("ORDER_SIZE_AMBIGUOUS")
        filled_quantity = _amount(entry.get("filled_qty"), "filled_qty")
        limit_price = _amount(entry.get("limit_price"), "limit_price", positive=True)
        normalized: dict[str, Any] = {
            "order_reference_sha256": order_hash,
            "ticker": ticker,
            "side": side,
            "order_type": order_type,
        }
        exact: dict[str, Any] = {
            "order_reference_sha256": order_hash,
            "ticker": ticker,
            "side": side,
        }
        if quantity_value is not None:
            quantity = _amount(quantity_value, "qty", positive=True)
            if filled_quantity >= quantity:
                raise PaperBundleNormalizationError("REMAINING_QUANTITY_NON_POSITIVE")
            remaining = quantity - filled_quantity
            remaining_notional = remaining * limit_price
            normalized.update(
                {
                    "remaining_quantity": _decimal_string(remaining),
                    "conservative_price_usd": _decimal_string(limit_price),
                    "remaining_notional_usd": _decimal_string(remaining_notional),
                }
            )
            exact.update(
                {
                    "remaining_quantity": _fraction_material(remaining),
                    "conservative_price_usd": _fraction_material(limit_price),
                    "remaining_notional_usd": _fraction_material(remaining_notional),
                }
            )
            if side == "SELL":
                sell_quantities[ticker] = sell_quantities.get(ticker, Fraction(0)) + remaining
        else:
            if filled_quantity != 0:
                raise PaperBundleNormalizationError("NOTIONAL_ORDER_PARTIALLY_FILLED")
            notional = _amount(notional_value, "notional", positive=True)
            remaining_notional = notional
            if side == "SELL":
                if ticker not in quantities:
                    raise PaperBundleNormalizationError("SELL_SYMBOL_NOT_HELD")
                implied_quantity = notional / limit_price
                sell_quantities[ticker] = (
                    sell_quantities.get(ticker, Fraction(0)) + implied_quantity
                )
            normalized.update(
                {
                    "remaining_quantity": None,
                    "conservative_price_usd": None,
                    "remaining_notional_usd": _decimal_string(notional),
                }
            )
            exact.update(
                {
                    "remaining_quantity": None,
                    "conservative_price_usd": None,
                    "remaining_notional_usd": _fraction_material(notional),
                }
            )
            extra_blockers.add("NOTIONAL_ORDER_QUANTITY_EVIDENCE_REQUIRED")
        if side == "BUY":
            pending_buy += remaining_notional
        else:
            pending_sell += remaining_notional
        open_orders.append(normalized)
        order_fractions.append(exact)

    for ticker, quantity in sell_quantities.items():
        if ticker not in quantities:
            raise PaperBundleNormalizationError("SELL_SYMBOL_NOT_HELD")
        if quantity > quantities[ticker]:
            raise PaperBundleNormalizationError("SELL_EXCEEDS_POSITION")
    order_key = lambda item: (
        item.get("ticker", ""),
        item.get("side", ""),
        item["order_reference_sha256"],
    )
    open_orders.sort(key=order_key)
    order_fractions.sort(key=order_key)
    position_total = sum(
        (_fraction(item["long_market_value_usd"], "long_market_value_usd") for item in position_fractions),
        Fraction(0),
    )
    if account_values["equity"] != account_values["cash"] + position_total:
        raise PaperBundleNormalizationError("ACCOUNT_POSITION_VALUE_INCONSISTENT")
    return {
        "account_values": {name: _decimal_string(value) for name, value in account_values.items()},
        "positions": positions,
        "open_orders": open_orders,
        "totals": (position_total, pending_buy, pending_sell),
        "exact_fractions": {
            "account_values": {
                name: _fraction_material(value) for name, value in account_values.items()
            },
            "positions": position_fractions,
            "open_orders": order_fractions,
            "candidate_position_gross_exposure_usd": _fraction_material(position_total),
            "candidate_pending_buy_notional_usd": _fraction_material(pending_buy),
            "candidate_pending_sell_notional_usd": _fraction_material(pending_sell),
        },
        "extra_blockers": sorted(extra_blockers),
    }


class ProviderPaperBundleNormalizationLedger:
    """Normalize one verified local PAPER bundle without granting authority."""

    def __init__(self, path: str | Path, bundle_ledger: PaperReadOnlyCollectionBundleLedger) -> None:
        self.path = Path(path)
        self.bundle_ledger = bundle_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            raw = _read_private_file(
                self.path,
                mode=0o600,
                name="Paper bundle normalization ledger",
                maximum_bytes=MAX_LEDGER_BYTES,
            )
        except OSError as error:
            raise LedgerIntegrityError("Paper bundle normalization ledger is unsafe.") from error
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError("Paper bundle normalization has an incomplete final line.")
        try:
            lines = raw.decode("utf-8").splitlines()
        except UnicodeDecodeError as error:
            raise LedgerIntegrityError("Paper bundle normalization ledger is not UTF-8.") from error
        records: list[dict[str, Any]] = []
        for index, line in enumerate(lines, start=1):
            if not line.strip():
                raise LedgerIntegrityError(f"Blank paper bundle normalization at line {index}.")
            try:
                record = json.loads(line)
            except json.JSONDecodeError as error:
                raise LedgerIntegrityError(
                    f"Invalid paper bundle normalization JSON at line {index}."
                ) from error
            if not isinstance(record, dict):
                raise LedgerIntegrityError(f"Paper bundle normalization {index} is not an object.")
            records.append(record)
        return records

    def record(
        self,
        *,
        collection_bundle_id: str,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        result = self._build(collection_bundle_id, recorded_at or datetime.now(timezone.utc))
        return self._append(result, allow_existing=allow_existing)

    def _build(self, bundle_id: Any, recorded_at: Any) -> dict[str, Any]:
        if not isinstance(bundle_id, str) or not bundle_id or bundle_id != bundle_id.strip():
            raise ValueError("collection_bundle_id must be an exact non-empty string")
        bundle, payloads = self.bundle_ledger.read_verified(bundle_id)
        if set(payloads) != set(PART_KINDS):
            raise LedgerIntegrityError("Paper collection bundle parts changed.")
        observed = _timestamp(bundle["collection_finished_at"])
        recorded = _timestamp(recorded_at)
        if observed > recorded or recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("normalization times are reverse ordered or future dated")
        errors: list[str] = []
        normalized: dict[str, Any] | None = None
        try:
            normalized = _normalize_payloads(payloads)
        except PaperBundleNormalizationError as error:
            errors = [error.reason]
        except ValueError:
            errors = ["MALFORMED_SOURCE_PAYLOAD"]
        succeeded = normalized is not None
        status = (
            "NORMALIZED_AWAITING_EXTERNAL_EVIDENCE"
            if succeeded
            else "NORMALIZATION_BLOCKED"
        )
        blockers = sorted(
            set(BASE_DOWNSTREAM_BLOCKERS)
            | (set(normalized["extra_blockers"]) if normalized else set())
        )
        hashes = _part_hashes(bundle)
        account_values = normalized["account_values"] if normalized else None
        positions = normalized["positions"] if normalized else []
        orders = normalized["open_orders"] if normalized else []
        totals = normalized["totals"] if normalized else (None, None, None)
        exact = normalized["exact_fractions"] if normalized else {}
        identity_material = [
            bundle["bundle_id"],
            bundle["record_hash"],
            status,
            errors,
            blockers,
            account_values,
            positions,
            orders,
            [None if value is None else _decimal_string(value) for value in totals],
        ]
        result = {
            "schema_version": SCHEMA_VERSION,
            "policy_version": POLICY_VERSION,
            "normalization_id": _normalization_id(identity_material),
            "record_type": "PROVIDER_PAPER_BUNDLE_NORMALIZATION",
            "status": status,
            "normalization_succeeded": succeeded,
            "collection_bundle_id": bundle["bundle_id"],
            "collection_bundle_record_hash": bundle["record_hash"],
            "broker": "Alpaca",
            "broker_environment": "PAPER",
            "account_reference_sha256": bundle["account_reference_sha256"],
            "account_open_payload_sha256": hashes["ACCOUNT_OPEN"],
            "account_close_payload_sha256": hashes["ACCOUNT_CLOSE"],
            "positions_source_payload_sha256": hashes["POSITIONS"],
            "open_orders_source_payload_sha256": hashes["OPEN_ORDERS"],
            "observed_at": observed.isoformat(),
            "recorded_at": recorded.isoformat(),
            "currency": "USD",
            "normalization_errors": errors,
            "downstream_blocking_reasons": blockers,
            "account_values": account_values,
            "positions": positions,
            "open_orders": orders,
            "candidate_position_gross_exposure_usd": (
                None if totals[0] is None else _decimal_string(totals[0])
            ),
            "candidate_pending_buy_notional_usd": (
                None if totals[1] is None else _decimal_string(totals[1])
            ),
            "candidate_pending_sell_notional_usd": (
                None if totals[2] is None else _decimal_string(totals[2])
            ),
            "exact_fractions": exact,
            "next_authority": "SETTLEMENT_AND_PRIOR_CLOSE_AUTHORITY_REQUIRED",
            **TRUE_FIELDS,
            **FALSE_FIELDS,
        }
        return result

    def verify(self) -> list[dict[str, Any]]:
        previous_hash = GENESIS_HASH
        seen: set[str] = set()
        previous_observed: dict[str, datetime] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if (
                record.get("previous_hash") != previous_hash
                or record.get("record_hash") != _record_hash(material)
            ):
                raise LedgerIntegrityError(f"Paper bundle normalization {index} was modified.")
            try:
                if set(record) != RECORD_FIELDS:
                    raise ValueError("normalization field alphabet is invalid")
                if any(record.get(name) is not value for name, value in BOOLEAN_FIELDS.items()):
                    raise ValueError("normalization authority flags changed")
                expected = self._build(
                    record.get("collection_bundle_id"), record.get("recorded_at")
                )
                comparable = {
                    key: value
                    for key, value in record.items()
                    if key not in {"previous_hash", "record_hash"}
                }
                if comparable != expected:
                    raise ValueError("normalization no longer matches its source bundle")
                identity = record["normalization_id"]
                account = record["account_reference_sha256"]
                observed = _timestamp(record["observed_at"])
                if identity in seen:
                    raise ValueError("normalization identity is duplicated")
                if account in previous_observed and observed <= previous_observed[account]:
                    raise ValueError("normalization observation did not move forward")
            except (KeyError, LedgerIntegrityError, TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Paper bundle normalization {index} violates its boundary."
                ) from error
            seen.add(identity)
            previous_observed[account] = observed
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        _private_directory(self.path.parent)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        try:
            descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | _no_follow(), 0o600)
        except OSError as error:
            raise LedgerIntegrityError("Paper bundle normalization lock is unsafe.") from error
        try:
            _check_regular_descriptor(
                descriptor, mode=0o600, name="Paper bundle normalization lock"
            )
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (
                    item
                    for item in records
                    if item["collection_bundle_id"] == result["collection_bundle_id"]
                ),
                None,
            )
            if existing is not None:
                ignored = {"previous_hash", "record_hash", "recorded_at"}
                left = {key: value for key, value in existing.items() if key not in ignored}
                right = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and left == right:
                    return existing
                raise LedgerIntegrityError("Paper bundle normalization conflicts with its source.")
            prior = next(
                (
                    item
                    for item in reversed(records)
                    if item["account_reference_sha256"] == result["account_reference_sha256"]
                ),
                None,
            )
            if prior is not None and _timestamp(result["observed_at"]) <= _timestamp(
                prior["observed_at"]
            ):
                raise ValueError("observed_at must move forward for each paper account")
            material = {
                **result,
                "previous_hash": records[-1]["record_hash"] if records else GENESIS_HASH,
            }
            record = {**material, "record_hash": _record_hash(material)}
            target = os.open(
                self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND | _no_follow(), 0o600
            )
            try:
                _check_regular_descriptor(
                    target, mode=0o600, name="Paper bundle normalization ledger"
                )
                _write_all(target, (_canonical_json(record) + "\n").encode("utf-8"))
                os.fsync(target)
            finally:
                os.close(target)
            _fsync_directory(self.path.parent)
            return record
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
