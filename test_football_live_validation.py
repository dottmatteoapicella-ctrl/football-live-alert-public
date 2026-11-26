import os
import unittest
from unittest.mock import patch, Mock

with patch.dict(os.environ, {"BSD_API_KEY": "test", "TELEGRAM_BOT_TOKEN": "test", "TELEGRAM_CHAT_ID": "test"}):
    import football_alert as bot

class LiveValidationTests(unittest.TestCase):
    def test_numeric_scores(self):
        self.assertEqual(bot.football_outcomes.score({"home_score": "10", "away_score": "2"}), [10, 2])
        self.assertIsNone(bot.football_outcomes.score({"home_score": "?", "away_score": 2}))

    @patch("football_alert.requests.post")
    def test_telegram_requires_message_id(self, post):
        post.return_value = Mock()
        post.return_value.json.return_value = {"ok": True, "result": {}}
        with self.assertRaises(RuntimeError):
            bot.send_telegram("test")
        post.return_value.json.return_value = {"ok": True, "result": {"message_id": 123}}
        self.assertEqual(bot.send_telegram("test"), 123)

if __name__ == "__main__":
    unittest.main()


class CoverageTimingTests(__import__("unittest").TestCase):
    def test_association_club_suffix(self):
        from football_datasports import associate
        t = "2026-11-26T18:00:00Z"
        p = [{"id":1,"home_team":"Alpha FC","away_team":"Beta","event_date":t}]
        s = [{"id":2,"home":"Alpha","away":"Beta SC","startTime":t}]
        self.assertEqual(associate(p,s), {"2":"1"})
        p.append(dict(p[0],id=3))
        self.assertEqual(associate(p,s), {})

    def test_timing_score_and_same_minute(self):
        from football_telegram_marker import first_goal_timing
        r = dict(event_id=1,half=1,minute="20",score_at_alert=[0,1])
        p = dict(event_id=1,incidents=[dict(type="goal",minute=27,home_score=1,away_score=1)])
        self.assertEqual(first_goal_timing(r,p,[1,1]),dict(elapsed=7,minute=27,side="Home"))
        p["incidents"][0]["minute"]=20
        self.assertIsNone(first_goal_timing(r,p,[1,1]))
