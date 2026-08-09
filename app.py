from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Portfolio Construction",
    page_icon="💼",
    layout="wide",
)

navigation = st.navigation(
    [
        st.Page(
            "pages/Portfolio_Construction.py",
            title="Portfolio Construction",
            icon="💼",
            default=True,
        )
    ]
)
navigation.run()
