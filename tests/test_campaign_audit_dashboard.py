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
    assert all("UNRESOLVED" in snapshot["entitlement_status"][field] for field in (
        "visible_dashboard_labels", "account_identity", "account_to_plan_binding", "daily_bars", "dividends",
        "stock_splits", "lookback", "zero_incremental_cost", "provider_origin",
    ))
    assert [item["status"] for item in snapshot["campaign_chain"]] == [
        "REGISTERED_INERT", "REGISTERED_PRODUCT_FACTS_ONLY",
        "BOUNDARY_IMPLEMENTED_NO_REAL_CAPTURE", "UNRESOLVED", "NOT_AUTHORIZED",
    ]
    assert len(snapshot["evidence_hashes"]) == 2
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
