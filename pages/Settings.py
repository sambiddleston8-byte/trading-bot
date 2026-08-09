from __future__ import annotations

import streamlit as st

from core.application.universe_service import UniverseService


st.set_page_config(page_title="Universe Settings", page_icon="⚙️", layout="wide")
st.title("Universe Settings")
st.caption("Manage the saved investable universe used by the research scanner.")

saved = UniverseService.load("both")
if saved:
    first, second, third = st.columns(3)
    first.metric("Combined companies", saved.get("count", len(saved.get("companies", []))))
    second.metric("Index overlaps", saved.get("overlap_count", 0))
    third.metric("Provider", saved.get("provider", "Saved data"))
    st.caption(f"Last refreshed: {saved.get('created_at', 'unknown')}")
else:
    st.warning("No saved combined universe was found.")

st.divider()
st.subheader("Refresh index constituents")
st.write("This downloads the latest public S&P 500 and Nasdaq-100 constituent data, validates it, and replaces the saved universe file.")

with st.form("universe_refresh"):
    selection = st.selectbox(
        "Universe",
        ["both", "sp500", "nasdaq100"],
        format_func=lambda value: {
            "both": "S&P 500 + Nasdaq-100",
            "sp500": "S&P 500",
            "nasdaq100": "Nasdaq-100",
        }[value],
    )
    submitted = st.form_submit_button("Refresh universe data", type="primary")

if submitted:
    try:
        with st.spinner("Downloading and validating index constituents."):
            completed = UniverseService.refresh(selection)
        st.success(f"Saved {completed['data'].get('count', 0)} companies to {completed['path'].name}.")
    except Exception as exc:
        st.error(f"Universe refresh failed: {exc}")

st.divider()
st.subheader("Operating safeguards")
st.markdown(
    "- Website research batches are limited to **10 tickers**.\n"
    "- A full-universe scan is intentionally not launched from the browser.\n"
    "- Portfolio construction requires a strict audit **PASS**.\n"
    "- The research pipeline saves a complete canonical record for every completed ticker."
)
