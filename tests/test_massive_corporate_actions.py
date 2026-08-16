import json

import pytest

from core.orchestration.massive_corporate_actions import normalize_corporate_actions


RETRIEVED = "2025-08-01T12:00:00+00:00"
DECISION = "2025-08-02T12:00:00+00:00"


def encoded(results, **extra):
    return json.dumps({"status": "OK", "results": results, **extra}, separators=(",", ":")).encode()


def dividend(**changes):
    value = {
        "id": "div-1", "ticker": "SPY", "cash_amount": "1.759",
        "currency": "USD", "declaration_date": "2025-06-12",
        "ex_dividend_date": "2025-06-20", "pay_date": "2025-07-31",
        "record_date": "2025-06-20", "reported_at": "2025-06-12T16:00:00+00:00",
    }
    value.update(changes)
    return value


def split(**changes):
    value = {
        "id": "split-1", "ticker": "AAPL", "execution_date": "2025-01-15",
        "split_from": "1", "split_to": "4", "adjustment_type": "forward_split",
        "reported_at": "2025-01-01T14:00:00+00:00",
    }
    value.update(changes)
    return value


def test_synthetic_dividend_normalizes_with_reported_at_as_unqualified_availability():
    result = normalize_corporate_actions(
        payload=encoded([dividend()]), kind="DIVIDEND", retrieved_at=RETRIEVED, decision_at=DECISION
    )
    assert result[0]["available_at"] == "2025-06-12T16:00:00.000000+00:00"
    assert result[0]["payload"]["cash_amount"] == "1.759"
    assert result[0]["payload"]["point_in_time_basis"] == "SYNTHETIC_REPORTED_AT_UNQUALIFIED"


def test_synthetic_split_preserves_exact_ratio_and_effective_date():
    result = normalize_corporate_actions(
        payload=encoded([split()]), kind="STOCK_SPLIT", retrieved_at=RETRIEVED, decision_at=DECISION
    )
    assert result[0]["payload"]["split_from"] == "1"
    assert result[0]["payload"]["split_to"] == "4"
    assert result[0]["effective_at"].startswith("2025-01-15T00:00:00")


def test_action_reported_after_decision_cutoff_is_rejected():
    with pytest.raises(ValueError, match="available_at is after the decision timestamp"):
        normalize_corporate_actions(
            payload=encoded([dividend()]), kind="DIVIDEND", retrieved_at=RETRIEVED,
            decision_at="2025-06-01T12:00:00+00:00",
        )


@pytest.mark.parametrize("payload", [
    encoded([dividend(historical_adjustment_factor="2")]),
    encoded([dividend(), dividend()]),
    encoded([dividend(reported_at="2025-08-02T00:00:00+00:00")]),
])
def test_dividend_parser_fails_closed_on_forbidden_duplicate_or_future_data(payload):
    with pytest.raises(ValueError):
        normalize_corporate_actions(
            payload=payload, kind="DIVIDEND", retrieved_at=RETRIEVED, decision_at=DECISION
        )


def test_pagination_and_binary_float_ratios_fail_closed():
    with pytest.raises(ValueError):
        normalize_corporate_actions(
            payload=encoded([split()], next_url="https://api.massive.com/page/2"),
            kind="STOCK_SPLIT", retrieved_at=RETRIEVED, decision_at=DECISION,
        )
    with pytest.raises(ValueError):
        normalize_corporate_actions(
            payload=encoded([split()], count=2), kind="STOCK_SPLIT",
            retrieved_at=RETRIEVED, decision_at=DECISION,
        )
    with pytest.raises(ValueError):
        normalize_corporate_actions(
            payload=encoded([split(split_to=4.0)]), kind="STOCK_SPLIT",
            retrieved_at=RETRIEVED, decision_at=DECISION,
        )


@pytest.mark.parametrize("event", [
    dividend(ticker="TSLA"),
    dividend(currency="GBP"),
    dividend(declaration_date="2025-06-21"),
    dividend(ex_dividend_date="2024-08-31"),
])
def test_campaign_scope_and_dividend_chronology_fail_closed(event):
    with pytest.raises(ValueError):
        normalize_corporate_actions(
            payload=encoded([event]), kind="DIVIDEND",
            retrieved_at=RETRIEVED, decision_at=DECISION,
        )
