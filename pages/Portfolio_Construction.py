from __future__ import annotations

from collections import defaultdict
import html

import altair as alt
import streamlit as st

from core.application.portfolio_construction_service import (
    PortfolioConstructionService,
)
from core.portfolio.portfolio_engine import PortfolioEngine
from core.application.portfolio_benchmark_service import (
    PortfolioBenchmarkService,
)
from core.application.portfolio_monitor_service import (
    PortfolioMonitorService,
)
from core.application.portfolio_research_batch_service import (
    PortfolioResearchBatchService,
)
from core.application.research_service import ResearchService
from core.data_sources.portfolio_data_provider_registry import (
    PortfolioDataProviderRegistry,
)
from core.research.research_contract import ResearchContract
from core.research_engine import ResearchEngine


TARGET_HOLDINGS = 15
SECTOR_COLOURS = [
    "#4C78A8", "#F58518", "#54A24B", "#E45756", "#72B7B2",
    "#B279A2", "#FF9DA6", "#9D755D", "#BAB0AC", "#59A14F",
    "#EDC948",
]


def percent(value: object) -> str:
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "—"


def score(value: object) -> str:
    try:
        return f"{float(value):.0f} / 100"
    except (TypeError, ValueError):
        return "Unrated"


def confidence_score(value: object) -> str:
    try:
        return f"{min(100.0, float(value)):.0f} / 100"
    except (TypeError, ValueError):
        return "Unrated"


def decision_rating_score(value: object) -> str:
    if isinstance(value, dict):
        value = value.get("score")
    try:
        return f"{min(100.0, float(value)):.0f} / 100"
    except (TypeError, ValueError):
        return "Unrated"


def currency(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except (TypeError, ValueError):
        return "—"


def ticker_from_url() -> str | None:
    value = st.query_params.get("ticker")
    if isinstance(value, list):
        value = value[0] if value else None
    return str(value).upper() if value else None


def conviction_label(value: object) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "Unrated"
    if number >= 80:
        return "Very strong evidence"
    if number >= 65:
        return "Strong evidence"
    if number >= 50:
        return "Moderate evidence"
    return "Limited evidence"


def markdown_list(items: object, empty_message: str) -> None:
    if not isinstance(items, list) or not items:
        st.caption(empty_message)
        return
    for item in items:
        st.markdown(f"- {item}")


def number(value: object) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def forecast_horizon_label(value: object) -> str:
    years = number(value)
    if years is None:
        return "Forecast period unavailable"
    return f"{years:g}-year DCF forecast period"


def holding_period_guidance(value: object) -> tuple[str, str]:
    years = number(value)
    if years is None or years <= 0:
        return (
            "Not yet set",
            "The research needs a stated valuation horizon before a suggested holding period can be shown.",
        )
    return (
        f"About {years:g} years",
        "This is the model's intended holding period, with a quarterly thesis review and earlier review if audit, thesis, technical or catalyst evidence changes.",
    )


def summary_value(result: dict) -> str:
    for key in ("Summary", "Assessment", "Classification", "Conclusion"):
        value = result.get(key)
        if value not in (None, ""):
            return str(value)
    return "No written summary was saved."


def score_value(result: dict) -> object:
    score_keys = [key for key in result if "score" in str(key).lower()]
    for key in score_keys:
        value = result.get(key)
        if isinstance(value, (int, float)):
            return value
    return None


def specialist_rows(specialist_research: object) -> list[dict]:
    specialist_research = specialist_research if isinstance(specialist_research, dict) else {}
    signals = specialist_research.get("signals") or {}
    rows = []
    for name, detail in signals.items():
        detail = detail if isinstance(detail, dict) else {}
        result = detail.get("result") if isinstance(detail.get("result"), dict) else {}
        rows.append(
            {
                "Research bot": str(name).replace("_", " ").title(),
                "Status": detail.get("status") or "—",
                "Assessment": result.get("Assessment") or result.get("Classification") or "—",
                "Score": score(result.get("Competitive Score") or score_value(result)),
                "Summary": summary_value(result),
            }
        )
    return rows


def relevant_headlines(raw_news: object, ticker: str, company_name: str | None) -> list[dict]:
    if not isinstance(raw_news, list):
        return []
    return [
        article
        for article in raw_news
        if isinstance(article, dict)
        and ResearchEngine.headline_is_relevant(
            article.get("Title"), ticker, company_name
        )
    ]


def relevant_catalysts(catalysts: object, ticker: str, company_name: str | None) -> list[dict]:
    if not isinstance(catalysts, list):
        return []
    return [
        catalyst
        for catalyst in catalysts
        if isinstance(catalyst, dict)
        and (
            str(catalyst.get("category") or "").lower() == "earnings"
            and str(catalyst.get("title") or "").lower().startswith("upcoming earnings")
            or ResearchEngine.headline_is_relevant(
                catalyst.get("title") or catalyst.get("description"), ticker, company_name
            )
        )
    ]


def portfolio_for_display() -> tuple[dict | None, str | None]:
    # Always load the newest persisted construction.  A Streamlit session can
    # otherwise retain an older in-memory portfolio after the construction
    # policy changes, leaving the website out of step with the saved record.
    portfolio, path = PortfolioConstructionService.latest_portfolio()
    return portfolio, str(path) if path else None


def holding_rows(portfolio: dict) -> list[dict]:
    rows = []
    for holding in portfolio.get("holdings", []):
        confidence = holding.get("research_confidence")
        rating_detail = holding.get("decision_rating")
        if not isinstance(rating_detail, dict):
            rating_detail = PortfolioEngine.decision_rating_detail(holding)
        rows.append(
            {
                "ticker": holding.get("ticker"),
                "name": holding.get("name") or holding.get("ticker"),
                "sector": holding.get("sector") or "Unclassified",
                "weight": holding.get("weight"),
                "decision_rating": rating_detail,
                "confidence": confidence,
                "label": conviction_label(confidence),
                # This is the construction decision, not an older single-name
                # research label retained in a saved research record.
                "decision": holding.get("portfolio_decision") or "SELECTED",
                "research_decision": holding.get("decision") or "—",
                "audit": holding.get("audit") or {},
                "reasoning": (holding.get("reasoning") or {}).get("summary"),
                "trigger": (holding.get("monitoring_conditions") or [None])[0],
            }
        )
    return sorted(
        rows,
        key=lambda item: float((item.get("decision_rating") or {}).get("score") or 0),
        reverse=True,
    )


def render_sector_chart(rows: list[dict]) -> None:
    sectors: dict[str, float] = defaultdict(float)
    for row in rows:
        try:
            sectors[row["sector"]] += float(row["weight"] or 0)
        except (TypeError, ValueError):
            continue
    chart_rows = []
    for sector, allocation in sorted(sectors.items(), key=lambda item: item[1], reverse=True):
        holdings = [row["ticker"] for row in rows if row["sector"] == sector]
        chart_rows.append(
            {
                "Sector": sector,
                "Allocation": allocation,
                "Holdings": len(holdings),
                "Companies": ", ".join(holdings),
            }
        )
    if not chart_rows:
        st.info("Sector allocation will appear after a portfolio is constructed.")
        return
    domain = [item["Sector"] for item in chart_rows]
    colours = [SECTOR_COLOURS[index % len(SECTOR_COLOURS)] for index in range(len(domain))]
    chart = (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_arc(innerRadius=72, padAngle=0.02)
        .encode(
            theta=alt.Theta("Allocation:Q", stack=True),
            color=alt.Color(
                "Sector:N",
                scale=alt.Scale(domain=domain, range=colours),
                legend=None,
            ),
            tooltip=[
                alt.Tooltip("Sector:N"),
                alt.Tooltip("Allocation:Q", format=".1%"),
                alt.Tooltip("Holdings:Q"),
                alt.Tooltip("Companies:N"),
            ],
        )
        .properties(height=330)
    )
    st.altair_chart(chart, width="stretch")
    key_items = "".join(
        "<span class='sector-key-item'><span class='sector-swatch' style='background:"
        + colour
        + "'></span>"
        + html.escape(item["Sector"])
        + " "
        + percent(item["Allocation"])
        + "</span>"
        for item, colour in zip(chart_rows, colours)
    )
    st.markdown("#### Allocation key")
    st.markdown(f"<div class='sector-key'>{key_items}</div>", unsafe_allow_html=True)
    st.dataframe(
        [
            {
                "Sector": item["Sector"],
                "Allocation": percent(item["Allocation"]),
                "Holdings": item["Holdings"],
                "Companies": item["Companies"],
                "Hard limit": "50.0%",
            }
            for item in chart_rows
        ],
        hide_index=True,
        width="stretch",
    )


def render_current_portfolio(portfolio: dict | None) -> None:
    """Show research-led paper-portfolio updates without permitting live trades."""
    st.subheader("Current portfolio")
    st.caption(
        "A paper-monitoring view. Changes are based on refreshed research, not a fixed price-loss rule; "
        "the proposed paper allocation updates automatically only when a balanced, constraint-checked change is supported. "
        "It never sends a broker order."
    )
    if portfolio is None:
        st.info(
            "Current-portfolio monitoring starts after a proposed portfolio has been constructed. "
            "The research system will not invent positions before then."
        )
        return

    snapshot = st.session_state.get("portfolio_health_snapshot")
    if not isinstance(snapshot, dict):
        snapshot, _ = PortfolioMonitorService.latest()

    left, right = st.columns([1.3, 2.7])
    with left:
        if st.button("Refresh market and thesis review", type="primary", use_container_width=True):
            with st.spinner("Checking current paper prices, saved research and allocation recommendations…"):
                snapshot = PortfolioMonitorService.evaluate(portfolio)
                applied_update = PortfolioMonitorService.apply_reallocation(portfolio, snapshot)
                if applied_update.get("status") == "APPLIED":
                    updated_path = PortfolioConstructionService.save_proposed_update(
                        applied_update["portfolio"]
                    )
                    snapshot["reallocation_plan"] = applied_update["reallocation_plan"]
                    snapshot["applied_portfolio_changes"] = applied_update["changes"]
                    snapshot["applied_portfolio_path"] = str(updated_path)
                elif applied_update.get("status", "NO_CHANGE").startswith("NOT_APPLIED"):
                    snapshot["reallocation_apply_status"] = applied_update.get("status")
                    snapshot["reallocation_apply_reason"] = applied_update.get("reason")
                snapshot_path = PortfolioMonitorService.save(snapshot)
            st.session_state["portfolio_health_snapshot"] = snapshot
            st.session_state["portfolio_health_snapshot_path"] = str(snapshot_path)
            st.rerun()
    with right:
        st.write(
            "The review compares each holding's construction-date price with its latest "
            "available market price, then rechecks saved audit, thesis and allocation evidence."
        )

    if not snapshot:
        st.info("No health-check snapshot exists yet. Run the check when you want an updated review.")
        return

    policy = snapshot.get("policy") or {}
    summary = snapshot.get("summary") or {}
    st.caption(f"Last checked: {snapshot.get('checked_at') or '—'}.")
    st.caption(
        "Review basis: refreshed fundamentals and valuation, technical and risk signals, catalysts and sentiment, "
        "plus the thesis challenge and evidence audit. Price movement is context only."
    )
    action_counts = summary.get("action_counts") or {}
    one, two, three, four = st.columns(4)
    one.metric("Positions monitored", summary.get("position_count", 0))
    two.metric("Hold", action_counts.get("HOLD", 0))
    three.metric("Review alerts", summary.get("alerts_required", 0))
    four.metric("Price unavailable", action_counts.get("DATA_UNAVAILABLE", 0))

    allocation_changes = [
        position
        for position in snapshot.get("positions") or []
        if str((position.get("allocation_recommendation") or {}).get("action") or "")
        not in {"", "NO_CHANGE", "RESEARCH_REFRESH"}
    ]
    applied_changes = snapshot.get("applied_portfolio_changes") or []
    if applied_changes:
        st.markdown("#### Automatic proposed-portfolio update")
        st.success(
            "The research-led model has updated the proposed paper portfolio. "
            "No broker order has been sent."
        )
        for change in applied_changes:
            movement = float(change.get("allocation_change") or 0.0)
            colour = "#15803d" if movement > 0 else "#b91c1c"
            action = "increased" if movement > 0 else "reduced"
            company = html.escape(str(change.get("company") or change.get("ticker") or "Company"))
            ticker = html.escape(str(change.get("ticker") or ""))
            reasons = "; ".join(
                html.escape(str(reason))
                for reason in change.get("reasons") or []
            ) or "The recorded research-led reallocation rationale."
            st.markdown(
                f"<div style='border-left: 5px solid {colour}; padding: 0.7rem 1rem; margin: 0.4rem 0; "
                f"background: rgba(148, 163, 184, 0.08); border-radius: 0.3rem;'>"
                f"<strong>{company} ({ticker})</strong> was <strong style='color:{colour}'>{action}</strong> "
                f"from <strong>{percent(change.get('before_weight'))}</strong> to "
                f"<strong>{percent(change.get('after_weight'))}</strong> "
                f"({percent(movement)}).<br/><span style='color:#94a3b8'>{reasons}</span></div>",
                unsafe_allow_html=True,
            )
        st.caption(
            f"Updated proposed portfolio: {snapshot.get('applied_portfolio_path') or 'saved as a dated portfolio record'}."
        )

    st.markdown("#### Research-led allocation review")
    if allocation_changes:
        for position in allocation_changes:
            recommendation = position.get("allocation_recommendation") or {}
            title = (
                f"{position.get('company')} ({position.get('ticker')}) · "
                f"{recommendation.get('action') or 'REVIEW'}"
            )
            with st.expander(title):
                one, two, three = st.columns(3)
                one.metric("Current allocation", percent(recommendation.get("current_weight")))
                two.metric("Suggested allocation", percent(recommendation.get("suggested_weight")))
                three.metric("Suggested change", percent(recommendation.get("allocation_change")))
                st.write(recommendation.get("reason") or "No explanation was saved.")
                details = [
                    f"Decision rating: {decision_rating_score(recommendation.get('decision_rating'))}",
                    f"Rating change: {recommendation.get('decision_rating_change', '—')}",
                    f"Audit: {recommendation.get('audit_status') or '—'}",
                    f"Thesis: {recommendation.get('thesis_result') or '—'}",
                ]
                st.caption(" · ".join(details))
                st.markdown(
                    f"[Open the updated research for {position.get('ticker')}](?ticker={position.get('ticker')})"
                )
    else:
        st.success("No research-led allocation change is currently recommended in the latest review.")

    reallocation_plan = snapshot.get("reallocation_plan") or {}
    st.markdown("#### Balanced allocation transfers")
    st.caption(
        "Capital moves only between holdings with opposing research-led recommendations. "
        "A balanced transfer is applied automatically to the proposed paper portfolio only after its position and sector limits are checked."
    )
    transfers = reallocation_plan.get("transfers") or []
    if transfers:
        if reallocation_plan.get("status") == "APPLIED_TO_PROPOSED_PORTFOLIO":
            st.success("These research-led transfers have been applied to the proposed paper portfolio.")
        else:
            st.info("These transfers will be applied automatically when the proposed portfolio is refreshed, provided its limits still clear.")
        st.dataframe(
            [
                {
                    "From": f"{item.get('from_company')} ({item.get('from_ticker')})",
                    "To": f"{item.get('to_company')} ({item.get('to_ticker')})",
                    "Portfolio allocation": percent(item.get("weight")),
                    "Why reduce": item.get("from_reason"),
                    "Why increase": item.get("to_reason"),
                }
                for item in transfers
            ],
            hide_index=True,
            width="stretch",
        )
    elif reallocation_plan.get("unfunded_increase"):
        st.info(
            "An increase is suggested, but no current holding independently has a research-led reduction recommendation to fund it. "
            "The system will not create cash or trim a supported holding automatically."
        )
    elif reallocation_plan.get("unused_reduction"):
        st.info(
            "A reduction is suggested, but no separately audit-cleared, master-approved replacement is currently available. "
            "The scheduled research cycle will keep expanding the sector-diverse replacement pool; the system will not create cash or use an unsupported company in the meantime."
        )
    else:
        st.success("No balanced research-led transfer is proposed in the latest review.")

    if snapshot.get("reallocation_apply_status"):
        st.warning(
            "The proposed paper portfolio was not changed because a safety constraint requires research review: "
            + str(snapshot.get("reallocation_apply_reason") or "No further detail was saved.")
        )

    exposure = snapshot.get("market_exposure") or {}
    st.markdown("#### Market overlap and liquidity")
    st.caption(
        "This checks diversification beyond company names and sectors: it uses observed price-history correlation, industry exposure and 20-day dollar-volume context. "
        "It is a portfolio-risk review, not a standalone trading signal."
    )
    if exposure.get("status") == "COMPLETE":
        one, two, three = st.columns(3)
        one.metric("History coverage", f"{exposure.get('covered_holdings', 0)} / {exposure.get('total_holdings', 0)}")
        two.metric("Effective position count", exposure.get("effective_position_count") or "—")
        top_pairs = exposure.get("highest_correlated_pairs") or []
        three.metric(
            "Highest overlap",
            f"{top_pairs[0].get('correlation', 0):.2f}" if top_pairs else "—",
        )
        if exposure.get("risk_alerts"):
            for alert in exposure.get("risk_alerts"):
                st.warning(alert)
        if top_pairs:
            st.dataframe(
                [
                    {
                        "First holding": item.get("first_ticker"),
                        "Second holding": item.get("second_ticker"),
                        "Observed correlation": item.get("correlation"),
                    }
                    for item in top_pairs[:5]
                ],
                hide_index=True,
                width="stretch",
            )
    else:
        st.info(exposure.get("reason") or "Market-overlap data is not available yet.")

    positions = snapshot.get("positions") or []
    alerts = [position for position in positions if position.get("action") != "HOLD"]
    if alerts:
        st.markdown("#### Changes requiring attention")
        for position in alerts:
            title = f"{position.get('company')} ({position.get('ticker')}) · {position.get('action')}"
            with st.expander(title):
                one, two, three = st.columns(3)
                one.metric("Allocation", percent(position.get("weight")))
                two.metric("Price change", percent(position.get("price_change")))
                three.metric("Current price", currency(position.get("current_price")))
                markdown_list(position.get("alerts"), "No detail was saved for this alert.")
                st.caption("Suggested next step: refresh company research. Any balanced, constraint-checked change will update the proposed paper portfolio automatically.")
    else:
        st.success("No research-led audit, thesis, technical or catalyst review is currently recorded in the latest check.")

    st.markdown("#### All monitored positions")
    st.dataframe(
        [
            {
                "Company": position.get("company"),
                "Ticker": position.get("ticker"),
                "Allocation": percent(position.get("weight")),
                "Entry price": currency(position.get("entry_price")),
                "Current price": currency(position.get("current_price")),
                "Change": percent(position.get("price_change")),
                "Action": position.get("action"),
                "Allocation review": (position.get("allocation_recommendation") or {}).get("action") or "—",
                "Suggested allocation": percent((position.get("allocation_recommendation") or {}).get("suggested_weight")),
            }
            for position in positions
        ],
        hide_index=True,
        width="stretch",
    )
    st.caption(
        "The same review can be run on a schedule with a full research refresh for every holding. "
        "For the demonstration, this button produces a dated, auditable snapshot and may create the next proposed paper-portfolio record."
    )


def render_research(ticker: str, name: str | None, selected_tickers: set[str]) -> None:
    raw = ResearchService.load(ticker)
    if raw is None:
        st.warning(f"No saved research is available for {ticker}.")
        return

    record = ResearchContract.from_pipeline_result(raw)
    research = raw.get("research") or {}
    synthesis = raw.get("synthesis") or {}
    fundamental = (raw.get("core") or {}).get("fundamental") or {}
    diagnostics = record.get("diagnostics") or {}
    audit = record.get("audit") or {}
    thesis = record.get("thesis") or {}
    signals = record.get("market_signals") or {}
    sentiment = record.get("sentiment") or {}
    valuation_quality = record.get("valuation_quality") or {}
    master = PortfolioConstructionService.current_master_decision(raw, record)
    provider_evidence = record.get("provider_evidence") or {}
    specialist = record.get("specialist_research") or {}
    catalysts = research.get("catalysts") or {}
    news = research.get("news") or {}

    st.divider()
    st.subheader(f"{name or ticker} · {ticker}")
    st.caption(
        "Saved, evidence-audited research used by the portfolio model. "
        "Evidence confidence is a research-quality score, not a probability or investment guarantee."
    )

    one, two, three, four = st.columns(4)
    confidence = (master.get("research_confidence") or {}).get("score") or master.get("confidence")
    rating_detail = PortfolioEngine.decision_rating_detail(record)
    one.metric("Decision rating", decision_rating_score(rating_detail))
    two.metric("Evidence confidence", confidence_score(confidence))
    three.metric("Portfolio selection", "SELECTED" if ticker in selected_tickers else "NOT SELECTED")
    four.metric("Evidence audit", audit.get("status") or "—")
    st.caption(
        "The decision rating combines opportunity, evidence quality, valuation reliability, "
        "risk, thesis strength and source breadth. It uses the full 0–100 scale and makes a small evidence-based uncertainty deduction, "
        "so it does not present a false 100/100 certainty score."
    )

    st.markdown("#### Portfolio decision")
    st.write(
        record.get("decision_reason")
        or synthesis.get("decision_reason")
        or "No decision rationale was saved."
    )
    hard_gates = master.get("hard_gate_reasons") or []
    if ticker not in selected_tickers and hard_gates:
        st.info("Why it is not currently held: " + " ".join(str(item) for item in hard_gates))
    elif ticker in selected_tickers:
        st.success("This company is included in the proposed portfolio after the research, audit and diversification checks.")

    st.markdown("#### Investment case and risks")
    left, right = st.columns(2)
    with left:
        st.markdown("**Supporting evidence**")
        markdown_list(synthesis.get("bull_case"), "No supporting evidence was saved.")
    with right:
        st.markdown("**Risks and challenge**")
        markdown_list(synthesis.get("bear_case"), "No material risks were saved.")

    st.markdown("#### Valuation and time horizon")
    one, two, three, four = st.columns(4)
    one.metric("Current price", currency(record.get("current_price")))
    two.metric("Base value", currency(record.get("base_intrinsic_value")))
    three.metric("Estimated price upside", percent(record.get("expected_return")))
    four.metric("Estimated yearly upside", percent(record.get("annualised_expected_return")))
    st.caption(
        f"{forecast_horizon_label(record.get('valuation_horizon_years'))}. "
        "Estimated price upside compares today's price with the model's base-case value over that period; "
        "it is not a guaranteed return by a certain date."
    )

    holding_period, holding_period_note = holding_period_guidance(
        record.get("valuation_horizon_years")
    )
    st.markdown("#### Suggested holding period")
    one, two = st.columns(2)
    one.metric("Suggested holding period", holding_period)
    two.metric("Thesis review cadence", "Quarterly")
    st.caption(holding_period_note)

    one, two, three = st.columns(3)
    one.metric("Valuation quality", valuation_quality.get("assessment") or "—")
    two.metric("Risk score", score(signals.get("risk_score")))
    three.metric("Thesis result", thesis.get("result") or "—")

    st.markdown("#### Technical and risk context")
    one, two, three, four = st.columns(4)
    one.metric("Technical score", score(signals.get("technical_score")))
    two.metric("60-day price change", percent(signals.get("return_60d")))
    three.metric("Drawdown from recent high", percent(signals.get("drawdown_from_252d_high")))
    four.metric("Annualised volatility", percent(signals.get("annualised_volatility")))
    one, two, three, four = st.columns(4)
    one.metric("Support level", currency(signals.get("support_level")))
    two.metric("Resistance level", currency(signals.get("resistance_level")))
    three.metric("20d/60d volume ratio", f"{signals.get('volume_ratio_20d_to_60d'):.2f}x" if isinstance(signals.get("volume_ratio_20d_to_60d"), (int, float)) else "—")
    four.metric("Nearest Fibonacci level", signals.get("nearest_fibonacci_level") or "—")
    st.caption(
        "Technical context combines 20-, 60-, 120- and 252-day price trends, moving averages, drawdown, trend persistence, volume confirmation, "
        "and support/resistance. Fibonacci levels are shown as reproducible range context, not a standalone prediction. Risk context considers beta, debt versus cash, "
        "overall and downside volatility, and observed drawdown. These are evidence inputs, not automatic trading signals."
    )

    st.markdown("#### Specialist research bots")
    specialist_summary = specialist_rows(specialist)
    if specialist_summary:
        st.caption(
            f"{specialist.get('completed_count', 0)} of {specialist.get('requested_count', 0)} specialist reviews completed."
        )
        st.dataframe(specialist_summary, hide_index=True, width="stretch")
    else:
        st.info("No specialist research summary is available for this saved record.")

    st.markdown("#### Catalysts and company-specific news")
    catalyst_summary = catalysts.get("summary") or {}
    cat_one, cat_two, cat_three = st.columns(3)
    cat_one.metric("Validated positive catalyst effect", score(catalyst_summary.get("positive_score")))
    cat_two.metric("Validated negative catalyst effect", score(catalyst_summary.get("negative_score")))
    cat_three.metric("News sentiment", sentiment.get("label") or "—")
    st.caption(
        f"{catalyst_summary.get('validated_count', 0)} of {catalyst_summary.get('discovered_count', 0)} discovered events have enough evidence to affect this summary. "
        "The score reflects event evidence and potential materiality, not a share-price forecast."
    )

    company_catalysts = relevant_catalysts(
        catalysts.get("validated_catalysts"), ticker, name
    )
    if company_catalysts:
        st.dataframe(
            [
                {
                    "Event": item.get("title") or item.get("description") or "—",
                    "Direction": item.get("direction") or "—",
                    "Impact": item.get("impact") or "—",
                    "Event evidence": (item.get("validation") or {}).get("confidence") or "—",
                    "Event likelihood": percent((item.get("probability_assessment") or {}).get("probability")),
                    "Expected date": item.get("expected_date") or "—",
                    "Source": item.get("source") or "—",
                }
                for item in company_catalysts[:8]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No company-specific validated catalysts are available in this saved record.")

    raw_news = (news.get("raw_research") or {}).get("News")
    company_headlines = relevant_headlines(raw_news, ticker, name)
    if company_headlines:
        st.dataframe(
            [
                {
                    "Headline": item.get("Title"),
                    "Source": item.get("Source"),
                    "Published": item.get("Published") or "—",
                }
                for item in company_headlines[:8]
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("No company-specific headline is retained after relevance filtering.")

    st.markdown("#### Market, macro and supplementary data")
    market_context = record.get("market_context") or {}
    market_regime = market_context.get("market_regime") or {}
    macro = market_context.get("macro_environment") or {}
    one, two, three, four = st.columns(4)
    one.metric("S&P 500 market regime", market_regime.get("regime") or "—")
    two.metric("Macro regime", macro.get("regime") or "—")
    three.metric("Technical score", score(signals.get("technical_score")))
    four.metric("Independent news sources", str(sentiment.get("independent_source_count") or 0))
    completed_roles = provider_evidence.get("completed_roles") or []
    unavailable_roles = provider_evidence.get("unavailable_roles") or []
    fresh_provider_count = provider_evidence.get("fresh_provider_count")
    st.caption(
        "Supplementary provider coverage: "
        + (", ".join(str(role).replace("_", " ") for role in completed_roles) or "none")
        + ". These sources improve coverage only; they do not override SEC facts or audit gates."
    )
    if fresh_provider_count is not None:
        st.caption(
            f"Fresh supplementary providers at the time of research: {fresh_provider_count}. "
            "Saved research is later flagged for a full refresh rather than treated as permanently current."
        )
    if unavailable_roles:
        st.caption(
            "Not currently available: "
            + ", ".join(str(role).replace("_", " ") for role in unavailable_roles)
            + "."
        )

    st.markdown("#### Audit and data-quality checks")
    if diagnostics.get("issues"):
        st.dataframe(
            [
                {
                    "Area": issue.get("component"),
                    "Impact": issue.get("severity"),
                    "Issue": issue.get("finding"),
                    "Next action": issue.get("recommended_action"),
                    "Useful sources": ", ".join(issue.get("recommended_sources") or []),
                }
                for issue in diagnostics.get("issues", [])
            ],
            hide_index=True,
            width="stretch",
        )
    else:
        findings = audit.get("findings") or []
        if findings:
            st.dataframe(
                [
                    {
                        "Impact": finding.get("severity") or "—",
                        "Area": finding.get("field") or finding.get("type") or "—",
                        "Finding": finding.get("message") or "No explanation was saved.",
                    }
                    for finding in findings
                ],
                hide_index=True,
                width="stretch",
            )
        elif audit.get("status") == "PASS":
            st.success("The saved evidence audit passed with no unresolved itemised findings.")
        else:
            st.warning(
                "This older record has a non-PASS audit but no itemised finding. "
                "Refresh its research to generate a diagnostic explanation."
            )

    with st.expander("Financial data and full audit detail"):
        financials = fundamental.get("financials") or {}
        st.dataframe(
            [
                {"Metric": "Revenue", "Value": currency(financials.get("revenue"))},
                {"Metric": "Free cash flow", "Value": currency(financials.get("free_cash_flow"))},
                {"Metric": "Net debt", "Value": currency(financials.get("net_debt"))},
                {"Metric": "ROIC", "Value": percent(fundamental.get("roic"))},
            ],
            hide_index=True,
            width="stretch",
        )
        findings = audit.get("findings") or []
        if findings:
            st.markdown("**Audit findings**")
            st.dataframe(findings, hide_index=True, width="stretch")


st.markdown(
    """
    <style>
        .block-container {max-width: 1200px; padding-top: 3rem; padding-bottom: 4rem;}
        [data-testid="stMetric"] {background: rgba(128,128,128,.08); border: 1px solid rgba(128,128,128,.16); border-radius: 12px; padding: 0.85rem;}
        div[data-testid="stExpander"] {border-radius: 12px;}
        .sector-key {display:flex; flex-wrap:wrap; gap:.45rem .75rem; margin:.25rem 0 1rem;}
        .sector-key-item {display:inline-flex; align-items:center; gap:.35rem; font-size:.88rem; white-space:nowrap;}
        .sector-swatch {width:.72rem; height:.72rem; border-radius:50%; display:inline-block;}
        @media (max-width: 700px) {
            .block-container {padding: 1.25rem 1rem 3rem;}
            [data-testid="stMetric"] {padding: 0.65rem;}
            h1 {font-size: 2rem;}
        }
    </style>
    """,
    unsafe_allow_html=True,
)

scan = PortfolioConstructionService.research_scan()
portfolio, portfolio_path = portfolio_for_display()
readiness = PortfolioConstructionService.portfolio_readiness(scan, TARGET_HOLDINGS)
benchmark = PortfolioBenchmarkService.disclosure(portfolio)
selected = ticker_from_url()

st.title("Portfolio Construction")
st.caption("A fully invested, risk-aware model portfolio built from the S&P 500 and Nasdaq-100 research universe.")

requested_view = str(st.query_params.get("view") or "").lower()
portfolio_view = st.radio(
    "Portfolio view",
    ("Proposed portfolio", "Current portfolio"),
    horizontal=True,
    index=1 if requested_view == "current" else 0,
)
if portfolio_view == "Current portfolio":
    render_current_portfolio(portfolio)
    st.stop()

if selected:
    selected_name = next(
        (item.get("name") for item in scan.get("results", []) if item.get("ticker") == selected),
        None,
    )
    selected_tickers = {
        str(holding.get("ticker") or "").upper()
        for holding in (portfolio or {}).get("holdings", [])
    }
    st.markdown("[← Back to proposed portfolio](?view=proposed)")
    render_research(selected, selected_name, selected_tickers)
    st.stop()

action_left, action_right = st.columns([3, 1])
with action_left:
    st.write("The proposed portfolio is ranked by decision rating and sized using evidence confidence, expected return, volatility and risk quality.")
with action_right:
    if st.button("Construct / refresh portfolio", type="primary", use_container_width=True):
        st.session_state["portfolio_result"] = PortfolioConstructionService.construct(TARGET_HOLDINGS)
        st.rerun()

if portfolio is None:
    st.warning(
        "No proposed portfolio is ready yet. "
        + readiness["message"]
        + " The research queue is being expanded instead of filling the portfolio with weak or unaudited names."
    )
    one, two, three, four = st.columns(4)
    one.metric("Research records", scan.get("requested_count", 0))
    two.metric("Audit-cleared", scan.get("audit_pass_count", 0))
    three.metric("Master-approved", scan.get("eligible_count", 0))
    four.metric("Target holdings", readiness["target_holdings"])
else:
    rows = holding_rows(portfolio)
    one, two, three, four = st.columns(4)
    one.metric("Holdings", len(rows))
    two.metric("Invested", percent(sum(float(row.get("weight") or 0) for row in rows)))
    three.metric("Cash", percent(portfolio.get("cash_weight")))
    three.caption("Cash is disabled by prototype policy.")
    four.metric("Risk review", "PASS" if (portfolio.get("risk_review") or {}).get("Pass") else "REVIEW")

    construction_readiness = portfolio.get("construction_readiness") or readiness
    if not construction_readiness.get("target_reached", True):
        st.info(
            f"This is a fully invested {len(rows)}-company portfolio made only from the currently eligible research. "
            f"{construction_readiness.get('shortfall', 0)} more audit-cleared, master-approved candidate"
            f"{'s are' if construction_readiness.get('shortfall', 0) != 1 else ' is'} needed to reach the "
            f"{construction_readiness.get('target_holdings', TARGET_HOLDINGS)}-company target."
        )

    top, bottom = st.columns([1, 1.45])
    with top:
        st.markdown("### Sector allocation")
        render_sector_chart(rows)
    with bottom:
        st.markdown("### Portfolio at a glance")
        horizon = forecast_horizon_label(portfolio.get("valuation_horizon_years"))
        st.metric("Estimated price upside", percent(portfolio.get("portfolio_expected_return")))
        st.metric("Estimated yearly upside", percent(portfolio.get("portfolio_annualised_expected_return")))
        st.metric("Portfolio risk review", "PASS" if (portfolio.get("risk_review") or {}).get("Pass") else "REVIEW")
        st.caption(
            f"{horizon}. Estimated upside is a model comparison, not a promise or a time-certain return forecast. "
            "Holdings below are ranked from highest to lowest decision rating."
        )

    st.markdown("### Portfolio performance versus S&P 500")
    benchmark_info = benchmark.get("benchmark") or {}
    comparison = benchmark.get("performance_comparison") or {}
    horizon_info = benchmark.get("valuation_time_horizon") or {}
    one, two, three = st.columns(3)
    one_month = comparison.get("one_month") or {}
    if comparison.get("status") == "COMPLETE":
        one.metric("Portfolio, past month", percent(one_month.get("portfolio_return")))
        two.metric("S&P 500, past month", percent(one_month.get("sp500_return")))
        three.metric("Difference", percent(one_month.get("relative_return")))
    else:
        one.metric("Portfolio, past month", "Not enough data")
        two.metric("S&P 500, past month", "Not enough data")
        three.metric("Difference", "Not enough data")
    st.caption(
        f"Benchmark: {benchmark_info.get('name') or 'S&P 500 Index'}. "
        + (comparison.get("reason") or "")
        + " "
        + (horizon_info.get("disclosure") or "")
    )
    performance_history = comparison.get("history") or []
    if len(performance_history) >= 2:
        chart_rows = [
            {"Date": item["checked_at"], "Series": "Portfolio", "Return": item["portfolio_return"]}
            for item in performance_history
        ] + [
            {"Date": item["checked_at"], "Series": "S&P 500", "Return": item["sp500_return"]}
            for item in performance_history
        ]
        chart = (
            alt.Chart(alt.Data(values=chart_rows))
            .mark_line(point=True)
            .encode(
                x=alt.X("Date:T", title=None),
                y=alt.Y("Return:Q", axis=alt.Axis(format="%"), title="Cumulative return"),
                color=alt.Color("Series:N", title=None),
                tooltip=[alt.Tooltip("Date:T"), alt.Tooltip("Series:N"), alt.Tooltip("Return:Q", format=".1%")],
            )
            .properties(height=250)
        )
        st.altair_chart(chart, width="stretch")
    else:
        st.info("The performance graph will appear after at least two dated portfolio health checks. A one-month comparison will appear after 30 days of saved observations.")

    st.markdown("### Proposed portfolio")
    st.caption(
        "Listed from highest to lowest decision rating. Select any ticker to open its full research report, including the investment case, risks, valuation, catalysts, specialist research and audit detail."
    )
    for position, row in enumerate(rows, start=1):
        audit_status = row["audit"].get("status") or "NOT_RUN"
        title = (
            f"{position}. {row['name']} ({row['ticker']}) · {percent(row['weight'])} · "
            f"Decision rating {decision_rating_score(row['decision_rating'])}"
        )
        with st.expander(title):
            one, two, three = st.columns(3)
            one.markdown(f"**Ticker**  \n[{row['ticker']}](?ticker={row['ticker']})")
            two.write(f"**Portfolio decision**  \n{row['decision']}")
            three.write(f"**Audit**  \n{audit_status}")
            one, two, three = st.columns(3)
            one.metric("Allocation", percent(row["weight"]))
            two.metric("Decision rating", decision_rating_score(row["decision_rating"]))
            three.metric("Evidence confidence", confidence_score(row["confidence"]))
            st.caption(row["reasoning"] or "Selected based on relative risk-adjusted opportunity and evidence confidence.")
            if audit_status != "PASS":
                st.markdown(f"[Explain the {audit_status} audit](?ticker={row['ticker']}&section=diagnostics)")
            st.markdown(f"[Open full research for {row['ticker']}](?ticker={row['ticker']}&section=reasoning)")

    with st.expander("Detailed comparison table (best viewed on a larger screen)"):
        header = st.columns([0.35, 1.8, 0.7, 1.1, 0.85, 0.7, 0.95, 1.15, 1.0])
        for column, label in zip(
            header,
            ["#", "Company", "Ticker", "Decision", "Audit", "Weight", "Decision rating", "Evidence confidence", "Research"],
        ):
            column.markdown(f"**{label}**")

        for position, row in enumerate(rows, start=1):
            audit_status = row["audit"].get("status") or "NOT_RUN"
            columns = st.columns([0.35, 1.8, 0.7, 1.1, 0.85, 0.7, 0.95, 1.15, 1.0])
            columns[0].write(position)
            columns[1].markdown(f"**{row['name']}**")
            columns[1].caption(row["sector"])
            columns[2].markdown(f"[{row['ticker']}](?ticker={row['ticker']}&section=reasoning)")
            columns[3].write(row["decision"])
            if audit_status == "PASS":
                columns[4].write("PASS")
            else:
                columns[4].markdown(
                    f"[{audit_status}](?ticker={row['ticker']}&section=diagnostics)"
                )
            columns[5].write(percent(row["weight"]))
            columns[6].write(decision_rating_score(row["decision_rating"]))
            columns[7].write(confidence_score(row["confidence"]))
            columns[7].caption(row["label"])
            columns[8].markdown(
                f"[View reasoning](?ticker={row['ticker']}&section=reasoning)"
            )
            if row["trigger"]:
                st.caption(f"{row['ticker']} review trigger: {row['trigger']}")

    if portfolio_path:
        st.caption(f"Latest portfolio record: {portfolio_path}")

st.markdown("### Watchlist")
st.caption("Candidates that need stronger evidence, a better opportunity score or a completed master decision. They are monitored, not allocated.")
watchlist = scan.get("watchlist") or []
if watchlist:
    for item in watchlist[:12]:
        name = item.get("name") or item.get("ticker")
        left, middle, right = st.columns([2.8, 1.2, 3])
        with left:
            st.markdown(f"[{name} · {item['ticker']}](?ticker={item['ticker']})")
            st.caption(item.get("sector") or "Unclassified")
        with middle:
            st.metric("Research progress", score(item.get("watchlist_priority")))
        with right:
            st.write(item.get("watchlist_reason") or "Monitoring research changes.")
else:
    st.info("No audit-cleared watchlist candidates are currently available.")

with st.expander("Research coverage and diagnostic queue"):
    one, two, three = st.columns(3)
    one.metric("Universe", "517 companies")
    two.metric("Research records", scan.get("requested_count", 0))
    three.metric("Master-approved", scan.get("eligible_count", 0))
    st.caption("The queue refreshes a limited share of old research while expanding into new sectors. It is paced, checkpointed and does not automatically change the portfolio.")
    if st.button("Research next 12 companies", type="secondary"):
        with st.spinner("Researching the next companies…"):
            PortfolioResearchBatchService.run_next_batch(batch_size=12)
        st.rerun()
    provider_status = PortfolioDataProviderRegistry.status()
    st.dataframe(
        [
            {"Research input": role.replace("_", " ").title(), "Provider": value["source"], "Ready": "Yes" if value["configured"] else "Needs API key"}
            for role, value in provider_status.items()
        ],
        hide_index=True,
        width="stretch",
    )
    manual_ticker = st.text_input("Research a specific ticker", placeholder="NVDA")
    if st.button("Research ticker", type="secondary"):
        with st.spinner("Building the research record…"):
            ResearchService.run(manual_ticker)
        st.rerun()
