from __future__ import annotations

import streamlit as st

from core.application.campaign_audit_dashboard_service import (
    campaign_audit_snapshot,
)


snapshot = campaign_audit_snapshot()

st.title("Campaign audit")
st.caption("Read-only Stage 1 preview — no credentials, provider requests, activation, or trading controls")

if snapshot["stage_0_status"] != "COMPLETE":
    st.error("Stage 0 incomplete — Stage 1 remains preview-only and locked")

revision = snapshot["approved_revision_2"]
first, second, third = st.columns(3)
first.metric("Stage 0", snapshot["stage_0_status"])
second.metric("Stage 1", snapshot["stage_1_status"])
third.metric("Evaluator", revision["authoritative_evaluator"])

st.subheader("Approved Campaign v2 Revision-2")
st.code(revision["proposal_sha256"], language=None)
st.write(f"Window: `{revision['window']}`")
st.write(
    f"Proposal basis: as of `{revision['proposal_as_of']}`; expires after "
    f"`{revision['proposal_valid_until']}` and must be revalidated before activation."
)
st.write(f"Preregistration: `{revision['preregistration_id']}`")

st.subheader("Campaign chain")
st.dataframe(snapshot["campaign_chain"], width="stretch", hide_index=True)

st.subheader("Entitlement status")
st.dataframe(
    [{"Fact": key.replace("_", " ").title(), "Status": value} for key, value in snapshot["entitlement_status"].items()],
    width="stretch",
    hide_index=True,
)

st.subheader("Proposal and evidence hashes")
hash_rows = [{"Identity": "Approved Revision-2 proposal", "SHA-256": revision["proposal_sha256"]}]
hash_rows.extend({"Identity": key.replace("_", " ").title(), "SHA-256": value} for key, value in snapshot["evidence_hashes"].items())
st.dataframe(hash_rows, width="stretch", hide_index=True)

variant = snapshot["requested_variant"]
st.subheader("Requested September-window reconciliation")
st.warning(variant["qualification"])
st.write(
    f"Requested window `{variant['window']}` has a {variant['history_buffer_days']}-day buffer; "
    f"the immutable minimum is {variant['minimum_history_buffer_days']} days. This calculation is "
    f"as of `{variant['calculated_as_of']}` and the proposal expires after "
    f"`{variant['proposal_valid_until']}`."
)

st.subheader("Compliance block reasons")
for reason in snapshot["compliance_block_reasons"]:
    st.markdown(f"- `{reason}`")

st.subheader("Authority matrix")
st.dataframe(
    [{"Capability": key.replace("_", " ").title(), "Allowed": "YES" if value is True else "NO"} for key, value in snapshot["authorities"].items()],
    width="stretch",
    hide_index=True,
)
