import ast
from pathlib import Path


def function_source(source: str, name: str) -> str:
    tree = ast.parse(source)
    node = next(
        item
        for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == name
    )
    return ast.get_source_segment(source, node) or ""


def test_portfolio_website_is_portfolio_first_and_keeps_research_links():
    source = Path("pages/Portfolio_Construction.py").read_text(encoding="utf-8")
    assert "Sector allocation" in source
    assert "Proposed portfolio" in source
    assert "Company" in source
    assert "Evidence confidence" in source
    assert "Decision rating" in source
    assert "Allocation key" in source
    assert "Estimated price upside" in source
    assert "Audit" in source
    assert "View reasoning" in source
    assert "Audit and data-quality checks" in source
    assert "Specialist research bots" in source
    assert "Catalysts and company-specific news" in source
    assert "Valuation and time horizon" in source
    assert "Portfolio performance versus S&P 500" in source
    assert "Portfolio, past month" in source
    assert "The performance graph will appear" in source
    assert "not a guaranteed return" in source
    assert "Construct / refresh portfolio" in source
    assert "?ticker=" in source
    assert "Back to proposed portfolio" in source
    assert "Cash is disabled by prototype policy" in source
    assert "currently eligible research" in source
    assert "Detailed comparison table" in source
    assert "Current portfolio" in source
    assert "Refresh market and thesis review" in source
    assert "Price movement is context only" in source
    assert "Suggested holding period" in source
    assert "Research-led allocation review" in source
    assert "Automatic proposed-portfolio update" in source
    assert "No broker order has been sent" in source
    assert "Market overlap and liquidity" in source
    assert "st.altair_chart(chart" in function_source(source, "render_sector_chart")
    assert "st.altair_chart(chart" not in function_source(source, "render_current_portfolio")


def test_proposed_and_current_portfolio_views_render_without_exceptions():
    from streamlit.testing.v1 import AppTest

    page = Path(__file__).resolve().parents[1] / "pages" / "Portfolio_Construction.py"
    app = AppTest.from_file(str(page))
    app.run(timeout=30)
    assert not app.exception, app.exception

    app.radio[0].set_value("Current portfolio")
    app.run(timeout=30)
    assert not app.exception, app.exception
    assert any("Current portfolio" in item.value for item in app.subheader)


if __name__ == "__main__":
    test_portfolio_website_is_portfolio_first_and_keeps_research_links()
    test_proposed_and_current_portfolio_views_render_without_exceptions()
    print("PORTFOLIO WEBSITE TESTS PASSED")
