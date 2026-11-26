import unittest
from football_poe3 import evaluate


class TestPOE3(unittest.TestCase):
    def test_pressure(self):
        before = {"expectedGoals":[0,0],"shotsOnGoal":[0,0],"totalShots":[0,0]}
        after = {"expectedGoals":[.6,0],"shotsOnGoal":[3,0],"totalShots":[5,0]}
        self.assertEqual(evaluate(before,after,10,[0,0],[0,0])["rating"],100)
        self.assertIsNone(evaluate(before,after,30,[0,0],[0,0]))
        self.assertIsNone(evaluate(before,after,10,[0,0],[1,0]))
        self.assertIsNone(evaluate(after,before,10,[0,0],[0,0]))
        self.assertIsNone(evaluate(before,{},10,[0,0],[0,0]))
