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
