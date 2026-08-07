class Watchlist:

    def __init__(self, filename):

        self.filename = filename

    def load(self):

        with open(self.filename, "r") as file:

            return [

                line.strip()

                for line in file

                if line.strip()

            ]