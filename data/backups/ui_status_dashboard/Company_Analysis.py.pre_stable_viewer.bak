import streamlit as st

from core.decision_engine import DecisionEngine


st.title("🔍 Company Analysis")

engine = DecisionEngine()

ticker = st.text_input(
    "Ticker",
    value="NVDA",
).upper()


if st.button("Analyse"):

    with st.spinner(
        f"Analysing {ticker}..."
    ):

        result = engine.analyse(ticker)

    st.success("Analysis complete.")

    # --------------------------------
    # Company Header
    # --------------------------------

    st.header(ticker)

    st.subheader(
        result["Rating"]
    )

    # --------------------------------
    # Main Scores
    # --------------------------------

    col1, col2, col3 = st.columns(3)

    with col1:

        st.metric(
            "Overall Score",
            result["Overall Score"],
        )

        st.progress(
            result["Overall Score"] / 100
        )

    with col2:

        st.metric(
            "Business Quality",
            result["Business Quality"],
        )

        st.progress(
            result["Business Quality"] / 100
        )

    with col3:

        st.metric(
            "Valuation",
            result["Valuation"],
        )

        st.progress(
            result["Valuation"] / 100
        )

    # --------------------------------
    # Secondary Scores
    # --------------------------------

    col4, col5, col6 = st.columns(3)

    with col4:

        st.metric(
            "Technical",
            result["Technical"],
        )

        st.progress(
            result["Technical"] / 100
        )

    with col5:

        st.metric(
            "Risk",
            result["Risk"],
        )

        st.progress(
            result["Risk"] / 100
        )

    with col6:

        st.metric(
            "News",
            result["News"],
        )

        st.progress(
            result["News"] / 100
        )

    st.divider()

    # --------------------------------
    # Research Comparison
    # --------------------------------

    st.header(
        "📊 Research History"
    )

    comparison = result.get(
        "Research Comparison"
    )

    if comparison and comparison.get(
        "Has Previous"
    ):

        changes = comparison.get(
            "Changes",
            []
        )

        if changes:

            for change in changes:

                metric = change[
                    "Metric"
                ]

                previous = change[
                    "Previous"
                ]

                current = change[
                    "Current"
                ]

                difference = change[
                    "Change"
                ]

                direction = change[
                    "Direction"
                ]

                if difference is not None:

                    if difference > 0:

                        st.success(
                            f"**{metric}** improved: "
                            f"{previous} → {current} "
                            f"(+{difference})"
                        )

                    else:

                        st.warning(
                            f"**{metric}** deteriorated: "
                            f"{previous} → {current} "
                            f"({difference})"
                        )

                else:

                    st.info(
                        f"**{metric}** changed: "
                        f"{previous} → {current}"
                    )

        else:

            st.info(
                "No material changes since the previous analysis."
            )

    else:

        st.info(
            "This is the first recorded analysis "
            "for this company."
        )

    st.divider()

    # --------------------------------
    # Strengths / Weaknesses
    # --------------------------------

    left, right = st.columns(2)

    with left:

        st.subheader(
            "✅ Strengths"
        )

        for strength in result[
            "Strengths"
        ]:

            st.success(
                strength
            )

    with right:

        st.subheader(
            "⚠️ Weaknesses"
        )

        if result["Weaknesses"]:

            for weakness in result[
                "Weaknesses"
            ]:

                st.warning(
                    weakness
                )

        else:

            st.success(
                "No major weaknesses identified."
            )

    st.divider()

    # --------------------------------
    # Investment Summary
    # --------------------------------

    st.subheader(
        "📝 Investment Summary"
    )

    st.write(
        result["Summary"]
    )

    st.divider()

    # --------------------------------
    # Key Metrics
    # --------------------------------

    st.subheader(
        "📈 Company Metrics"
    )

    metrics = result.get(
        "Metrics",
        {}
    )

    metric_columns = st.columns(4)

    metric_items = [
        ("Market Cap", "Market Cap"),
        ("Revenue", "Revenue"),
        ("Net Income", "Net Income"),
        ("PE", "PE"),
        ("Forward PE", "Forward PE"),
        ("PEG", "PEG"),
        ("Beta", "Beta"),
        ("Employees", "Employees"),
    ]

    for index, (
        label,
        key,
    ) in enumerate(metric_items):

        with metric_columns[
            index % 4
        ]:

            value = metrics.get(
                key
            )

            if value is not None:

                if isinstance(
                    value,
                    (int, float)
                ):

                    if key == "Market Cap":

                        display = (
                            f"${value / 1_000_000_000:.1f}B"
                        )

                    elif key in [
                        "Revenue",
                        "Net Income",
                    ]:

                        display = (
                            f"${value / 1_000_000_000:.1f}B"
                        )

                    else:

                        display = (
                            f"{value:,.2f}"
                        )

                else:

                    display = str(value)

                st.metric(
                    label,
                    display,
                )

    st.divider()

    # --------------------------------
    # Catalysts
    # --------------------------------

    st.subheader(
        "📰 Key Catalysts"
    )

    catalysts = result.get(
        "Catalysts",
        []
    )

    if catalysts:

        for catalyst in catalysts:

            st.info(
                catalyst
            )

    else:

        st.write(
            "No major catalysts detected."
        )

    # --------------------------------
    # News
    # --------------------------------

    st.subheader(
        "🗞️ Recent Headlines"
    )

    headlines = result.get(
        "Headlines",
        []
    )

    if headlines:

        for headline in headlines:

            st.write(
                f"• {headline}"
            )

    else:

        st.write(
            "No recent headlines available."
        )

    st.divider()

    # --------------------------------
    # Full Specialist Analysis
    # --------------------------------

    st.header(
        "🤖 Specialist Bots"
    )

    with st.expander(
        "Business Quality"
    ):

        st.json(
            result["Business Analysis"]
        )

    with st.expander(
        "Valuation"
    ):

        st.json(
            result["Valuation Analysis"]
        )

    with st.expander(
        "Technical"
    ):

        st.json(
            result["Technical Analysis"]
        )

    with st.expander(
        "Risk"
    ):

        st.json(
            result["Risk Analysis"]
        )

    with st.expander(
        "News"
    ):

        st.json(
            result["News Analysis"]
        )

    with st.expander(
        "Management"
    ):

        st.json(
            result["Management Analysis"]
        )