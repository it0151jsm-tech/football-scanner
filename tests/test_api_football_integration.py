"""Actual app fallback/cache boundaries with synthetic responses and temporary SQLite.

No source request escapes a Mock. Sample goals and IDs are test data, never an
assertion about a real team's results or cross-provider identity.
"""
import ast
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import test_regressions as regression
from api_football_data import ApiFootballUnavailable, fetch_finished_fixtures

UTC = timezone.utc
KEY = 'SYNTHETIC_API_FOOTBALL_INTEGRATION_KEY'
MATCH = {'home_team': 'Arsenal', 'away_team': 'Chelsea'}


class ApiFootballIntegrationTests(unittest.TestCase):
    setUp = regression.Regressions.setUp

    def configure(self, *, payload=None, http=200):
        self.now = datetime.now(UTC)
        self.get = Mock(return_value=SimpleNamespace(status_code=http, json=Mock(return_value=payload or {'response': []})))
        self.f.update(requests=SimpleNamespace(get=self.get), HISTORY_TIMEOUT=(1, 1))
        self.key_patch = patch.dict(os.environ, {'API_FOOTBALL_KEY': KEY})
        self.key_patch.start()
        self.addCleanup(self.key_patch.stop)
        return self

    def fallback(self, frame=None, matches=None, sport='soccer_epl'):
        return self.f['optional_api_football_fallback'](
            sport, pd.DataFrame() if frame is None else frame,
            [dict(MATCH, commence_time=(self.now + timedelta(hours=3)).isoformat())] if matches is None else matches, self.now)

    def rows(self):
        season = self.now.year if self.now.month >= 7 else self.now.year - 1
        out = []
        for index in range(10):
            when = self.now.replace(hour=18, minute=0, second=0, microsecond=0) - timedelta(days=2 + index * 2)
            out.append({
                'fixture': {'id': index + 1, 'timestamp': int(when.timestamp()),
                            'date': when.isoformat(), 'status': {'short': 'FT'}},
                'league': {'id': 39, 'season': season},
                'teams': {'home': {'id': 42, 'name': 'Arsenal'},
                          'away': {'id': 49, 'name': 'Chelsea'}},
                'goals': {'home': 2, 'away': 1},
            })
        return out

    def valid_candidate(self):
        season = self.now.year if self.now.month >= 7 else self.now.year - 1
        response = SimpleNamespace(status_code=200, json=Mock(return_value={'response': self.rows()}))
        return fetch_finished_fixtures('soccer_epl', season, KEY, Mock(return_value=response))

    def assert_not_called(self, frame, reason):
        report = frame.attrs['api_football_diagnostics']
        self.assertEqual(report['state'], 'not_called')
        self.assertEqual(report['reason'], reason)
        self.assertFalse(report['request_attempted'])
        self.assertFalse(report['used_for_form'])
        self.get.assert_not_called()

    def test_csv_name_ready_preserves_existing_frame_and_never_requests(self):
        self.configure()
        csv, _ = self.valid_candidate()
        csv.attrs.clear()
        csv.attrs['history_diagnostics'] = {'status': 'ok', 'provider': 'football-data'}
        result = self.fallback(csv)
        self.assert_not_called(result, 'csv_sufficient')
        pd.testing.assert_frame_equal(result, csv)
        self.assertEqual(result.attrs['history_diagnostics'], csv.attrs['history_diagnostics'])
        self.assertNotIn('api_football_diagnostics', csv.attrs)  # input is not mutated

    def test_old_roster_or_insufficient_csv_does_not_hide_fallback(self):
        self.configure()
        original, _ = self.valid_candidate()
        for kind in ('old_roster', 'insufficient'):
            with self.subTest(kind=kind):
                csv = original.copy()
                if kind == 'old_roster':
                    csv['_history_season'] = '2324'
                else:
                    csv = csv.tail(4).copy()
                self.get.reset_mock()
                with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_BACKOFF_REVISION': f'independent-csv-{kind}'}):
                    returned = self.fallback(csv)
                self.get.assert_called_once()
                self.assertEqual(returned.attrs['api_football_diagnostics']['state'], 'empty')
                self.assertFalse(returned.attrs['api_football_diagnostics']['used_for_form'])
                pd.testing.assert_frame_equal(returned, csv)

    def test_missing_key_is_visible_without_network(self):
        self.configure()
        with patch.dict(os.environ, {'API_FOOTBALL_KEY': ''}):
            result = self.fallback()
        self.assert_not_called(result, 'not_configured')

    def test_unsupported_league_never_requests(self):
        self.configure()
        self.assert_not_called(self.fallback(sport='soccer_uefa_champs_league'), 'unsupported_league')

    def test_no_filtered_fixtures_never_requests(self):
        self.configure()
        self.assert_not_called(self.fallback(matches=[]), 'no_fixtures')

    def test_cached_http_failure_retains_provider_state_and_original_receipt(self):
        self.configure()
        receipt = (self.now - timedelta(hours=1)).isoformat()
        diagnostic = {'provider': 'api-football', 'status': 'http_error', 'state': 'http_error',
                      'http_status': 403, 'received_at': receipt, 'request_attempted': True}
        self.f['fetch_api_football_with_diagnostics'] = Mock(return_value=(pd.DataFrame(), diagnostic))
        result = self.fallback()
        report = result.attrs['api_football_diagnostics']
        self.assertEqual(report['state'], 'cache')
        self.assertEqual(report['provider_state'], 'http_error')
        self.assertTrue(report['cache_hit'])
        self.assertEqual(report['http_status'], 403)
        self.assertEqual(report['received_at'], receipt)
        self.assertFalse(report['used_for_form'])
        self.assertNotIn('cache_hit', diagnostic)  # cached evidence was not mutated
        self.get.assert_not_called()

    def test_cached_transport_failure_uses_original_requested_at(self):
        self.configure()
        requested = (self.now - timedelta(hours=1)).isoformat()
        diagnostic = {'status': 'download_error', 'state': 'download_error',
                      'requested_at': requested, 'request_attempted': True}
        self.f['fetch_api_football_with_diagnostics'] = Mock(return_value=(pd.DataFrame(), diagnostic))
        report = self.fallback().attrs['api_football_diagnostics']
        self.assertTrue(report['cache_hit'])
        self.assertEqual(report['provider_state'], 'download_error')
        self.assertEqual(report['requested_at'], requested)
        self.assertNotIn('received_at', report)

    def test_actual_http_403_is_preserved_after_empty_fallback(self):
        self.configure(http=403)
        report = self.fallback().attrs['api_football_diagnostics']
        self.assertEqual(report['state'], 'http_error')
        self.assertEqual(report['http_status'], 403)
        self.assertFalse(report['used_for_form'])
        self.assertIn('received_at', report)
        self.assertNotIn(KEY, json.dumps(report))
        self.get.assert_called_once()
        self.get.return_value.json.assert_not_called()

    def test_actual_http_200_plan_restriction_is_not_hidden(self):
        self.configure(payload={'errors': {'requests': 'Free plans do not have access to this season ' + KEY}, 'response': []})
        report = self.fallback().attrs['api_football_diagnostics']
        self.assertEqual(report['state'], 'plan_unsupported')
        self.assertEqual(report['http_status'], 200)
        self.assertFalse(report['used_for_form'])
        self.assertNotIn(KEY, json.dumps(report))
        self.get.assert_called_once()

    def test_empty_data_status_is_retained(self):
        self.configure(payload={'response': []})
        result = self.fallback()
        self.assertTrue(result.empty)
        report = result.attrs['api_football_diagnostics']
        self.assertEqual(report['state'], 'empty')
        self.assertEqual(report['provider_results'], 0)
        self.assertFalse(report['used_for_form'])

    def test_missing_verification_columns_cannot_be_used(self):
        self.configure()
        candidate, diagnostic = self.valid_candidate()
        candidate = candidate.drop(columns=['_provider_league_verified', '_provider_identity_verified'])
        self.f['fetch_api_football_with_diagnostics'] = Mock(return_value=(candidate, diagnostic))
        result = self.fallback()
        self.assertTrue(result.empty)
        report = result.attrs['api_football_diagnostics']
        self.assertEqual(report['reason'], 'provider_metadata_unverified')
        self.assertFalse(report['used_for_form'])

    def test_unverified_league_or_ids_cannot_be_used(self):
        self.configure()
        for column in ('_provider_league_verified', '_provider_identity_verified'):
            with self.subTest(column=column):
                candidate, diagnostic = self.valid_candidate()
                candidate[column] = False
                self.f['fetch_api_football_with_diagnostics'] = Mock(return_value=(candidate, diagnostic))
                result = self.fallback()
                self.assertTrue(result.empty)
                self.assertEqual(result.attrs['api_football_diagnostics']['reason'], 'provider_metadata_unverified')
                self.assertFalse(result.attrs['api_football_diagnostics']['used_for_form'])

    def test_provider_name_mismatch_is_blocked_without_fuzzy_matching(self):
        self.configure()
        candidate, diagnostic = self.valid_candidate()
        candidate['HomeTeam'] = 'Arsenal U21'
        self.f['fetch_api_football_with_diagnostics'] = Mock(return_value=(candidate, diagnostic))
        result = self.fallback()
        self.assertTrue(result.empty)
        report = result.attrs['api_football_diagnostics']
        self.assertEqual(report['reason'], 'provider_names_unmatched')
        self.assertFalse(report['used_for_form'])

    def test_verified_provider_frame_can_recover_form_without_xg(self):
        self.configure()
        self.get.return_value.json.return_value = {'response': self.rows()}
        result = self.fallback()
        self.assertEqual(len(result), 10)
        self.assertTrue(result.attrs['api_football_diagnostics']['used_for_form'])
        self.assertEqual(result.attrs['history_diagnostics']['fallback_from'], 'football-data')
        kickoff = self.now + timedelta(hours=3)
        home = self.f['team_form_profile'](result, 'Arsenal', kickoff, venue='home')
        away = self.f['team_form_profile'](result, 'Chelsea', kickoff, venue='away')
        self.assertEqual((home['sample5'], home['sample10']), (5, 10))
        self.assertEqual((away['sample5'], away['sample10']), (5, 10))
        self.assertFalse(any('xg' in column.lower() for column in result.columns))

    def test_actual_scan_recovers_a_but_keeps_b_ev_unavailable_without_understat(self):
        self.configure()
        self.get.return_value.json.return_value = {'response': self.rows()}
        kickoff = self.now + timedelta(hours=3)
        event = dict(id='synthetic-api-football-integration', sport_key='soccer_epl',
            home_team='Arsenal', away_team='Chelsea', commence_time=kickoff.isoformat(),
            _received_at=self.now.isoformat(), bookmakers=[dict(key='synthetic', title='Synthetic fixture', markets=[
                dict(key='totals', last_update=self.now.isoformat(), outcomes=[
                    dict(name='Over', point=2.5, price=1.95), dict(name='Under', point=2.5, price=1.95)])])])
        unavailable = pd.DataFrame()
        unavailable.attrs['xg_diagnostics'] = {'status': 'http_error', 'sources': [
            {'season': 2026, 'http_status': 403, 'status': 'http_error', 'received_at': self.now.isoformat()}]}
        self.f.update(fetch_odds=Mock(return_value=([event], '100', '2')),
                      fetch_history=Mock(return_value=pd.DataFrame()),
                      fetch_understat_history=Mock(return_value=unavailable))
        league, code = self.f['LEAGUES']['soccer_epl']
        rows, _, _, _ = self.f['scan_league']('soccer_epl', league, code, 24, 1.8, 2.15, 'uk', 'SYNTHETIC_NO_ODDS_CALL')
        self.assertEqual(len(rows), 2)
        for row in rows:
            a, b = json.loads(row['analysis_a_json']), json.loads(row['analysis_b_json'])
            self.assertEqual(a['home_form']['sample5'], 5)
            self.assertEqual(a['away_form']['sample5'], 5)
            self.assertIsNotNone(a['score'])
            report = a['home_form']['diagnostics']['api_football']
            self.assertTrue(report['used_for_form'])
            self.assertEqual(report['provider_state'], 'success')
            self.assertFalse(b['available'])
            self.assertIsNone(row['ev_pct'])
            self.assertIsNone(row['lambda_home'])
            self.assertIsNone(row['lambda_away'])
        self.get.assert_called_once()

    def test_success_boundary_returns_frame_and_rejects_failures_from_long_cache(self):
        self.configure()
        candidate, diagnostic = self.valid_candidate()
        with patch('api_football_data.fetch_finished_fixtures', return_value=(candidate, diagnostic)):
            returned, report = self.f['fetch_api_football_form']('soccer_epl', 2026, KEY)
            self.assertIs(returned, candidate)
            self.assertIs(report, diagnostic)
        for case_index, failure in enumerate(({'status': 'http_error', 'state': 'http_error', 'http_status': 403},
                        {'status': 'provider_error', 'state': 'plan_unsupported'},
                        {'status': 'empty_history', 'state': 'empty'},
                        {'status': 'ok', 'state': 'success', 'rejected_rows': 1})):
            with self.subTest(failure=failure):
                with patch('api_football_data.fetch_finished_fixtures', return_value=(candidate, failure)):
                    # Independent provider attempts keep this boundary test from
                    # reusing the deliberate cooldown tested in the backoff suite.
                    with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_BACKOFF_REVISION': f'inner-boundary-{case_index}'}):
                        with self.assertRaises(ApiFootballUnavailable) as caught:
                            self.f['fetch_api_football_form']('soccer_epl', 2026, KEY)
                    self.assertIs(caught.exception.frame, candidate)
                    with patch.dict(os.environ, {'VALUE_BET_API_FOOTBALL_BACKOFF_REVISION': f'outer-boundary-{case_index}'}):
                        returned, report = self.f['fetch_api_football_with_diagnostics']('soccer_epl', 2026, KEY)
                    self.assertIs(returned, candidate)
                    self.assertEqual(report, failure)

    def test_cache_ttls_keep_success_six_hours_and_failure_fifteen_minutes(self):
        tree = ast.parse((Path(__file__).resolve().parents[1] / 'app.py').read_text())
        expected = {'fetch_api_football_form': 21600, 'fetch_api_football_with_diagnostics': 900}
        found = {}
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name in expected:
                found[node.name] = next(ast.literal_eval(keyword.value)
                    for decorator in node.decorator_list for keyword in decorator.keywords if keyword.arg == 'ttl')
        self.assertEqual(found, expected)


if __name__ == '__main__':
    unittest.main()
