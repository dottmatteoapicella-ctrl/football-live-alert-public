"""F1 observation only, no network requests."""
from collections import Counter
from football_poe import number, minute
from football_outcomes import score

def observe(state,matches,now,half_of):
    records=state.setdefault("f1",{})
    counts=Counter()
    for m in matches:
        if not isinstance(m,dict) or m.get("id") is None: continue
        eid=str(m["id"])
        if eid in records: continue
        if half_of(m)!=2 or str(m.get("status","")).lower() not in {"live","inprogress","in_progress","inplay","in_play","in-progress"}: continue
        counts["second_half"]+=1
        fav=state.get("odds_cache",{}).get(eid)
        if not isinstance(fav,(list,tuple)) or len(fav)!=2 or fav[0] not in ("Home","Away"): continue
        odd=number(fav[1])
        if odd is None or not 1<odd<=1.4: continue
        sc=score(m)
        i=int(fav[0]=="Away")
        if sc is None or sc[1-i]-sc[i] not in (0,1): continue
        stats=m.get("statistics")
        if not isinstance(stats,dict): stats={}
        side=fav[0].lower()
        vals=[]
        for aliases in (("possession","ball_possession","ballPossession"),("shots_on_target","shotsOnTarget","shotsOnGoal"),("xg","expected_goals","expectedGoals"),("red_cards","redCards")):
            raw=next((stats[k] for k in aliases if k in stats),None)
            v=raw.get(side) if isinstance(raw,dict) else None
            if isinstance(v,str) and v.endswith("%"): v=v[:-1]
            vals.append(number(v))
        if any(v is None for v in vals):
            counts["missing_stats"]+=1
            continue
        p,t,x,red=vals
        if not (60<=p<=100 and t>=4 and x>=.3 and red==0): continue
        records[eid]=dict(side=i,score=sc,minute=m.get("current_minute"),time=now.isoformat(),odd=odd,stats=vals,result="pending",teams=[m.get("home_team"),m.get("away_team")])
        counts["new"]+=1
    state["f1_cycle"]=dict(counts)

def settle(state,matches):
    for m in matches:
        if not isinstance(m,dict): continue
        r=state.get("f1",{}).get(str(m.get("id")))
        if not r or r["result"]!="pending": continue
        if str(m.get("status","")).strip().lower() not in {"ft","full-time","full time","finished","ended"}: continue
        if not all(r["teams"]) or r["teams"]!=[m.get("home_team"),m.get("away_team")]: continue
        sc=score(m)
        if sc is None: continue
        if any(a<b for a,b in zip(sc,r["score"])): r["result"]="unknown"
        else: r["result"]="success" if sc[r["side"]]>r["score"][r["side"]] else "failure"

def report(state):
    rows=list(state.get("f1",{}).values())
    c=Counter(r["result"] for r in rows if r.get("telegram_message_id"))
    n=c["success"]+c["failure"]
    return dict(observed=len(rows),delivered=sum(bool(r.get("telegram_message_id")) for r in rows),cycle=state.get("f1_cycle",{}),outcomes=dict(c),verified=n,success_pct=round(100*c["success"]/n,2) if n else None)
