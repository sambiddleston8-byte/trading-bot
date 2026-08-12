import pytest

from core.performance.pinned_support import resolve_pinned_records


RECORDS = [
    {"item_id": "A", "record_hash": "hash-a"},
    {"item_id": "B", "record_hash": "hash-b"},
]


def test_resolves_exact_pinned_order():
    resolved, reasons = resolve_pinned_records(
        RECORDS,
        ["B", "A"],
        ["hash-b", "hash-a"],
        id_field="item_id",
        label="test",
    )
    assert [item["item_id"] for item in resolved] == ["B", "A"]
    assert reasons == []


@pytest.mark.parametrize(
    "ids,hashes,fragment",
    [
        (["A", "MISSING"], ["hash-a", "missing"], "missing"),
        (["A"], ["changed"], "hash has changed"),
        (["A", "A"], ["hash-a", "hash-a"], "duplicated"),
        (["A", "B"], ["hash-a"], "misaligned"),
    ],
)
def test_invalid_pinned_support_fails_closed(ids, hashes, fragment):
    resolved, reasons = resolve_pinned_records(
        RECORDS,
        ids,
        hashes,
        id_field="item_id",
        label="test",
    )
    assert resolved == []
    assert fragment in " ".join(reasons)
