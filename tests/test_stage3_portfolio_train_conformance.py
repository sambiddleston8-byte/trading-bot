from pathlib import Path

from core.research.stage3_portfolio_train_conformance import (
    STATUS,
    evaluate_train_portfolio_conformance,
)


def test_train_portfolio_conformance_is_bounded_and_deterministic():
    report = evaluate_train_portfolio_conformance(Path.cwd(), write_output=False)
    assert report["status"] == STATUS
    assert report["partition_role"] == "TRAIN"
    assert report["portfolio_wide_batching_complete"] is True
    assert report["shared_cash_reservation_complete"] is True
    assert report["cross_symbol_order_conformance_complete"] is True
    assert report["validation_data_read"] is False
    assert report["untouched_test_included"] is False
    assert report["promotion_allowed"] is False
    assert all(
        scenario["input_order_conformance"] is True
        for scenario in report["scenarios"].values()
    )
    assert report["report_sha256"] == (
        "84b60115a60691aa1fea0ac938b5f0cb28b27c718632409f2f884e872914b442"
    )
