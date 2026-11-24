"""Track post-alert goals and final outcomes without changing Telegram rules."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

FINISHED = {"ft", "full-time", "full time", "finished", "ended", "aet", "after extra time"}
REPORT = Path("football_alert_outcomes.json")


def score(match):
    try:
        home, away = int(match["home_score"]), int(match["away_score"])
        if min(home, away) < 0:
            return None
        return [home, away]
    except (KeyError, TypeError, ValueError):
        return None


def halftime_score(match):
    """Accept only an explicit BSD half-time score, never infer it from full-time."""
    raw = match.get("half_time_score")
    if isinstance(raw, (list, tuple)) and len(raw) == 2:
        values = raw
    elif isinstance(raw, dict):
        values = [raw.get("home", raw.get("home_score")), raw.get("away", raw.get("away_score"))]
    else:
        return None
    try:
        result = [int(v) for v in values]
    except (ValueError, TypeError):
        return None
    return result if min(result) >= 0 else None


def update_halftime(state, matches):
    """Resolve H1 outcomes only from a documented, explicit HT score."""
    by_id = {str(m["id"]): m for m in matches if isinstance(m, dict) and m.get("id") is not None}
    changed = False
    for item in state.setdefault("alert_outcomes", {}).values():
        if item.get("half") != 1 or item.get("halftime_outcome", "pending") != "pending":
            continue
        match = by_id.get(str(item["event_id"]))
        if not match:
            continue
        ht = halftime_score(match)
        if ht is None:
            continue
        initial = item["score_at_alert"]
        if any(ht[i] < initial[i] for i in (0, 1)):
            item["halftime_outcome"] = "unverified"
        else:
            side = 0 if item["favorite_side"] == "Home" else 1
            item["halftime_score"] = ht
            item["halftime_goals_after_alert"] = sum(ht) - sum(initial)
            item["halftime_favorite_goals_after_alert"] = ht[side] - initial[side]
            item["halftime_outcome"] = "verified"
        changed = True
    return changed


def register(state, event_id, half, match, side, odd, now):
    """Register only after Telegram confirms successful delivery."""
    records = state.setdefault("alert_outcomes", {})
    key = f"{event_id}:H{half}"
    if key in records:
        return
    initial = score(match)
    if initial is None:
        return
    records[key] = {
        "event_id": event_id, "half": half, "sent_utc": now.isoformat(),
        "home": str(match.get("home_team", "")), "away": str(match.get("away_team", "")),
        "league": str(match.get("league_name", "")),
        "favorite_side": side, "favorite_odds": odd,
        "minute": str(match.get("current_minute", "")),
        "score_at_alert": initial, "latest_score": initial[:],
        "final_score": None, "outcome": "pending",
        "halftime_outcome": "pending" if half == 1 else "not_applicable",
    }


def update(state, matches):
    records = state.setdefault("alert_outcomes", {})
    by_id = {str(m["id"]): m for m in matches if isinstance(m, dict) and m.get("id") is not None}
    changed = False
    for item in records.values():
        if item.get("outcome") != "pending":
            continue
        match = by_id.get(item["event_id"])
        if not match:
            continue
        current = score(match)
        if current is None:
            continue
        if item.get("latest_score") != current:
            item["latest_score"] = current
            changed = True
        if str(match.get("status", "")).strip().lower() in FINISHED:
            initial = item["score_at_alert"]
            # A score regression indicates unreliable data; leave unverified.
            if current[0] < initial[0] or current[1] < initial[1]:
                item["outcome"] = "unverified"
            else:
                side = 0 if item["favorite_side"] == "Home" else 1
                other = 1 - side
                item["final_score"] = current
                item["goals_after_alert"] = sum(current) - sum(initial)
                item["favorite_goals_after_alert"] = current[side] - initial[side]
                item["favorite_equalized"] = current[side] >= current[other]
                item["favorite_won"] = current[side] > current[other]
                item["outcome"] = "verified"
            changed = True
    return changed


def report(state):
    records = list(state.setdefault("alert_outcomes", {}).values())
    verified = [r for r in records if r.get("outcome") == "verified"]
    # Match-level: first alert chronologically wins, avoid counting same game twice.
    first = {}
    for r in sorted(verified, key=lambda r: r["sent_utc"]):
        first.setdefault(r["event_id"], r)
    matches = list(first.values())
    summary = {
        "alerts_recorded": len(records),
        "alerts_verified": len(verified),
        "alerts_pending": sum(r.get("outcome") == "pending" for r in records),
        "alerts_unverified": sum(r.get("outcome") == "unverified" for r in records),
        "unique_matches_verified": len(matches),
        "matches_with_any_goal_after_alert": sum(r["goals_after_alert"] > 0 for r in matches),
        "matches_with_favorite_goal_after_alert": sum(r["favorite_goals_after_alert"] > 0 for r in matches),
        "matches_favorite_equalized": sum(r["favorite_equalized"] for r in matches),
        "matches_favorite_won": sum(r["favorite_won"] for r in matches),
    }
    h1 = [r for r in records if r.get("half") == 1]
    h1_verified = [r for r in h1 if r.get("halftime_outcome") == "verified"]
    summary.update({
        "h1_alerts": len(h1),
        "h1_halftime_verified": len(h1_verified),
        "h1_halftime_pending": sum(r.get("halftime_outcome", "pending") == "pending" for r in h1),
        "h1_halftime_unverified": sum(r.get("halftime_outcome") == "unverified" for r in h1),
        "h1_with_goal_before_halftime": sum(r["halftime_goals_after_alert"] > 0 for r in h1_verified),
        "h1_with_favorite_goal_before_halftime": sum(r["halftime_favorite_goals_after_alert"] > 0 for r in h1_verified),
    })
    REPORT.write_text(json.dumps({"summary": summary, "alerts": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("FOOTBALL_OUTCOMES:", json.dumps(summary, ensure_ascii=False))
