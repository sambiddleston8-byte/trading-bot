class ScoringEngine:

    def overall_score(

        self,

        fundamental,

        technical,

    ):

        return round(

            (

                fundamental

                +

                technical

            )

            / 2,

            1,

        )