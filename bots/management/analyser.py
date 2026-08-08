from core.company_context import CompanyContext


class ManagementAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info

        score = 50

        strengths = []
        weaknesses = []

        # --------------------------------
        # Insider Ownership
        # --------------------------------

        insider = info.get("heldPercentInsiders")

        if insider is not None:

            if insider >= 0.10:
                score += 20
                strengths.append(
                    "High insider ownership"
                )

            elif insider >= 0.05:
                score += 10
                strengths.append(
                    "Meaningful insider ownership"
                )

            elif insider < 0.02:
                score -= 10
                weaknesses.append(
                    "Very low insider ownership"
                )

        # --------------------------------
        # Institutional Ownership
        # --------------------------------

        institutions = info.get(
            "heldPercentInstitutions"
        )

        if institutions is not None:

            if institutions >= 0.60:
                score += 10
                strengths.append(
                    "Strong institutional ownership"
                )

            elif institutions < 0.30:
                score -= 5
                weaknesses.append(
                    "Low institutional ownership"
                )

        # --------------------------------
        # Company Size / Workforce
        # --------------------------------

        employees = info.get(
            "fullTimeEmployees"
        )

        if employees is not None and employees > 10000:

            score += 5

        # --------------------------------
        # Management Score
        # --------------------------------

        score = max(
            0,
            min(score, 100)
        )

        if score >= 80:

            summary = (
                "Management appears strongly aligned "
                "with shareholders."
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