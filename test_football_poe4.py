import unittest
from football_poe4 import report
class TestPoisson(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(report({})[0]["status"],"insufficient_sample")
    def test_holdout(self):
        rows={}
        for i in range(40):
            rows[str(i)]=dict(event_id=i,half=1,minute="30",sent_utc=f"2026-11-{1+i//2:02d}T12:00:00+00:00",halftime_outcome="verified",halftime_goals_after_alert=i%2)
        r=report({"alert_outcomes":rows})[0]
        self.assertEqual(r["status"],"experimental")
        self.assertEqual(r["train"]+r["test"],40)
        self.assertAlmostEqual(r["mean_probability"],0.3935,places=4)
