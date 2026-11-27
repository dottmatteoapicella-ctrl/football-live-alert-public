"""Rolling 30-day unique live event coverage; alert outcomes use existing history."""
from datetime import timedelta
import json

def observe(state, matches, now, complete):
    rows = state.setdefault("league_coverage", {})
    cutoff = (now - timedelta(days=30)).isoformat()
    for key in list(rows):
        if rows[key].get("last", "") < cutoff:
            del rows[key]
    for m in matches:
        if not isinstance(m, dict) or m.get("id") is None:
            continue
        key = str(m["id"])
        r = rows.setdefault(key, {})
        league = m.get("league_name")
        if isinstance(league, str) and league.strip():
            r["league"] = league.strip()
        r["last"] = now.isoformat()
        r["favorite"] = bool(r.get("favorite") or state.get("odds_cache", {}).get(key))
        r["stats"] = bool(r.get("stats") or (isinstance(m.get("statistics"), dict) and m["statistics"]))
        r["complete"] = bool(r.get("complete") or complete(m))

def report(state):
    groups = {}
    def group(name):
        return groups.setdefault(name or "N/D", {"live":0,"favorite":0,"stats":0,"complete":0,"H1":{"alerts":0,"wins":0,"losses":0,"pending":0,"other":0},"H2":{"alerts":0,"wins":0,"losses":0,"pending":0,"other":0}})
    for r in state.get("league_coverage", {}).values():
        g=group(r.get("league"))
        g["live"]+=1
        for k in ("favorite","stats","complete"):
            g[k]+=bool(r.get(k))
    for r in state.get("alert_outcomes", {}).values():
        half=r.get("half")
        if half not in (1,2): continue
        g=group(r.get("league") or state.get("league_coverage",{}).get(str(r.get("event_id")),{}).get("league"))["H"+str(half)]
        g["alerts"]+=1
        status=r.get("halftime_outcome" if half==1 else "outcome","pending")
        goals=r.get("halftime_goals_after_alert" if half==1 else "goals_after_alert")
        k=("wins" if goals>0 else "losses") if status=="verified" and type(goals) is int and goals>=0 else ("pending" if status=="pending" else "other")
        g[k]+=1
    for name,g in sorted(groups.items()):
        for h in ("H1","H2"):
            s=g[h]
            n=s["wins"]+s["losses"]
            s["success_pct"]=round(100*s["wins"]/n,2) if n else None
            s["roi_pct"]=None
        print("FOOTBALL_LEAGUE_REPORT:",json.dumps({"league":name,**g},ensure_ascii=True))
    return groups
