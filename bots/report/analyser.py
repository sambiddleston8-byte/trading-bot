class ReportAnalyser:

    def build(self, analysis):

        report = []

        report.append(f"Investment Thesis for {analysis.ticker}")
        report.append("")

        if analysis.summary:
            report.append(analysis.summary)
            report.append("")

        important = []

        for headline in analysis.catalysts:

            lower = headline.lower()

            # Ignore headlines that don't mention the company
            if analysis.ticker.lower() not in lower:
                continue

            # Only keep genuinely important catalysts
            if any(
                word in lower
                for word in [
                    "earnings",
                    "beat",
                    "miss",
                    "approval",
                    "contract",
                    "partnership",
                    "launch",
                    "upgrade",
                    "downgrade",
                    "lawsuit",
                ]
            ):
                important.append(headline)

        if important:

            report.append("Key Catalysts:")

            for catalyst in important[:3]:
                report.append(f"- {catalyst}")

            report.append("")

        return "\n".join(report)