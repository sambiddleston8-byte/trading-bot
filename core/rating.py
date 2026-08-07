def get_rating(score):

    if score >= 90:
        return "★★★★★  STRONG BUY"

    elif score >= 80:
        return "★★★★☆  BUY"

    elif score >= 65:
        return "★★★☆☆  HOLD"

    elif score >= 50:
        return "★★☆☆☆  WEAK"

    else:
        return "★☆☆☆☆  SELL"