import json
import os
from datetime import datetime, timezone

from core.multi_factor_engine import MultiFactorEngine
from core.investment_committee import InvestmentCommittee
from core.catalyst_engine import CatalystEngine
from core.research_intelligence import ResearchIntelligence
from core.evidence_engine import EvidenceEngine
from core.research_history import ResearchHistory


class ResearchEngine:

    def __init__(self):

        self.multi_factor = (
            MultiFactorEngine()
        )

        self.committee = (
            InvestmentCommittee()
        )

        self.catalyst = (
            CatalystEngine()
        )

        self.intelligence = (
            ResearchIntelligence()
        )

        self.evidence = (
            EvidenceEngine()
        )

        self.history = (
            ResearchHistory()
        )

        self.output_directory = (
            "data/research"
        )

        os.makedirs(
            self.output_directory,
            exist_ok=True,
        )

    # ============================================================
    # TIME
    # ============================================================

    def utc_now(self):

        return datetime.now(
            timezone.utc
        ).isoformat()

    # ============================================================
    # SAVE
    # ============================================================

    def save(
        self,
        symbol,
        research,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                research,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # LOAD EXISTING
    # ============================================================

    def load(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        path = os.path.join(
            self.output_directory,
            f"{symbol}.json",
        )

        if not os.path.exists(
            path
        ):

            return None

        try:

            with open(
                path,
                "r",
            ) as file:

                return json.load(
                    file
                )

        except Exception:

            return None

    # ============================================================
    # CATALYST RESEARCH
    # ============================================================

    def run_catalyst_research(
        self,
        symbol,
    ):

        try:

            if hasattr(
                self.catalyst,
                "analyse",
            ):

                return self.catalyst.analyse(
                    symbol
                )

            if hasattr(
                self.catalyst,
                "analyze",
            ):

                return self.catalyst.analyze(
                    symbol
                )

            if hasattr(
                self.catalyst,
                "run",
            ):

                return self.catalyst.run(
                    symbol
                )

            return {

                "Status":
                    "UNAVAILABLE",

                "Reason":
                    (
                        "Catalyst engine has "
                        "no recognised analysis "
                        "method."
                    ),

            }

        except Exception as error:

            print(
                f"Catalyst research failed "
                f"for {symbol}: {error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # FUNDAMENTAL RESEARCH
    # ============================================================

    def run_factor_research(
        self,
        symbol,
    ):

        try:

            result = (
                self.multi_factor.analyse(
                    symbol
                )
            )

            if result is None:

                return {

                    "Status":
                        "FAILED",

                    "Error":
                        "No analysis returned.",

                }

            return result

        except Exception as error:

            print(
                f"Factor research failed "
                f"for {symbol}: {error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # INVESTMENT COMMITTEE
    # ============================================================

    def run_committee(
        self,
        factor_research,
    ):

        try:

            return (
                self.committee.review(
                    factor_research
                )
            )

        except Exception as error:

            print(
                "Investment Committee "
                f"failed: {error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # BUILD DOSSIER
    # ============================================================

    def build_dossier(
        self,
        symbol,
        factor_research,
        committee,
        catalyst,
    ):

        expected_return = (
            factor_research.get(
                "Expected Return"
            )
        )

        expected_return_horizon = (
            factor_research.get(
                "Expected Return Horizon Days",
                252,
            )
        )

        expected_return_confidence = (
            factor_research.get(
                "Expected Return Confidence"
            )
        )

        expected_return_analysis = (
            factor_research.get(
                "Expected Return Analysis"
            )
        )

        intrinsic_value_return = (
            factor_research.get(
                "Intrinsic Value Return",
                {},
            )
        )

        return {

            "Ticker":
                symbol,

            "Company":
                factor_research.get(
                    "Company"
                ),

            "Sector":
                factor_research.get(
                    "Sector"
                ),

            "Industry":
                factor_research.get(
                    "Industry"
                ),

            "Generated At":
                self.utc_now(),

            # ====================================================
            # DECISION
            # ====================================================

            "Decision":
                committee,

            # ====================================================
            # FUNDAMENTAL RESEARCH
            # ====================================================

            "Fundamental Research":
                factor_research,

            # ====================================================
            # CATALYST RESEARCH
            # ====================================================

            "Catalyst Research":
                catalyst,

            # ====================================================
            # VALUATION RESEARCH
            #
            # This will become the dedicated PV / DCF research
            # section as we integrate the valuation engine.
            # ====================================================

            "Valuation Research":
                factor_research.get(
                    "Valuation Research",
                    {},
                ),

            # ====================================================
            # EXPECTED RETURNS
            # ====================================================

            "Expected Returns": {

                "Factor Model": {

                    "Expected Return":
                        expected_return,

                    "Horizon Days":
                        expected_return_horizon,

                    "Confidence":
                        expected_return_confidence,

                    "Analysis":
                        expected_return_analysis,

                },

                "Intrinsic Value Model":
                    intrinsic_value_return,

            },

            # ====================================================
            # RESEARCH SOURCES
            # ====================================================

            "Research Sources": {

                "Fundamental Model":
                    "Multi-Factor Engine",

                "Catalysts":
                    "Catalyst Engine",

                "Market Data":
                    "Yahoo Finance",

            },

        }

    # ============================================================
    # RUN INTELLIGENCE
    # ============================================================

    def run_intelligence(
        self,
        dossier,
    ):

        try:

            result = (
                self.intelligence.analyse(
                    dossier.get(
                        "Ticker"
                    ),
                    research=dossier,
                )
            )

            # ----------------------------------------------------
            # IMPORTANT:
            #
            # ResearchIntelligence currently returns the original
            # research dossier inside "Research Dossier".
            #
            # That is useful when viewing the intelligence object
            # independently, but it must NOT be embedded back into
            # the master dossier because that creates:
            #
            # dossier
            #   -> intelligence
            #      -> research dossier
            #         -> intelligence
            #
            # which produces a circular JSON reference.
            # ----------------------------------------------------

            if isinstance(
                result,
                dict,
            ):

                result.pop(
                    "Research Dossier",
                    None,
                )

                result.pop(
                    "Output Path",
                    None,
                )

            return result

        except Exception as error:

            print(
                f"Research intelligence failed: "
                f"{error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # RUN EVIDENCE
    # ============================================================

    def run_evidence(
        self,
        dossier,
    ):

        try:

            result = (
                self.evidence.analyse(
                    dossier.get(
                        "Ticker"
                    ),
                    research=dossier,
                )
            )

            # Evidence has its own saved file, so we don't need
            # another copy of the path inside the master dossier.

            if isinstance(
                result,
                dict,
            ):

                result.pop(
                    "Output Path",
                    None,
                )

            return result

        except Exception as error:

            print(
                f"Evidence engine failed: "
                f"{error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # RECORD HISTORY
    # ============================================================

    def record_history(
        self,
        dossier,
    ):

        try:

            return (
                self.history.record(
                    dossier
                )
            )

        except Exception as error:

            print(
                f"Research history failed: "
                f"{error}"
            )

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # COMPARE HISTORY
    # ============================================================

    def compare_history(
        self,
        symbol,
    ):

        try:

            return (
                self.history.report(
                    symbol
                )
            )

        except Exception as error:

            return {

                "Status":
                    "FAILED",

                "Error":
                    str(error),

            }

    # ============================================================
    # FULL RESEARCH
    # ============================================================

    def analyse(
        self,
        symbol,
    ):

        symbol = (
            symbol
            .upper()
            .strip()
        )

        print()
        print("=" * 80)
        print(
            f"RESEARCH ENGINE — {symbol}"
        )
        print("=" * 80)

        # ========================================================
        # STEP 1
        # ========================================================

        print()
        print(
            "STEP 1 — FUNDAMENTAL ANALYSIS"
        )

        factor_research = (
            self.run_factor_research(
                symbol
            )
        )

        if (
            factor_research.get(
                "Status"
            )
            == "FAILED"
        ):

            return factor_research

        print(
            f"Overall factor score: "
            f"{factor_research.get('Overall Score')}"
        )

        # ========================================================
        # STEP 2
        # ========================================================

        print()
        print(
            "STEP 2 — INVESTMENT COMMITTEE"
        )

        committee = (
            self.run_committee(
                factor_research
            )
        )

        print(
            f"Committee score: "
            f"{committee.get('Committee Score')}"
        )

        print(
            f"Recommendation: "
            f"{committee.get('Recommendation')}"
        )

        # ========================================================
        # STEP 3
        # ========================================================

        print()
        print(
            "STEP 3 — CATALYST RESEARCH"
        )

        catalyst = (
            self.run_catalyst_research(
                symbol
            )
        )

        print(
            f"Catalyst score: "
            f"{catalyst.get('Catalyst Score')}"
        )

        # ========================================================
        # STEP 4
        # ========================================================

        print()
        print(
            "STEP 4 — BUILD RESEARCH DOSSIER"
        )

        dossier = (
            self.build_dossier(
                symbol,
                factor_research,
                committee,
                catalyst,
            )
        )

        # ========================================================
        # STEP 5
        # ========================================================

        print()
        print(
            "STEP 5 — RESEARCH INTELLIGENCE"
        )

        intelligence = (
            self.run_intelligence(
                dossier
            )
        )

        dossier[
            "Research Intelligence"
        ] = intelligence

        if (
            intelligence.get(
                "Status"
            )
            == "COMPLETE"
        ):

            rating = (
                intelligence.get(
                    "Investment Rating",
                    {},
                )
            )

            print(
                f"Final rating: "
                f"{rating.get('Rating')}"
            )

            print(
                f"Final score: "
                f"{rating.get('Score')}"
            )

        # ========================================================
        # STEP 6
        # ========================================================

        print()
        print(
            "STEP 6 — EVIDENCE"
        )

        evidence = (
            self.run_evidence(
                dossier
            )
        )

        dossier[
            "Evidence"
        ] = evidence

        # ========================================================
        # STEP 7
        # ========================================================

        print()
        print(
            "STEP 7 — HISTORICAL SNAPSHOT"
        )

        previous_history = (
            self.compare_history(
                symbol
            )
        )

        dossier[
            "Historical Comparison"
        ] = previous_history

        # --------------------------------------------------------
        # Record current research AFTER obtaining the previous
        # snapshot.
        # --------------------------------------------------------

        history_result = (
            self.record_history(
                dossier
            )
        )

        dossier[
            "History Recording"
        ] = {

            "Snapshot ID":
                history_result.get(
                    "Snapshot",
                    {},
                ).get(
                    "Snapshot ID"
                ),

            "History Count":
                history_result.get(
                    "History Count"
                ),

        }

        print(
            f"Historical snapshots: "
            f"{history_result.get('History Count')}"
        )

        # ========================================================
        # STEP 8
        # ========================================================

        print()
        print(
            "STEP 8 — SAVE MASTER DOSSIER"
        )

        path = (
            self.save(
                symbol,
                dossier,
            )
        )

        print()
        print(
            f"Saved to: {path}"
        )

        # ========================================================
        # COMPLETE
        # ========================================================

        print()
        print("=" * 80)
        print(
            "RESEARCH COMPLETE"
        )
        print("=" * 80)

        return dossier


if __name__ == "__main__":

    engine = ResearchEngine()

    engine.analyse(
        "NVDA"
    )