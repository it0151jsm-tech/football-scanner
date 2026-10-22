"""Personal statistics: no network, bookmaker calls, or real user data."""
import sqlite3
import unittest
from personal_stats import clean_stats, save_verified, read_history

class PersonalStatsTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row

    def test_verified_entry_and_upsert(self):
        args=dict(match_key="fixture-1", kickoff_utc="2026-10-21T18:00:00+00:00",
                  home_team="Home",away_team="Away",home_goals=2,away_goals=1,
                  source="FotMob (user verified)",stats={"xG":{"home":1.8,"away":0.7}})
        save_verified(self.db,**args)
        save_verified(self.db,**args)
        rows=read_history(self.db,team="Home")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["home_goals"],2)
        self.assertEqual(len(read_history(self.db,team="Away",before="2026-10-20T00:00:00+00:00")),0)

    def test_reject_future_and_unaware(self):
        args=dict(match_key="f",kickoff_utc="2030-10-21T18:00:00+00:00",
                  home_team="H",away_team="A",home_goals=1,away_goals=0,
                  source="user",stats={})
        with self.assertRaises(ValueError): save_verified(self.db,**args)
        args["kickoff_utc"]="2026-10-21T18:00:00"
        with self.assertRaises(ValueError): save_verified(self.db,**args)

    def test_reject_invented_bad_values(self):
        for value in (float("nan"),float("inf"),-1,"unreadable",True):
            with self.assertRaises(ValueError):
                clean_stats({"xG":{"home":value,"away":1}})
        self.assertEqual(clean_stats({"xG":{"home":None,"away":0.7}})["xG"]["home"],None)

if __name__=="__main__":
    unittest.main()
