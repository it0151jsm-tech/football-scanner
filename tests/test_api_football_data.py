import unittest
from unittest.mock import Mock
from datetime import datetime, timezone

from api_football_data import fetch_finished_fixtures, LEAGUE_IDS


class ApiFootballAdapterTests(unittest.TestCase):
    def fixture(self, home="Arsenal", away="Chelsea", score=(2, 1), status="FT", fixture_id=1):
        return {"fixture": {"id": fixture_id, "timestamp": 1780000000, "status": {"short": status}},
                "teams": {"home": {"name": home}, "away": {"name": away}},
                "goals": {"home": score[0], "away": score[1]}}

    def test_five_league_codes(self):
        self.assertEqual(set(LEAGUE_IDS.values()), {39, 140, 78, 135, 61})

    def test_missing_key_never_calls_provider(self):
        get = Mock()
        frame, diag = fetch_finished_fixtures("soccer_epl", 2026, "", get)
        self.assertTrue(frame.empty)
        self.assertEqual(diag["status"], "not_configured")
        get.assert_not_called()

    def test_only_finished_valid_scores(self):
        response = Mock(status_code=200)
        response.json.return_value = {"response": [
            self.fixture(), self.fixture(status="NS", fixture_id=2),
            self.fixture(score=(None, 1), fixture_id=3),
            self.fixture(home="", fixture_id=4)]}
        get = Mock(return_value=response)
        frame, diag = fetch_finished_fixtures("soccer_epl", 2026, "dummy-test-key", get)
        self.assertEqual(diag["status"], "ok")
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]["FTHG"], 2)
        self.assertEqual(get.call_args.kwargs["params"], {"league": 39, "season": 2026, "status": "FT"})

    def test_conflicting_scores_are_excluded(self):
        response = Mock(status_code=200)
        response.json.return_value = {"response": [self.fixture(), self.fixture(score=(3, 1), fixture_id=2)]}
        frame, diag = fetch_finished_fixtures("soccer_epl", 2026, "dummy-test-key", Mock(return_value=response))
        self.assertTrue(frame.empty)
        self.assertEqual(diag["status"], "conflicting_scores")

    def test_http_error_does_not_fabricate_results(self):
        response = Mock(status_code=429)
        frame, diag = fetch_finished_fixtures("soccer_epl", 2026, "dummy-test-key", Mock(return_value=response))
        self.assertTrue(frame.empty)
        self.assertEqual(diag["http_status"], 429)

    def test_provider_error_does_not_fabricate_results(self):
        response = Mock(status_code=200)
        response.json.return_value = {"errors": {"plan": "restricted"}, "response": []}
        frame, diag = fetch_finished_fixtures("soccer_epl", 2026, "dummy-test-key", Mock(return_value=response))
        self.assertTrue(frame.empty)
        self.assertEqual(diag["status"], "provider_error")


if __name__ == "__main__":
    unittest.main()
