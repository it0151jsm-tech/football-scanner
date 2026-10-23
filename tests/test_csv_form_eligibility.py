"""Decide when CSV evidence is sufficient before optional fallback; offline only."""
import unittest
from datetime import datetime, timezone

import pandas as pd

from source_diagnostics import csv_form_ready


KICKOFF = datetime(2026, 10, 24, 19, tzinfo=timezone.utc)
FIXTURE = {'home_team': 'Ipswich Town', 'away_team': 'Nottingham Forest',
           'commence_time': KICKOFF.isoformat()}


def history(*, dates=None, season='2627', home='Ipswich', away="Nott'm Forest"):
    dates = dates or [f'2026-10-{day:02d}' for day in range(1, 6)]
    return pd.DataFrame([
        {'Date_dt': pd.Timestamp(day), 'HomeTeam': home, 'AwayTeam': away,
         'FTHG': 2, 'FTAG': 1, '_history_season': season}
        for day in dates
    ])


class CsvFormEligibilityTests(unittest.TestCase):
    def test_current_reviewed_names_and_five_games_per_venue_are_ready(self):
        frame = history()
        before = frame.copy(deep=True)
        self.assertTrue(csv_form_ready(frame, [FIXTURE]))
        pd.testing.assert_frame_equal(frame, before)

    def test_old_season_roster_is_not_enough_even_with_five_recent_games(self):
        frame = history(dates=[f'2026-06-{day:02d}' for day in range(26, 31)],
                        season='2526')
        self.assertFalse(csv_form_ready(frame, [FIXTURE]))

    def test_four_current_games_still_try_optional_provider(self):
        self.assertFalse(csv_form_ready(history().iloc[:4], [FIXTURE]))

    def test_games_in_opposite_venue_do_not_satisfy_required_form(self):
        reversed_frame = history(home="Nott'm Forest", away='Ipswich')
        self.assertFalse(csv_form_ready(reversed_frame, [FIXTURE]))

    def test_current_season_but_stale_sample_still_needs_optional_provider(self):
        frame = history(dates=[f'2026-07-{day:02d}' for day in range(1, 6)])
        december = dict(FIXTURE, commence_time='2026-12-01T19:00:00+00:00')
        self.assertFalse(csv_form_ready(frame, [december]))

    def test_unrelated_league_roster_and_similar_youth_name_are_not_ready(self):
        for frame, fixture in [
            (history(home='Other League Club', away='Other League Opponent'), FIXTURE),
            (history(), dict(FIXTURE, home_team='Ipswich Town U21')),
        ]:
            with self.subTest(team=fixture['home_team']):
                self.assertFalse(csv_form_ready(frame, [fixture]))

    def test_same_day_or_future_games_cannot_fill_missing_fifth_result(self):
        frame = history(dates=['2026-10-01', '2026-10-02', '2026-10-03',
                               '2026-10-04', '2026-10-24', '2026-10-25'])
        self.assertFalse(csv_form_ready(frame, [FIXTURE]))

    def test_invalid_result_cannot_supply_fifth_form_game(self):
        frame = history()
        frame.loc[frame.index[-1], 'FTHG'] = -1
        self.assertFalse(csv_form_ready(frame, [FIXTURE]))

    def test_missing_season_or_unconfirmed_kickoff_is_not_ready(self):
        self.assertFalse(csv_form_ready(history().drop(columns='_history_season'), [FIXTURE]))
        self.assertFalse(csv_form_ready(history(), [dict(FIXTURE, commence_time='2026-10-24T19:00:00')]))
        self.assertFalse(csv_form_ready(history(), []))
        self.assertFalse(csv_form_ready(None, [FIXTURE]))

    def test_all_requested_fixtures_must_have_form_before_skipping_fallback(self):
        missing = dict(FIXTURE, away_team='Unknown Team')
        self.assertFalse(csv_form_ready(history(), [FIXTURE, missing]))


if __name__ == '__main__':
    unittest.main()
