"""Experimental pressure monitor; no network or Telegram calls."""
import math
import re
from datetime import datetime
from collections import Counter
from football_outcomes import score

def minute(m):
    raw = re.fullmatch(r"(\d{1,3})'?", str(m.get("current_minute", "")).strip())
    return int(raw[1]) if raw else None

def number(v):
    if isinstance(v, bool):
        return None
    try:
        n = float(v)
        return n if math.isfinite(n) and n >= 0 else None
    except (ValueError, TypeError):
        return None

def side_stats(m, side):
    stats = m.get("statistics")
    if not isinstance(stats, dict):
        return None
    out = []
    for aliases in (("xg","expected_goals","expectedGoals"), ("shots_on_target","shotsOnTarget"),
                    ("total_shots","shots_total","shots"), ("momentum","attack_momentum"), ("red_cards","redCards")):
        v = next((stats[k] for k in aliases if k in stats), None)
        out.append(number(v.get(side)) if isinstance(v, dict) else None)
    return out if all(v is not None for v in out) and out[3] <= 100 else None

def rating(old, new, elapsed, mn):
    if not 5 <= elapsed <= 15 or new[4] != 0 or any(new[i] < old[i] for i in range(3)):
        return None
    xg, target, shots = [(new[i]-old[i])*10/elapsed for i in range(3)]
    return round(40*min(xg/.6,1)+20*min(target/2,1)+10*min(shots/4,1)+20*new[3]/100+10*min(mn/60,1),1)

def observe(state, matches, now):
    p = state.setdefault("poe", {"snapshots":{}, "signals":{}, "counts":{}})
    snapshots, signals = p["snapshots"], p["signals"]
    counts = Counter()
    for m in matches:
        counts["matches_seen"] += 1
        eid = str(m.get("id"))
        mn, sc = minute(m), score(m)
        if mn is None or sc is None or str(m.get("status","")).lower() not in {"live","inprogress","in_progress","inplay","in_play","in-progress"}:
            continue
        if eid in signals and signals[eid]["result"] == "pending":
            r = signals[eid]
            delta = mn-r["minute"]
            if delta >= 0:
                if any(sc[i]<r["score"][i] for i in range(2)):
                    r["result"]="unknown"
                elif delta <= 15 and sum(sc)>sum(r["score"]):
                    r["result"]="goal_observed"
                elif delta >= 15:
                    r["result"]="no_goal_observed" if sum(sc)==sum(r["score"]) else "unknown"
        if not 25 <= mn <= 80:
            continue
        for side in ("home","away"):
            key = eid+":"+side
            data = side_stats(m,side)
            if data is None:
                counts["missing_stats"] += 1
                continue
            old = snapshots.get(key)
            snapshots[key] = {"minute":mn,"data":data,"time":now.isoformat()}
            if not old or not 40 <= mn <= 80 or abs(sc[0]-sc[1])>1:
                continue
            age=(now-datetime.fromisoformat(old["time"])).total_seconds()/60
            if not 0 < age <= 20:
                continue
            value=rating(old["data"],data,mn-old["minute"],mn)
            if value is None:
                counts["rating_rejected"] += 1
                continue
            counts["rated"] += 1
            if value < 75:
                counts["below_threshold"] += 1
            if value>=75 and eid not in signals:
                signals[eid]={"minute":mn,"score":sc,"rating":value,"side":side,"time":now.isoformat(),"result":"pending"}
                counts["new"] += 1
    for r in signals.values():
        if r["result"]=="pending" and (now-datetime.fromisoformat(r["time"])).total_seconds()>3600:
            r["result"]="unknown"
    p["snapshots"]={k:v for k,v in snapshots.items() if (now-datetime.fromisoformat(v["time"])).total_seconds()<3600}
    p["counts"]=dict(counts)

def report(state):
    p=state.get("poe",{})
    return {"cycle":p.get("counts",{}),"outcomes":dict(Counter(r["result"] for r in p.get("signals",{}).values()))}

def rating_v2(old, new, rival_old, rival_new, elapsed):
    if not 5 <= elapsed <= 15 or new[4] != 0:
        return None
    if any(b[i]<a[i] for a,b in ((old,new),(rival_old,rival_new)) for i in range(3)):
        return None
    x,t,s = [(new[i]-old[i])*10/elapsed for i in range(3)]
    rx,rt,rs = [(rival_new[i]-rival_old[i])*10/elapsed for i in range(3)]
    if x < .25 or t < 1:
        return None
    dominance = sum(a/(a+b) if a+b else .5 for a,b in ((x,rx),(t,rt),(s,rs)))/3
    return round(30*min(x/.6,1)+25*min(t/3,1)+20*dominance+15*min(s/5,1)+10*new[3]/100,1)

def observe_v2(state, matches, now):
    p=state.setdefault("poe_v2", {"snapshots":{}, "signals":{}, "counts":{}})
    snapshots,signals=p["snapshots"],p["signals"]
    counts=Counter()
    for m in matches:
        counts["matches_seen"] += 1
        if not isinstance(m,dict) or m.get("id") is None:
            continue
        eid=str(m["id"])
        mn,sc=minute(m),score(m)
        if mn is None or sc is None or str(m.get("status","")).lower() not in {"live","inprogress","in_progress","inplay","in_play","in-progress"}:
            continue
        r=signals.get(eid)
        if r and r["result"]=="pending":
            delta=mn-r["minute"]
            if delta>=0:
                if any(sc[i]<r["score"][i] for i in range(2)):
                    r["result"]="unknown"
                elif delta<=15 and sum(sc)>sum(r["score"]):
                    r["result"]="goal_observed"
                elif delta>=15:
                    r["result"]="no_goal_observed" if sum(sc)==sum(r["score"]) else "unknown"
        if not 25<=mn<=80:
            continue
        data=[side_stats(m,side) for side in ("home","away")]
        if any(d is None for d in data):
            counts["missing_stats"]+=1
            continue
        old=snapshots.get(eid)
        if old and old["minute"]==mn:
            continue
        snapshots[eid]={"minute":mn,"data":data,"time":now.isoformat()}
        if not old or not 40<=mn<=80 or abs(sc[0]-sc[1])>1 or eid in signals:
            continue
        age=(now-datetime.fromisoformat(old["time"])).total_seconds()/60
        if not 0<age<=20:
            continue
        elapsed=mn-old["minute"]
        candidates=[]
        for i in (0,1):
            value=rating_v2(old["data"][i],data[i],old["data"][1-i],data[1-i],elapsed)
            if value is not None:
                counts["rated"]+=1
                if value < 75:
                    counts["below_threshold"] += 1
                if value>=75:
                    candidates.append((value,i))
            else:
                counts["rating_rejected"] += 1
        if candidates:
            value,i=max(candidates)
            signals[eid]={"minute":mn,"score":sc,"rating":value,"side":("home","away")[i],"time":now.isoformat(),"result":"pending","window":elapsed,"deltas":[[round((data[j][k]-old["data"][j][k])*10/elapsed,2) for k in range(3)] for j in (0,1)]}
            counts["new"]+=1
    for r in signals.values():
        if r["result"]=="pending" and (now-datetime.fromisoformat(r["time"])).total_seconds()>3600:
            r["result"]="unknown"
    p["snapshots"]={k:v for k,v in snapshots.items() if (now-datetime.fromisoformat(v["time"])).total_seconds()<3600}
    p["counts"]=dict(counts)

def report_v2(state):
    p=state.get("poe_v2",{})
    return {"cycle":p.get("counts",{}),"outcomes":dict(Counter(r["result"] for r in p.get("signals",{}).values()))}
