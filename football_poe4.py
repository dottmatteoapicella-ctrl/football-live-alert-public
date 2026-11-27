"""Retrospective baseline on existing verified alerts, not a live betting model."""
import math
from datetime import datetime

def report(state):
    output=[]
    for half in (1,2):
        rows=[]
        for r in state.get("alert_outcomes",{}).values():
            status="halftime_outcome" if half==1 else "outcome"
            goals="halftime_goals_after_alert" if half==1 else "goals_after_alert"
            if r.get("half")!=half or r.get(status)!="verified" or type(r.get(goals)) is not int or r[goals]<0:
                continue
            try:
                minute=int(r["minute"])
                stamp=datetime.fromisoformat(r["sent_utc"])
                if stamp.tzinfo is None: continue
            except (KeyError,ValueError,TypeError): continue
            if not (0<=minute<45 if half==1 else 45<=minute<90): continue
            rows.append((stamp,str(r["event_id"]), (45 if half==1 else 90)-minute,r[goals]))
        rows.sort()
        unique={}
        for row in rows: unique.setdefault(row[1],row)
        rows=list(unique.values())
        # Hold out the latest third; avoid overlapping UTC dates between sets.
        cut=rows[len(rows)*2//3][0].date() if rows else None
        train=[r for r in rows if r[0].date()<cut] if cut else []
        test=[r for r in rows if r[0].date()>=cut] if cut else []
        result=dict(half=half,eligible=len(rows),train=len(train),test=len(test),status="insufficient_sample")
        if len(train)>=20 and len(test)>=10:
            rate=sum(r[3] for r in train)/sum(r[2] for r in train)
            predictions=[-math.expm1(-rate*r[2]) for r in test]
            actual=[int(r[3]>0) for r in test]
            base=sum(r[3]>0 for r in train)/len(train)
            result.update(status="experimental",rate_per_minute=round(rate,5),mean_probability=round(sum(predictions)/len(test),4),observed_rate=round(sum(actual)/len(test),4),brier=round(sum((p-y)**2 for p,y in zip(predictions,actual))/len(test),4),baseline_brier=round(sum((base-y)**2 for y in actual)/len(test),4))
        output.append(result)
    return output
