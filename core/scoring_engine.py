class ScoringEngine:

    def score_growth(self, value):

        if value is None:
            return 0

        if value >= 0.20:
            return 100
        elif value >= 0.10:
            return 80
        elif value >= 0.05:
            return 60
        elif value >= 0:
            return 40
        else:
            return 0

    def score_margin(self, value):

        if value is None:
            return 0

        if value >= 0.50:
            return 100
        elif value >= 0.30:
            return 80
        elif value >= 0.20:
            return 60
        elif value >= 0.10:
            return 40
        else:
            return 0

    def score_return(self, value):

        if value is None:
            return 0

        if value >= 0.30:
            return 100
        elif value >= 0.20:
            return 80
        elif value >= 0.15:
            return 60
        elif value >= 0.10:
            return 40
        else:
            return 0

    def score_pe(self, value):

        if value is None:
            return 50

        if value <= 15:
            return 100
        elif value <= 20:
            return 80
        elif value <= 30:
            return 60
        elif value <= 40:
            return 40
        else:
            return 20

    def score_ratio(self, value):

        if value is None:
            return 0

        if value >= 2:
            return 100
        elif value >= 1:
            return 80
        elif value >= 0.5:
            return 60
        elif value >= 0.25:
            return 40
        else:
            return 0