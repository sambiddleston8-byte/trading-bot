from __future__ import annotations

import ast
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest

import core.orchestration.massive_entitlement_evidence as module
from core.orchestration.massive_entitlement_evidence import (
    CAPTURE_APPROVAL_RECORD_ID,
    PUBLIC_DOCUMENT_CONTRACTS,
    _derive_documented_facts,
    assess_capture_activation_evidence,
    build_authenticated_account_evidence,
    build_public_documentation_evidence,
)


NOW = datetime(2026, 8, 16, 12, 0, tzinfo=timezone.utc)


def _document_payload(contract: dict) -> bytes:
    fragments = [
        fragment
        for claim in contract["fact_claims"].values()
        for fragment in claim["required_fragments"]
    ]
    return ("Synthetic deterministic documentation fixture\n" + "\n".join(fragments)).encode()


def _documents() -> list[dict]:
    return [
        {
            "document_id": document_id,
            "uri": contract["uri"],
            "retrieved_at": "2026-08-16T11:00:00+00:00",
            "payload_bytes": _document_payload(contract),
        }
        for document_id, contract in PUBLIC_DOCUMENT_CONTRACTS.items()
    ]


def _public() -> dict:
    return build_public_documentation_evidence(_documents(), assessed_at=NOW)


def _payloads() -> dict[str, bytes]:
    return {
        document["document_id"]: document["payload_bytes"]
        for document in _documents()
    }


def _account(**changes) -> dict:
    evidence = {
        "captured_at": "2026-08-16T11:30:00+00:00",
        "account_binding_evidence_kind": "AUTHENTICATED_DASHBOARD_EXPORT",
        "account_binding_payload_sha256": "a" * 64,
        "account_identity_sha256": "b" * 64,
        "plan_name": "Stocks Basic Free",
        "plan_evidence_payload_sha256": "c" * 64,
        "custom_bars_access_confirmed": True,
        "historical_lookback_start": "2024-08-01",
        "request_limit_per_minute": 5,
        "incremental_cost_usd": "0.00",
        "terms_uri": "https://massive.com/stocks",
        "terms_retrieved_at": "2026-08-16T11:15:00+00:00",
        "terms_payload_sha256": "d" * 64,
        "authenticated_account_session": True,
        "credential_material_recorded": False,
        "market_data_response_used_for_account_binding": False,
        "request_id_used_for_account_binding": False,
    }
    evidence.update(changes)
    return build_authenticated_account_evidence(evidence, assessed_at=NOW)


def _rehash(value: dict, field: str) -> None:
    material = {key: item for key, item in value.items() if key != field}
    value[field] = hashlib.sha256(
        json.dumps(
            material,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode()
    ).hexdigest()


def test_public_docs_preserve_product_facts_but_never_prove_account_binding():
    evidence = _public()

    assert evidence["custom_bars_published_free_plan_access"] is True
    assert evidence["flat_files_published_free_plan_access"] is False
    assert evidence["public_documentation_proves_account_identity"] is False
    assert evidence["public_documentation_proves_account_plan"] is False
    assert evidence["public_documentation_proves_current_account_entitlement"] is False
    assert all(
        document["authenticated_account_evidence"] is False
        for document in evidence["documents"]
    )
    by_id = {document["document_id"]: document for document in evidence["documents"]}
    assert by_id["MASSIVE_STOCKS_DIVIDENDS"]["documented_facts"][
        "stocks_basic_free_history_years"
    ] == 2
    assert by_id["MASSIVE_STOCKS_SPLITS"]["documented_facts"][
        "endpoint_path"
    ] == "/stocks/v1/splits"
    for document in evidence["documents"]:
        source = next(
            item for item in _documents() if item["document_id"] == document["document_id"]
        )
        assert document["payload_sha256"] == hashlib.sha256(
            source["payload_bytes"]
        ).hexdigest()
        assert set(document["fact_evidence"]) == set(document["documented_facts"])


def test_public_docs_must_bind_all_exact_official_bytes_before_assessment():
    with pytest.raises(ValueError, match="all exact"):
        build_public_documentation_evidence(_documents()[:-1], assessed_at=NOW)

    wrong = _documents()
    wrong[0]["uri"] = "https://example.invalid/docs"
    with pytest.raises(ValueError, match="official Massive|differs"):
        build_public_documentation_evidence(wrong, assessed_at=NOW)

    future = _documents()
    future[0]["retrieved_at"] = "2026-08-16T12:00:01+00:00"
    with pytest.raises(ValueError, match="after assessment"):
        build_public_documentation_evidence(future, assessed_at=NOW)

    missing_fact = _documents()
    missing_fact[1]["payload_bytes"] = b"unrelated official-looking words"
    with pytest.raises(ValueError, match="do not prove"):
        build_public_documentation_evidence(missing_fact, assessed_at=NOW)

    caller_hash = _documents()
    caller_hash[0]["payload_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="fields differ"):
        build_public_documentation_evidence(caller_hash, assessed_at=NOW)

    decoy = _documents()
    decoy[1]["payload_bytes"] = (
        b"Stocks Basic Free Not included elsewhere Plan Access Included "
        b"without the reviewed all-plans statement"
    )
    with pytest.raises(ValueError, match="do not prove"):
        build_public_documentation_evidence(decoy, assessed_at=NOW)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ("not-bytes", "exact bytes"),
        (b"", "nonempty"),
        (b"x" * 2_000_001, "at most 2 MB"),
        (b"\xff\xfe", "strict UTF-8"),
    ],
)
def test_public_document_payload_boundary_rejects_invalid_bytes(payload, message):
    documents = _documents()
    documents[0]["payload_bytes"] = payload
    with pytest.raises(ValueError, match=message):
        build_public_documentation_evidence(documents, assessed_at=NOW)


def test_document_hash_changes_when_nonsemantic_payload_bytes_change():
    original = _documents()
    changed = _documents()
    changed[0]["payload_bytes"] += b"\nProvider footer revision"
    original_evidence = build_public_documentation_evidence(original, assessed_at=NOW)
    changed_evidence = build_public_documentation_evidence(changed, assessed_at=NOW)
    original_by_id = {
        item["document_id"]: item for item in original_evidence["documents"]
    }
    changed_by_id = {
        item["document_id"]: item for item in changed_evidence["documents"]
    }
    assert original_by_id["MASSIVE_REST_QUICKSTART"]["documented_facts"] == (
        changed_by_id["MASSIVE_REST_QUICKSTART"]["documented_facts"]
    )
    assert original_by_id["MASSIVE_REST_QUICKSTART"]["payload_sha256"] != (
        changed_by_id["MASSIVE_REST_QUICKSTART"]["payload_sha256"]
    )


def test_documentation_bytes_reject_account_or_credential_material():
    secret = _documents()
    secret[0]["payload_bytes"] += b"\napiKey=liveAccountToken1234567890abcdef"
    with pytest.raises(ValueError, match="account or credential material"):
        build_public_documentation_evidence(secret, assessed_at=NOW)

    placeholder = _documents()
    placeholder[0]["payload_bytes"] += b"\napiKey=YOUR_API_KEY"
    build_public_documentation_evidence(placeholder, assessed_at=NOW)

    tagged_placeholder = _documents()
    tagged_placeholder[0]["payload_bytes"] += (
        b"\n<code>apiKey=YOUR_API_KEY</code></pre><div>public example</div>"
    )
    build_public_documentation_evidence(tagged_placeholder, assessed_at=NOW)

    escaped_legacy_placeholder = _documents()
    escaped_legacy_placeholder[0]["payload_bytes"] += (
        b"\nAuthorization: Bearer POLYGON_STOCKS_API_KEY\\\\n\\"
    )
    build_public_documentation_evidence(
        escaped_legacy_placeholder,
        assessed_at=NOW,
    )

    escaped_massive_placeholder = _documents()
    escaped_massive_placeholder[0]["payload_bytes"] += (
        b"\nAuthorization: Bearer YOUR_MASSIVE_API_KEY\\\\\\n\\"
    )
    build_public_documentation_evidence(
        escaped_massive_placeholder,
        assessed_at=NOW,
    )

    documented_global_placeholder = _documents()
    documented_global_placeholder[0]["payload_bytes"] += (
        b"\napiKey=GLOBAL_TOKEN_API_KEY\\\\n\\"
    )
    build_public_documentation_evidence(
        documented_global_placeholder,
        assessed_at=NOW,
    )


def test_documented_facts_are_derived_across_html_tags_but_not_scripts():
    contract = {
        "fact_claims": {
            "plan_access": {
                "value": True,
                "required_fragments": (
                    "Plan Access Included in all Stocks plans",
                ),
            }
        }
    }
    payload = (
        b"<html><body><p>Plan <span>Access</span> Included in all Stocks "
        b"plans</p><script>irrelevant executable text</script>"
        b"</body></html>"
    )
    _, facts, evidence = _derive_documented_facts(payload, contract)
    assert facts == {"plan_access": True}
    assert evidence["plan_access"][0]["normalized_text_start"] == 0


def test_script_only_fact_does_not_satisfy_public_document_contract():
    contract = {
        "fact_claims": {
            "plan_access": {
                "value": True,
                "required_fragments": ("Plan Access Included",),
            }
        }
    }
    with pytest.raises(ValueError, match="do not prove"):
        _derive_documented_facts(
            b"<html><script>Plan Access Included</script><body>Other</body></html>",
            contract,
        )


def test_assessment_rederives_every_fact_and_locator_from_supplied_exact_bytes():
    public = _public()
    payloads = _payloads()
    for document in public["documents"]:
        normalized = " ".join(
            payloads[document["document_id"]].decode("utf-8").split()
        )
        contract = PUBLIC_DOCUMENT_CONTRACTS[document["document_id"]]
        for fact_name, claim in contract["fact_claims"].items():
            for locator, fragment in zip(
                document["fact_evidence"][fact_name],
                claim["required_fragments"],
                strict=True,
            ):
                assert normalized[
                    locator["normalized_text_start"] : locator["normalized_text_end"]
                ] == fragment

    tampered = deepcopy(public)
    tampered["documents"][0]["fact_evidence"][
        next(iter(tampered["documents"][0]["fact_evidence"]))
    ][0]["normalized_text_start"] += 1
    _rehash(tampered, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=tampered,
            public_documentation_payloads=payloads,
            authenticated_account=None,
        )

    replaced_payloads = dict(payloads)
    replaced_payloads[public["documents"][0]["document_id"]] = (
        b"synthetic replacement without contract evidence"
    )
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=public,
            public_documentation_payloads=replaced_payloads,
            authenticated_account=None,
        )

    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=public,
            public_documentation_payloads=None,
            authenticated_account=None,
        )


@pytest.mark.parametrize(
    "kind",
    [
        "API_REQUEST_ID",
        "MARKET_DATA_RESPONSE",
        "PUBLIC_DOCUMENTATION",
        "USER_ASSERTION",
    ],
)
def test_market_data_and_public_material_cannot_be_account_binding(kind):
    with pytest.raises(ValueError, match="cannot prove"):
        _account(account_binding_evidence_kind=kind)


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"authenticated_account_session": False}, "authenticated account"),
        ({"credential_material_recorded": True}, "credential material"),
        (
            {"market_data_response_used_for_account_binding": True},
            "market-data response",
        ),
        ({"request_id_used_for_account_binding": True}, "request_id"),
        ({"historical_lookback_start": "2024-08-02"}, "lookback"),
        ({"request_limit_per_minute": 6}, "approved value 5"),
        ({"incremental_cost_usd": "0.01"}, "exactly 0.00"),
        ({"terms_uri": "https://massive.com/other"}, "approved proposal"),
        ({"custom_bars_access_confirmed": False}, "custom-bars access"),
    ],
)
def test_authenticated_evidence_rejects_missing_or_substituted_proof(changes, message):
    with pytest.raises(ValueError, match=message):
        _account(**changes)


def test_public_docs_alone_leave_activation_blocked_and_every_authority_false():
    assessment = assess_capture_activation_evidence(
        public_documentation=_public(),
        public_documentation_payloads=_payloads(),
        authenticated_account=None,
    )

    assert assessment["capture_approval_record_id"] == CAPTURE_APPROVAL_RECORD_ID
    assert assessment["evidence_complete_for_separate_activation_record"] is False
    assert assessment["missing_evidence"] == [
        "AUTHENTICATED_ACCOUNT_BOUND_ENTITLEMENT_EVIDENCE"
    ]
    assert assessment["public_documentation_is_not_account_binding"] is True
    assert assessment["free_plan_flat_file_access_inferred"] is False
    for field in (
        "capture_activation_issued",
        "data_calls_allowed",
        "provider_bytes_accessed",
        "untouched_test_opened",
        "dataset_admitted",
        "evaluation_allowed",
        "broker_connection_allowed",
        "orders_submitted",
        "live_trading_enabled",
    ):
        assert assessment[field] is False


def test_complete_evidence_only_qualifies_a_later_activation_record():
    assessment = assess_capture_activation_evidence(
        public_documentation=_public(),
        public_documentation_payloads=_payloads(),
        authenticated_account=_account(),
    )

    assert assessment["evidence_complete_for_separate_activation_record"] is True
    assert assessment["missing_evidence"] == []
    assert assessment["capture_activation_issued"] is False
    assert assessment["data_calls_allowed"] is False
    assert assessment["provider_bytes_accessed"] is False


def test_rehashed_semantic_tampering_still_fails_closed():
    public = deepcopy(_public())
    public["public_documentation_proves_account_identity"] = True
    _rehash(public, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=public,
            public_documentation_payloads=_payloads(),
            authenticated_account=None,
        )

    account = deepcopy(_account())
    account["request_id_used_for_account_binding"] = True
    _rehash(account, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=_public(),
            public_documentation_payloads=_payloads(),
            authenticated_account=account,
        )

    malformed_date = deepcopy(_account())
    malformed_date["historical_lookback_start"] = "not-a-date"
    _rehash(malformed_date, "evidence_bundle_sha256")
    with pytest.raises(ValueError, match="invalid or tampered"):
        assess_capture_activation_evidence(
            public_documentation=_public(),
            public_documentation_payloads=_payloads(),
            authenticated_account=malformed_date,
        )


def test_schema_has_no_network_credential_file_replay_or_broker_capability():
    tree = ast.parse(Path(module.__file__).read_text())
    imports = {
        node.module or ""
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }
    imports.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    )
    forbidden = {
        "requests",
        "httpx",
        "urllib.request",
        "core.orchestration.massive_historical_adapter",
        "core.orchestration.massive_historical_quarantine",
        "core.research.vectorbt_pilot",
        "core.guardrailed_backtest",
    }
    assert imports.isdisjoint(forbidden)
    assert not any("broker" in value or "alpaca" in value for value in imports)
    calls = {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert not calls & {"open", "eval", "exec", "__import__"}
