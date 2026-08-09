from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from core.application.portfolio_workflow_service import PortfolioWorkflowService


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRECTORY = ROOT / "data" / "research" / "universe_scans"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def scan_rows(scan: dict) -> list[dict]:
    rows = []
    for item in scan.get("results", []):
        audit = item.get("audit") or {}
        rows.append(
            {
                "Ticker": item.get("ticker", "—"),
                "Sector": item.get("sector", "—"),
                "Research": item.get("research_status", "—"),
                "Audit": audit.get("status") or item.get("audit_status", "—"),
                "Score": item.get("investment_case_score"),
                "Decision": item.get("decision"),
            }
        )
    return rows


st.set_page_config(page_title="Market Scanner", page_icon="🛰️", layout="wide")
st.title("Market Scanner")
st.caption("Run a controlled research batch, then inspect its ranking and audit outcomes.")

with st.form("batch_scan"):
    tickers_text = st.text_area(
        "Tickers",
        placeholder="NVDA, AAPL, MSFT",
        help="Up to ten tickers per website batch. This keeps research runs reviewable and avoids an accidental whole-universe run.",
    )
    submitted = st.form_submit_button("Run research batch", type="primary")

if submitted:
    try:
        tickers = [value.strip() for value in tickers_text.replace("\n", ",").split(",")]
        with st.spinner("Running research and audit checks for the batch."):
            completed = PortfolioWorkflowService.scan_tickers(tickers)
        st.success(f"Saved scan: {completed['path'].name}")
        st.session_state["selected_scan_path"] = str(completed["path"])
    except Exception as exc:
        st.error(f"Batch scan failed: {exc}")

paths = sorted(SCAN_DIRECTORY.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
if not paths:
    st.info("No saved universe scans are available yet.")
    st.stop()

path_by_name = {path.name: path for path in paths}
default_path = st.session_state.get("selected_scan_path")
names = list(path_by_name)
index = names.index(Path(default_path).name) if default_path and Path(default_path).name in names else 0
selected_name = st.selectbox("Saved scan", names, index=index)
selected = path_by_name[selected_name]

try:
    scan = load_json(selected)
except Exception as exc:
    st.error(f"Could not load scan: {exc}")
    st.stop()

requested = scan.get("requested_count", scan.get("requested", 0))
completed_count = scan.get("completed_count", scan.get("completed", 0))
first, second, third, fourth = st.columns(4)
first.metric("Requested", requested)
second.metric("Completed", completed_count)
third.metric("Audit PASS", scan.get("audit_pass_count", 0))
fourth.metric("Eligible", scan.get("eligible_count", 0))

st.subheader("Research and audit results")
rows = scan_rows(scan)
if rows:
    st.dataframe(
        rows,
        hide_index=True,
        width="stretch",
        column_config={"Score": st.column_config.NumberColumn(format="%.2f")},
    )
else:
    st.info("This scan does not contain result rows.")

st.subheader("Audit evidence")
for item in scan.get("results", []):
    audit = item.get("audit") or {}
    findings = audit.get("findings") or []
    if findings:
        with st.expander(f"{item.get('ticker')} — {audit.get('status', 'UNKNOWN')}"):
            st.dataframe(findings, hide_index=True, width="stretch")
