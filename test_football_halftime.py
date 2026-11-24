import unittest
import football_outcomes as o

class HalftimeTests(unittest.TestCase):
    def test_score_shapes(self):
        self.assertEqual(o.halftime_score({"half_time_score":{"home":1,"away":2}}), [1,2])
        self.assertEqual(o.halftime_score({"half_time_score":[0,1]}), [0,1])
        self.assertIsNone(o.halftime_score({"home_score":3,"away_score":2}))
        self.assertIsNone(o.halftime_score({"half_time_score":None}))
    def test_goal_after_alert_h1(self):
        state={"alert_outcomes":{}}
        o.register(state,"10",1,{"home_score":0,"away_score":1,"current_minute":30},"Home",1.5,__import__("datetime").datetime.now(__import__("datetime").timezone.utc))
        self.assertTrue(o.update_halftime(state,[{"id":"10","half_time_score":{"home":1,"away":1}}]))
        r=state["alert_outcomes"]["10:H1"]
        self.assertEqual(r["halftime_goals_after_alert"],1)
        self.assertEqual(r["halftime_favorite_goals_after_alert"],1)
        self.assertEqual(r["halftime_outcome"],"verified")
    def test_no_goal_and_regression(self):
        state={"alert_outcomes":{}}
        from datetime import datetime,timezone
        o.register(state,"11",1,{"home_score":0,"away_score":1},"Home",1.5,datetime.now(timezone.utc))
        o.update_halftime(state,[{"id":"11","half_time_score":[0,1]}])
        self.assertEqual(state["alert_outcomes"]["11:H1"]["halftime_goals_after_alert"],0)
        state2={"alert_outcomes":{}}
        o.register(state2,"12",1,{"home_score":1,"away_score":2},"Home",1.5,datetime.now(timezone.utc))
        o.update_halftime(state2,[{"id":"12","half_time_score":[0,1]}])
        self.assertEqual(state2["alert_outcomes"]["12:H1"]["halftime_outcome"],"unverified")
if __name__=="__main__":
    unittest.main()
