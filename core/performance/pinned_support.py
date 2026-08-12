from __future__ import annotations

"""Resolve immutable ledger support from exact stored IDs and hashes."""

from typing import Any, Mapping, Sequence


def resolve_pinned_records(
    records: Sequence[Mapping[str, Any]],
    pinned_ids: Any,
    pinned_hashes: Any,
    *,
    id_field: str,
    label: str,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    if not isinstance(pinned_ids, list) or not isinstance(pinned_hashes, list):
        return [], [f"Pinned {label} support is invalid."]
    source_ids = [item.get(id_field) for item in records]
    if any(not item for item in source_ids) or len(set(source_ids)) != len(source_ids):
        return [], [f"Verified {label} support has duplicate or missing IDs."]
    if len(pinned_ids) != len(pinned_hashes) or len(set(pinned_ids)) != len(
        pinned_ids
    ):
        return [], [f"Pinned {label} support is duplicated or misaligned."]
    by_id = {item[id_field]: item for item in records}
    resolved = [by_id.get(item_id) for item_id in pinned_ids]
    if any(item is None for item in resolved):
        return [], [f"Pinned {label} support is missing."]
    material = [item for item in resolved if item is not None]
    if [item.get("record_hash") for item in material] != pinned_hashes:
        return [], [f"Pinned {label} support hash has changed."]
    return material, []
