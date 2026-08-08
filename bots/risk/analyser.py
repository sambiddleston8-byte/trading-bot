from core.company_context import CompanyContext


class RiskAnalyser:

    def analyse(self, context: CompanyContext):

        info = context.info
        balance_sheet = context.balance_sheet
        history = context.history

        score = 100

        # --------------------------------
        # Beta / Market Volatility
        # --------------------------------

        beta = info.get("beta")

        if beta is not None:

            if beta > 2.0:
                score -= 25

            elif beta > 1.5:
                score -= 15

            elif beta > 1.0:
                score -= 5

        # --------------------------------
        # Debt Risk
        # --------------------------------

        if balance_sheet is not None:

            debt = None
            cash = None

            if "Total Debt" in balance_sheet.index:
                debt = balance_sheet.loc[
                    "Total Debt"
                ].iloc[0]

            if "Cash And Cash Equivalents" in balance_sheet.index:
                cash = balance_sheet.loc[
                    "Cash And Cash Equivalents"
                ].iloc[0]

            if debt is not None and cash is not None:

                if debt > cash * 2:
                    score -= 25

                elif debt > cash:
                    score -= 15

                elif cash > debt:
                    score += 0

        # --------------------------------
        # Price Volatility
        # --------------------------------

        if history is not None and not history.empty:

            close = history["Close"].dropna()

            if len(close) >= 30:

                returns = close.pct_change().dropna()

                volatility = returns.std() * (
                    252 ** 0.5
                )

                if volatility > 0.60:
                    score -= 25

                elif volatility > 0.40:
                    score -= 15

                elif volatility > 0.25:
                    score -= 5

        # --------------------------------
        # Risk Score
        # --------------------------------

        score = max(
            0,
            min(score, 100)
        )

        return {

            "Risk Score": round(
                score,
                1,
            ),

            "Beta": beta,

        }