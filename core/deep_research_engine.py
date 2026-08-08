import json
import os
from datetime import datetime

from core.research_engine import ResearchEngine


class DeepResearchEngine:

    def __init__(
        self,
        shortlist_path="data/research_shortlist.json",
        output_path="data/deep_research.json",
    ):

        self.shortlist_path = shortlist_path
        self.output_path = output_path

        self.research_engine = (
            ResearchEngine()
        )

        directory = os.path.dirname(
            output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------
    # Load Shortlist
    # --------------------------------

    def load_shortlist(self):

        if not os.path.exists(
            self.shortlist_path
        ):

            raise FileNotFoundError(
                f"Shortlist not found: "
                f"{self.shortlist_path}"
            )

        with open(
            self.shortlist_path,
            "r",
        ) as file:

            data = json.load(
                file
            )

        return data.get(
            "Shortlist",
            [],
        )

    # --------------------------------
    # Research One Company
    # --------------------------------

    def research_company(
        self,
        stock,
    ):

        symbol = stock.get(
            "Ticker"
        )

        print()
        print(
            f"Researching {symbol}..."
        )

        try:

            research = (
                self.research_engine
                .collect(
                    symbol
                )
            )

        except Exception as error:

            print(
                f"{symbol} research failed: "
                f"{error}"
            )

            research = {

                "Ticker":
                    symbol,

                "Error":
                    str(error),

                "Sources":
                    [],

                "News":
                    [],

                "Financial Data":
                    {},

                "SEC Filings":
                    {},

            }

        return {

            "Ticker":
                symbol,

            "Research Rank":
                stock.get(
                    "Research Rank"
                ),

            "Multi Factor Score":
                stock.get(
                    "Overall Score"
                ),

            "Factor Scores":
                stock.get(
                    "Factor Scores",
                    {},
                ),

            "Company":
                stock.get(
                    "Company"
                ),

            "Sector":
                stock.get(
                    "Sector"
                ),

            "Industry":
                stock.get(
                    "Industry"
                ),

            "Deep Research":
                research,

            "Timestamp":
                datetime.now().isoformat(),

        }

    # --------------------------------
    # Research All
    # --------------------------------

    def run(self):

        shortlist = (
            self.load_shortlist()
        )

        print()
        print("=" * 80)
        print("DEEP RESEARCH ENGINE")
        print("=" * 80)

        print()
        print(
            f"Companies to research: "
            f"{len(shortlist)}"
        )

        results = []

        for index, stock in enumerate(
            shortlist,
            start=1,
        ):

            print()
            print(
                f"[{index}/{len(shortlist)}]"
            )

            result = (
                self.research_company(
                    stock
                )
            )

            results.append(
                result
            )

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Companies Researched":
                len(results),

            "Results":
                results,

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

        print()
        print("=" * 80)
        print("DEEP RESEARCH COMPLETE")
        print("=" * 80)

        print()
        print(
            f"Companies researched: "
            f"{len(results)}"
        )

        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return results


if __name__ == "__main__":

    engine = DeepResearchEngine()

    engine.run()