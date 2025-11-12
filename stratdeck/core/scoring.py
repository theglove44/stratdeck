from .config import scoring_conf

C = scoring_conf()

def score_candidate(c):
    w = C["weights"]
    # defensives
    width = max(c.get("width", 1), 0.01)
    credit_ratio = float(c.get("credit", 0)) / width
    pop = float(c.get("pop", 0))
    ivr = float(c.get("ivr", 0))
    liquidity = 1.0 if c.get("liquidity") == "GOOD" else 0.0

    score = (
        credit_ratio * w.get("credit_ratio", 0) +
        pop * w.get("pop", 0) +
        ivr * w.get("ivr", 0) +
        liquidity * w.get("liquidity", 0)
    )
    # small boost for diversification when caller aggregates
    return round(score, 6)