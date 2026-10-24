"""Synthetic offline parity checks for the unchanged original baseline."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

from backtest.baseline import (APP_FUNCTIONS, Baseline, BaselineIntegrityError,
                               PROTECTED_FUNCTIONS, ROOT as FROZEN_ROOT,
                               current_source_hashes, existing_functions)


ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
ASOF = datetime(2020, 10, 20, 12, tzinfo=UTC)


def histories(year=2020, count=10):
    return pd.DataFrame([
        dict(Date_dt=pd.Timestamp(f'{year}-10-{day:02}'), HomeTeam='Synthetic Home',
             AwayTeam='Synthetic Away', FTHG=2, FTAG=1, xG_home=1.7, xG_away=1.1,
             _history_season=f'{year % 100:02}{(year + 1) % 100:02}')
        for day in range(1, count+1)
    ])


def event(asof=ASOF):
    return dict(event_id='synthetic-backtest', home_team='Synthetic Home', away_team='Synthetic Away',
                kickoff_utc=(asof+timedelta(hours=3)).isoformat(), league='soccer_epl', sport_key='soccer_epl')


def quotes(asof=ASOF, line=2.5):
    return [dict(id=index, quote_id='source-'+str(index), market='Over/Under', side=side,
                 point=line, odds=1.95, bookmaker='Synthetic book', bookmaker_key='synthetic-book',
                 scanned_at=asof.isoformat(), received_at=asof.isoformat(),
                 source_updated_at=asof.isoformat()) for index, side in [(1, 'over'), (2, 'under')]]


def copy_sources(folder):
    folder = Path(folder)
    for filename in ('app.py', 'history_data.py', 'mobile_dashboard.py', 'readiness.py',
                     'market_recommendations.py', 'ou_market.py'):
        shutil.copy2(FROZEN_ROOT / filename, folder / filename)
    target = folder / 'fixtures/readiness'
    target.mkdir(parents=True)
    shutil.copy2(FROZEN_ROOT / 'fixtures/readiness/pr2-contract.json', target / 'pr2-contract.json')
    return folder


class BacktestBaselineTests(unittest.TestCase):
    def setUp(self):
        self.baseline = Baseline()
        no_http = patch('requests.sessions.Session.request', side_effect=AssertionError('Baseline cannot use HTTP'))
        self.http = no_http.start()
        self.addCleanup(no_http.stop)

    def tearDown(self):
        self.http.assert_not_called()

    def rows(self, asof=ASOF, line=2.5):
        frame = histories(asof.year)
        return self.baseline.evaluate(frame, frame, event(asof), quotes(asof, line), asof)

    def test_all_original_eleven_hashes_are_pinned_and_bundle_is_reproducible(self):
        expected = json.loads((ROOT / 'fixtures/readiness/pr2-contract.json').read_text())['formula_source_sha256']
        manifest = current_source_hashes()
        self.assertEqual(manifest['formula_sha256'], expected)
        self.assertEqual(set(expected), PROTECTED_FUNCTIONS)
        self.assertEqual(len(expected), 11)
        self.assertEqual(manifest, self.baseline.version)
        self.assertEqual(Baseline().version['bundle_sha256'], manifest['bundle_sha256'])
        self.assertEqual(len(manifest['bundle_sha256']), 64)
        self.assertTrue(all(len(digest) == 64 for digest in manifest['source_sha256'].values()))
        self.assertEqual(self.baseline.root,FROZEN_ROOT)
        self.assertEqual(manifest['reference_scope'],'frozen_pr6')

    def test_operational_scan_changes_cannot_change_frozen_backtest(self):
        before=self.rows()
        original_read=Path.read_text
        live_sources={ROOT/name for name in ('app.py','history_data.py','market_recommendations.py',
                      'ou_market.py','readiness.py','mobile_dashboard.py')}
        def changed_operational_source(path,*args,**kwargs):
            if path in live_sources:
                raise AssertionError('Backtest read the mutable operational source')
            return original_read(path,*args,**kwargs)
        with patch.object(Path,'read_text',changed_operational_source):
            after=self.rows()
            self.assertEqual(self.baseline.c_pick_ids(after,ASOF),{1})
        self.assertEqual(before,after)

    def test_default_frozen_reference_detects_tampering_even_outside_formula_whitelist(self):
        original_read=Path.read_bytes
        target=FROZEN_ROOT/'bill_backup.py'
        def tampered_reference(path,*args,**kwargs):
            content=original_read(path,*args,**kwargs)
            return content+b'\n# altered reference' if path==target else content
        with patch.object(Path,'read_bytes',tampered_reference):
            with self.assertRaisesRegex(BaselineIntegrityError,'Frozen PR6 source differs: bill_backup.py'):
                Baseline()

    def test_whitelist_excludes_fetch_database_and_ui_top_level_execution(self):
        functions = existing_functions()
        self.assertEqual(set(functions), APP_FUNCTIONS)
        self.assertTrue(all(callable(value) for value in functions.values()))
        self.assertFalse(any(name.startswith(('fetch_', 'save_', 'load_', 'render_')) or name in ('db', 'init_db') for name in functions))
        with tempfile.TemporaryDirectory() as folder:
            root = copy_sources(folder)
            with (root / 'app.py').open('a') as handle:
                handle.write('\nraise AssertionError("App top-level/UI executed")\n')
                handle.write('\ndef fetch_unapproved():\n    raise AssertionError("Unapproved function")\n')
            isolated = Baseline(root)
            self.assertNotIn('fetch_unapproved', isolated.functions)
            self.assertEqual(isolated.settle_pick(dict(market='Over/Under', side='over', point=2.5, odds=1.95), 2, 1)[1], .95)

    def test_changed_protected_formula_fails_before_evaluation(self):
        with tempfile.TemporaryDirectory() as folder:
            root = copy_sources(folder)
            with (root / 'app.py').open('a') as handle:
                handle.write('\ndef choose_recommendation(score, ev_pct, odds_in_range, has_data=True):\n    return "tampered"\n')
            with self.assertRaisesRegex(BaselineIntegrityError, 'choose_recommendation'):
                Baseline(root)

    def test_changed_policy_after_initialization_cannot_keep_an_old_manifest(self):
        with tempfile.TemporaryDirectory() as folder:
            root = copy_sources(folder)
            isolated = Baseline(root)
            with (root / 'ou_market.py').open('a') as handle:
                handle.write('\n# source changed after artifact initialization\n')
            with self.assertRaisesRegex(BaselineIntegrityError, 'bundle changed'):
                isolated.c_pick_ids([], ASOF)

    def test_real_formula_output_matches_independent_two_point_eight_poisson_example(self):
        rows = self.rows()
        over = rows[0]
        a, b = json.loads(over['analysis_a_json']), json.loads(over['analysis_b_json'])
        self.assertEqual((a['home_form']['sample5'], a['away_form']['sample5']), (5, 5))
        self.assertEqual(a['score'], 100)
        self.assertEqual(b['expected_goals'], [1.7, 1.1])
        p_over = 1-math.exp(-2.8)*(1+2.8+2.8**2/2)
        self.assertEqual(over['ev_pct'], round((1.95*p_over-1)*100, 2))
        self.assertAlmostEqual(sum(b['hda'].values()), 100, delta=.03)
        self.assertAlmostEqual(sum(b['selection'][key] for key in ('p_win', 'p_loss', 'p_partial')), 100, delta=.03)
        self.assertFalse(b['calibrated'])

    def test_c_uses_historical_asof_in_both_markets_without_shifting_quote_times(self):
        rows = self.rows()
        before = deepcopy(rows)
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF), {1})
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF+timedelta(seconds=899)), {1})
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF+timedelta(seconds=901)), set())
        self.assertEqual(rows, before)
        self.assertEqual(rows[0]['received_at'], ASOF.isoformat())
        # An AH evaluation follows the same original C eligibility policy.
        ah = dict(quotes()[0], id=3, market='Asian Handicap', side='home', point=-.25, odds=4.0)
        frame = histories()
        ah_rows = self.baseline.evaluate(frame, frame, event(), [ah], ASOF)
        self.assertGreater(ah_rows[0]['ev_pct'], 0)
        self.assertEqual(self.baseline.c_pick_ids(ah_rows, ASOF), {3})

    def test_c_clones_do_not_mutate_live_modules_environment_or_database(self):
        import market_recommendations as live_policy
        import readiness as live_readiness
        import ou_market as live_ou
        originals = (live_policy.datetime, live_policy.quote_freshness, live_readiness.datetime, live_ou.datetime)
        modules = {name: sys.modules.get(name) for name in ('app', 'streamlit', 'readiness', 'ou_market', 'market_recommendations')}
        with patch.dict(os.environ, {'VALUE_BET_QUOTE_MAX_AGE_SECONDS': 'invalid-host-setting'}):
            before_env = dict(os.environ)
            with patch.object(sqlite3, 'connect', side_effect=AssertionError('No database access')):
                self.assertEqual(self.baseline.c_pick_ids(self.rows(), ASOF), {1})
            self.assertEqual(dict(os.environ), before_env)
        self.assertEqual((live_policy.datetime, live_policy.quote_freshness, live_readiness.datetime, live_ou.datetime), originals)
        self.assertEqual({name: sys.modules.get(name) for name in modules}, modules)

    def test_parallel_evaluations_keep_independent_decision_clocks(self):
        old, future = ASOF, ASOF.replace(year=2030)
        rows_old, rows_future = self.rows(old), self.rows(future)
        tasks = [(rows_old, old, {1}), (rows_future, future, {1}),
                 (rows_old, future, set()), (rows_future, old, set())]
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(self.baseline.c_pick_ids, rows, asof) for rows, asof, _ in tasks]
            self.assertEqual([task.result() for task in futures], [expected for _, _, expected in tasks])

    def test_quote_age_can_tighten_but_never_loosen_original_nine_hundred_seconds(self):
        rows = self.rows()
        strict = Baseline(quote_max_age_seconds=60)
        self.assertEqual(strict.c_pick_ids(rows, ASOF+timedelta(seconds=61)), set())
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF+timedelta(seconds=61)), {1})
        for value in (0, -1, 901, True, '900'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                Baseline(quote_max_age_seconds=value)

    def test_c_does_not_mix_bookmaker_line_or_snapshot_to_complete_ou_pair(self):
        for field, value in [('bookmaker_key', 'other-book'), ('point', 2.75),
                             ('scanned_at', (ASOF-timedelta(seconds=1)).isoformat()),
                             ('source_updated_at', (ASOF-timedelta(seconds=1)).isoformat())]:
            with self.subTest(field=field):
                rows = self.rows()
                rows[1][field] = value
                self.assertEqual(self.baseline.c_pick_ids(rows, ASOF), set())

    def test_missing_freshness_evidence_never_becomes_an_observed_timestamp(self):
        for field in ('received_at', 'source_updated_at', 'bookmaker_key', 'scanned_at'):
            with self.subTest(field=field):
                supplied = quotes()
                for item in supplied:
                    item.pop(field)
                frame = histories()
                rows = self.baseline.evaluate(frame, frame, event(), supplied, ASOF)
                self.assertTrue(all(field not in row for row in rows))
                self.assertEqual(self.baseline.c_pick_ids(rows, ASOF), set())

    def test_missing_xg_keeps_real_a_but_never_creates_poisson_or_ev(self):
        rows = self.baseline.evaluate(histories(), pd.DataFrame(), event(), quotes(), ASOF)
        for row in rows:
            a, b = json.loads(row['analysis_a_json']), json.loads(row['analysis_b_json'])
            self.assertIsNotNone(a['score'])
            self.assertEqual(a['home_form']['last5_gf'], 2)
            self.assertIsNone(a['xg']['home'])
            self.assertIsNone(row['ev_pct'])
            self.assertFalse(b['available'])
            self.assertNotIn('expected_goals', b)
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF), set())

    def test_changing_actual_goals_cannot_change_xg_probabilities_or_ev(self):
        scores, xg = histories(), histories()
        before = self.baseline.evaluate(scores, xg, event(), quotes(), ASOF)
        scores['FTHG'], scores['FTAG'] = 0, 0
        after = self.baseline.evaluate(scores, xg, event(), quotes(), ASOF)
        self.assertNotEqual(before[0]['score'], after[0]['score'])
        for original, changed in zip(before, after):
            self.assertEqual(json.loads(original['analysis_b_json']), json.loads(changed['analysis_b_json']))
            self.assertEqual(original['ev_pct'], changed['ev_pct'])

    def test_four_xg_samples_cannot_supply_an_invented_fifth_observation(self):
        rows = self.baseline.evaluate(histories(), histories(count=4), event(), quotes(), ASOF)
        self.assertEqual(json.loads(rows[0]['analysis_a_json'])['home_form']['sample5'], 5)
        self.assertFalse(json.loads(rows[0]['analysis_b_json'])['available'])
        self.assertIsNone(rows[0]['ev_pct'])

    def test_european_cup_keeps_b_and_ev_disabled_despite_domestic_xg(self):
        cup = dict(event(), league='soccer_uefa_champs_league', sport_key='soccer_uefa_champs_league')
        rows = self.baseline.evaluate(histories(), histories(), cup, quotes(), ASOF)
        b = json.loads(rows[0]['analysis_b_json'])
        self.assertFalse(b['available'])
        self.assertEqual(b['scope'], 'domestic_context_only')
        self.assertIsNone(rows[0]['ev_pct'])
        self.assertEqual(self.baseline.c_pick_ids(rows, ASOF), set())

    def test_quarter_line_evaluation_and_settlement_keep_original_half_outcomes(self):
        rows = self.rows(line=2.75)
        self.assertEqual(json.loads(rows[0]['analysis_b_json'])['ou_line'], 2.75)
        self.assertAlmostEqual(sum(json.loads(rows[0]['analysis_b_json'])['selection'][key]
                                   for key in ('p_win', 'p_loss', 'p_partial')), 100, delta=.03)
        self.assertEqual(self.baseline.settle_pick(rows[0], 2, 1), ('ชนะครึ่ง', .475, None))
        self.assertEqual(self.baseline.settle_pick(rows[1], 2, 1), ('แพ้ครึ่ง', -.5, None))
        for point, expected in [(-.25, ('แพ้ครึ่ง', -.5, None)), (.25, ('ชนะครึ่ง', .475, None))]:
            row = dict(market='Asian Handicap', side='home', point=point, odds=1.95)
            self.assertEqual(self.baseline.settle_pick(row, 1, 1), expected)
        self.assertIsNone(self.baseline.settle_pick(dict(market='ไม่มีราคา', odds=None), 1, 1))

    def test_bad_market_identity_and_naive_decision_fail_without_guessing(self):
        frame = histories()
        for quote in [dict(quotes()[0], event_id='different-event'), dict(quotes()[0], point=2.6),
                      dict(quotes()[0], odds=float('nan')), dict(quotes()[0], side='home')]:
            with self.subTest(quote=quote), self.assertRaises(ValueError):
                self.baseline.evaluate(frame, frame, event(), [quote], ASOF)
        with self.assertRaises(ValueError):
            self.baseline.evaluate(frame, frame, event(), quotes(), ASOF.replace(tzinfo=None))
        with self.assertRaises(ValueError):
            self.baseline.evaluate(frame, frame, event(), quotes(), ASOF+timedelta(days=1))

    def test_evaluation_does_not_mutate_input_frames_evidence_or_quotes(self):
        frame = histories()
        frame.attrs['history_diagnostics'] = {'sources': [{'received_at': '2020-10-19T12:00:00+00:00'}]}
        snapshot, attrs = frame.copy(deep=True), deepcopy(frame.attrs)
        supplied, fixture = quotes(), event()
        before_quotes, before_event = deepcopy(supplied), deepcopy(fixture)
        rows = self.baseline.evaluate(frame, frame, fixture, supplied, ASOF)
        self.baseline.c_pick_ids(rows, ASOF)
        pd.testing.assert_frame_equal(frame, snapshot)
        self.assertEqual(frame.attrs, attrs)
        self.assertEqual(supplied, before_quotes)
        self.assertEqual(fixture, before_event)


if __name__ == '__main__':
    unittest.main()
