"""Experimental scoring only; no API or Telegram calls."""
import math


def evaluate(before, after, elapsed, score, red_cards):
    if not isinstance(elapsed, (int, float)) or not 5 <= elapsed <= 15:
        return None
    if len(score) != 2 or abs(score[0] - score[1]) > 1 or tuple(red_cards) != (0, 0):
        return None
    keys = ("expectedGoals", "shotsOnGoal", "totalShots")
    deltas = []
    for key in keys:
        a, b = before.get(key), after.get(key)
        if not isinstance(a, (list, tuple)) or not isinstance(b, (list, tuple)) or len(a) != 2 or len(b) != 2:
            return None
        if not all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in (*a, *b)):
            return None
        if any(y < x for x, y in zip(a, b)):
            return None
        deltas.append([(y-x)*10/elapsed for x,y in zip(a,b)])
    ratings = []
    for i in range(2):
        xg, target, shots = (d[i] for d in deltas)
        dominance = sum(d[i]/sum(d) if sum(d) else 0 for d in deltas)/3
        rating = 40*min(xg/.6,1)+25*min(target/3,1)+20*dominance+15*min(shots/5,1)
        if xg >= .20 and target >= 1 and rating >= 65:
            ratings.append((rating, i))
    if not ratings:
        return None
    rating, side = max(ratings)
    return {"side": ("home", "away")[side], "rating": round(rating, 2), "window": elapsed}
