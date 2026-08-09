from pathlib import Path

from core.portfolio_manager import PortfolioManager


def test_public_portfolio_entry_path_does_not_import_legacy_decision_engine():
    manager_source = Path("core/portfolio_manager.py").read_text(encoding="utf-8")
    scanner_source = Path("bots/scanner/analyser.py").read_text(encoding="utf-8")
    assert "from core.decision_engine" not in manager_source
    assert "from core.decision_engine" not in scanner_source
    assert hasattr(PortfolioManager, "construct_portfolio")


if __name__ == "__main__":
    test_public_portfolio_entry_path_does_not_import_legacy_decision_engine()
    print("PORTFOLIO ENTRY PATH TESTS PASSED")
