from __future__ import annotations

from pathlib import Path

import streamlit as st

from core.application.research_service import ResearchService
from core.research.research_contract import ResearchContract


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_DIRECTORY = PROJECT_ROOT / "data" / "research" / "pipeline"


def format_number(value: object, digits: int = 2) -> str:
    if value is None:
        return "Not available"
    try:
        return f"{float(value):,.{digits}f}"
    except (TypeError, ValueError):
        return str(value)


def format_percent(value: object) -> str:
    if value is None:
        return "Not available"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return str(value)


def render_record(raw_record: dict, fallback_ticker: str) -> None:
    record = ResearchContract.from_pipeline_result(raw_record)
    st.subheader(record.get("ticker") or fallback_ticker)
    st.caption(f"Research status: {record.get('research_status', 'UNKNOWN')}")

    first, second, third, fourth = st.columns(4)
    first.metric("Investment-case score", format_number(record.get("investment_case_score")))
    second.metric("Decision", record.get("decision") or "Not available")
    third.metric("Current price", format_number(record.get("current_price")))
    fourth.metric("Expected return", format_percent(record.get("expected_return")))

    left, right = st.columns(2)
    with left:
        st.markdown("#### Valuation")
        st.write({
            "Current price": format_number(record.get("current_price")),
            "Base intrinsic value": format_number(record.get("base_intrinsic_value")),
            "Expected return": format_percent(record.get("expected_return")),
        })

    with right:
        audit = record.get("audit") or {}
        st.markdown("#### Audit")
        st.write({
            "Status": audit.get("status") or "NOT_RUN",
            "Critical findings": audit.get("critical", 0),
            "High findings": audit.get("high", 0),
            "Medium findings": audit.get("medium", 0),
        })

    thesis = record.get("thesis") or {}
    st.markdown("#### Thesis challenge")
    st.write(thesis.get("result") or "No thesis result was recorded.")

    with st.expander("Research detail"):
        st.json(record)


st.set_page_config(page_title="Company Research", page_icon="🔎", layout="wide")
st.title("Company Research")
st.caption("Run the integrated research workflow or inspect a completed research record.")

with st.form("research_run"):
    ticker_input = st.text_input(
        "Ticker",
        placeholder="NVDA",
        help="Runs fundamentals, valuation, decision, news, catalysts, thesis challenge, synthesis, and audit.",
    )
    submitted = st.form_submit_button("Run full research", type="primary")

if submitted:
    try:
        ticker = ResearchService.normalise_ticker(ticker_input)
        with st.spinner(f"Researching {ticker}. This can take a little while."):
            completed = ResearchService.run(ticker)
        st.success(f"{completed['ticker']} research completed and saved.")
        st.session_state["selected_research_ticker"] = completed["ticker"]
    except Exception as exc:
        st.error(f"Research run failed: {exc}")

records = sorted(PIPELINE_DIRECTORY.glob("*.json"))
if not records:
    st.info("No completed research records are available yet. Run a ticker above to create one.")
    st.stop()

record_by_ticker = {path.stem.upper(): path for path in records}
default_ticker = st.session_state.get("selected_research_ticker")
options = sorted(record_by_ticker)
index = options.index(default_ticker) if default_ticker in options else 0
ticker = st.selectbox("Completed research record", options, index=index)

try:
    raw_record = ResearchService.load(ticker)
    if raw_record is None:
        raise ValueError("Saved record could not be read.")
    render_record(raw_record, ticker)
except Exception as exc:
    st.error(f"Could not display {ticker}: {exc}")
