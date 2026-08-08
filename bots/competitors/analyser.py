import yfinance as yf

from core.company_context import CompanyContext


class CompetitorAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        symbol = context.symbol

        sector = info.get(
            "sector"
        )

        industry = info.get(
            "industry"
        )

        # --------------------------------
        # Initial Peer Universe
        # --------------------------------

        peer_map = {

            "Semiconductors":
                [
                    "AMD",
                    "AVGO",
                    "QCOM",
                    "INTC",
                    "MU",
                ],

            "Software—Application":
                [
                    "MSFT",
                    "ORCL",
                    "CRM",
                    "ADBE",
                    "NOW",
                ],

            "Internet Retail":
                [
                    "AMZN",
                    "BABA",
                    "MELI",
                    "SHOP",
                    "JD",
                ],

            "Biotechnology":
                [
                    "AMGN",
                    "GILD",
                    "REGN",
                    "VRTX",
                    "BIIB",
                ],

            "Banks—Diversified":
                [
                    "JPM",
                    "BAC",
                    "C",
                    "WFC",
                    "GS",
                ],

            "Oil & Gas Integrated":
                [
                    "XOM",
                    "CVX",
                    "SHEL",
                    "COP",
                    "EOG",
                ],
        }

        peers = peer_map.get(
            industry,
            []
        )

        peers = [
            peer
            for peer in peers
            if peer != symbol
        ]

        # --------------------------------
        # Collect Peer Data
        # --------------------------------

        companies = []

        for peer in peers:

            try:

                peer_info = yf.Ticker(
                    peer
                ).info

                if not peer_info:
                    continue

                companies.append(
                    {
                        "Ticker": peer,

                        "Company":
                            peer_info.get(
                                "longName"
                            ),

                        "Market Cap":
                            peer_info.get(
                                "marketCap"
                            ),

                        "PE":
                            peer_info.get(
                                "trailingPE"
                            ),

                        "Forward PE":
                            peer_info.get(
                                "forwardPE"
                            ),

                        "Revenue Growth":
                            peer_info.get(
                                "revenueGrowth"
                            ),

                        "Profit Margin":
                            peer_info.get(
                                "profitMargins"
                            ),

                        "Operating Margin":
                            peer_info.get(
                                "operatingMargins"
                            ),

                        "ROE":
                            peer_info.get(
                                "returnOnEquity"
                            ),

                        "ROIC":
                            peer_info.get(
                                "returnOnInvestedCapital"
                            ),
                    }
                )

            except Exception:

                continue

        # --------------------------------
        # Subject Company
        # --------------------------------

        subject = {

            "Ticker":
                symbol,

            "Company":
                info.get(
                    "longName"
                ),

            "Market Cap":
                info.get(
                    "marketCap"
                ),

            "PE":
                info.get(
                    "trailingPE"
                ),

            "Forward PE":
                info.get(
                    "forwardPE"
                ),

            "Revenue Growth":
                info.get(
                    "revenueGrowth"
                ),

            "Profit Margin":
                info.get(
                    "profitMargins"
                ),

            "Operating Margin":
                info.get(
                    "operatingMargins"
                ),

            "ROE":
                info.get(
                    "returnOnEquity"
                ),

            "ROIC":
                info.get(
                    "returnOnInvestedCapital"
                ),
        }

        # --------------------------------
        # Relative Analysis
        # --------------------------------

        valuation_rank = None
        growth_rank = None
        profitability_rank = None

        if companies:

            # Lower PE is better

            pe_values = [
                c["PE"]
                for c in companies
                if c["PE"] is not None
            ]

            if subject["PE"] is not None:

                pe_values.append(
                    subject["PE"]
                )

                ordered = sorted(
                    pe_values
                )

                valuation_rank = (
                    ordered.index(
                        subject["PE"]
                    ) + 1
                )

            # Higher growth is better

            growth_values = [
                c["Revenue Growth"]
                for c in companies
                if c["Revenue Growth"] is not None
            ]

            if subject[
                "Revenue Growth"
            ] is not None:

                growth_values.append(
                    subject[
                        "Revenue Growth"
                    ]
                )

                ordered = sorted(
                    growth_values,
                    reverse=True,
                )

                growth_rank = (
                    ordered.index(
                        subject[
                            "Revenue Growth"
                        ]
                    ) + 1
                )

            # Higher profitability is better

            profit_values = [
                c["Profit Margin"]
                for c in companies
                if c["Profit Margin"] is not None
            ]

            if subject[
                "Profit Margin"
            ] is not None:

                profit_values.append(
                    subject[
                        "Profit Margin"
                    ]
                )

                ordered = sorted(
                    profit_values,
                    reverse=True,
                )

                profitability_rank = (
                    ordered.index(
                        subject[
                            "Profit Margin"
                        ]
                    ) + 1
                )

        # --------------------------------
        # Competitive Score
        # --------------------------------

        score = 50

        total_companies = (
            len(companies) + 1
        )

        if (
            growth_rank is not None
            and growth_rank
            <= max(
                1,
                total_companies // 2,
            )
        ):

            score += 15

        if (
            profitability_rank is not None
            and profitability_rank
            <= max(
                1,
                total_companies // 2,
            )
        ):

            score += 15

        if (
            valuation_rank is not None
            and valuation_rank
            <= max(
                1,
                total_companies // 2,
            )
        ):

            score += 10

        score = max(
            0,
            min(
                score,
                100,
            ),
        )

        # --------------------------------
        # Summary
        # --------------------------------

        if score >= 75:

            assessment = (
                "The company compares favourably "
                "with its selected peers."
            )

        elif score >= 60:

            assessment = (
                "The company appears competitive "
                "relative to its selected peers."
            )

        else:

            assessment = (
                "The company does not currently "
                "stand out strongly against its peers."
            )

        return {

            "Competitive Score":
                score,

            "Sector":
                sector,

            "Industry":
                industry,

            "Peers":
                companies,

            "Subject Company":
                subject,

            "Valuation Rank":
                valuation_rank,

            "Growth Rank":
                growth_rank,

            "Profitability Rank":
                profitability_rank,

            "Assessment":
                assessment,
        }