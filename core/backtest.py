import pandas as pd


def run_backtest(data):
    cash = 10000
    shares = 0

    for i in range(len(data)):
        price = data["Close"].iloc[i]

        if shares == 0:
            shares = cash / price
            cash = 0

        else:
            continue

    final_value = cash + (shares * data["Close"].iloc[-1])

    return final_value