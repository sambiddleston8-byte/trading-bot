from __future__ import annotations

"""External simulated cash flows fixed to verified valuation boundaries."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import fcntl
from fractions import Fraction
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError, canonical_timestamp
from core.performance.portfolio_valuation import SimulatedPortfolioValuationLedger


PORTFOLIO_CASH_FLOW_SCHEMA_VERSION = "1.0"
PORTFOLIO_CASH_FLOW_POLICY_VERSION = "post-valuation-boundary-v1"
MAX_CLOCK_SKEW = timedelta(minutes=5)
FLOW_TYPES = {"CONTRIBUTION", "WITHDRAWAL"}


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _record_hash(material: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()


def _write_all(descriptor: int, payload: bytes) -> None:
    written = 0
    while written < len(payload):
        count = os.write(descriptor, payload[written:])
        if count <= 0:
            raise OSError("Unable to complete append-only portfolio cash-flow write")
        written += count


def _as_datetime(value: str | datetime) -> datetime:
    return datetime.fromisoformat(canonical_timestamp(value))


def _positive_fraction(value: Any, name: str) -> Fraction:
    try:
        resolved = Fraction(Decimal(str(value)))
    except (InvalidOperation, OverflowError, TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a positive exact decimal") from error
    if resolved <= 0:
        raise ValueError(f"{name} must be a positive exact decimal")
    return resolved


def _fraction(material: Mapping[str, Any], name: str) -> Fraction:
    try:
        denominator = int(material["denominator"])
        value = Fraction(int(material["numerator"]), denominator)
    except (KeyError, TypeError, ValueError, ZeroDivisionError) as error:
        raise ValueError(f"{name} exact fraction is invalid") from error
    if denominator <= 0:
        raise ValueError(f"{name} exact fraction denominator must be positive")
    return value


def _fraction_material(value: Fraction) -> dict[str, str]:
    return {"numerator": str(value.numerator), "denominator": str(value.denominator)}


def _decimal_string(value: Fraction) -> str:
    decimal = Decimal(value.numerator) / Decimal(value.denominator)
    if decimal == 0:
        return "0"
    return format(decimal.normalize(), "f")


def _flow_id(portfolio_version: str, valuation_id: str, flow_type: str) -> str:
    material = [portfolio_version, valuation_id, flow_type, PORTFOLIO_CASH_FLOW_POLICY_VERSION]
    return "PCF-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:32].upper()


class PortfolioCashFlowLedger:
    """Append-only cash movements occurring after a market valuation."""

    def __init__(
        self, path: str | Path, valuation_ledger: SimulatedPortfolioValuationLedger
    ) -> None:
        self.path = Path(path)
        self.valuation_ledger = valuation_ledger

    def records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        raw = self.path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise LedgerIntegrityError(
                "Portfolio cash-flow ledger has an incomplete final line; run explicit tail repair."
            )
        records = []
        with self.path.open("r", encoding="utf-8") as source:
            for line_number, line in enumerate(source, start=1):
                if not line.strip():
                    raise LedgerIntegrityError(
                        f"Blank portfolio cash-flow line at {line_number}."
                    )
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as error:
                    raise LedgerIntegrityError(
                        f"Invalid JSON at portfolio cash-flow line {line_number}."
                    ) from error
                if not isinstance(record, dict):
                    raise LedgerIntegrityError(
                        f"Portfolio cash-flow line {line_number} is not an object."
                    )
                records.append(record)
        return records

    def record(
        self,
        *,
        portfolio_version: str,
        horizon: str,
        flow_type: str,
        amount: Any,
        recorded_at: str | datetime | None = None,
        allow_existing: bool = True,
    ) -> dict[str, Any]:
        version = str(portfolio_version or "").strip()
        resolved_horizon = str(horizon or "").upper()
        resolved_type = str(flow_type or "").upper()
        if resolved_type not in FLOW_TYPES:
            raise ValueError("flow_type must be CONTRIBUTION or WITHDRAWAL")
        value = _positive_fraction(amount, "amount")
        valuations = self.valuation_ledger.verify()
        valuation = next(
            (
                item
                for item in valuations
                if item.get("portfolio_version") == version
                and item.get("horizon") == resolved_horizon
            ),
            None,
        )
        if valuation is None:
            raise ValueError("A verified portfolio valuation is required at the cash-flow boundary")
        recorded = _as_datetime(recorded_at or datetime.now(timezone.utc))
        effective = _as_datetime(valuation["outcome_asset_price_effective_at"])
        if recorded < max(effective, _as_datetime(valuation["calculated_at"])):
            raise ValueError("recorded_at cannot predate the supporting valuation")
        if recorded > datetime.now(timezone.utc) + MAX_CLOCK_SKEW:
            raise ValueError("recorded_at cannot be in the future")
        existing = [
            item for item in self.verify() if item["portfolio_version"] == version
        ]
        if existing and effective <= _as_datetime(existing[-1]["effective_at"]):
            retry_id = _flow_id(version, valuation["valuation_id"], resolved_type)
            retry = next((item for item in existing if item["flow_id"] == retry_id), None)
            if retry is not None and allow_existing:
                proposed_amount = _fraction(retry["exact_amount"], "existing amount")
                if proposed_amount == value and retry["recorded_at"] == recorded.isoformat():
                    return retry
            raise LedgerIntegrityError(
                "Portfolio cash flows must use strictly increasing valuation boundaries"
            )
        prior_net = sum(
            (
                _fraction(item["exact_signed_amount"], "signed cash flow")
                for item in existing
            ),
            Fraction(0),
        )
        base_cash = _fraction(
            valuation["exact_fractions"]["remaining_cash"], "remaining cash"
        )
        signed = value if resolved_type == "CONTRIBUTION" else -value
        cash_after = base_cash + prior_net + signed
        if cash_after < 0:
            raise ValueError("Withdrawal exceeds simulated cash available after valuation")
        result = {
            "schema_version": PORTFOLIO_CASH_FLOW_SCHEMA_VERSION,
            "policy_version": PORTFOLIO_CASH_FLOW_POLICY_VERSION,
            "flow_id": _flow_id(version, valuation["valuation_id"], resolved_type),
            "status": "RECORDED",
            "record_type": "SIMULATED_EXTERNAL_PORTFOLIO_CASH_FLOW",
            "flow_type": resolved_type,
            "simulation_only": True,
            "currency": "USD",
            "amount": _decimal_string(value),
            "signed_amount": _decimal_string(signed),
            "exact_amount": _fraction_material(value),
            "exact_signed_amount": _fraction_material(signed),
            "cash_after_flow": _decimal_string(cash_after),
            "exact_cash_after_flow": _fraction_material(cash_after),
            "effective_at": effective.isoformat(),
            "recorded_at": recorded.isoformat(),
            "timing_policy": "AFTER_MARKET_VALUATION_END_OF_SUBPERIOD",
            "portfolio_version": version,
            "horizon": resolved_horizon,
            "valuation_id": valuation["valuation_id"],
            "valuation_record_hash": valuation["record_hash"],
            "portfolio_return_calculated": False,
            "alpha_calculated": False,
            "learning_eligible": False,
            "track_record_claim": False,
            "strategy_version": valuation["strategy_version"],
            "model_versions": valuation["model_versions"],
            "git_revision": valuation["git_revision"],
        }
        return self._append(result, allow_existing=allow_existing)

    def verify(self) -> list[dict[str, Any]]:
        valuations = {
            item["valuation_id"]: item for item in self.valuation_ledger.verify()
        }
        previous_hash = GENESIS_HASH
        seen_ids = set()
        last_effective_by_portfolio: dict[str, datetime] = {}
        net_by_portfolio: dict[str, Fraction] = {}
        records = self.records()
        for index, record in enumerate(records, start=1):
            material = {key: value for key, value in record.items() if key != "record_hash"}
            if record.get("previous_hash") != previous_hash:
                raise LedgerIntegrityError(
                    f"Portfolio cash-flow chain is broken at record {index}."
                )
            if record.get("record_hash") != _record_hash(material):
                raise LedgerIntegrityError(
                    f"Portfolio cash-flow record {index} has been modified."
                )
            valuation = valuations.get(record.get("valuation_id"))
            if valuation is None:
                raise LedgerIntegrityError(
                    f"Portfolio cash-flow record {index} lost its valuation."
                )
            version = str(record.get("portfolio_version") or "")
            flow_type = str(record.get("flow_type") or "")
            try:
                value = _fraction(record.get("exact_amount") or {}, "amount")
                signed = _fraction(
                    record.get("exact_signed_amount") or {}, "signed amount"
                )
                stored_cash_after = _fraction(
                    record.get("exact_cash_after_flow") or {}, "cash after flow"
                )
                effective = _as_datetime(record.get("effective_at"))
                recorded = _as_datetime(record.get("recorded_at"))
                base_cash = _fraction(
                    valuation["exact_fractions"]["remaining_cash"], "remaining cash"
                )
            except (TypeError, ValueError) as error:
                raise LedgerIntegrityError(
                    f"Portfolio cash-flow record {index} has invalid values."
                ) from error
            expected_signed = value if flow_type == "CONTRIBUTION" else -value
            prior_net = net_by_portfolio.get(version, Fraction(0))
            expected_cash_after = base_cash + prior_net + expected_signed
            expected_id = _flow_id(version, valuation["valuation_id"], flow_type)
            last_effective = last_effective_by_portfolio.get(version)
            boundary = (
                bool(version)
                and flow_type in FLOW_TYPES
                and value > 0
                and signed == expected_signed
                and expected_cash_after >= 0
                and stored_cash_after == expected_cash_after
                and record.get("amount") == _decimal_string(value)
                and record.get("signed_amount") == _decimal_string(signed)
                and record.get("cash_after_flow") == _decimal_string(expected_cash_after)
                and record.get("exact_amount") == _fraction_material(value)
                and record.get("exact_signed_amount") == _fraction_material(signed)
                and record.get("exact_cash_after_flow")
                == _fraction_material(expected_cash_after)
                and record.get("schema_version") == PORTFOLIO_CASH_FLOW_SCHEMA_VERSION
                and record.get("policy_version") == PORTFOLIO_CASH_FLOW_POLICY_VERSION
                and record.get("flow_id") == expected_id
                and expected_id not in seen_ids
                and record.get("status") == "RECORDED"
                and record.get("record_type")
                == "SIMULATED_EXTERNAL_PORTFOLIO_CASH_FLOW"
                and record.get("simulation_only") is True
                and record.get("currency") == "USD"
                and effective
                == _as_datetime(valuation["outcome_asset_price_effective_at"])
                and (last_effective is None or effective > last_effective)
                and recorded >= max(effective, _as_datetime(valuation["calculated_at"]))
                and recorded <= datetime.now(timezone.utc) + MAX_CLOCK_SKEW
                and record.get("timing_policy")
                == "AFTER_MARKET_VALUATION_END_OF_SUBPERIOD"
                and record.get("horizon") == valuation["horizon"]
                and record.get("valuation_record_hash") == valuation["record_hash"]
                and record.get("portfolio_return_calculated") is False
                and record.get("alpha_calculated") is False
                and record.get("learning_eligible") is False
                and record.get("track_record_claim") is False
                and record.get("strategy_version") == valuation["strategy_version"]
                and record.get("model_versions") == valuation["model_versions"]
                and record.get("git_revision") == valuation["git_revision"]
            )
            if not boundary:
                raise LedgerIntegrityError(
                    f"Portfolio cash-flow record {index} violates its boundary."
                )
            seen_ids.add(expected_id)
            last_effective_by_portfolio[version] = effective
            net_by_portfolio[version] = prior_net + signed
            previous_hash = record["record_hash"]
        return records

    def _append(self, result: dict[str, Any], *, allow_existing: bool) -> dict[str, Any]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            records = self.verify()
            existing = next(
                (item for item in records if item["flow_id"] == result["flow_id"]), None
            )
            if existing:
                ignored = {"previous_hash", "record_hash"}
                current = {key: value for key, value in existing.items() if key not in ignored}
                proposed = {key: value for key, value in result.items() if key not in ignored}
                if allow_existing and current == proposed:
                    return existing
                raise LedgerIntegrityError(
                    f"Portfolio cash flow {result['flow_id']} already exists."
                )
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

    def repair_incomplete_tail(self) -> Path | None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            if not self.path.exists():
                return None
            raw = self.path.read_bytes()
            if not raw or raw.endswith(b"\n"):
                return None
            complete_end = raw.rfind(b"\n") + 1
            prefix, tail = raw[:complete_end], raw[complete_end:]
            try:
                json.loads(tail.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                backup = self.path.with_suffix(
                    self.path.suffix + f".incomplete-tail-{uuid4().hex}"
                )
                backup_descriptor = os.open(
                    backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
                )
                try:
                    _write_all(backup_descriptor, tail)
                    os.fsync(backup_descriptor)
                finally:
                    os.close(backup_descriptor)
                target = os.open(self.path, os.O_WRONLY | os.O_TRUNC)
                try:
                    _write_all(target, prefix)
                    os.fsync(target)
                finally:
                    os.close(target)
                self.verify()
                return backup
            target = os.open(self.path, os.O_WRONLY | os.O_APPEND)
            try:
                _write_all(target, b"\n")
                os.fsync(target)
            finally:
                os.close(target)
            self.verify()
            return None
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)
