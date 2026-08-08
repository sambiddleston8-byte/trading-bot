from core.portfolio.universe_engine import (
    UniverseEngine,
)

from core.portfolio.portfolio_engine import (
    PortfolioEngine,
)


def test_universe_engine_interface():

    assert hasattr(
        UniverseEngine,
        "get_universe",
    )

    assert hasattr(
        UniverseEngine,
        "save",
    )


def test_portfolio_engine_interface():

    assert hasattr(
        PortfolioEngine,
        "rank",
    )

    assert hasattr(
        PortfolioEngine,
        "construct",
    )


if __name__ == "__main__":

    test_universe_engine_interface()
    test_portfolio_engine_interface()

    print(
        "PORTFOLIO INTERFACE TEST PASSED"
    )
