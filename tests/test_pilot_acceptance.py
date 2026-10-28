"""Count observable offline pilot outcomes, never imply live provider coverage.

Existing fixture builders exercise extracted real scan functions against disposable
SQLite. Imported modules stay modules, avoiding duplicate unittest discovery of
their TestCase classes. No football provider or paid AI endpoint is contacted.
"""
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

import test_issue9_scan as issue9
import test_ucl_scan as ucl


class PilotAcceptanceTests(unittest.TestCase):
    def scenario(self, case_type, method):
        case = case_type(methodName=method)
        case.setUp()
        self.addCleanup(case.doCleanups)
        return case

    def summary(self, case, rows):
        analyses = [(row, json.loads(row['analysis_a_json']),
                     json.loads(row['analysis_b_json'])) for row in rows]
        row, a, b = analyses[0]
        saved = a['scan_diagnostics']
        self.assertEqual(saved['analyzed_fixtures'], 1)
        self.assertEqual(saved['teams_checked'], 2)
        self.assertEqual(saved['teams_with_form5'], 2)
        for _, analysis_a, _ in analyses:
            self.assertEqual(analysis_a['scan_diagnostics'], saved)
            self.assertEqual(analysis_a['home_form']['sample5'], 5)
            self.assertEqual(analysis_a['away_form']['sample5'], 5)
        case.f['fetch_odds'].assert_called_once()
        return {
            'data_mode': 'synthetic_test_fixture', 'sport_key': saved['sport_key'],
            'event_id': row['event_id'], 'home_team': row['home_team'],
            'away_team': row['away_team'],
            'api_returned': saved['api_returned'],
            'passed_filter': saved['passed_filter'],
            'analyzed_fixtures': saved['analyzed_fixtures'],
            'teams_with_form5': saved['teams_with_form5'],
            'a_available_fixtures': saved['a_available_fixtures'],
            'b_available_fixtures': saved['b_available_fixtures'],
            'home_sample10': a['home_form']['sample10'],
            'away_sample10': a['away_form']['sample10'],
            'b_available': b.get('available') is True,
            'mock_odds_calls': case.f['fetch_odds'].call_count,
            'real_provider_calls': 0,
        }

    def test_three_mock_fixtures_six_teams_a_three_b_two_with_cup_boundary(self):
        scenarios = []
        for english in (False, True):
            case = self.scenario(issue9.Issue9ScanTests,
                'test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form')
            case.prepare(english=english)
            case.event['id'] = 'pilot-english-synthetic' if english else 'pilot-german-synthetic'
            rows, _, _, _ = case.scan()
            scenarios.append(self.summary(case, rows))
            self.assertTrue(all(row['ev_pct'] is not None for row in rows))
        cup = self.scenario(ucl.UCLScanTests,
            'test_bayern_arsenal_uses_two_domestic_sources_and_preserves_model_boundary')
        cup.prepare()
        cup_rows, _, _, _ = cup.scan()
        scenarios.append(self.summary(cup, cup_rows))
        self.assertTrue(all(row['ev_pct'] is None for row in cup_rows))
        for row in cup_rows:
            self.assertEqual(json.loads(row['analysis_b_json'])['scope'], 'domestic_context_only')
        totals = {
            key: sum(scenario[key] for scenario in scenarios)
            for key in ('api_returned', 'passed_filter', 'analyzed_fixtures',
                        'teams_with_form5', 'a_available_fixtures', 'b_available_fixtures',
                        'mock_odds_calls', 'real_provider_calls')
        }
        totals['unique_teams'] = len({scenario[side] for scenario in scenarios
                                     for side in ('home_team', 'away_team')})
        self.assertEqual(totals, {
            'api_returned': 3, 'passed_filter': 3, 'analyzed_fixtures': 3,
            'teams_with_form5': 6, 'a_available_fixtures': 3, 'b_available_fixtures': 2,
            'mock_odds_calls': 3, 'real_provider_calls': 0, 'unique_teams': 6,
        })
        report = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'data_mode': 'synthetic_test_fixture',
            'scope': 'Offline regression acceptance only; not live fixtures, source availability, calibration or ROI.',
            'live_scan': {'performed': False, 'usable_form_teams': None,
                          'analyzed_fixtures': None, 'reason': 'No paid/provider API requested in tests.'},
            'scenarios': scenarios, 'totals': totals,
        }
        artifact_directory = os.environ.get('DASHBOARD_SCREENSHOTS')
        if artifact_directory:
            target = Path(artifact_directory)
            target.mkdir(parents=True, exist_ok=True)
            (target / 'pilot-offline-acceptance.json').write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_complete_mock_form_does_not_supply_b_when_understat_is_blocked(self):
        case = self.scenario(issue9.Issue9ScanTests,
            'test_blocked_understat_preserves_a_and_precise_no_bet_reason')
        case.prepare(blocked=True)
        rows, _, _, _ = case.scan()
        scenario = self.summary(case, rows)
        self.assertEqual(scenario['teams_with_form5'], 2)
        self.assertEqual(scenario['a_available_fixtures'], 1)
        self.assertEqual(scenario['b_available_fixtures'], 0)
        self.assertFalse(scenario['b_available'])
        self.assertTrue(all(row['ev_pct'] is None for row in rows))


if __name__ == '__main__':
    unittest.main()
