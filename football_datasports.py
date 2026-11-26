import os
from datetime import datetime
import math
import unicodedata
import re
import requests
import football_poe3


def normalized(value):
    if isinstance(value, dict):
        value = value.get("name")
    if not isinstance(value, str):
        return ""
    return " ".join(re.findall(r"[a-z0-9]+", unicodedata.normalize("NFKD", value.casefold()).encode("ascii", "ignore").decode()))


def kickoff(value):
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError, AttributeError):
        return None


def team_key(value):
    # Remove only generic club designators; retain youth, reserve and gender labels.
    return " ".join(t for t in normalized(value).split() if t not in {"fc", "cf", "afc", "fk", "sc"})


def associate(primary, secondary):
    pairs = {}
    counts = dict(invalid=0, names_missing=0, time_mismatch=0, ambiguous=0)
    for g in secondary:
        home, away = team_key(g.get("home")), team_key(g.get("away"))
        start = kickoff(g.get("startTime"))
        if not home or not away or not start or g.get("id") is None:
            counts["invalid"] += 1
            continue
        names = [m for m in primary if m.get("id") is not None
                 and team_key(m.get("home_team")) == home and team_key(m.get("away_team")) == away]
        candidates = [str(m["id"]) for m in names if kickoff(m.get("event_date"))
                      and abs((kickoff(m["event_date"])-start).total_seconds()) <= 900]
        if len(candidates) == 1:
            pairs[str(g["id"])] = candidates[0]
        else:
            counts["ambiguous" if candidates else "time_mismatch" if names else "names_missing"] += 1
    unique = {k:v for k,v in pairs.items() if list(pairs.values()).count(v) == 1}
    counts["ambiguous"] += len(pairs) - len(unique)
    print("DataSports association diagnostics:", counts)
    return unique


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
    fresh = budget.get("slot") != slot
    snapshots = budget.setdefault("poe3_snapshots", {})
    signals = state.setdefault("poe3_signals", {})
    due = [k for k,v in snapshots.items() if 5 <= (now-kickoff(v["at"])).total_seconds()/60 <= 15 and k not in signals]
    if not fresh and not due:
        return
    if fresh:
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
    budget["live_identity"] = [{k:g.get(k) for k in ("id","home","away","startTime")} for g in live]
    budget["identity_at"] = now.isoformat()
    save(state)
    candidates = []
    minutes = {}
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
            minutes[str(g["id"])] = minute
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
    # Follow-ups take precedence; extra live calls share the same hard cap.
    selected = sorted([g for g in candidates if str(g["id"]) in due], key=lambda g: snapshots[str(g["id"])]["at"]) + ([g for g in selected if str(g["id"]) not in due] if fresh else [])
    for g in selected[:2]:
        if budget["stats"] >= 32 or budget["used"] >= 80 or budget.get("blocked"):
            break
        budget["stats"] += 1
        seen[str(g["id"])] = now.isoformat()
        save(state)
        data = get("fixtures/" + str(g["id"]) + "/statistics")
        stats = extract_stats(data)
        complete = {"expectedGoals", "totalShots", "shotsOnGoal"} <= stats.keys()
        league = league_id(g)
        if league:
            record = history.setdefault(league, {"ok": 0, "missing": 0})
            field = "ok" if complete else "missing"
            record[field] += 1
        gid = str(g["id"])
        minute = minutes[gid]
        scores = [g.get(k) or {} for k in ("homeScore", "awayScore")]
        score = [v.get("current") for v in scores]
        red = [v.get("redCards") for v in scores]
        old = snapshots.get(gid)
        if complete and all(type(v) is int and v >= 0 for v in score+red):
            if old and gid not in signals and 40 <= minute <= 80 and old["score"] == score and old["red"] == red:
                wall = (now-kickoff(old["at"])).total_seconds()/60
                if 5 <= wall <= 15:
                    result = football_poe3.evaluate(old["stats"], stats, minute-old["minute"], score, red)
                    if result:
                        signals[gid] = dict(result, at=now.isoformat(), minute=minute, score=score, result="pending", home=normalized(g.get("home")), away=normalized(g.get("away")))
            snapshots[gid] = dict(at=now.isoformat(), minute=minute, stats=stats, score=score, red=red)
        save(state)
        print("DataSports statistics:", int(complete), "momentum:", int("momentum" in stats))
    for g in live:
        gid = str(g["id"])
        record = signals.get(gid)
        if not record or record["result"] != "pending":
            continue
        minute = minutes.get(gid)
        score = [(g.get(k) or {}).get("current") for k in ("homeScore","awayScore")]
        if minute is None or not all(type(v) is int for v in score):
            continue
        elapsed = minute-record["minute"]
        if 0 <= elapsed <= 15 and sum(score) > sum(record["score"]):
            record["result"] = "goal_observed"
        elif elapsed >= 15:
            record["result"] = "no_goal_observed" if score == record["score"] else "unknown"
    for record in signals.values():
        if record["result"] == "pending" and (now-kickoff(record["at"])).total_seconds() > 3600:
            record["result"] = "unknown"
    save(state)
    print("DataSports POE3 signals:", len(signals), "goals:", sum(v["result"]=="goal_observed" for v in signals.values()))
    print("DataSports budget:", budget["used"], "/ 80")
