import yfinance as yf


class ManagementAnalyser:

    def analyse(self, symbol):

        info = yf.Ticker(symbol).info

        score = 50

        strengths = []
        weaknesses = []

        employees = info.get("fullTimeEmployees")
        insider = info.get("heldPercentInsiders")
        institutions = info.get("heldPercentInstitutions")

        if insider:

            if insider > 0.10:
                score += 15
                strengths.append("High insider ownership")

            elif insider < 0.02:
                score -= 10
                weaknesses.append("Very low insider ownership")

        if institutions:

            if institutions > 0.60:
                score += 10
                strengths.append("Strong institutional ownership")

        if employees:

            if employees > 10000:
                score += 5

        score = max(0, min(score, 100))

        if score >= 80:

            summary = (
                "Management appears strongly aligned with shareholders."
            )

        elif score >= 60:

            summary = (
                "Management quality appears satisfactory."
            )

        else:

            summary = (
                "Management quality raises some concerns."
            )

        return {

            "Management Score": score,

            "Confidence": 75,

            "Strengths": strengths,

            "Weaknesses": weaknesses,

            "Summary": summary,

            "Insider Ownership": insider,

            "Institutional Ownership": institutions,

            "Employees": employees,

        }