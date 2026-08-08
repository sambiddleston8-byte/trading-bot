import math


class RiskEngine:

    def __init__(
        self,
        max_position_weight=0.10,
        max_sector_weight=0.30,
        max_portfolio_volatility=0.25,
        minimum_cash_weight=0.05,
        maximum_drawdown_limit=0.20,
        high_risk_threshold=40,
    ):

        self.max_position_weight = (
            max_position_weight
        )

        self.max_sector_weight = (
            max_sector_weight
        )

        self.max_portfolio_volatility = (
            max_portfolio_volatility
        )

        self.minimum_cash_weight = (
            minimum_cash_weight
        )

        self.maximum_drawdown_limit = (
            maximum_drawdown_limit
        )

        self.high_risk_threshold = (
            high_risk_threshold
        )

    # ============================================================
    # BASIC HELPERS
    # ============================================================

    def safe_float(
        self,
        value,
        default=0,
    ):

        try:

            value = float(
                value
            )

            if math.isnan(value):
                return default

            if math.isinf(value):
                return default

            return value

        except (
            TypeError,
            ValueError,
        ):

            return default

    # ============================================================
    # POSITION CHECK
    # ============================================================

    def check_position(
        self,
        position,
    ):

        ticker = position.get(
            "Ticker",
            "UNKNOWN",
        )

        weight = (
            self.safe_float(
                position.get(
                    "Weight %",
                    0,
                )
            )
            / 100
        )

        flags = []

        if weight > (
            self.max_position_weight
        ):

            flags.append(
                "Position exceeds maximum weight."
            )

        return {

            "Ticker":
                ticker,

            "Weight":
                weight,

            "Flags":
                flags,

            "Pass":
                len(flags) == 0,

        }

    # ============================================================
    # SECTOR EXPOSURE
    # ============================================================

    def sector_exposure(
        self,
        holdings,
    ):

        sectors = {}

        for position in holdings:

            sector = position.get(
                "Sector",
                "Unknown",
            )

            weight = (
                self.safe_float(
                    position.get(
                        "Weight %",
                        0,
                    )
                )
                / 100
            )

            sectors[sector] = (
                sectors.get(
                    sector,
                    0,
                )
                + weight
            )

        return sectors

    # ============================================================
    # SECTOR CHECK
    # ============================================================

    def check_sectors(
        self,
        holdings,
    ):

        sectors = (
            self.sector_exposure(
                holdings
            )
        )

        flags = []

        for sector, weight in (
            sectors.items()
        ):

            if weight > (
                self.max_sector_weight
            ):

                flags.append({

                    "Sector":
                        sector,

                    "Weight":
                        round(
                            weight * 100,
                            2,
                        ),

                    "Limit":
                        round(
                            self.max_sector_weight
                            * 100,
                            2,
                        ),

                })

        return {

            "Sector Exposure":
                {
                    sector:
                        round(
                            weight * 100,
                            2,
                        )

                    for sector, weight
                    in sectors.items()
                },

            "Flags":
                flags,

            "Pass":
                len(flags) == 0,

        }

    # ============================================================
    # CASH CHECK
    # ============================================================

    def check_cash(
        self,
        portfolio,
    ):

        cash = (
            self.safe_float(
                portfolio.get(
                    "Cash Weight %",
                    0,
                )
            )
            / 100
        )

        flag = (
            cash
            < self.minimum_cash_weight
        )

        return {

            "Cash Weight":
                round(
                    cash * 100,
                    2,
                ),

            "Minimum Required":
                round(
                    self.minimum_cash_weight
                    * 100,
                    2,
                ),

            "Pass":
                not flag,

            "Flag":
                (
                    "Cash reserve below minimum."
                    if flag
                    else None
                ),

        }

    # ============================================================
    # RISK SCORE CHECK
    # ============================================================

    def check_risk_scores(
        self,
        holdings,
    ):

        flags = []

        for position in holdings:

            risk = self.safe_float(
                position.get(
                    "Risk Score",
                    50,
                ),
                default=50,
            )

            if risk < (
                self.high_risk_threshold
            ):

                flags.append({

                    "Ticker":
                        position.get(
                            "Ticker"
                        ),

                    "Risk Score":
                        risk,

                    "Weight %":
                        position.get(
                            "Weight %",
                            0,
                        ),

                })

        return {

            "Flags":
                flags,

            "Pass":
                len(flags) == 0,

        }

    # ============================================================
    # CONCENTRATION
    # ============================================================

    def concentration_score(
        self,
        holdings,
    ):

        if not holdings:
            return 0

        weights = [

            self.safe_float(
                position.get(
                    "Weight %",
                    0,
                )
            )
            / 100

            for position in holdings

        ]

        total = sum(
            weights
        )

        if total <= 0:
            return 0

        # Herfindahl-style concentration
        # measure.

        hhi = sum(
            weight ** 2
            for weight in weights
        )

        effective_positions = (
            1 / hhi
            if hhi > 0
            else 0
        )

        return {

            "HHI":
                round(
                    hhi,
                    4,
                ),

            "Effective Positions":
                round(
                    effective_positions,
                    2,
                ),

        }

    # ============================================================
    # PORTFOLIO VOLATILITY
    # ============================================================

    def portfolio_volatility(
        self,
        holdings,
        volatility_data=None,
    ):

        if not holdings:
            return 0

        if not volatility_data:
            return None

        weighted_variance = 0

        for position in holdings:

            ticker = position.get(
                "Ticker"
            )

            weight = (
                self.safe_float(
                    position.get(
                        "Weight %",
                        0,
                    )
                )
                / 100
            )

            volatility = (
                self.safe_float(
                    volatility_data.get(
                        ticker,
                        0,
                    )
                )
            )

            weighted_variance += (
                weight ** 2
                * volatility ** 2
            )

        return math.sqrt(
            weighted_variance
        )

    # ============================================================
    # DRAWDOWN CHECK
    # ============================================================

    def check_drawdown(
        self,
        current_drawdown,
    ):

        drawdown = abs(
            self.safe_float(
                current_drawdown,
                0,
            )
        )

        # Accept either decimal or percentage.

        if drawdown > 1:

            drawdown /= 100

        breached = (
            drawdown
            > self.maximum_drawdown_limit
        )

        return {

            "Current Drawdown %":
                round(
                    drawdown * 100,
                    2,
                ),

            "Maximum Allowed %":
                round(
                    self.maximum_drawdown_limit
                    * 100,
                    2,
                ),

            "Pass":
                not breached,

            "Flag":
                (
                    "Maximum portfolio drawdown breached."
                    if breached
                    else None
                ),

        }

    # ============================================================
    # FULL PORTFOLIO REVIEW
    # ============================================================

    def review(
        self,
        portfolio,
        volatility_data=None,
        current_drawdown=0,
    ):

        holdings = portfolio.get(
            "Holdings",
            [],
        )

        position_checks = []

        for position in holdings:

            position_checks.append(
                self.check_position(
                    position
                )
            )

        sector_check = (
            self.check_sectors(
                holdings
            )
        )

        cash_check = (
            self.check_cash(
                portfolio
            )
        )

        risk_check = (
            self.check_risk_scores(
                holdings
            )
        )

        concentration = (
            self.concentration_score(
                holdings
            )
        )

        volatility = (
            self.portfolio_volatility(
                holdings,
                volatility_data,
            )
        )

        volatility_flag = False

        if volatility is not None:

            volatility_flag = (
                volatility
                > self.max_portfolio_volatility
            )

        drawdown_check = (
            self.check_drawdown(
                current_drawdown
            )
        )

        all_position_pass = all(
            check["Pass"]
            for check in position_checks
        )

        overall_pass = (
            all_position_pass
            and sector_check["Pass"]
            and cash_check["Pass"]
            and risk_check["Pass"]
            and drawdown_check["Pass"]
            and not volatility_flag
        )

        flags = []

        for check in position_checks:

            flags.extend(
                check["Flags"]
            )

        flags.extend(
            sector_check["Flags"]
        )

        if not cash_check["Pass"]:

            flags.append(
                cash_check["Flag"]
            )

        for flag in (
            risk_check["Flags"]
        ):

            flags.append(
                (
                    f"{flag['Ticker']} has "
                    f"elevated risk."
                )
            )

        if volatility_flag:

            flags.append(
                "Portfolio volatility exceeds maximum."
            )

        if not drawdown_check["Pass"]:

            flags.append(
                drawdown_check["Flag"]
            )

        return {

            "Pass":
                overall_pass,

            "Position Checks":
                position_checks,

            "Sector Check":
                sector_check,

            "Cash Check":
                cash_check,

            "Risk Check":
                risk_check,

            "Concentration":
                concentration,

            "Portfolio Volatility":
                (
                    round(
                        volatility * 100,
                        2,
                    )
                    if volatility is not None
                    else None
                ),

            "Portfolio Volatility Limit":
                round(
                    self.max_portfolio_volatility
                    * 100,
                    2,
                ),

            "Drawdown Check":
                drawdown_check,

            "Flags":
                flags,

        }

    # ============================================================
    # PRINT REPORT
    # ============================================================

    def print_report(
        self,
        report,
    ):

        print()
        print("=" * 80)
        print("PORTFOLIO RISK REVIEW")
        print("=" * 80)

        print()

        print(
            "STATUS: "
            + (
                "PASS"
                if report["Pass"]
                else "REVIEW REQUIRED"
            )
        )

        print()

        print(
            f"Effective Positions: "
            f"{report['Concentration'].get('Effective Positions', 0)}"
        )

        print(
            f"Cash: "
            f"{report['Cash Check']['Cash Weight']}%"
        )

        volatility = (
            report[
                "Portfolio Volatility"
            ]
        )

        if volatility is not None:

            print(
                f"Portfolio Volatility: "
                f"{volatility}%"
            )

        print()

        if report["Flags"]:

            print(
                "RISK FLAGS"
            )

            for flag in (
                report["Flags"]
            ):

                print(
                    f"  - {flag}"
                )

        else:

            print(
                "No risk flags."
            )