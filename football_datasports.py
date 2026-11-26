import os
from datetime import datetime
import math
import requests


def league_id(game):
    league = game.get("league")
    return str(league.get("id", "")) if isinstance(league, dict) else ""


def extract_stats(data):
    result = {}
    if not isinstance(data, dict):
        return result
    for p in data.get("periods", []):
        if p.get("period") != "all":
            continue
        for group in p.get("groups", []):
            for item in group.get("items", []):
                values = [(item.get(side) or {}).get("value") for side in ("home", "away")]
                if all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values):
                    result[item.get("key")] = values
    return result


def priority(game, history, seen):
    league = league_id(game)
    record = history.get(league, {})
    # Conference League supplied verified xG and shots in the initial probe.
    successes = record.get("ok", 0) + int(league == "38322")
    failures = record.get("missing", 0)
    return (-(successes + 1) / (successes + failures + 2),
            seen.get(str(game["id"]), ""), game["id"])


def observe(state, now, save):
    key = os.environ.get("DATASPORTS_API_KEY")
    if not key:
        return
    budget = state.setdefault("datasports", {})
    day = now.date().isoformat()
    if budget.get("day") != day:
        budget.clear()
        budget.update(day=day, used=0, stats=0)
    history = state.setdefault("datasports_leagues", {})
    slot = now.hour * 2 + now.minute // 30
    if budget.get("slot") == slot:
        return
    budget["slot"] = slot
    save(state)

    def get(path):
        if budget["used"] >= 80 or budget.get("blocked"):
            return None
        budget["used"] += 1
        save(state)
        try:
            r = requests.get("https://api.datasportsapi.com/v2/" + path,
                             headers={"Authorization": "Bearer " + key}, timeout=15)
            remaining = r.headers.get("ratelimit-remaining")
            if remaining is not None and int(remaining) <= 10:
                budget["blocked"] = True
            if r.status_code in (401, 403, 429):
                budget["blocked"] = True
            save(state)
            r.raise_for_status()
            return r.json().get("data")
        except (requests.RequestException, ValueError, AttributeError):
            return None

    games = get("livescores?sport=football")
    if not isinstance(games, list):
        print("DataSports feed unavailable")
        return
    live = [g for g in games if isinstance(g, dict) and isinstance(g.get("status"), dict)
            and g["status"].get("type") == "inprogress"]
    leagues = {str(g["league"].get("id")) for g in live if isinstance(g.get("league"), dict)}
    print("DataSports live:", len(live), "leagues:", len(leagues))
    candidates = []
    for g in live:
        clock = g.get("clock") or {}
        minute = clock.get("playedSeconds")
        if isinstance(minute, (int, float)):
            minute = minute / 60
        else:
            try:
                start = datetime.fromisoformat(clock["periodStartedAt"].replace("Z", "+00:00"))
                minute = ((now - start).total_seconds() + clock["periodOffsetSeconds"]) / 60
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        if 25 <= minute <= 80 and isinstance(g.get("id"), int):
            candidates.append(g)
    seen = budget.setdefault("seen", {})
    candidates.sort(key=lambda g: priority(g, history, seen))
    # One exploitation and one exploration candidate, preferably different leagues.
    selected = candidates[:1]
    if selected:
        other = [g for g in candidates[1:] if league_id(g) != league_id(selected[0])]
        other.sort(key=lambda g: (history.get(league_id(g), {}).get("ok", 0) +
                                  history.get(league_id(g), {}).get("missing", 0),
                                  seen.get(str(g["id"]), ""), g["id"]))
        selected += (other or candidates[1:])[:1]
    for g in selected:
        if budget["stats"] >= 32 or budget["used"] >= 80 or budget.get("blocked"):
            break
        budget["stats"] += 1
        seen[str(g["id"])] = now.isoformat()
        save(state)
        data = get("fixtures/" + str(g["id"]) + "/statistics")
        if not isinstance(data, dict):
            continue
        stats = extract_stats(data)
        complete = {"expectedGoals", "totalShots", "shotsOnGoal"} <= stats.keys()
        league = league_id(g)
        if league:
            record = history.setdefault(league, {"ok": 0, "missing": 0})
            field = "ok" if complete else "missing"
            record[field] += 1
        save(state)
        print("DataSports statistics:", int(complete), "momentum:", int("momentum" in stats))
    print("DataSports budget:", budget["used"], "/ 80")
