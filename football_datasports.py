import os
from datetime import timedelta
import requests


def observe(state, now, save):
    key = os.environ.get("DATASPORTS_API_KEY")
    if not key:
        return
    budget = state.setdefault("datasports", {})
    day = now.date().isoformat()
    if budget.get("day") != day:
        budget.clear()
        budget.update(day=day, used=0, stats=0)
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
                from datetime import datetime
                start = datetime.fromisoformat(clock["periodStartedAt"].replace("Z", "+00:00"))
                minute = ((now - start).total_seconds() + clock["periodOffsetSeconds"]) / 60
            except (KeyError, ValueError, TypeError, AttributeError):
                continue
        if 25 <= minute <= 80 and isinstance(g.get("id"), int):
            candidates.append(g)
    seen = budget.setdefault("seen", {})
    candidates.sort(key=lambda g: (seen.get(str(g["id"]), ""), g["id"]))
    for g in candidates[:2]:
        if budget["stats"] >= 32:
            break
        budget["stats"] += 1
        seen[str(g["id"])] = now.isoformat()
        save(state)
        data = get("fixtures/" + str(g["id"]) + "/statistics")
        if not isinstance(data, dict):
            continue
        keys = {item.get("key") for p in data.get("periods", []) if p.get("period") == "all"
                for group in p.get("groups", []) for item in group.get("items", [])}
        print("DataSports statistics:", int({"expectedGoals", "totalShots", "shotsOnGoal"} <= keys),
              "momentum:", int("momentum" in keys))
    print("DataSports budget:", budget["used"], "/ 80")
