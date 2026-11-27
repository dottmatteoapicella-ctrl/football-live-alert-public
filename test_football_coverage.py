import unittest
from datetime import datetime,timezone,timedelta
from football_coverage import observe,report
class CoverageTests(unittest.TestCase):
    def test_coverage(self):
        now=datetime.now(timezone.utc)
        s={"odds_cache":{"1":["Home",1.5]},"alert_outcomes":{"1:H1":{"event_id":"1","half":1,"league":"A","halftime_outcome":"verified","halftime_goals_after_alert":1}}}
        m={"id":1,"league_name":"A","statistics":{"home":{}}}
        observe(s,[m,m],now,lambda m:False)
        g=report(s)["A"]
        self.assertEqual((g["live"],g["favorite"],g["stats"],g["complete"]),(1,1,1,0))
        self.assertEqual(g["H1"]["success_pct"],100)
        self.assertIsNone(g["H1"]["roi_pct"])
        observe(s,[],now+timedelta(days=31),lambda m:False)
        self.assertEqual(report(s)["A"]["live"],0)
