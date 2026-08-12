import json
import os
from datetime import datetime


class PaperExecutionEngine:

    def __init__(
        self,
        initial_capital=100000,
        transaction_cost=0.001,
    ):

        self.initial_capital = float(
            initial_capital
        )

        self.cash = float(
            initial_capital
        )

        self.transaction_cost = float(
            transaction_cost
        )

        self.positions = {}

        self.trade_history = []

    # ============================================================
    # PRICE
    # ============================================================

    def get_price(
        self,
        prices,
        ticker,
    ):

        if ticker not in prices:
            return None

        data = prices[ticker]

        if data is None:
            return None

        try:

            if hasattr(data, "iloc"):

                return float(
                    data["Close"].iloc[-1]
                )

            return float(data)

        except Exception:

            return None

    # ============================================================
    # BUY
    # ============================================================

    def buy(
        self,
        ticker,
        quantity,
        price,
    ):

        ticker = ticker.upper()

        quantity = float(quantity)
        price = float(price)

        if quantity <= 0:
            return False

        gross_value = quantity * price

        fees = (
            gross_value
            * self.transaction_cost
        )

        total_cost = (
            gross_value
            + fees
        )

        if total_cost > self.cash:
            return False

        existing = self.positions.get(
            ticker,
            {
                "Quantity": 0,
                "Average Price": 0,
            },
        )

        old_quantity = float(
            existing["Quantity"]
        )

        old_average = float(
            existing["Average Price"]
        )

        new_quantity = (
            old_quantity
            + quantity
        )

        total_position_cost = (
            old_quantity
            * old_average
            + quantity
            * price
        )

        if new_quantity > 0:

            new_average = (
                total_position_cost
                / new_quantity
            )

        else:

            new_average = 0

        self.positions[ticker] = {

            "Quantity":
                new_quantity,

            "Average Price":
                new_average,

        }

        self.cash -= total_cost

        self.record_trade(
            action="BUY",
            ticker=ticker,
            quantity=quantity,
            price=price,
            fees=fees,
        )

        return True

    # ============================================================
    # SELL
    # ============================================================

    def sell(
        self,
        ticker,
        quantity,
        price,
    ):

        ticker = ticker.upper()

        quantity = float(quantity)
        price = float(price)

        position = self.positions.get(
            ticker
        )

        if position is None:
            return False

        current_quantity = float(
            position["Quantity"]
        )

        if quantity <= 0:
            return False

        if quantity > current_quantity:
            return False

        gross_value = (
            quantity
            * price
        )

        fees = (
            gross_value
            * self.transaction_cost
        )

        net_value = (
            gross_value
            - fees
        )

        average_price = float(
            position["Average Price"]
        )

        realised_pnl = (
            (
                price
                - average_price
            )
            * quantity
            - fees
        )

        remaining = (
            current_quantity
            - quantity
        )

        if remaining <= 0:

            del self.positions[ticker]

        else:

            self.positions[ticker] = {

                "Quantity":
                    remaining,

                "Average Price":
                    average_price,

            }

        self.cash += net_value

        self.record_trade(
            action="SELL",
            ticker=ticker,
            quantity=quantity,
            price=price,
            fees=fees,
            realised_pnl=realised_pnl,
        )

        return True

    # ============================================================
    # RECORD TRADE
    # ============================================================

    def record_trade(
        self,
        action,
        ticker,
        quantity,
        price,
        fees,
        realised_pnl=0,
    ):

        self.trade_history.append({

            "Timestamp":
                datetime.now().isoformat(),

            "Action":
                action,

            "Ticker":
                ticker,

            "Quantity":
                quantity,

            "Price":
                price,

            "Gross Value":
                quantity * price,

            "Fees":
                fees,

            "Realised PnL":
                realised_pnl,

        })

    # ============================================================
    # PORTFOLIO VALUE
    # ============================================================

    def portfolio_value(
        self,
        prices,
    ):

        value = self.cash

        for ticker, position in (
            self.positions.items()
        ):

            price = self.get_price(
                prices,
                ticker,
            )

            if price is None:

                raise ValueError(
                    f"A current price is required to value {ticker}."
                )

            value += (
                position["Quantity"]
                * price
            )

        return value

    # ============================================================
    # POSITION VALUES
    # ============================================================

    def position_values(
        self,
        prices,
    ):

        values = {}

        for ticker, position in (
            self.positions.items()
        ):

            price = self.get_price(
                prices,
                ticker,
            )

            if price is None:

                raise ValueError(
                    f"A current price is required to value {ticker}."
                )

            values[ticker] = (
                position["Quantity"]
                * price
            )

        return values

    # ============================================================
    # UNREALISED P&L
    # ============================================================

    def unrealised_pnl(
        self,
        prices,
    ):

        total = 0

        for ticker, position in (
            self.positions.items()
        ):

            price = self.get_price(
                prices,
                ticker,
            )

            if price is None:
                continue

            total += (
                (
                    price
                    - position[
                        "Average Price"
                    ]
                )
                * position[
                    "Quantity"
                ]
            )

        return total

    # ============================================================
    # REALISED P&L
    # ============================================================

    def realised_pnl(
        self,
    ):

        return sum(
            trade.get(
                "Realised PnL",
                0,
            )
            for trade
            in self.trade_history
        )

    # ============================================================
    # SUMMARY
    # ============================================================

    def summary(
        self,
        prices,
    ):

        value = self.portfolio_value(
            prices
        )

        total_return = (
            (
                value
                / self.initial_capital
            )
            - 1
        )

        return {

            "Initial Capital":
                round(
                    self.initial_capital,
                    2,
                ),

            "Cash":
                round(
                    self.cash,
                    2,
                ),

            "Portfolio Value":
                round(
                    value,
                    2,
                ),

            "Total Return %":
                round(
                    total_return
                    * 100,
                    2,
                ),

            "Realised PnL":
                round(
                    self.realised_pnl(),
                    2,
                ),

            "Unrealised PnL":
                round(
                    self.unrealised_pnl(
                        prices
                    ),
                    2,
                ),

            "Positions":
                len(
                    self.positions
                ),

            "Trades":
                len(
                    self.trade_history
                ),

        }

    # ============================================================
    # SAVE STATE
    # ============================================================

    def save(
        self,
        path="data/paper_portfolio.json",
    ):

        directory = os.path.dirname(
            path
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

        output = {

            "Initial Capital":
                self.initial_capital,

            "Cash":
                self.cash,

            "Positions":
                self.positions,

            "Trade History":
                self.trade_history,

        }

        with open(
            path,
            "w",
        ) as file:

            json.dump(
                output,
                file,
                indent=2,
                default=str,
            )

        return path

    # ============================================================
    # LOAD STATE
    # ============================================================

    def load(
        self,
        path="data/paper_portfolio.json",
    ):

        if not os.path.exists(path):
            return False

        with open(
            path,
            "r",
        ) as file:

            data = json.load(
                file
            )

        self.initial_capital = float(
            data.get(
                "Initial Capital",
                self.initial_capital,
            )
        )

        self.cash = float(
            data.get(
                "Cash",
                self.initial_capital,
            )
        )

        self.positions = data.get(
            "Positions",
            {},
        )

        self.trade_history = data.get(
            "Trade History",
            [],
        )

        return True

    # ============================================================
    # PRINT SUMMARY
    # ============================================================

    def print_summary(
        self,
        prices,
    ):

        summary = self.summary(
            prices
        )

        print()
        print("=" * 70)
        print("PAPER PORTFOLIO")
        print("=" * 70)

        print()

        print(
            f"Initial Capital: "
            f"${summary['Initial Capital']:,.2f}"
        )

        print(
            f"Cash: "
            f"${summary['Cash']:,.2f}"
        )

        print(
            f"Portfolio Value: "
            f"${summary['Portfolio Value']:,.2f}"
        )

        print(
            f"Return: "
            f"{summary['Total Return %']:.2f}%"
        )

        print(
            f"Realised P&L: "
            f"${summary['Realised PnL']:,.2f}"
        )

        print(
            f"Unrealised P&L: "
            f"${summary['Unrealised PnL']:,.2f}"
        )

        print(
            f"Positions: "
            f"{summary['Positions']}"
        )

        print(
            f"Trades: "
            f"{summary['Trades']}"
        )

        print()

        if self.positions:

            print("POSITIONS")
            print()

            values = (
                self.position_values(
                    prices
                )
            )

            for ticker, position in (
                self.positions.items()
            ):

                value = values.get(
                    ticker,
                    0,
                )

                print(
                    f"{ticker:<6} "
                    f"{position['Quantity']:.4f} shares "
                    f"${value:,.2f}"
                )

        else:

            print(
                "No open positions."
            )

    # ============================================================
    # INTERNAL TEST
    # ============================================================

    def test_order(
        self,
    ):

        print()
        print(
            "PAPER EXECUTION ENGINE TEST"
        )
        print()

        buy_success = self.buy(
            "TEST",
            10,
            100,
        )

        print(
            f"BUY TEST: "
            f"{'PASS' if buy_success else 'FAIL'}"
        )

        sell_success = self.sell(
            "TEST",
            5,
            110,
        )

        print(
            f"SELL TEST: "
            f"{'PASS' if sell_success else 'FAIL'}"
        )

        summary = self.summary(
            {
                "TEST": 110,
            }
        )

        print(
            f"Portfolio Value: "
            f"${summary['Portfolio Value']:,.2f}"
        )

        print(
            f"Realised P&L: "
            f"${summary['Realised PnL']:,.2f}"
        )


if __name__ == "__main__":

    engine = PaperExecutionEngine()

    engine.test_order()
