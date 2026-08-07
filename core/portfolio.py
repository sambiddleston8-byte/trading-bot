class Portfolio:
    def __init__(self, cash=10000):
        self.cash = cash
        self.positions = {}

    def buy(self, symbol, price):
        if symbol not in self.positions:
            quantity = self.cash / price
            self.positions[symbol] = quantity
            self.cash = 0

    def sell(self, symbol, price):
        if symbol in self.positions:
            self.cash = self.positions[symbol] * price
            del self.positions[symbol]