import unittest
from stats_quality import missing_fields

class StatsQualityTests(unittest.TestCase):
    def test_missing_required_both_sides(self):
        self.assertEqual(missing_fields({"xG":{"home":2.7,"away":None},
                                         "shots":{"home":21,"away":10}}),
                         ["xG.away","shots_on_target.home","shots_on_target.away"])
    def test_complete(self):
        self.assertEqual(missing_fields({"xG":{"home":2.7,"away":0.74},
                                         "shots":{"home":21,"away":10},
                                         "shots_on_target":{"home":10,"away":3}}),[])
if __name__=="__main__":
    unittest.main()
