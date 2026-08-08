import json
import os
from datetime import datetime


class FinalInvestmentCommittee:

    def __init__(
        self,
        multi_factor_path="data/multi_factor_rankings.json",
        evidence_path="data/evidence_analysis.json",
        output_path="data/final_investment_rankings.json",
    ):

        self.multi_factor_path = multi_factor_path
        self.evidence_path = evidence_path
        self.output_path = output_path

        directory = os.path.dirname(
            output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------
    # Load JSON
    # --------------------------------

    def load_json(
        self,
        path,
    ):

        if not os.path.exists(path):

            raise FileNotFoundError(
                f"File not found: {path}"
            )

        with open(
            path,
            "r",
        ) as file:

            return json.load(
                file
            )

    # --------------------------------
    # Recommendation
    # --------------------------------

    def recommendation(
        self,
        score,
    ):

        if score >= 85:
            return "STRONG BUY"

        if score >= 75:
            return "BUY"

        if score >= 65:
            return "HOLD / WATCH"

        if score >= 50:
            return "REDUCE / AVOID"

        return "SELL / AVOID"

    # --------------------------------
    # Confidence
    # --------------------------------

    def confidence(
        self,
        multi_factor,
        evidence,
        risk,
    ):

        scores = [
            multi_factor,
            evidence,
            risk,
        ]

        spread = (
            max(scores)
            - min(scores)
        )

        confidence = (
            90
            - (
                spread
                * 0.35
            )
        )

        return round(
            max(
                50,
                min(
                    confidence,
                    95,
                ),
            ),
            1,
        )

    # --------------------------------
    # Risk Adjustment
    # --------------------------------

    def risk_adjustment(
        self,
        factor_scores,
    ):

        risk = factor_scores.get(
            "Risk",
            50,
        )

        balance_sheet = factor_scores.get(
            "Balance Sheet",
            50,
        )

        adjustment = 0

        if risk < 40:

            adjustment -= 8

        elif risk < 50:

            adjustment -= 4

        elif risk >= 75:

            adjustment += 4

        if balance_sheet < 40:

            adjustment -= 5

        elif balance_sheet >= 75:

            adjustment += 2

        return adjustment

    # --------------------------------
    # Build Decision
    # --------------------------------

    def build_decision(
        self,
        multi_factor,
        evidence,
    ):

        factor_scores = multi_factor.get(
            "Factor Scores",
            {},
        )

        multi_factor_score = float(
            multi_factor.get(
                "Overall Score",
                50,
            )
        )

        evidence_score = float(
            evidence.get(
                "Evidence Score",
                50,
            )
        )

        news_score = float(
            evidence.get(
                "News Analysis",
                {}
            ).get(
                "Sentiment Score",
                50,
            )
        )

        risk_score = float(
            factor_scores.get(
                "Risk",
                50,
            )
        )

        risk_adjustment = (
            self.risk_adjustment(
                factor_scores
            )
        )

        # --------------------------------
        # Core Committee Score
        # --------------------------------

        score = (

            multi_factor_score
            * 0.60

            + evidence_score
            * 0.25

            + news_score
            * 0.05

            + risk_score
            * 0.10

        )

        score += risk_adjustment

        score = round(
            max(
                0,
                min(
                    score,
                    100,
                ),
            ),
            2,
        )

        recommendation = (
            self.recommendation(
                score
            )
        )

        confidence = (
            self.confidence(
                multi_factor_score,
                evidence_score,
                risk_score,
            )
        )

        strengths = (
            evidence.get(
                "Strengths",
                [],
            )
        )

        weaknesses = (
            evidence.get(
                "Weaknesses",
                [],
            )
        )

        catalysts = (
            evidence.get(
                "Catalysts",
                [],
            )
        )

        risks = (
            evidence.get(
                "Risks",
                [],
            )
        )

        return {

            "Ticker":
                multi_factor.get(
                    "Ticker"
                ),

            "Company":
                multi_factor.get(
                    "Company"
                ),

            "Sector":
                multi_factor.get(
                    "Sector"
                ),

            "Industry":
                multi_factor.get(
                    "Industry"
                ),

            "Committee Score":
                score,

            "Recommendation":
                recommendation,

            "Confidence":
                confidence,

            "Multi Factor Score":
                multi_factor_score,

            "Evidence Score":
                evidence_score,

            "News Score":
                news_score,

            "Risk Score":
                risk_score,

            "Risk Adjustment":
                risk_adjustment,

            "Factor Scores":
                factor_scores,

            "Strengths":
                strengths,

            "Weaknesses":
                weaknesses,

            "Catalysts":
                catalysts,

            "Risks":
                risks,

            "Committee Summary":
                (
                    f"{multi_factor.get('Ticker')} "
                    f"receives a committee score of "
                    f"{score}/100 and is classified as "
                    f"{recommendation}. "
                    f"Confidence is "
                    f"{confidence}%."
                ),

            "Timestamp":
                datetime.now().isoformat(),

        }

    # --------------------------------
    # Run
    # --------------------------------

    def run(self):

        multi_factor_data = (
            self.load_json(
                self.multi_factor_path
            )
        )

        evidence_data = (
            self.load_json(
                self.evidence_path
            )
        )

        multi_factor_results = (
            multi_factor_data.get(
                "Rankings",
                [],
            )
        )

        evidence_results = (
            evidence_data.get(
                "Results",
                [],
            )
        )

        evidence_by_ticker = {

            result.get(
                "Ticker"
            ): result

            for result
            in evidence_results

        }

        final_results = []

        for multi_factor in (
            multi_factor_results
        ):

            ticker = multi_factor.get(
                "Ticker"
            )

            evidence = (
                evidence_by_ticker.get(
                    ticker,
                    {},
                )
            )

            decision = (
                self.build_decision(
                    multi_factor,
                    evidence,
                )
            )

            final_results.append(
                decision
            )

        # --------------------------------
        # Rank
        # --------------------------------

        final_results.sort(
            key=lambda item: (
                item.get(
                    "Committee Score",
                    0,
                )
            ),
            reverse=True,
        )

        for rank, result in enumerate(
            final_results,
            start=1,
        ):

            result[
                "Committee Rank"
            ] = rank

        # --------------------------------
        # Save
        # --------------------------------

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Companies":
                len(final_results),

            "Results":
                final_results,

        }

        with open(
            self.output_path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        # --------------------------------
        # Print
        # --------------------------------

        print()
        print("=" * 90)
        print("FINAL INVESTMENT COMMITTEE")
        print("=" * 90)
        print()

        for result in final_results[:20]:

            print(
                f"{result['Committee Rank']:>2}. "
                f"{result['Ticker']:<6} "
                f"Score: "
                f"{result['Committee Score']:>6.2f} "
                f"{result['Recommendation']:<15} "
                f"Confidence: "
                f"{result['Confidence']:>5.1f}%"
            )

        print()
        print(
            f"Companies ranked: "
            f"{len(final_results)}"
        )

        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return final_results


if __name__ == "__main__":

    committee = (
        FinalInvestmentCommittee()
    )

    committee.run()