import json
import os
from datetime import datetime


class ResearchShortlist:

    def __init__(
        self,
        rankings_path="data/multi_factor_rankings.json",
        output_path="data/research_shortlist.json",
        shortlist_size=30,
    ):

        self.rankings_path = rankings_path
        self.output_path = output_path
        self.shortlist_size = shortlist_size

        directory = os.path.dirname(
            self.output_path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------
    # Load Rankings
    # --------------------------------

    def load_rankings(self):

        if not os.path.exists(
            self.rankings_path
        ):

            raise FileNotFoundError(
                f"Ranking file not found: "
                f"{self.rankings_path}"
            )

        with open(
            self.rankings_path,
            "r",
        ) as file:

            data = json.load(
                file
            )

        return data.get(
            "Rankings",
            [],
        )

    # --------------------------------
    # Build Shortlist
    # --------------------------------

    def build(
        self,
    ):

        rankings = (
            self.load_rankings()
        )

        shortlist = (
            rankings[
                :self.shortlist_size
            ]
        )

        # Reassign shortlist rank
        # so the research stage has
        # a clean 1 -> 30 ranking.

        for rank, stock in enumerate(
            shortlist,
            start=1,
        ):

            stock[
                "Research Rank"
            ] = rank

        return shortlist

    # --------------------------------
    # Save
    # --------------------------------

    def save(
        self,
        shortlist,
    ):

        output = {

            "Timestamp":
                datetime.now().isoformat(),

            "Source Universe":
                len(
                    self.load_rankings()
                ),

            "Shortlist Size":
                len(shortlist),

            "Shortlist":
                shortlist,

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

        return output

    # --------------------------------
    # Print
    # --------------------------------

    def print_shortlist(
        self,
        shortlist,
    ):

        print()
        print("=" * 80)
        print("DEEP RESEARCH SHORTLIST")
        print("=" * 80)
        print()

        for stock in shortlist:

            print(
                f"{stock['Research Rank']:>2}. "
                f"{stock['Ticker']:<6} "
                f"Score: "
                f"{stock.get('Overall Score', 0):>6.2f} "
                f"{stock.get('Company', '')}"
            )

        print()

    # --------------------------------
    # Run
    # --------------------------------

    def run(self):

        shortlist = self.build()

        self.save(
            shortlist
        )

        self.print_shortlist(
            shortlist
        )

        print(
            f"Saved to: "
            f"{self.output_path}"
        )

        return shortlist


if __name__ == "__main__":

    shortlist = ResearchShortlist()

    shortlist.run()