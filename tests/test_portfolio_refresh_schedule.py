import importlib.util
from pathlib import Path


def load_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "install_portfolio_refresh_schedule.py"
    spec = importlib.util.spec_from_file_location("portfolio_refresh_schedule", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_daily_schedule_uses_the_full_research_cycle_and_project_logs():
    schedule = load_module()
    payload = schedule.schedule_payload(18, 30, 12)

    assert payload["Label"] == schedule.LABEL
    assert payload["StartCalendarInterval"] == {"Hour": 18, "Minute": 30}
    assert payload["ProgramArguments"][-2:] == ["--candidate-batch-size", "12"]
    assert payload["ProgramArguments"][1].endswith("run_portfolio_research_cycle.py")
    assert "scheduler_logs" in payload["StandardOutPath"]


if __name__ == "__main__":
    test_daily_schedule_uses_the_full_research_cycle_and_project_logs()
    print("PORTFOLIO REFRESH SCHEDULE TESTS PASSED")
