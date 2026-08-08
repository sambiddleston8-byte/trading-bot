import yfinance as yf


class BenchmarkEngine:

    def __init__(self, benchmark="^GSPC"):

        self.benchmark = benchmark

    def get_return(
        self,
        start_date,
        end_date=None,
    ):

        try:

            data = yf.Ticker(
                self.benchmark
            ).history(
                start=start_date,
                end=end_date,
            )

            if data.empty:
                return None

            start_price = float(
                data["Close"].iloc[0]
            )

            end_price = float(
                data["Close"].iloc[-1]
            )

            if start_price <= 0:
                return None

            return round(
                (
                    end_price
                    / start_price
                    - 1
                ) * 100,
                2,
            )

        except Exception:

            return None

    def get_relative_return(
        self,
        stock_return,
        benchmark_return,
    ):

        if (
            stock_return is None
            or benchmark_return is None
        ):

            return None

        return round(
            stock_return
            - benchmark_return,
            2,
        )