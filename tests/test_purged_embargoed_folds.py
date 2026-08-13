import pytest

from core.orchestration.purged_embargoed_folds import PurgedEmbargoedForwardFolds


OBSERVATIONS = [
    {"observation_id": "A", "decision_at": "2020-01-01T00:00:00+00:00", "label_end_at": "2020-02-01T00:00:00+00:00"},
    {"observation_id": "B", "decision_at": "2020-02-01T00:00:00+00:00", "label_end_at": "2020-05-01T00:00:00+00:00"},
    {"observation_id": "C", "decision_at": "2020-04-01T00:00:00+00:00", "label_end_at": "2020-04-20T00:00:00+00:00"},
    {"observation_id": "D", "decision_at": "2020-05-01T00:00:00+00:00", "label_end_at": "2020-06-01T00:00:00+00:00"},
    {"observation_id": "E", "decision_at": "2020-06-03T00:00:00+00:00", "label_end_at": "2020-07-01T00:00:00+00:00"},
    {"observation_id": "F", "decision_at": "2020-08-01T00:00:00+00:00", "label_end_at": "2020-09-01T00:00:00+00:00"},
]
WINDOWS = [{"test_not_before": "2020-05-01T00:00:00+00:00", "test_not_after": "2020-06-01T00:00:00+00:00"}]


def build(**overrides):
    values = {"observations": OBSERVATIONS, "test_windows": WINDOWS, "embargo_days": 7}
    values.update(overrides)
    return PurgedEmbargoedForwardFolds.build(**values)


def test_purges_overlapping_train_labels_and_declares_post_test_embargo():
    result = build()
    fold = result["folds"][0]
    assert fold["train_observation_ids"] == ["A", "C"]
    assert fold["purged_observation_ids"] == ["B"]
    assert fold["test_observation_ids"] == ["D"]
    assert fold["embargoed_observation_ids"] == ["E"]
    assert fold["train_labels_end_strictly_before_test"] is True
    assert result["random_split_allowed"] is False
    assert result["model_trained"] is False
    assert result["replay_executed"] is False


def test_deterministic_identity_is_order_independent():
    first = build()
    second = build(observations=list(reversed(OBSERVATIONS)))
    assert first == second


def test_long_horizon_label_at_test_boundary_is_purged():
    fold = build()["folds"][0]
    assert "B" in fold["purged_observation_ids"]
    assert "B" not in fold["train_observation_ids"]


@pytest.mark.parametrize("changes,fragment", [
    ({"embargo_days": 0}, "between"),
    ({"test_windows": []}, "cannot be empty"),
    ({"test_windows": [{"test_not_before": "2020-06-01T00:00:00+00:00", "test_not_after": "2020-05-01T00:00:00+00:00"}]}, "later"),
    ({"test_windows": [{"test_not_before": "2021-01-01T00:00:00+00:00", "test_not_after": "2021-02-01T00:00:00+00:00"}]}, "contain at least one"),
    ({"observations": [{"observation_id": "A", "decision_at": "2020-01-01T00:00:00+00:00", "label_end_at": "2020-01-01T00:00:00+00:00"}]}, "later than"),
])
def test_invalid_folds_fail_closed(changes, fragment):
    with pytest.raises(ValueError, match=fragment):
        build(**changes)


def test_overlapping_test_windows_are_rejected():
    windows = [
        {"test_not_before": "2020-05-01T00:00:00+00:00", "test_not_after": "2020-06-15T00:00:00+00:00"},
        {"test_not_before": "2020-06-01T00:00:00+00:00", "test_not_after": "2020-09-01T00:00:00+00:00"},
    ]
    with pytest.raises(ValueError, match="non-overlapping"):
        build(test_windows=windows)
