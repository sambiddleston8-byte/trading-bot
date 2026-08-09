from __future__ import annotations

import json
from pathlib import Path

import streamlit as st

from core.application.portfolio_workflow_service import PortfolioWorkflowService


ROOT = Path(__file__).resolve().parents[1]
SCAN_DIRECTORY = ROOT / "data" / "research" / "universe_scans"
PORTFOLIO_DIRECTORY = ROOT / "data" / "research" / "portfolios"


def load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


st.set_page_config(page_title="Portfolio Construction", page_icon="💼", layout="wide")
st.title("Portfolio Construction")
st.caption("Construct portfolios only from completed research that passes the strict evidence audit.")

scan_paths = sorted(SCAN_DIRECTORY.glob("*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
if scan_paths:
    scan_by_name = {path.name: path for path in scan_paths}
    selected_name = st.selectbox("Source scan", list(scan_by_name))
    selected_scan = scan_by_name[selected_name]
    scan = load_json(selected_scan)

    first, second, third = st.columns(3)
    first.metric("Researched", scan.get("completed_count", scan.get("completed", 0)))
    second.metric("Audit PASS", scan.get("audit_pass_count", 0))
    third.metric("Eligible", scan.get("eligible_count", 0))

    with st.form("portfolio_construction"):
        holdings = st.number_input("Target holdings", min_value=5, max_value=20, value=10)
        construct = st.form_submit_button("Construct portfolio", type="primary")

    if construct:
        try:
            with st.spinner("Applying audit, diversification, and position-size constraints."):
                completed = PortfolioWorkflowService.construct_portfolio(scan, holdings=int(holdings))
            st.success(f"Portfolio saved: {completed['path'].name}")
        except RuntimeError as exc:
            st.warning(str(exc))
        except Exception as exc:
            st.error(f"Portfolio construction failed: {exc}")
else:
    st.info("Run a research batch before constructing a portfolio.")

portfolio_paths = sorted(
    PORTFOLIO_DIRECTORY.glob("*.json"),
    key=lambda path: path.stat().st_mtime,
    reverse=True,
) if PORTFOLIO_DIRECTORY.exists() else []

st.divider()
st.subheader("Saved portfolios")
if not portfolio_paths:
    st.info("No portfolio has been generated yet.")
else:
    portfolio_by_name = {path.name: path for path in portfolio_paths}
    portfolio_name = st.selectbox("Saved portfolio", list(portfolio_by_name))
    portfolio = load_json(portfolio_by_name[portfolio_name])

    first, second, third = st.columns(3)
    first.metric("Holdings", portfolio.get("number_of_stocks", 0))
    second.metric("Expected return", f"{portfolio.get('portfolio_expected_return', 0):.2%}")
    third.metric("Eligible candidates", portfolio.get("eligible_count", 0))

    st.markdown("#### Holdings")
    st.dataframe(portfolio.get("holdings", []), hide_index=True, width="stretch")
    st.markdown("#### Sector allocation")
    st.bar_chart(portfolio.get("sector_weights", {}))
