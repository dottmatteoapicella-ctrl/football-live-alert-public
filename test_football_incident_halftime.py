import unittest
from football_outcomes import incident_halftime

class TestIncidentHalftime(unittest.TestCase):
    def test_explicit_period(self):
        self.assertEqual(incident_halftime({"event_id": 1, "incidents": [{"type":"period","minute":45,"home_score":0,"away_score":1},{"type":"period","minute":90,"home_score":2,"away_score":1}]}, "1"), [0,1])
    def test_goal_not_period(self):
        self.assertIsNone(incident_halftime({"event_id":1,"incidents":[{"type":"goal","minute":45,"home_score":1,"away_score":1}]},1))
    def test_wrong_id(self):
        self.assertIsNone(incident_halftime({"event_id":2,"incidents":[]},1))
    def test_conflict(self):
        self.assertIsNone(incident_halftime({"event_id":1,"incidents":[{"type":"period","minute":45,"home_score":0,"away_score":1},{"type":"period","minute":45,"home_score":1,"away_score":1}]},1))
