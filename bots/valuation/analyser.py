import yfinance as yf


class ValuationAnalyser:

    def analyse(self, symbol):

        stock = yf.Ticker(symbol)
        info = stock.info

        pe = info.get("trailingPE")
        forward_pe = info.get("forwardPE")
        peg = info.get("pegRatio")
        ps = info.get("priceToSalesTrailing12Months")
        pb = info.get("priceToBook")

        score = 50
        strengths = []
        weaknesses = []

        # P/E

        if pe:

            if pe < 20:
                score += 10
                strengths.append("Low P/E ratio")

            elif pe > 35:
                score -= 10
                weaknesses.append("High P/E ratio")

        # Forward P/E

        if forward_pe:

            if forward_pe < 20:
                score += 10
                strengths.append("Attractive forward earnings valuation")

            elif forward_pe > 35:
                score -= 10
                weaknesses.append("Expensive forward valuation")

        # PEG

        if peg:

            if peg < 1.5:
                score += 10
                strengths.append("PEG indicates attractive growth valuation")

            elif peg > 3:
                score -= 10
                weaknesses.append("Growth appears expensive")

        # Price to Sales

        if ps:

            if ps < 5:
                score += 10
                strengths.append("Reasonable Price/Sales ratio")

            elif ps > 15:
                score -= 10
                weaknesses.append("Very expensive relative to revenue")

        # Price to Book

        if pb:

            if pb < 3:
                score += 10
                strengths.append("Reasonable Price/Book ratio")

            elif pb > 10:
                score -= 10
                weaknesses.append("High Price/Book multiple")

        score = max(0, min(score, 100))

        confidence = 80

        if score >= 80:

            summary = (
                "Valuation appears attractive compared with current fundamentals."
            )

        elif score >= 60:

            summary = (
                "Valuation appears broadly reasonable."
            )

        else:

            summary = (
                "Shares appear expensive based on current valuation metrics."
            )

        return {

            "PE": pe,

            "Forward PE": forward_pe,

            "PEG": peg,

            "Price to Sales": ps,

            "Price to Book": pb,

            "Valuation Score": score,

            "Confidence": confidence,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

        }