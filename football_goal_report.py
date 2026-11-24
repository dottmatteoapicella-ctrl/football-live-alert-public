import json
from pathlib import Path
state = json.loads(Path("football_alert_state.json").read_text())
records = list(state.get("alert_outcomes", {}).values())
for half in (1, 2):
    rows = [r for r in records if r.get("half") == half]
    status = "halftime_outcome" if half == 1 else "outcome"
    goals = "halftime_goals_after_alert" if half == 1 else "goals_after_alert"
    valid = [r for r in rows if r.get(status) == "verified" and isinstance(r.get(goals), int) and r[goals] >= 0]
    wins = sum(r[goals] > 0 for r in valid)
    print("FOOTBALL_GOAL_REPORT:", json.dumps({"half": half, "alerts": len(rows), "verified": len(valid), "with_goal": wins, "without_goal": len(valid)-wins, "goals": sum(r[goals] for r in valid), "pending": sum(r.get(status, "pending") == "pending" for r in rows), "other": len(rows)-len(valid)-sum(r.get(status, "pending") == "pending" for r in rows)}))
