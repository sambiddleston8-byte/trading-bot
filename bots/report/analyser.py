class ReportAnalyser:

    def build(self, analysis):

        report = []

        report.append("=" * 60)
        report.append(f"Investment Report: {analysis.ticker}")
        report.append("=" * 60)
        report.append("")

        report.append(f"Overall Rating : {analysis.rating}")
        report.append(f"Overall Score  : {analysis.overall}")
        report.append("")

        report.append("SECTION 1 - INVESTMENT THESIS")
        report.append("----------------------------------------")
        report.append(analysis.summary)
        report.append("")

        report.append("SECTION 2 - SCORE BREAKDOWN")
        report.append("----------------------------------------")
        report.append(f"Business Quality : {analysis.business_quality}")
        report.append(f"Valuation        : {analysis.valuation}")
        report.append(f"Technical        : {analysis.technical}")
        report.append(f"Risk             : {analysis.risk}")
        report.append(f"News             : {analysis.news}")
        report.append(f"Catalysts        : {analysis.catalyst}")
        report.append("")

        report.append("SECTION 3 - KEY CATALYSTS")
        report.append("----------------------------------------")

        if analysis.catalysts:
            for catalyst in analysis.catalysts[:5]:
                report.append(f"• {catalyst}")
        else:
            report.append("No significant catalysts identified.")

        report.append("")
        report.append("=" * 60)

        return "\n".join(report)