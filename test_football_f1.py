import unittest
from datetime import datetime,timezone
from football_f1 import observe,settle,report
class TestF1(unittest.TestCase):
    def test_signal(self):
        state={"odds_cache":{"1":["Home",1.4]}}
        m=dict(id=1,status="live",current_minute=60,home_score=0,away_score=1,home_team="A",away_team="B",statistics={k:{"home":v} for k,v in [("possession",60),("shots_on_target",4),("xg",.3),("red_cards",0)]})
        now=datetime.now(timezone.utc)
        observe(state,[m],now,lambda m:2)
        observe(state,[m],now,lambda m:2)
        self.assertEqual(len(state["f1"]),1)
        state["f1"]["1"]["telegram_message_id"]=123
        settle(state,[dict(m,status="finished",away_score=2)])
        self.assertEqual(report(state)["outcomes"],{"failure":1})
    def test_missing(self):
        state={"odds_cache":{"1":["Home",1.4]}}
        observe(state,[dict(id=1,status="live",home_score=0,away_score=0)],datetime.now(timezone.utc),lambda m:2)
        self.assertFalse(state["f1"])

    def test_only_red_cards_required(self):
        now=datetime.now(timezone.utc)
        for stats,expected in [
            ({"red_cards":{"home":0}},True),
            ({"red_cards":{"home":0},"possession":{"home":20},"shots_on_target":{"home":0},"xg":{"home":0}},True),
            ({"red_cards":{"home":1}},False),
            ({},False),
            ({"redCards":{"home":0}},True),
        ]:
            with self.subTest(stats=stats):
                state={"odds_cache":{"1":["Home",1.4]}}
                m=dict(id=1,status="live",current_minute=60,home_score=0,away_score=0,home_team="A",away_team="B",statistics=stats)
                observe(state,[m],now,lambda m:2)
                self.assertEqual(bool(state["f1"]),expected)
    def test_other_filters_unchanged(self):
        for odd,half,home,away in [(1.41,2,0,0),(1.4,1,0,0),(1.4,2,0,2),(1.4,2,1,0)]:
            state={"odds_cache":{"1":["Home",odd]}}
            m=dict(id=1,status="live",home_score=home,away_score=away,statistics={"red_cards":{"home":0}})
            observe(state,[m],datetime.now(timezone.utc),lambda m:half)
            self.assertFalse(state["f1"])
