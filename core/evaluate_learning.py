from core.learning_engine import LearningEngine


def main():

    engine = LearningEngine()

    result = engine.evaluate_predictions()

    print()
    print("================================")
    print("LEARNING EVALUATION")
    print("================================")
    print()

    print(
        "Predictions evaluated:",
        result["Evaluated"],
    )

    print(
        "Total predictions:",
        result["Total Predictions"],
    )

    print()

    performance = engine.performance()

    print(
        "Overall Performance:"
    )

    print(
        performance
    )

    print()

    specialist = (
        engine.specialist_performance()
    )

    print(
        "Specialist Performance:"
    )

    if not specialist:

        print(
            "No evaluated specialist predictions yet."
        )

    else:

        for analyst, data in specialist.items():

            print(
                analyst,
                "->",
                data,
            )


if __name__ == "__main__":

    main()