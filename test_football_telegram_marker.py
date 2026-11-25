import unittest
from unittest.mock import patch, Mock
import football_telegram_marker as marker

class MarkerTests(unittest.TestCase):
    def record(self, half=1):
        return {"event_id":"42", "half":half, "score_at_alert":[0,1],
                "telegram_message_id":123, "telegram_text":"Original alert"}
    def test_h1_goal(self):
        r=self.record()
        match={"id":"42","status":"live","current_minute":"39","home_score":1,"away_score":1}
        self.assertTrue(marker.desired_marker(r,match)[0].startswith("✅"))
    def test_h1_no_goal_until_halftime_verified(self):
        r=self.record()
        self.assertIsNone(marker.desired_marker(r,{"status":"ht","home_score":0,"away_score":1}))
        r.update(halftime_outcome="verified",halftime_score=[0,1])
        self.assertTrue(marker.desired_marker(r)[0].startswith("❌"))
    def test_no_h1_goal_from_second_half(self):
        r=self.record()
        self.assertIsNone(marker.desired_marker(r,{"status":"live","current_minute":"65","home_score":1,"away_score":1}))
    def test_h2_goal(self):
        r=self.record(2)
        self.assertTrue(marker.desired_marker(r,{"home_score":1,"away_score":1})[0].startswith("✅"))
    def test_h2_no_goal_verified_final_without_live_match(self):
        r=self.record(2)
        r.update(outcome="verified", final_score=[0,1])
        self.assertEqual(marker.desired_marker(r)[0], "❌ NESSUN GOL DOPO L'ALERT")

    def test_h2_no_goal_not_marked_before_final(self):
        r=self.record(2)
        self.assertIsNone(marker.desired_marker(r,{"home_score":0,"away_score":1}))

    def test_h2_verified_final_overrides_stale_live(self):
        r=self.record(2)
        r.update(outcome="verified", final_score=[0,1])
        self.assertTrue(marker.desired_marker(r,{"home_score":1,"away_score":1})[0].startswith("❌"))

    def test_official_goal_minutes_only_after_alert(self):
        r=self.record(1)
        r["minute"]="32"
        m={"events":[{"type":"goal","minute":"21"},{"type":"goal","minute":"45+2"},
                     {"type":"goal","minute":"70"},{"type":"yellow card","minute":"40"}]}
        self.assertEqual(marker.official_goal_minutes(r,m),["45+2'"])

    def test_no_guess_without_events(self):
        r=self.record(2)
        r["minute"]="65"
        self.assertEqual(marker.official_goal_minutes(r,{"current_minute":75,"home_score":2}),[])

    @patch("football_telegram_marker.requests.post")
    def test_final_score_added_after_green_marker(self, post):
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={"ok":True}
        r=self.record(2)
        state={"alert_outcomes":{"42:H2":r}}
        live=[{"id":"42","home_score":1,"away_score":1}]
        self.assertTrue(marker.edit_pending(state,live,"token","chat"))
        self.assertIn("✅",post.call_args.kwargs["data"]["text"])
        r.update(outcome="verified",final_score=[2,1])
        self.assertTrue(marker.edit_pending(state,[],"token","chat"))
        self.assertIn("🏁 RISULTATO FINALE: 2-1",post.call_args.kwargs["data"]["text"])
        self.assertEqual(post.call_count,2)
        self.assertFalse(marker.edit_pending(state,[],"token","chat"))

    @patch("football_telegram_marker.requests.post")
    def test_final_no_goal_adds_cross_and_final_score(self, post):
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={"ok":True}
        r=self.record(2)
        r.update(outcome="verified",final_score=[0,1])
        state={"alert_outcomes":{"42:H2":r}}
        self.assertTrue(marker.edit_pending(state,[],"token","chat"))
        body=post.call_args.kwargs["data"]["text"]
        self.assertIn("❌ NESSUN GOL",body)
        self.assertIn("🏁 RISULTATO FINALE: 0-1",body)

    @patch("football_telegram_marker.requests.post")
    def test_edits_once(self, post):
        post.return_value=Mock(status_code=200)
        post.return_value.json.return_value={"ok":True}
        state={"alert_outcomes":{"42:H1":self.record()}}
        live=[{"id":"42","status":"live","current_minute":"39","home_score":1,"away_score":1}]
        self.assertTrue(marker.edit_pending(state,live,"token","chat"))
        self.assertFalse(marker.edit_pending(state,live,"token","chat"))
        self.assertEqual(post.call_count,1)
        self.assertEqual(post.call_args.kwargs["data"]["message_id"],123)
    @patch("football_telegram_marker.requests.post")
    def test_incident_backfill_finished_alert(self, post):
        from datetime import datetime, timezone
        now = datetime(2026, 11, 25, 10, tzinfo=timezone.utc)
        r = self.record(2)
        r.update(minute="60", sent_utc="2026-11-24T20:00:00+00:00",
                 outcome="verified", final_score=[2,1])
        state = {"alert_outcomes":{"42:H2":r}}
        api = Mock(return_value={"event_id":42,"incidents":[
            {"type":"goal","minute":"90+3"},
            {"type":"goal","minute":"67"},
            {"type":"goal","minute":"20"}]})
        self.assertTrue(marker.recover_goal_minutes(state, {}, now, api, {}, set()))
        self.assertEqual(r["official_goal_minutes"], ["67'", "90+3'"])
        post.return_value.json.return_value = {"ok":True}
        self.assertTrue(marker.edit_pending(state, [], "token", "chat"))
        self.assertIn("67', 90+3'", post.call_args.kwargs["data"]["text"])
        self.assertFalse(marker.recover_goal_minutes(state, {}, now, api, {}, set()))
        self.assertEqual(api.call_count, 1)

if __name__=="__main__":
    unittest.main()
