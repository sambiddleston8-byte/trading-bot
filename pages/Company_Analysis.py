import streamlit as st

from core.decision_engine import DecisionEngine

st.title("🔍 Company Analysis")

engine = DecisionEngine()

ticker = st.text_input(
    "Ticker",
    value="NVDA",
)

if st.button("Analyse"):

    with st.spinner("Analysing..."):

        result = engine.analyse(
            ticker.upper()
        )

    st.success("Analysis complete.")

    st.header(f"{ticker.upper()}")

    st.subheader(result["Rating"])

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Overall Score",
            result["Overall Score"],
        )
        st.progress(result["Overall Score"] / 100)

        st.metric(
            "Technical",
            result["Technical"],
        )
        st.progress(result["Technical"] / 100)

    with col2:

        st.metric(
            "Business Quality",
            result["Business Quality"],
        )
        st.progress(result["Business Quality"] / 100)

        st.metric(
            "Risk",
            result["Risk"],
        )
        st.progress(result["Risk"] / 100)

    with col3:

        st.metric(
            "Valuation",
            result["Valuation"],
        )
        st.progress(result["Valuation"] / 100)

        st.metric(
            "News",
            result["News"],
        )
        st.progress(result["News"] / 100)

    st.divider()

    left, right = st.columns(2)

    with left:

        st.subheader("✅ Strengths")

        if result["Strengths"]:
            for strength in result["Strengths"]:
                st.success(strength)
        else:
            st.info("No major strengths identified.")

    with right:

        st.subheader("⚠️ Weaknesses")

        if result["Weaknesses"]:
            for weakness in result["Weaknesses"]:
                st.warning(weakness)
        else:
            st.success("No major weaknesses identified.")

    st.divider()

    st.subheader("📝 Investment Summary")

    st.write(result["Summary"])

    st.divider()

    st.subheader("📰 Key Catalysts")

    if result["Catalysts"]:
        for catalyst in result["Catalysts"]:
            st.info(catalyst)
    else:
        st.info("No major catalysts detected.")