"""Pilot integration identity boundaries; all scores/provider IDs are synthetic.

API-Football names below are test inputs, not evidence of current live coverage.
The reviewed identity evidence remains the separate Issue #9 fixture. Numeric
IDs are provider-local metadata and never an implicit cross-provider name bridge.
"""
import unittest
from datetime import datetime, timezone

import pandas as pd

from domestic_data import resolve_domestic_sources
from history_data import match_team, team_history
from source_diagnostics import xg_diagnostics


KICKOFF = datetime(2026, 10, 24, 19, tzinfo=timezone.utc)
DEFINITIONS = {
    'soccer_epl': {'csv_code': 'E0', 'label': 'Premier League', 'understat': 'EPL'},
    'soccer_efl_champ': {'csv_code': 'E1', 'label': 'Championship', 'understat': None},
}


def provider_history(home='Ipswich Town', away='Nottingham Forest', *,
                     season='2627', dates=None, home_id=9991, away_id=9992):
    dates = dates or [f'2026-10-{day:02d}' for day in range(1, 11)]
    frame = pd.DataFrame([
        {'Date_dt': pd.Timestamp(day), 'HomeTeam': home, 'AwayTeam': away,
         'FTHG': i % 3, 'FTAG': (i + 1) % 3, '_history_season': season,
         '_provider_home_team_id': home_id, '_provider_away_team_id': away_id}
        for i, day in enumerate(dates)
    ])
    frame.attrs['history_diagnostics'] = {
        'status': 'ok', 'provider': 'api-football',
        'sources': [{'status': 'ok', 'season': season,
                     'url': 'https://example.invalid/synthetic-fixtures',
                     'received_at': '2026-10-23T10:00:00+00:00'}],
    }
    return frame


class PilotIdentityBoundaryTests(unittest.TestCase):
    def test_reviewed_labels_match_after_switching_score_provider(self):
        # This checks transporting already-reviewed labels, not an API team-ID map.
        cases = [('VfB Stuttgart', 'Stuttgart'),
                 ('Borussia Monchengladbach', "M'gladbach"),
                 ('Ipswich Town', 'Ipswich'),
                 ('Nottingham Forest', "Nott'm Forest")]
        for api_name, reviewed_name in cases:
            with self.subTest(team=api_name):
                frame = provider_history(reviewed_name, 'Synthetic Opponent')
                games, report = team_history(frame, api_name, KICKOFF, 'home')
                self.assertEqual(report['mapping'], 'verified_alias')
                self.assertEqual(report['source']['provider'], 'api-football')
                self.assertEqual(len(games), 10)
                self.assertEqual(report['sample5_dates'], [
                    f'2026-10-{day:02d}' for day in range(6, 11)])

    def test_unverified_pr13_abbreviations_do_not_gain_identity_by_similarity(self):
        # PR #13 supplies no per-team evidence for these remaining variants.
        # When evidence is reviewed, this fixture must be replaced explicitly.
        for full, short in [('Newcastle United', 'Newcastle'),
                            ('Tottenham Hotspur', 'Spurs'),
                            ('West Ham United', 'West Ham'),
                            ('Brighton and Hove Albion', 'Brighton'),
                            ('Wolverhampton Wanderers', 'Wolves')]:
            with self.subTest(team=full):
                self.assertEqual(match_team(full, [short]), (None, 'name_unmatched'))

    def test_newly_reviewed_manchester_aliases_keep_club_identity_evidence(self):
        from history_data import alias_evidence
        for full, short in [('Manchester City', 'Man City'),
                            ('Manchester United', 'Man United')]:
            with self.subTest(team=full):
                self.assertEqual(match_team(full, [short]), (short, 'verified_alias'))
                evidence = alias_evidence(full, short)
                self.assertEqual(evidence['evidence_file'], 'fixtures/issue15_identity_evidence.json')
                self.assertIn('identity_evidence', evidence)
                self.assertEqual(match_team(full, [full, short]), (None, 'name_ambiguous'))
                self.assertIsNone(match_team(full, [short+' U21'])[0])

    def test_shared_numeric_id_is_not_an_alias_for_a_different_named_club(self):
        # A local ID coinciding with one supplied by another provider is no proof.
        frame = provider_history('Notts County', 'Synthetic Opponent', home_id=9992)
        games, report = team_history(frame, 'Nottingham Forest', KICKOFF, 'home')
        self.assertTrue(games.empty)
        self.assertEqual(report['status'], 'name_unmatched')
        self.assertIsNone(report['matched_name'])

    def test_numeric_provider_label_cannot_resolve_a_named_api_team(self):
        frame = provider_history('9992', 'Synthetic Opponent', home_id=9992)
        games, report = team_history(frame, 'Nottingham Forest', KICKOFF, 'home')
        self.assertTrue(games.empty)
        self.assertEqual(report['status'], 'name_unmatched')

    def test_old_provider_id_membership_cannot_override_current_league_evidence(self):
        frames = {
            'soccer_epl': provider_history(season='2526', dates=['2026-05-10']),
            'soccer_efl_champ': provider_history(),
        }
        report = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF,
                                          DEFINITIONS)['Ipswich Town']
        self.assertEqual(report['status'], 'matched')
        self.assertEqual(report['sport_key'], 'soccer_efl_champ')
        self.assertEqual(report['source_season'], '2627')

    def test_future_provider_membership_does_not_prove_current_league(self):
        frames = {
            'soccer_epl': provider_history(dates=['2026-10-25']),
            'soccer_efl_champ': provider_history('Other Team', 'Other Opponent'),
        }
        report = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF,
                                          DEFINITIONS)['Ipswich Town']
        self.assertNotEqual(report['status'], 'matched')
        self.assertIsNone(report['sport_key'])

    def test_same_name_and_id_in_two_current_leagues_stays_ambiguous(self):
        frames = {sport: provider_history() for sport in DEFINITIONS}
        report = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF,
                                          DEFINITIONS)['Ipswich Town']
        self.assertEqual(report['status'], 'ambiguous')
        self.assertIsNone(report['sport_key'])

    def test_score_provider_with_complete_form_cannot_claim_xg_available(self):
        frame = provider_history()
        games, form = team_history(frame, 'Ipswich Town', KICKOFF, 'home')
        self.assertEqual(form['status'], 'ok')
        self.assertEqual(len(games), 10)
        diagnostic = xg_diagnostics(frame, 'Ipswich Town', 'Nottingham Forest', KICKOFF)
        self.assertEqual(diagnostic['status'], 'missing_columns')
        self.assertEqual(diagnostic['home']['sample'], 0)
        self.assertEqual(diagnostic['away']['sample'], 0)
        self.assertEqual(diagnostic['home']['status'], 'source_unavailable')
        self.assertEqual(diagnostic['away']['status'], 'source_unavailable')


if __name__ == '__main__':
    unittest.main()
