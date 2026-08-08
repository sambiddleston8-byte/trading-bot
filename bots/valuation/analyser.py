from core.company_context import CompanyContext


class ValuationAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        pe = info.get("trailingPE")
        forward_pe = info.get("forwardPE")
        peg = info.get("pegRatio")
        price_to_sales = info.get(
            "priceToSalesTrailing12Months"
        )
        price_to_book = info.get("priceToBook")

        score = 50

        strengths = []
        weaknesses = []

        # --------------------------------
        # P/E
        # --------------------------------

        if pe is not None:

            if pe < 20:
                score += 10
                strengths.append(
                    "Low P/E ratio"
                )

            elif pe > 35:
                score -= 10
                weaknesses.append(
                    "High P/E ratio"
                )

        # --------------------------------
        # Forward P/E
        # --------------------------------

        if forward_pe is not None:

            if forward_pe < 20:
                score += 10
                strengths.append(
                    "Attractive forward earnings valuation"
                )

            elif forward_pe > 35:
                score -= 10
                weaknesses.append(
                    "Expensive forward valuation"
                )

        # --------------------------------
        # PEG
        # --------------------------------

        if peg is not None:

            if peg < 1.5:
                score += 10
                strengths.append(
                    "PEG indicates attractive growth valuation"
                )

            elif peg > 3:
                score -= 10
                weaknesses.append(
                    "Growth appears expensive"
                )

        # --------------------------------
        # Price / Sales
        # --------------------------------

        if price_to_sales is not None:

            if price_to_sales < 5:
                score += 10
                strengths.append(
                    "Reasonable Price/Sales ratio"
                )

            elif price_to_sales > 15:
                score -= 10
                weaknesses.append(
                    "Very expensive relative to revenue"
                )

        # --------------------------------
        # Price / Book
        # --------------------------------

        if price_to_book is not None:

            if price_to_book < 3:
                score += 10
                strengths.append(
                    "Reasonable Price/Book ratio"
                )

            elif price_to_book > 10:
                score -= 10
                weaknesses.append(
                    "High Price/Book multiple"
                )

        score = max(
            0,
            min(score, 100)
        )

        # --------------------------------
        # Summary
        # --------------------------------

        if score >= 80:

            summary = (
                "Shares appear attractively valued "
                "relative to current fundamentals."
            )

        elif score >= 60:

            summary = (
                "Valuation appears broadly reasonable "
                "with a balanced mix of attractive and "
                "expensive metrics."
            )

        else:

            summary = (
                "Shares appear expensive based on "
                "current valuation metrics."
            )

        return {

            "PE": pe,

            "Forward PE": forward_pe,

            "PEG": peg,

            "Price to Sales": price_to_sales,

            "Price to Book": price_to_book,

            "Valuation Score": score,

            "Confidence": 80,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

        }