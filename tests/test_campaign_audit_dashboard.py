from pathlib import Path

from core.application.campaign_audit_dashboard_service import campaign_audit_snapshot


def test_snapshot_fails_closed_and_rejects_requested_revision_2_variant():
    snapshot = campaign_audit_snapshot()
    assert snapshot["stage_0_status"] == "INCOMPLETE_BLOCKED"
    assert snapshot["stage_1_status"] == "PREVIEW_ONLY_LOCKED_BY_STAGE_0"
    assert snapshot["approved_revision_2"]["proposal_sha256"] == "cafbaef235d8379e29b17d057ac87a77c452260680afd20bf8c7e4fd24671654"
    assert snapshot["requested_variant"]["history_buffer_days"] == 16
    assert snapshot["requested_variant"]["minimum_history_buffer_days"] == 30
    assert snapshot["requested_variant"]["calculated_as_of"] == "2026-08-16"
    assert snapshot["requested_variant"]["proposal_valid_until"] == "2026-09-01"
    assert snapshot["requested_variant"]["qualification"].startswith("REJECTED")
    assert all(value is False for value in snapshot["authorities"].values())
    assert snapshot["entitlement_status"]["visible_dashboard_labels"] == (
        "REGISTERED_PARTIAL_LABELS_ONLY_NOT_ACCOUNT_BINDING"
    )
    assert all("UNRESOLVED" in snapshot["entitlement_status"][field] for field in (
        "account_identity", "account_to_plan_binding", "daily_bars", "dividends",
        "stock_splits", "lookback", "zero_incremental_cost", "provider_origin",
    ))
    assert [item["status"] for item in snapshot["campaign_chain"]] == [
        "REGISTERED_INERT", "REGISTERED_PRODUCT_FACTS_ONLY",
        "REGISTERED_PARTIAL_LABEL_EVIDENCE", "UNRESOLVED", "NOT_AUTHORIZED",
    ]
    assert len(snapshot["evidence_hashes"]) == 5
    assert snapshot["campaign_chain"][2]["identity"] == "CV2R2DASH-6DF966621092349395BC7B782BDC03E7"
    assert snapshot["evidence_hashes"]["dashboard_record_sha256"] == "f724c56171ecce98a75cf761e505ba05c2a0e1a32b5a4a689ca4d206999857d5"
    assert snapshot["evidence_hashes"]["dashboard_payload_sha256"] == "5a2ef5f4aad07421662567d54fb6731f6db25099385ed56c697eab6c5da2a832"
    assert snapshot["evidence_hashes"]["dashboard_bundle_sha256"] == "6df966621092349395bc7b782bdc03e78b2a7b36bb38ad3064154e674272be6c"
    assert snapshot["entitlement_status"]["dashboard_authentication_basis"] == "LOCAL_OPERATOR_ATTESTATION_ONLY"
    assert snapshot["entitlement_status"]["row_selection_semantics"].endswith("NOT_INDEPENDENTLY_VERIFIED")
    assert "NO_CAPTURE_ACTIVATION_OR_PROVIDER_REQUEST_AUTHORITY" in snapshot["compliance_block_reasons"]


def test_campaign_audit_page_renders_without_controls_or_exceptions():
    from streamlit.testing.v1 import AppTest

    page = Path(__file__).resolve().parents[1] / "pages" / "Campaign_Audit.py"
    app = AppTest.from_file(str(page))
    app.run(timeout=30)
    assert not app.exception, app.exception
    assert any("Campaign audit" in item.value for item in app.title)
    assert any("Stage 0 incomplete" in item.value for item in app.error)
    for control in (
        "button", "text_input", "text_area", "selectbox", "multiselect",
        "checkbox", "toggle", "radio", "slider", "number_input",
        "date_input", "chat_input",
    ):
        assert not getattr(app, control), control
    authority_table = app.dataframe[-1].value
    assert set(authority_table["Allowed"]) == {"NO"}
