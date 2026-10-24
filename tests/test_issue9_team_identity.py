"""Issue #9 identities: reviewed label evidence, synthetic scores, no network."""
import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from domestic_data import resolve_domestic_sources, safe_match_team
from history_data import alias_evidence, load_history, match_team, team_history


ROOT = Path(__file__).resolve().parents[1]
KICKOFF = datetime(2026, 10, 23, 19, tzinfo=timezone.utc)
LEAGUES = {
    'soccer_epl': {'csv_code': 'E0', 'label': 'Premier League', 'understat': 'EPL'},
    'soccer_germany_bundesliga': {'csv_code': 'D1', 'label': 'Bundesliga', 'understat': 'Bundesliga'},
    'soccer_efl_champ': {'csv_code': 'E1', 'label': 'Championship', 'understat': None},
}
IDENTITIES = (
    ('VfB Stuttgart', 'Stuttgart', 'D1'),
    ('Borussia Monchengladbach', "M'gladbach", 'D1'),
    ('Ipswich Town', 'Ipswich', 'E0'),
    ('Nottingham Forest', "Nott'm Forest", 'E0'),
)


def history(home='Stuttgart', away="M'gladbach", season='2627', dates=None):
    dates = dates or [f'2026-10-{day:02}' for day in range(1, 11)]
    frame = pd.DataFrame([
        dict(Date_dt=pd.Timestamp(day), HomeTeam=home, AwayTeam=away,
             FTHG=index % 3, FTAG=(index + 1) % 3, _history_season=season)
        for index, day in enumerate(dates)
    ])
    frame.attrs['history_diagnostics'] = {
        'status': 'ok', 'sources': [{'url': 'https://example.invalid/synthetic.csv',
                                   'season': season, 'status': 'ok'}]}
    return frame


def full_scope():
    return {
        'soccer_epl': history('Ipswich', "Nott'm Forest"),
        'soccer_germany_bundesliga': history(),
        'soccer_efl_champ': history('Other Championship Club', 'Other Championship Opponent'),
    }


class Issue9TeamIdentityTests(unittest.TestCase):
    def test_all_four_reported_labels_resolve_exact_reviewed_csv_names(self):
        for api_name, csv_name, _ in IDENTITIES:
            with self.subTest(team=api_name):
                self.assertEqual(match_team(api_name, [csv_name]), (csv_name, 'verified_alias'))
                self.assertEqual(match_team(csv_name, [api_name]), (api_name, 'verified_alias'))

    def test_reviewed_understat_labels_resolve_to_same_csv_club(self):
        for understat, csv_name in [('VfB Stuttgart', 'Stuttgart'),
                                    ('Borussia M.Gladbach', "M'gladbach"),
                                    ('Nottingham Forest', "Nott'm Forest")]:
            with self.subTest(team=understat):
                self.assertEqual(safe_match_team(understat, [csv_name]), (csv_name, 'verified_alias'))
        self.assertEqual(match_team('Borussia Monchengladbach', ['Borussia M.Gladbach']),
                         ('Borussia M.Gladbach', 'verified_alias'))

    def test_case_and_punctuation_normalization_never_removes_team_suffixes(self):
        self.assertEqual(match_team('vfb STUTTGART', ['VfB Stuttgart']), ('VfB Stuttgart', 'exact'))
        self.assertEqual(match_team('Borussia M Gladbach', ['Borussia M.Gladbach']),
                         ('Borussia M.Gladbach', 'exact'))
        for suffix in (' II', ' U21', ' U23', ' Women', ' Reserves'):
            for api_name, csv_name, _ in IDENTITIES:
                with self.subTest(team=api_name, suffix=suffix):
                    self.assertIsNone(match_team(api_name, [csv_name + suffix])[0])
                    self.assertIsNone(match_team(api_name + suffix, [csv_name])[0])

    def test_similar_other_clubs_are_not_aliases(self):
        for name, candidates in [('VfB Stuttgart', ['Stuttgarter Kickers']),
                                 ('Borussia Monchengladbach', ['Borussia Dortmund']),
                                 ('Ipswich Town', ['Ipswich Wanderers']),
                                 ('Nottingham Forest', ['Notts County'])]:
            with self.subTest(team=name):
                self.assertEqual(match_team(name, candidates), (None, 'name_unmatched'))

    def test_exact_plus_alias_in_one_roster_is_ambiguous_in_both_orders(self):
        for api_name, csv_name, _ in IDENTITIES:
            for candidates in ([api_name, csv_name], [csv_name, api_name]):
                with self.subTest(team=api_name, candidates=candidates):
                    self.assertEqual(match_team(api_name, candidates), (None, 'name_ambiguous'))
                    self.assertEqual(match_team(csv_name, candidates), (None, 'name_ambiguous'))

    def test_distinct_case_or_spacing_labels_are_ambiguous_but_repeat_rows_are_not(self):
        self.assertEqual(match_team('VfB Stuttgart', ['Stuttgart', 'STUTTGART']),
                         (None, 'name_ambiguous'))
        self.assertEqual(match_team('Ipswich Town', ['Ipswich', 'Ipswich ']),
                         (None, 'name_ambiguous'))
        self.assertEqual(match_team('Ipswich Town', ['Ipswich', 'Ipswich']),
                         ('Ipswich', 'verified_alias'))

    def test_lossy_unicode_collisions_fail_in_direct_and_domestic_matchers(self):
        for name, candidate in [('Málaga', 'Mlaga'), ('Stuttgárt', 'Stuttgrt'),
                                ('Nottinghám Forest', 'Nottinghm Forest')]:
            with self.subTest(name=name):
                self.assertEqual(match_team(name, [candidate]), (None, 'lossy_normalization'))
                self.assertEqual(safe_match_team(name, [candidate]), (None, 'lossy_normalization'))

    def test_unreviewed_accent_variants_cannot_enter_reviewed_alias_group(self):
        for name, candidates in [('Borussia Mönchengladbach', ["M'gladbach"]),
                                 ('VfB Stuttgarté', ['Stuttgart']),
                                 ('Nottingham Foresté', ["Nott'm Forest"])]:
            with self.subTest(name=name):
                self.assertIsNone(match_team(name, candidates)[0])
                self.assertIsNone(alias_evidence(name, candidates[0]))

    def test_unicode_exact_identity_is_valid_without_transliterating_another_name(self):
        self.assertEqual(match_team('Málaga', ['Málaga']), ('Málaga', 'exact'))
        self.assertEqual(match_team('Ma\u0301laga', ['Málaga']), ('Málaga', 'exact'))
        self.assertIsNone(match_team('Malaga', ['Málaga'])[0])

    def test_all_aliases_have_real_label_evidence_without_fabricated_odds_response(self):
        evidence = json.loads((ROOT / 'fixtures/team_identity_evidence.json').read_text())
        self.assertEqual(evidence['api_label_evidence']['kind'], 'user-reported API fixture labels')
        self.assertIn('not a new Odds API request', evidence['scope'])
        for api_name, csv_name, code in IDENTITIES:
            with self.subTest(team=api_name):
                self.assertIn(api_name, evidence['api_label_evidence']['labels'])
                source = next(s for s in evidence['csv_label_evidence'] if s['code'] == code)
                self.assertIn(csv_name, source['labels'])
                self.assertEqual(source['http_status'], 200)
                self.assertEqual(source['season'], '2627')
                self.assertRegex(source['sha256'], r'^[a-f0-9]{64}$')
                self.assertIsNotNone(datetime.fromisoformat(source['received_at']).tzinfo)
                record = alias_evidence(api_name, csv_name)
                self.assertEqual(record['csv_source'], source['url'])
                self.assertEqual(record['api_evidence'], evidence['api_label_evidence']['url'])

    def test_understat_evidence_reports_scope_and_does_not_invent_ipswich_coverage(self):
        evidence = json.loads((ROOT / 'fixtures/team_identity_evidence.json').read_text())
        audit = json.loads((ROOT / 'reports/league-source-audit.json').read_text())
        seen = {r['understat_name'] for league in audit['leagues']
                for r in league.get('cross_source_results', [])}
        self.assertTrue(set(evidence['understat_label_evidence']['labels']) <= seen)
        self.assertNotIn('understat_evidence', alias_evidence('Ipswich Town', 'Ipswich'))
        self.assertIsNone(alias_evidence('Borussia Dortmund', "M'gladbach"))

    def test_history_diagnostics_use_club_specific_evidence_not_oxford_e2(self):
        for api_name, csv_name, code in IDENTITIES:
            frame = history(csv_name, 'Synthetic Opponent')
            games, report = team_history(frame, api_name, KICKOFF, 'home')
            with self.subTest(team=api_name):
                self.assertEqual(len(games), 10)
                self.assertEqual(report['status'], 'ok')
                self.assertEqual(report['mapping'], 'verified_alias')
                self.assertEqual(report['matched_name'], csv_name)
                self.assertTrue(report['alias_source'].endswith(f'/2627/{code}.csv'))
                self.assertNotIn('/E2.csv', report['alias_source'])
                self.assertEqual(report['alias_evidence']['api_evidence'],
                                 'https://github.com/it0151jsm-tech/football-scanner/issues/9')
                self.assertNotIn('xG_home', games)

    def test_original_reviewed_aliases_still_have_original_evidence(self):
        for api_name, csv_name in [('Oxford United', 'Oxford'), ('Wigan Athletic', 'Wigan')]:
            games, report = team_history(history(csv_name, 'Opponent'), api_name, KICKOFF, 'home')
            self.assertEqual(len(games), 10)
            self.assertTrue(report['alias_source'].endswith('/2627/E2.csv'))

    def test_history_excludes_same_day_and_future_after_alias_matching(self):
        frame = history('Stuttgart', "M'gladbach",
                        dates=['2026-10-22', '2026-10-23', '2026-10-24'])
        games, report = team_history(frame, 'VfB Stuttgart', KICKOFF, 'home')
        self.assertEqual(games.Date_dt.dt.strftime('%Y-%m-%d').tolist(), ['2026-10-22'])
        self.assertEqual(report['status'], 'insufficient_history')
        self.assertEqual(report['venue_before_count'], 1)

    def test_source_failure_remains_distinct_from_identity_failure(self):
        get = Mock(side_effect=TimeoutError('offline test only'))
        frame = load_history('D1', get, seasons=('2627',))
        _, report = team_history(frame, 'VfB Stuttgart', KICKOFF, 'home')
        self.assertEqual(report['status'], 'download_error')
        self.assertEqual(report['source']['sources'][0]['csv_code'], 'D1')
        self.assertIsNone(report['matched_name'])
        get.assert_called_once()

    def test_four_clubs_resolve_only_from_current_season_roster_evidence(self):
        output = resolve_domestic_sources([item[0] for item in IDENTITIES], full_scope(), KICKOFF, LEAGUES)
        for api_name, csv_name, code in IDENTITIES:
            with self.subTest(team=api_name):
                self.assertEqual(output[api_name]['status'], 'matched')
                self.assertEqual(output[api_name]['csv_name'], csv_name)
                self.assertEqual(output[api_name]['csv_code'], code)
                self.assertEqual(output[api_name]['source_season'], '2627')

    def test_old_promoted_or_relegated_membership_never_proves_current_league(self):
        frames = full_scope()
        frames['soccer_epl'] = history('Ipswich', "Nott'm Forest", '2526', ['2026-05-01'])
        result = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF, LEAGUES)['Ipswich Town']
        self.assertEqual(result['status'], 'stale')
        self.assertIsNone(result['sport_key'])

    def test_verified_alias_does_not_force_old_league_over_current_membership(self):
        frames = full_scope()
        frames['soccer_epl'] = history('Ipswich', 'Past EPL Opponent', '2526', ['2026-05-01'])
        frames['soccer_efl_champ'] = history('Ipswich', 'Current Championship Opponent')
        result = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF, LEAGUES)['Ipswich Town']
        self.assertEqual(result['status'], 'matched')
        self.assertEqual(result['sport_key'], 'soccer_efl_champ')
        self.assertEqual(result['csv_code'], 'E1')

    def test_multiple_current_leagues_remain_ambiguous_after_alias_recovery(self):
        frames = full_scope()
        frames['soccer_efl_champ'] = history('Ipswich', 'Current Championship Opponent')
        result = resolve_domestic_sources(['Ipswich Town'], frames, KICKOFF, LEAGUES)['Ipswich Town']
        self.assertEqual(result['status'], 'ambiguous')
        self.assertIsNone(result['sport_key'])

    def test_alias_cannot_fill_missing_understat_or_create_a_probability(self):
        frame = history('Ipswich', "Nott'm Forest")
        original = frame.copy(deep=True)
        games, report = team_history(frame, 'Ipswich Town', KICKOFF, 'home')
        self.assertEqual(report['status'], 'ok')
        pd.testing.assert_frame_equal(frame, original)
        self.assertTrue({'xG_home', 'xG_away', 'ev_pct', 'probability'}.isdisjoint(games.columns))


if __name__ == '__main__':
    unittest.main()
