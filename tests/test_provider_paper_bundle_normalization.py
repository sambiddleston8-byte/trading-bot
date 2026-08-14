from datetime import datetime, timedelta, timezone
from decimal import Decimal
from fractions import Fraction
import inspect
import json

import pytest

from core.broker import (
    PaperReadOnlyCollectionBundleLedger,
    ProviderPaperBundleNormalizationLedger,
)
from core.broker import provider_paper_bundle_normalization as module
from core.decision_ledger import GENESIS_HASH, LedgerIntegrityError


BASE = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)


def body(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def account(**changes):
    value = {
        "id": "synthetic-paper-account",
        "status": "ACTIVE",
        "currency": "USD",
        "cash": "1000",
        "buying_power": "1000",
        "equity": "1650",
    }
    value.update(changes)
    return body(value)


def positions(*items):
    values = list(items) or [
        {
            "symbol": "AAPL",
            "asset_class": "us_equity",
            "side": "long",
            "qty": "5",
            "current_price": "100",
            "market_value": "500",
        },
        {
            "symbol": "MSFT",
            "asset_class": "us_equity",
            "side": "long",
            "qty": "3",
            "current_price": "50",
            "market_value": "150",
        },
    ]
    return body(values)


def order(identifier="buy-1", **changes):
    value = {
        "id": identifier,
        "symbol": "MSFT",
        "asset_class": "us_equity",
        "side": "buy",
        "status": "new",
        "type": "limit",
        "order_class": "",
        "legs": None,
        "qty": "2",
        "notional": None,
        "filled_qty": "0",
        "limit_price": "60",
    }
    value.update(changes)
    return value


def orders(*items):
    values = list(items) or [
        order(),
        order(
            "sell-1",
            symbol="AAPL",
            side="sell",
            qty="2",
            filled_qty="0.5",
            limit_price="95",
        ),
    ]
    return body(values)


def source_ledger(tmp_path):
    return PaperReadOnlyCollectionBundleLedger(
        tmp_path / "source.jsonl", tmp_path / "source-blobs"
    )


def admit_source(
    source,
    *,
    start=0,
    opening=None,
    closing=None,
    position_payload=None,
    order_payload=None,
):
    opening = opening or account()
    closing = closing or opening
    payloads = {
        "ACCOUNT_OPEN": opening,
        "POSITIONS": position_payload or positions(),
        "OPEN_ORDERS": order_payload or orders(),
        "ACCOUNT_CLOSE": closing,
    }
    return source._admit(
        payloads=payloads,
        fetched_at={
            kind: BASE + timedelta(seconds=start + index + 1)
            for index, kind in enumerate(module.PART_KINDS)
        },
        collection_started_at=BASE + timedelta(seconds=start),
        collection_finished_at=BASE + timedelta(seconds=start + 4),
        recorded_at=BASE + timedelta(seconds=start + 5),
        monotonic_elapsed_microseconds=4_000_000,
    )


def ledgers(tmp_path, **source_changes):
    source = source_ledger(tmp_path)
    bundle = admit_source(source, **source_changes)
    normalization = ProviderPaperBundleNormalizationLedger(
        tmp_path / "normalized.jsonl", source
    )
    return source, bundle, normalization


def normalize(target, bundle, *, seconds=10):
    return target.record(
        collection_bundle_id=bundle["bundle_id"],
        recorded_at=BASE + timedelta(seconds=seconds),
    )


def test_valid_bundle_is_normalized_but_never_adoption_eligible(tmp_path):
    _, bundle, target = ledgers(tmp_path)
    result = normalize(target, bundle)
    assert result["status"] == "NORMALIZED_AWAITING_EXTERNAL_EVIDENCE"
    assert result["normalization_succeeded"] is True
    assert result["normalization_errors"] == []
    assert result["downstream_blocking_reasons"] == [
        "POSITIONS_ORDERS_ACCOUNT_BINDING_UNPROVEN",
        "PREVIOUS_CLOSE_EVIDENCE_REQUIRED",
        "SETTLEMENT_BREAKDOWN_EVIDENCE_REQUIRED",
    ]
    assert result["account_values"] == {
        "cash": "1000",
        "buying_power": "1000",
        "equity": "1650",
    }
    assert result["candidate_position_gross_exposure_usd"] == "650"
    assert result["candidate_pending_buy_notional_usd"] == "120"
    assert result["candidate_pending_sell_notional_usd"] == "142.5"
    assert result["previous_hash"] == GENESIS_HASH
    assert result["account_open_payload_sha256"] == result[
        "account_close_payload_sha256"
    ]
    for name in module.TRUE_FIELDS:
        assert result[name] is True
    for name in module.FALSE_FIELDS:
        assert result[name] is False
    assert target.verify() == [result]
    assert target.path.stat().st_mode & 0o777 == 0o600


def assert_blocked(result, reason):
    assert result["status"] == "NORMALIZATION_BLOCKED"
    assert result["normalization_succeeded"] is False
    assert result["normalization_errors"] == [reason]
    assert result["account_values"] is None
    assert result["positions"] == []
    assert result["open_orders"] == []
    assert result["candidate_position_gross_exposure_usd"] is None
    assert result["candidate_pending_buy_notional_usd"] is None
    assert result["candidate_pending_sell_notional_usd"] is None
    assert result["exact_fractions"] == {}


def test_account_value_drift_is_audited_without_monetary_values(tmp_path):
    _, bundle, target = ledgers(tmp_path, closing=account(cash="999"))
    result = normalize(target, bundle)
    assert_blocked(result, "ACCOUNT_VALUE_DRIFT")
    assert target.verify() == [result]


def test_position_quantity_price_mismatch_blocks(tmp_path):
    bad = positions(
        {
            "symbol": "AAPL",
            "asset_class": "us_equity",
            "side": "long",
            "qty": "5",
            "current_price": "100",
            "market_value": "499.99",
        }
    )
    _, bundle, target = ledgers(tmp_path, position_payload=bad, order_payload=orders())
    assert_blocked(normalize(target, bundle), "QUANTITY_PRICE_IDENTITY_MISMATCH")


@pytest.mark.parametrize(
    "order_type,reason",
    [
        ("market", "MARKET_ORDER_NO_EXACT_PRICE"),
        ("stop", "STOP_ORDER_TRIGGER_NOT_FILL_PRICE"),
    ],
)
def test_orders_without_exact_fill_bound_block(tmp_path, order_type, reason):
    payload = orders(order(type=order_type, limit_price=None))
    _, bundle, target = ledgers(tmp_path, order_payload=payload)
    assert_blocked(normalize(target, bundle), reason)


def test_unfilled_notional_limit_is_exact_but_quantity_evidence_is_required(tmp_path):
    payload = orders(order(qty=None, notional="250", filled_qty="0"))
    _, bundle, target = ledgers(tmp_path, order_payload=payload)
    result = normalize(target, bundle)
    assert result["normalization_succeeded"] is True
    assert result["candidate_pending_buy_notional_usd"] == "250"
    assert result["open_orders"][0]["remaining_quantity"] is None
    assert "NOTIONAL_ORDER_QUANTITY_EVIDENCE_REQUIRED" in result[
        "downstream_blocking_reasons"
    ]


def test_notional_sell_still_requires_a_verified_holding_and_value_bound(tmp_path):
    absent = orders(
        order(side="sell", symbol="TSLA", qty=None, notional="10", filled_qty="0")
    )
    _, bundle, target = ledgers(tmp_path / "absent", order_payload=absent)
    assert_blocked(normalize(target, bundle), "SELL_SYMBOL_NOT_HELD")

    excessive = orders(
        order(side="sell", symbol="AAPL", qty=None, notional="501", filled_qty="0")
    )
    _, bundle, target = ledgers(tmp_path / "excessive", order_payload=excessive)
    assert_blocked(normalize(target, bundle), "SELL_EXCEEDS_POSITION")


@pytest.mark.parametrize(
    "sell_orders",
    [
        [
            order("n1", side="sell", symbol="AAPL", qty=None, notional="300", limit_price="100"),
            order("n2", side="sell", symbol="AAPL", qty=None, notional="300", limit_price="100"),
        ],
        [
            order("q1", side="sell", symbol="AAPL", qty="4", limit_price="100"),
            order("n1", side="sell", symbol="AAPL", qty=None, notional="200", limit_price="100"),
        ],
    ],
)
def test_all_sell_orders_are_aggregated_against_one_holding(tmp_path, sell_orders):
    _, bundle, target = ledgers(tmp_path, order_payload=orders(*sell_orders))
    assert_blocked(normalize(target, bundle), "SELL_EXCEEDS_POSITION")


def test_partially_filled_notional_order_blocks(tmp_path):
    payload = orders(order(qty=None, notional="250", filled_qty="0.5"))
    _, bundle, target = ledgers(tmp_path, order_payload=payload)
    assert_blocked(normalize(target, bundle), "NOTIONAL_ORDER_PARTIALLY_FILLED")


def test_fully_filled_order_on_open_endpoint_blocks(tmp_path):
    payload = orders(order(qty="2", filled_qty="2"))
    _, bundle, target = ledgers(tmp_path, order_payload=payload)
    assert_blocked(normalize(target, bundle), "REMAINING_QUANTITY_NON_POSITIVE")


@pytest.mark.parametrize(
    "item,reason",
    [
        (order(side="sell", symbol="TSLA", qty="1"), "SELL_SYMBOL_NOT_HELD"),
        (order(side="sell", symbol="AAPL", qty="6"), "SELL_EXCEEDS_POSITION"),
    ],
)
def test_sell_orders_cannot_exceed_verified_holdings(tmp_path, item, reason):
    _, bundle, target = ledgers(tmp_path, order_payload=orders(item))
    assert_blocked(normalize(target, bundle), reason)


@pytest.mark.parametrize(
    "field,value",
    [
        ("current_price", 100.0),
        ("current_price", True),
        ("current_price", " NaN"),
        ("market_value", "Infinity"),
        ("qty", "-1"),
        ("qty", "1E+999999999"),
        ("qty", "0.0000000000000000001"),
    ],
)
def test_ambiguous_or_nonfinite_numbers_are_never_coerced(tmp_path, field, value):
    item = {
        "symbol": "AAPL",
        "asset_class": "us_equity",
        "side": "long",
        "qty": "5",
        "current_price": "100",
        "market_value": "500",
    }
    item[field] = value
    _, bundle, target = ledgers(
        tmp_path, position_payload=positions(item), order_payload=orders()
    )
    assert_blocked(normalize(target, bundle), "NON_DECIMAL_STRING")


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"asset_class": "crypto"}, "UNSUPPORTED_ASSET_CLASS"),
        ({"order_class": "bracket", "legs": [{}]}, "COMPLEX_ORDER_UNSUPPORTED"),
        ({"qty": None, "notional": None}, "ORDER_SIZE_AMBIGUOUS"),
        ({"side": "hold"}, "UNSUPPORTED_ORDER_SIDE"),
        ({"type": "trailing_stop"}, "UNSUPPORTED_ORDER_TYPE"),
    ],
)
def test_unsupported_order_semantics_have_stable_block_codes(tmp_path, changes, reason):
    _, bundle, target = ledgers(tmp_path, order_payload=orders(order(**changes)))
    assert_blocked(normalize(target, bundle), reason)


def test_inconsistent_account_values_block(tmp_path):
    _, bundle, target = ledgers(tmp_path, opening=account(cash="1700", equity="1650"))
    assert_blocked(normalize(target, bundle), "ACCOUNT_VALUE_INCONSISTENT")


def test_account_equity_must_equal_cash_plus_long_position_value(tmp_path):
    opening = account(cash="1000", equity="1000")
    _, bundle, target = ledgers(tmp_path, opening=opening)
    assert_blocked(normalize(target, bundle), "ACCOUNT_POSITION_VALUE_INCONSISTENT")


def test_terminal_order_status_is_rejected_defensively():
    payloads = {
        "ACCOUNT_OPEN": account(),
        "ACCOUNT_CLOSE": account(),
        "POSITIONS": positions(),
        "OPEN_ORDERS": orders(order(status="filled")),
    }
    with pytest.raises(module.PaperBundleNormalizationError, match="ORDER_STATUS_NOT_OPEN"):
        module._normalize_payloads(payloads)


def test_account_status_and_currency_are_rechecked_defensively():
    for changes in ({"status": "INACTIVE"}, {"currency": "GBP"}):
        payloads = {
            "ACCOUNT_OPEN": account(**changes),
            "ACCOUNT_CLOSE": account(**changes),
            "POSITIONS": positions(),
            "OPEN_ORDERS": orders(),
        }
        with pytest.raises(
            module.PaperBundleNormalizationError,
            match="ACCOUNT_STATUS_CURRENCY_UNSUPPORTED",
        ):
            module._normalize_payloads(payloads)


def test_decimal_strings_and_exact_fractions_are_identical_beyond_28_digits(tmp_path):
    precise = "1" + ("0" * 28) + "1"
    precise_equity = str(int(precise) * 2)
    opening = account(cash=precise, buying_power=precise_equity, equity=precise_equity)
    precise_position = positions(
        {
            "symbol": "AAPL",
            "asset_class": "us_equity",
            "side": "long",
            "qty": precise,
            "current_price": "1",
            "market_value": precise,
        }
    )
    _, bundle, target = ledgers(
        tmp_path,
        opening=opening,
        position_payload=precise_position,
        order_payload=body([]),
    )
    result = normalize(target, bundle)
    assert result["normalization_succeeded"] is True
    assert result["account_values"]["cash"] == precise
    assert result["positions"][0]["quantity"] == precise

    def exact(material):
        return Fraction(int(material["numerator"]), int(material["denominator"]))

    assert exact(result["exact_fractions"]["account_values"]["cash"]) == Fraction(
        Decimal(result["account_values"]["cash"])
    )
    assert exact(result["exact_fractions"]["positions"][0]["quantity"]) == Fraction(
        Decimal(result["positions"][0]["quantity"])
    )
    assert exact(
        result["exact_fractions"]["candidate_position_gross_exposure_usd"]
    ) == Fraction(Decimal(result["candidate_position_gross_exposure_usd"]))


def test_source_tamper_stops_record_and_later_verification(tmp_path):
    source, bundle, target = ledgers(tmp_path)
    result = normalize(target, bundle)
    part = next(item for item in bundle["parts"] if item["part_kind"] == "POSITIONS")
    blob = source.blob_directory / part["blob_relative_path"]
    blob.chmod(0o600)
    blob.write_bytes(b"[]")
    blob.chmod(0o400)
    with pytest.raises(LedgerIntegrityError):
        target.verify()
    other = ProviderPaperBundleNormalizationLedger(tmp_path / "other.jsonl", source)
    with pytest.raises(LedgerIntegrityError):
        other.record(collection_bundle_id=bundle["bundle_id"])
    assert result["collection_bundle_id"] == bundle["bundle_id"]


def test_idempotence_and_forward_only_account_observations(tmp_path):
    source = source_ledger(tmp_path)
    older = admit_source(source, start=0)
    newer = admit_source(source, start=20)
    target = ProviderPaperBundleNormalizationLedger(tmp_path / "normalized.jsonl", source)
    first = normalize(target, newer, seconds=30)
    assert normalize(target, newer, seconds=31) == first
    with pytest.raises(ValueError, match="move forward"):
        normalize(target, older, seconds=32)
    assert target.verify() == [first]


def test_same_account_multiple_forward_records_verify_end_to_end(tmp_path):
    source = source_ledger(tmp_path)
    first_bundle = admit_source(source, start=0)
    second_bundle = admit_source(source, start=20)
    target = ProviderPaperBundleNormalizationLedger(tmp_path / "normalized.jsonl", source)
    first = normalize(target, first_bundle, seconds=30)
    second = normalize(target, second_bundle, seconds=31)
    assert second["previous_hash"] == first["record_hash"]
    assert target.verify() == [first, second]


def test_two_record_chain_links_and_reordering_is_detected(tmp_path):
    source = source_ledger(tmp_path)
    first_bundle = admit_source(source, start=0)
    other = account(id="another-synthetic-paper-account")
    second_bundle = admit_source(source, start=20, opening=other, closing=other)
    target = ProviderPaperBundleNormalizationLedger(tmp_path / "normalized.jsonl", source)
    first = normalize(target, first_bundle, seconds=30)
    second = normalize(target, second_bundle, seconds=31)
    assert second["previous_hash"] == first["record_hash"]
    assert target.verify() == [first, second]
    lines = target.path.read_text().splitlines()
    target.path.write_text(lines[1] + "\n" + lines[0] + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_rehashed_normalization_cannot_expand_authority(tmp_path):
    _, bundle, target = ledgers(tmp_path)
    normalize(target, bundle)
    record = json.loads(target.path.read_text())
    record["paper_order_submission_allowed"] = True
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_rehashed_normalization_field_alphabet_cannot_change(tmp_path):
    _, bundle, target = ledgers(tmp_path)
    normalize(target, bundle)
    record = json.loads(target.path.read_text())
    record["unexpected_authority"] = False
    material = {key: value for key, value in record.items() if key != "record_hash"}
    record["record_hash"] = module._record_hash(material)
    target.path.write_text(json.dumps(record) + "\n")
    with pytest.raises(LedgerIntegrityError):
        target.verify()


def test_unsafe_ledger_links_and_modes_fail_closed(tmp_path):
    for case in ("symlink", "hardlink", "mode"):
        case_path = tmp_path / case
        case_path.mkdir(mode=0o700)
        source = source_ledger(case_path)
        bundle = admit_source(source)
        target = ProviderPaperBundleNormalizationLedger(
            case_path / "normalized.jsonl", source
        )
        outside = case_path / "outside"
        outside.write_bytes(b"")
        outside.chmod(0o600)
        if case == "symlink":
            target.path.symlink_to(outside)
        elif case == "hardlink":
            target.path.hardlink_to(outside)
        else:
            target.path.write_bytes(b"")
            target.path.chmod(0o644)
        with pytest.raises(LedgerIntegrityError):
            normalize(target, bundle)


def test_normalizer_has_no_network_order_or_downstream_write_surface(tmp_path):
    _, bundle, target = ledgers(tmp_path)
    normalize(target, bundle)
    source = inspect.getsource(module).lower()
    for token in ("requests", ".post(", ".put(", ".patch(", ".delete("):
        assert token not in source
    for name in (
        "submit",
        "cancel",
        "replace",
        "record_snapshot",
        "record_risk_snapshot",
        "connect",
    ):
        assert not hasattr(ProviderPaperBundleNormalizationLedger, name)
    assert list(tmp_path.glob("*"))
    assert not (tmp_path / "account-snapshot.jsonl").exists()
    assert not (tmp_path / "risk-snapshot.jsonl").exists()
