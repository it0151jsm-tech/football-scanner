"""Issue15 form availability evidence, separate from unchanged A/C formulas.

Every row is synthetic. No network, production database or reported live pair
identity is used; the issue supplied aggregate counts rather than raw fixtures.
"""
import ast
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import pandas as pd

from form_readiness import form_data_readiness
from market_recommendations import assess, recommended_pick_ids
from source_diagnostics import annotate_form_source, csv_form_ready, describe_history_cache, model_data_readiness
from test_market_recommendations import quote
from test_regressions import load_functions


ROOT = Path(__file__).resolve().parents[1]
KICKOFF = datetime(2026, 10, 24, 19, tzinfo=timezone.utc)
HOME, AWAY = 'Verified Home', 'Verified Away'


def history(dates=None, *, season='2627', home=HOME, away=AWAY):
    dates = dates if dates is not None else ['2026-10-01', '2026-10-03', '2026-10-05', '2026-10-07', '2026-10-09']
    frame = pd.DataFrame([
        dict(Date_dt=pd.Timestamp(day), HomeTeam=home, AwayTeam=away,
             FTHG=3, FTAG=0, _history_season=season)
        for day in dates
    ])
    frame.attrs['history_diagnostics'] = dict(provider='Football-Data', status='ok', sources=[{
        'status': 'ok', 'http_status': 200, 'season': season,
        'url': 'https://example.invalid/synthetic-score-fixture.csv',
        'received_at': '2026-10-24T12:00:00+00:00', 'valid_rows': len(frame),
    }])
    return frame


class Issue15FormReadinessTests(unittest.TestCase):
    def setUp(self):
        no_http = patch('requests.sessions.Session.request', side_effect=AssertionError('Issue15 tests forbid HTTP'))
        self.http = no_http.start()
        self.addCleanup(no_http.stop)

    def tearDown(self):
        self.http.assert_not_called()

    def test_current_five_are_actual_pre_cutoff_venue_games_with_explicit_source(self):
        report = form_data_readiness(history(), HOME, KICKOFF, 'home')
        self.assertEqual((report['current_season_venue_count'], report['prior_season_venue_count'],
                          report['full_history_venue_count']), (5, 0, 5))
        self.assertEqual(report['status'], 'available')
        self.assertEqual(report['sample5_dates'], ['2026-10-01', '2026-10-03', '2026-10-05', '2026-10-07', '2026-10-09'])
        self.assertIn('ฤดูกาล 2627 5 นัด', report['summary'])
        self.assertEqual(report['source']['sources'][0]['received_at'], '2026-10-24T12:00:00+00:00')
        self.assertEqual(report['provider'], 'Football-Data')

    def test_mixed_season_five_never_become_five_current_season_games(self):
        prior = history(['2026-06-21', '2026-06-23', '2026-06-25'], season='2526')
        current = history(['2026-10-01', '2026-10-03'])
        frame = pd.concat([prior, current], ignore_index=True)
        report = form_data_readiness(frame, HOME, KICKOFF, 'home')
        self.assertEqual((report['current_season_venue_count'], report['prior_season_venue_count'],
                          report['full_history_venue_count']), (2, 3, 5))
        self.assertEqual(report['status'], 'available')
        self.assertIn('ฤดูกาล 2627 2 นัด', report['summary'])
        self.assertIn('ฤดูกาลก่อน 3 นัด', report['summary'])
        self.assertNotIn('ฤดูกาล 2627 5 นัด', report['summary'])
        self.assertIn('อาจข้ามฤดูกาล', report['summary'])

    def test_prior_five_without_current_membership_remain_explicit_prior_context(self):
        frame = history(['2026-06-21', '2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25'], season='2526')
        report = form_data_readiness(frame, HOME, KICKOFF, 'home')
        self.assertEqual((report['current_season_team_count'], report['current_season_venue_count'],
                          report['prior_season_venue_count']), (0, 0, 5))
        self.assertIn('no_current_season_results', report['reason_codes'])
        self.assertIn('ฤดูกาล 2627 0 นัด', report['summary'])
        self.assertIn('ฤดูกาลก่อน 5 นัด', report['summary'])
        fixture = dict(home_team=HOME, away_team=AWAY, commence_time=KICKOFF.isoformat())
        self.assertFalse(csv_form_ready(frame, [fixture]))

    def test_july_rollover_relabels_actual_games_without_importing_future_results(self):
        frame = history(['2026-06-21', '2026-06-22', '2026-06-23', '2026-06-24', '2026-06-25'], season='2526')
        before = form_data_readiness(frame, HOME, '2026-06-30T19:00:00+00:00', 'home')
        after = form_data_readiness(frame, HOME, '2026-07-01T19:00:00+00:00', 'home')
        self.assertEqual((before['requested_season'], before['current_season_venue_count'], before['prior_season_venue_count']), ('2526', 5, 0))
        self.assertEqual((after['requested_season'], after['current_season_venue_count'], after['prior_season_venue_count']), ('2627', 0, 5))
        self.assertEqual(before['sample5_dates'], after['sample5_dates'])
        self.assertIn('no_current_season_results', after['reason_codes'])

    def test_three_real_observations_can_score_a_but_do_not_qualify_c_as_value_bet(self):
        frame = history(['2026-10-01', '2026-10-03', '2026-10-05'])
        functions = load_functions(ROOT / 'app.py')
        form5 = functions['estimate_form_signal'](frame, HOME, AWAY, KICKOFF, 'spreads', 'home', -.25, window=5)
        form10 = functions['estimate_form_signal'](frame, HOME, AWAY, KICKOFF, 'spreads', 'home', -.25, window=10)
        form_all = functions['estimate_form_signal'](frame, HOME, AWAY, KICKOFF, 'spreads', 'home', -.25, window=None)
        home = functions['team_form_profile'](frame, HOME, KICKOFF, 'home')
        away = functions['team_form_profile'](frame, AWAY, KICKOFF, 'away')
        a = functions['system_a_analysis'](form5, form10, form_all, home, away, {}, {}, {})
        self.assertEqual((form5['form_sample'], home['sample5'], away['sample5']), (3, 3, 3))
        # Three wins produce genuine evidence; the existing formula caps a
        # sample below five at 52 instead of suppressing its score entirely.
        self.assertEqual(form5['form_rate'], 100)
        self.assertEqual(a['score'], 52)
        report = form_data_readiness(frame, HOME, KICKOFF, 'home')
        self.assertEqual(report['status'], 'limited')
        self.assertIn('insufficient_venue_samples', report['reason_codes'])
        self.assertFalse(report['a_scoring_gate'])
        row = quote('Asian Handicap', -.25, 'home', odds=4.0)
        row.update(analysis_a_json=json.dumps(a), kickoff_utc=KICKOFF.isoformat())
        candidate = assess(row, functions['choose_recommendation'])
        self.assertTrue(candidate['model_valid'])
        self.assertGreater(candidate['ev'], 0)
        self.assertNotEqual(candidate['tier'], 4)
        self.assertNotIn(row['id'], recommended_pick_ids([row], functions['choose_recommendation']))
        # Independently protect C's minimum sample rule even if an old/tampered
        # saved display score claims 99: that number cannot create two games.
        overstated = deepcopy(a)
        overstated['score'] = 99
        row['analysis_a_json'] = json.dumps(overstated)
        self.assertNotEqual(assess(row, functions['choose_recommendation'])['tier'], 4)
        self.assertNotIn(row['id'], recommended_pick_ids([row], functions['choose_recommendation']))

    def test_opposite_venue_games_cannot_supply_missing_home_samples(self):
        home = history(['2026-10-01', '2026-10-03'])
        away = history(['2026-10-02', '2026-10-04', '2026-10-06', '2026-10-08', '2026-10-10'], home=AWAY, away=HOME)
        report = form_data_readiness(pd.concat([home, away], ignore_index=True), HOME, KICKOFF, 'home')
        self.assertEqual((report['current_season_team_count'], report['current_season_venue_count'],
                          report['full_history_venue_count']), (7, 2, 2))
        self.assertEqual(report['status'], 'limited')
        self.assertIn('insufficient_venue_samples', report['reason_codes'])

    def test_provider_failure_differs_from_an_empty_successful_result_file(self):
        failed = pd.DataFrame()
        failed.attrs['history_diagnostics'] = dict(provider='Football-Data', status='http_error', sources=[
            dict(status='http_error', http_status=404, url='https://example.invalid/not-found.csv')])
        report = form_data_readiness(failed, HOME, KICKOFF, 'home')
        empty = pd.DataFrame()
        empty.attrs['history_diagnostics'] = {'status': 'empty_history'}
        absent = form_data_readiness(empty, HOME, KICKOFF, 'home')
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('provider_failure', report['reason_codes'])
        self.assertNotIn('name_unmatched', report['reason_codes'])
        self.assertEqual(report['source']['sources'][0]['http_status'], 404)
        self.assertIn('empty_history', absent['reason_codes'])
        self.assertNotIn('provider_failure', absent['reason_codes'])

    def test_unmatched_similar_youth_name_is_not_padded_from_senior_history(self):
        report = form_data_readiness(history(home='Ipswich'), 'Ipswich Town U21', KICKOFF, 'home')
        self.assertEqual(report['status'], 'unavailable')
        self.assertIsNone(report['matched_name'])
        self.assertIn('name_unmatched', report['reason_codes'])
        self.assertEqual(report['full_history_venue_count'], 0)
        self.assertNotIn('sample5_dates', report)

    def test_invalid_score_date_and_team_rows_cannot_pad_a_three_game_sample(self):
        frame = history(['2026-10-01', '2026-10-03', '2026-10-05'])
        bad = history(['2026-10-07', '2026-10-09', '2026-10-11'])
        bad.loc[0, 'FTHG'] = -1
        bad.loc[1, 'Date_dt'] = pd.NaT
        bad.loc[2, 'AwayTeam'] = None
        report = form_data_readiness(pd.concat([frame, bad], ignore_index=True), HOME, KICKOFF, 'home')
        self.assertEqual((report['full_history_venue_count'], report['current_season_venue_count']), (3, 3))
        self.assertIn('historical_source_invalidity', report['reason_codes'])
        self.assertIn('insufficient_venue_samples', report['reason_codes'])
        self.assertEqual(report['validation']['valid_rows'], 3)
        missing = form_data_readiness(frame.drop(columns='FTHG'), HOME, KICKOFF, 'home')
        self.assertIn('historical_source_invalidity', missing['reason_codes'])
        self.assertEqual(missing['full_history_venue_count'], 0)

    def test_cutoff_uses_utc_day_and_excludes_same_day_and_future_games(self):
        frame = history(['2026-10-01', '2026-10-03', '2026-10-05', '2026-10-07',
                         '2026-10-23', '2026-10-24', '2026-10-25'])
        # 24 October at 02:00 Thai is 23 October at 19:00 UTC.
        report = form_data_readiness(frame, HOME, '2026-10-24T02:00:00+07:00', 'home')
        self.assertEqual(report['cutoff_date'], '2026-10-23')
        self.assertEqual((report['full_history_venue_count'], report['after_cutoff_count']), (4, 3))
        self.assertIn('after_cutoff', report['reason_codes'])
        self.assertNotIn('2026-10-23', report['sample5_dates'])
        self.assertIn('insufficient_venue_samples', report['reason_codes'])

    def test_unknown_season_rows_remain_unknown_not_current_or_prior(self):
        for label in (None, 'unknown', '2026/27', '2625', '2728'):
            with self.subTest(label=label):
                report = form_data_readiness(history(season=label), HOME, KICKOFF, 'home')
                self.assertEqual((report['current_season_venue_count'], report['prior_season_venue_count']), (0, 0))
                self.assertEqual(report['unknown_season_venue_count'], 5)
                self.assertIn('unknown_season', report['reason_codes'])
                self.assertIn('no_current_season_results', report['reason_codes'])
        report = form_data_readiness(history().drop(columns='_history_season'), HOME, KICKOFF, 'home')
        self.assertEqual(report['unknown_season_venue_count'], 5)
        self.assertEqual(report['current_season_venue_count'], 0)

    def test_stale_threshold_is_explicit_and_does_not_claim_fresh_current_five(self):
        for age, expected_stale in [(120, False), (121, True)]:
            with self.subTest(age=age):
                last = KICKOFF.date()-timedelta(days=age)
                dates = [last-timedelta(days=4-index) for index in range(5)]
                report = form_data_readiness(history(dates, season='2526'), HOME, KICKOFF, 'home')
                self.assertEqual(report['prior_season_venue_count'], 5)
                self.assertEqual(report['current_season_venue_count'], 0)
                self.assertEqual('stale_history' in report['reason_codes'], expected_stale)

    def test_mislabelled_prior_results_cannot_claim_current_season_membership(self):
        frame = history(['2025-10-01', '2025-10-03', '2025-10-05', '2025-10-07', '2025-10-09'], season='2627')
        report = form_data_readiness(frame, HOME, KICKOFF, 'home')
        self.assertEqual(report['current_season_venue_count'], 0)
        self.assertEqual(report['current_season_team_count'], 0)
        self.assertIn('historical_source_invalidity', report['reason_codes'])
        self.assertNotIn('ฤดูกาล 2627 5 นัด', report['summary'])

    def test_annotation_changes_diagnostics_only_and_keeps_evidence_immutable(self):
        functions = load_functions(ROOT / 'app.py')
        frame = history(['2026-10-01', '2026-10-03', '2026-10-05'])
        before_frame, before_attrs = frame.copy(deep=True), deepcopy(frame.attrs)
        profile = functions['team_form_profile'](frame, HOME, KICKOFF, 'home')
        before_values = {key: deepcopy(value) for key, value in profile.items() if key != 'diagnostics'}
        annotate_form_source(profile, frame, HOME, KICKOFF, 'soccer_epl')
        self.assertEqual({key: value for key, value in profile.items() if key != 'diagnostics'}, before_values)
        self.assertIn('insufficient_venue_samples', profile['diagnostics']['readiness']['reason_codes'])
        self.assertIn('ฤดูกาล 2627 3 นัด', profile['diagnostics']['message'])
        self.assertIn('2026-10-24T12:00:00+00:00', profile['diagnostics']['message'])
        pd.testing.assert_frame_equal(frame, before_frame)
        self.assertEqual(frame.attrs, before_attrs)
        for kickoff in (None, 'not a timestamp', '2026-10-24T19:00:00'):
            with self.subTest(kickoff=kickoff):
                report = form_data_readiness(frame, HOME, kickoff, 'home')
                self.assertIn('invalid_kickoff', report['reason_codes'])
                self.assertNotIn('cutoff_date', report)
                self.assertEqual(report['full_history_venue_count'], 0)

    def test_protected_a_poisson_ev_and_settlement_sources_match_original_contract(self):
        contract = json.loads((ROOT / 'fixtures/readiness/pr2-contract.json').read_text())
        source = (ROOT / 'app.py').read_text()
        nodes = {node.name: node for node in ast.parse(source).body if isinstance(node, ast.FunctionDef)}
        self.assertGreaterEqual(len(contract['formula_source_sha256']), 10)
        for name, expected in contract['formula_source_sha256'].items():
            with self.subTest(formula=name):
                actual = hashlib.sha256(ast.get_source_segment(source, nodes[name]).encode()).hexdigest()
                self.assertEqual(actual, expected)

    def test_cached_provider_failure_preserves_http_status_and_original_receipt(self):
        frame = pd.DataFrame()
        receipt = '2026-10-24T11:59:00+00:00'
        frame.attrs['history_diagnostics'] = dict(status='http_error', sources=[
            dict(status='http_error', http_status=429, received_at=receipt,
                 url='https://example.invalid/cached-unavailable.csv')])
        saved = describe_history_cache(frame, '2026-10-24T12:00:00+00:00')
        self.assertEqual(saved.attrs['cache_diagnostics']['status'], 'cache')
        self.assertEqual(saved.attrs['cache_diagnostics']['source_status'], 'http_error')
        self.assertEqual(saved.attrs['cache_diagnostics']['ttl_seconds'], 900)
        self.assertEqual(saved.attrs['cache_diagnostics']['received_at'], [receipt])
        self.assertEqual(saved.attrs['history_diagnostics']['sources'][0]['http_status'], 429)
        functions = load_functions(ROOT / 'app.py')
        profile = functions['team_form_profile'](saved, HOME, KICKOFF, 'home')
        annotate_form_source(profile, saved, HOME, KICKOFF, 'soccer_epl')
        self.assertIn('CSV cache: cache', profile['diagnostics']['message'])
        self.assertIn('http_error', profile['diagnostics']['message'])
        self.assertIn('provider_failure', profile['diagnostics']['readiness']['reason_codes'])
        self.assertIsNone(profile['last5_gf'])

    def test_success_received_during_network_call_is_fetched_without_restamping(self):
        frame = history()
        receipt = '2026-10-24T12:00:01+00:00'
        frame.attrs['history_diagnostics']['sources'][0]['received_at'] = receipt
        saved = describe_history_cache(frame, '2026-10-24T12:00:00+00:00')
        report = saved.attrs['cache_diagnostics']
        self.assertEqual(report['status'], 'fetched')
        self.assertEqual(report['source_status'], 'ok')
        self.assertEqual(report['received_at'], [receipt])
        self.assertTrue(report['receipt_preserved'])
        self.assertEqual(saved.attrs['history_diagnostics']['sources'][0]['received_at'], receipt)
        pd.testing.assert_frame_equal(saved, frame)

    def test_missing_naive_or_invalid_receipt_never_claims_cache_or_new_fetch(self):
        for receipt in (None, 'not a timestamp', '2026-10-24T12:00:00'):
            with self.subTest(receipt=receipt):
                frame = history()
                frame.attrs['history_diagnostics']['sources'][0]['received_at'] = receipt
                saved = describe_history_cache(frame, '2026-10-24T12:00:00+00:00')
                self.assertEqual(saved.attrs['cache_diagnostics']['status'], 'unknown_receipt')
                self.assertEqual(saved.attrs['cache_diagnostics']['received_at'], [])
                self.assertEqual(saved.attrs['history_diagnostics']['sources'][0]['received_at'], receipt)

    def test_cache_description_never_mutates_input_values_or_nested_attrs(self):
        frame = history()
        frame.attrs['cache_diagnostics'] = {'previous': 'untouched'}
        before, attrs = frame.copy(deep=True), deepcopy(frame.attrs)
        saved = describe_history_cache(frame, '2026-10-24T12:01:00+00:00')
        pd.testing.assert_frame_equal(frame, before)
        self.assertEqual(frame.attrs, attrs)
        saved.attrs['history_diagnostics']['sources'][0]['status'] = 'changed-copy'
        saved.loc[0, 'FTHG'] = 99
        self.assertEqual(frame.attrs, attrs)
        self.assertEqual(frame.loc[0, 'FTHG'], 3)

    def synthetic_scan_case(self, *, blocked=False):
        from test_issue9_scan import Issue9ScanTests
        case = Issue9ScanTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare(blocked=blocked)
        return case

    def test_scan_b_source_unavailable_keeps_real_a_without_fabricated_ev(self):
        case = self.synthetic_scan_case(blocked=True)
        rows, _, _, _ = case.scan()
        row = case.over(rows)
        a, b = case.analyses(row)
        self.assertEqual(a['home_form']['sample5'], 5)
        self.assertIsNotNone(a['score'])
        self.assertFalse(b['available'])
        self.assertIsNone(row['ev_pct'])
        self.assertNotIn('expected_goals', b)
        self.assertEqual(b['data_readiness']['status'], 'xg_unavailable')
        self.assertIn('home_source_unavailable', b['data_readiness']['reason_codes'])
        self.assertIn('403', b['data_readiness']['message'])

    def test_scan_available_b_remains_uncalibrated_and_no_quote_is_separate(self):
        case = self.synthetic_scan_case()
        rows, _, _, _ = case.scan()
        row = case.over(rows)
        _, b = case.analyses(row)
        self.assertTrue(b['available'])
        self.assertIsNotNone(row['ev_pct'])
        self.assertFalse(b['calibrated'])
        self.assertEqual(b['data_readiness']['status'], 'available_uncalibrated')
        self.assertEqual(b['data_readiness']['calibration_status'], 'not_validated')
        metrics = case.f['estimate_goals'](case.xg, *case.api, case.kickoff)
        self.assertIsNotNone(metrics)
        matrix = case.f['score_matrix'](metrics['home_lambda'], metrics['away_lambda'])
        no_quote = case.f['system_b_analysis'](metrics, matrix, None, case.f['split_line'](2.5), 2.5)
        report = model_data_readiness({'home': {'status': 'available'}, 'away': {'status': 'available'}}, metrics, no_quote)
        self.assertFalse(no_quote['available'])
        self.assertNotIn('selection', no_quote)
        self.assertEqual(report['status'], 'no_valid_market')
        self.assertNotIn('model_gate_failed', report['reason_codes'])

    def test_scan_b_model_gate_failure_is_not_mislabelled_as_missing_xg(self):
        case = self.synthetic_scan_case()
        # A real permissible xG value is zero. A league baseline containing only
        # zeros fails the existing positive-mean model gate despite ten samples.
        case.xg.loc[:, ['xG_home', 'xG_away']] = 0.0
        rows, _, _, _ = case.scan()
        row = case.over(rows)
        a, b = case.analyses(row)
        provider = a['xg']['diagnostics']
        self.assertEqual((provider['home']['status'], provider['away']['status']), ('available', 'available'))
        self.assertEqual((provider['home']['sample'], provider['away']['sample']), (10, 10))
        self.assertFalse(b['available'])
        self.assertIsNone(row['ev_pct'])
        self.assertNotIn('expected_goals', b)
        self.assertEqual(b['data_readiness']['status'], 'model_gate_failed')
        self.assertIn('ฐานลีก', b['status'])
        self.assertNotIn('ยังไม่มี xG', b['status'])


if __name__ == '__main__':
    unittest.main()
