import unittest
from datetime import datetime, timezone, timedelta
from football_poe import observe, rating

class PressureTest(unittest.TestCase):
    def test_rating(self):
        self.assertEqual(rating([0,0,0,90,0],[.6,2,4,100,0],10,60),100)
        self.assertIsNone(rating([0,0,0,90,0],[.6,2,4,100,1],10,60))
    def test_observation(self):
        now=datetime.now(timezone.utc)
        state={}
        m={"id":1,"status":"live","current_minute":50,"home_score":0,"away_score":0}
        def stats(x,t,s):
            return {k:{"home":v,"away":0} for k,v in zip(("xg","shots_on_target","total_shots","momentum","red_cards"),(x,t,s,100,0))}
        m["statistics"]=stats(0,0,0)
        observe(state,[m],now)
        m.update(current_minute=60,statistics=stats(.6,2,4))
        observe(state,[m],now+timedelta(minutes=10))
        self.assertEqual(state["poe"]["signals"]["1"]["result"],"pending")
        m.update(current_minute=70,home_score=1)
        observe(state,[m],now+timedelta(minutes=20))
        self.assertEqual(state["poe"]["signals"]["1"]["result"],"goal_observed")

    def test_v2(self):
        from football_poe import rating_v2, observe_v2
        self.assertEqual(rating_v2([0,0,0,100,0],[.6,3,5,100,0],[0,0,0,0,0],[0,0,0,0,0],10),100)
        self.assertIsNone(rating_v2([0,0,0,100,0],[.3,3,5,100,0],[0,0,0,0,0],[0,0,0,0,0],10))
        now=datetime.now(timezone.utc)
        state={}
        m={"id":2,"status":"live","current_minute":50,"home_score":0,"away_score":0}
        def stats(x,t,s):
            return {k:{"home":v,"away":0} for k,v in zip(("xg","shots_on_target","total_shots","momentum","red_cards"),(x,t,s,100,0))}
        m["statistics"]=stats(0,0,0)
        observe_v2(state,[m],now)
        m.update(current_minute=60,statistics=stats(.6,3,5))
        observe_v2(state,[m],now+timedelta(minutes=10))
        self.assertEqual(state["poe_v2"]["signals"]["2"]["result"],"pending")
        m.update(current_minute=70,home_score=1)
        observe_v2(state,[m],now+timedelta(minutes=20))
        self.assertEqual(state["poe_v2"]["signals"]["2"]["result"],"goal_observed")
