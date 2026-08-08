class Universe:

    def __init__(self, filename):

        self.filename = filename

    def load(self):

        with open(self.filename) as file:

            return [
                line.strip()
                for line in file
                if line.strip()
            ]