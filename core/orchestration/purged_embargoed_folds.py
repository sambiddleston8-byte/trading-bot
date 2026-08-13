from __future__ import annotations

"""Deterministic time-aware folds that purge overlapping outcome labels."""

from datetime import datetime, timedelta
import hashlib
import json
from typing import Any, Mapping, Sequence

from core.decision_ledger import canonical_timestamp


POLICY_VERSION = "purged-forward-chaining-embargo-v1"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _timestamp(value: str | datetime, name: str) -> datetime:
    try:
        return datetime.fromisoformat(canonical_timestamp(value))
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be a timezone-aware timestamp") from error


def _positive_days(value: Any, name: str, maximum: int = 3650) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    try:
        resolved = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{name} must be an integer") from error
    if str(resolved) != str(value).strip() or not 1 <= resolved <= maximum:
        raise ValueError(f"{name} must be between 1 and {maximum}")
    return resolved


def _normalise_observations(values: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    seen: set[str] = set()
    for value in values:
        identifier = str(value.get("observation_id") or "").strip()
        if not identifier or identifier in seen:
            raise ValueError("observation_id must be present and unique")
        decision = _timestamp(value.get("decision_at"), "decision_at")
        label_end = _timestamp(value.get("label_end_at"), "label_end_at")
        if label_end <= decision:
            raise ValueError("label_end_at must be later than decision_at")
        records.append({
            "observation_id": identifier,
            "decision_at": decision.isoformat(),
            "label_end_at": label_end.isoformat(),
        })
        seen.add(identifier)
    if not records:
        raise ValueError("observations cannot be empty")
    return sorted(records, key=lambda item: (item["decision_at"], item["observation_id"]))


def _fold_id(index: int, train_ids: list[str], test_ids: list[str], embargo_days: int) -> str:
    material = [index, train_ids, test_ids, embargo_days, POLICY_VERSION]
    return "FOLD-" + hashlib.sha256(
        _canonical_json(material).encode("utf-8")
    ).hexdigest()[:24].upper()


class PurgedEmbargoedForwardFolds:
    """Build expanding forward folds without train labels overlapping test time."""

    @classmethod
    def build(
        cls,
        observations: Sequence[Mapping[str, Any]],
        *,
        test_windows: Sequence[Mapping[str, Any]],
        embargo_days: int,
    ) -> dict[str, Any]:
        records = _normalise_observations(observations)
        resolved_embargo = _positive_days(embargo_days, "embargo_days")
        if not test_windows:
            raise ValueError("test_windows cannot be empty")
        folds: list[dict[str, Any]] = []
        prior_test_end: datetime | None = None
        used_test_ids: set[str] = set()
        for index, window in enumerate(test_windows, start=1):
            test_start = _timestamp(window.get("test_not_before"), "test_not_before")
            test_end = _timestamp(window.get("test_not_after"), "test_not_after")
            if test_end <= test_start:
                raise ValueError("test_not_after must be later than test_not_before")
            if prior_test_end is not None and test_start <= prior_test_end:
                raise ValueError("test windows must be chronological and non-overlapping")
            test = [
                item for item in records
                if test_start <= _timestamp(item["decision_at"], "decision_at") < test_end
            ]
            if not test:
                raise ValueError("every test window must contain at least one observation")
            test_ids = [item["observation_id"] for item in test]
            if used_test_ids.intersection(test_ids):
                raise ValueError("an observation cannot be reused across test folds")
            purge_boundary = test_start
            train = [
                item for item in records
                if _timestamp(item["decision_at"], "decision_at") < test_start
                and _timestamp(item["label_end_at"], "label_end_at") < purge_boundary
            ]
            purged = [
                item for item in records
                if _timestamp(item["decision_at"], "decision_at") < test_start
                and _timestamp(item["label_end_at"], "label_end_at") >= purge_boundary
            ]
            if not train:
                raise ValueError("every fold must retain at least one non-overlapping train observation")
            embargo_end = test_end + timedelta(days=resolved_embargo)
            embargoed = [
                item for item in records
                if test_end <= _timestamp(item["decision_at"], "decision_at") < embargo_end
            ]
            train_ids = [item["observation_id"] for item in train]
            folds.append({
                "fold_id": _fold_id(index, train_ids, test_ids, resolved_embargo),
                "fold_number": index,
                "test_not_before": test_start.isoformat(),
                "test_not_after": test_end.isoformat(),
                "purge_boundary_at": purge_boundary.isoformat(),
                "embargo_not_after": embargo_end.isoformat(),
                "train_observation_ids": train_ids,
                "purged_observation_ids": [item["observation_id"] for item in purged],
                "test_observation_ids": test_ids,
                "embargoed_observation_ids": [item["observation_id"] for item in embargoed],
                "train_count": len(train_ids),
                "purged_count": len(purged),
                "test_count": len(test_ids),
                "embargoed_count": len(embargoed),
                "train_labels_end_strictly_before_test": True,
                "random_split_used": False,
                "test_observation_reused": False,
            })
            used_test_ids.update(test_ids)
            prior_test_end = test_end
        identity = [records, folds, resolved_embargo, POLICY_VERSION]
        return {
            "fold_set_id": "FOLDS-" + hashlib.sha256(
                _canonical_json(identity).encode("utf-8")
            ).hexdigest()[:32].upper(),
            "policy_version": POLICY_VERSION,
            "status": "PURGED_EMBARGOED_FORWARD_FOLDS_BUILT_NOT_EXECUTED",
            "observations": records,
            "folds": folds,
            "fold_count": len(folds),
            "embargo_days": resolved_embargo,
            "forward_chaining": True,
            "expanding_training_window": True,
            "outcome_overlap_purged": True,
            "post_test_embargo_declared": True,
            "random_split_allowed": False,
            "test_period_reuse_allowed": False,
            "model_trained": False,
            "replay_executed": False,
            "performance_calculated": False,
            "promotion_approved": False,
            "broker_submission_enabled": False,
            "live_trading_enabled": False,
        }
