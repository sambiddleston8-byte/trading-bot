from __future__ import annotations

from core.research.specialist_research_engine import SpecialistResearchEngine


class WorkingAnalyser:
    def analyse(self, context):
        assert context == {"ticker": "TEST"}
        return {"Score": 75, "Summary": "Working specialist result."}


class FailingAnalyser:
    def analyse(self, context):
        raise RuntimeError("Expected test failure")


def test_specialist_research_preserves_completed_and_failed_coverage():
    result = SpecialistResearchEngine.analyse(
        {"ticker": "TEST"},
        analyser_classes={
            "working": WorkingAnalyser,
            "failing": FailingAnalyser,
        },
    )

    assert result["status"] == "PARTIAL"
    assert result["completed_count"] == 1
    assert result["signals"]["working"]["status"] == "COMPLETE"
    assert result["signals"]["failing"]["status"] == "ERROR"
    assert "management_transcript_review" in result["deferred_analyses"]


if __name__ == "__main__":
    test_specialist_research_preserves_completed_and_failed_coverage()
    print("SPECIALIST RESEARCH ENGINE TESTS PASSED")
