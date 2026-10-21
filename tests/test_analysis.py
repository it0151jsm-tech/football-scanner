"""Independent evidence/model analysis and saved mobile presentations."""
import json
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import Mock

import pandas as pd

import test_regressions as regression


class AnalysisTests(unittest.TestCase):
    setUp = regression.Regressions.setUp
    pick = regression.Regressions.pick
    save_bill = regression.Regressions.save_bill
    def evidence(self):
        form = dict(form_rate=80., form_sample=10)
        return self.f['system_a_analysis'](form, form, form, {'summary': 'ยิง 2.0 เสีย 1.0'},
                                         {'summary': 'ยิง 1.0 เสีย 2.0'}, {}, {'home': None, 'away': None}, {})

    def model(self, market='totals', side='over', point=2.25):
        metrics = dict(home_lambda=1.7, away_lambda=1.1)
        matrix = self.f['score_matrix'](1.7, 1.1)
        evaluation = self.f['evaluate_market_poisson'](matrix, market, side, point, 1.95)
        return self.f['system_b_analysis'](metrics, matrix, evaluation, self.f['split_line'](point), point)

    def test_a_remains_available_without_ev_or_xg_and_never_claims_value(self):
        a = self.evidence()
        self.assertEqual(a['score'], 80)
        self.assertNotIn('p_win', a)
        for ev in [None, float('nan'), 0, -10]:
            result = self.f['choose_recommendation'](a['score'], ev, True)
            self.assertNotIn('ตัวเลือกเด่น', result)
        self.assertIn('ยังประเมินความคุ้มค่าราคาไม่ได้', self.f['choose_recommendation'](80, None, True))
        self.assertIn('ตัวเลือกเด่น', self.f['choose_recommendation'](80, 4, True))

    def test_b_full_model_distributions_are_normalized_for_ah_and_ou(self):
        for market, side, point in [('totals', 'over', 2.25), ('totals', 'under', 2.75),
                                    ('spreads', 'home', -.25), ('spreads', 'away', .75)]:
            with self.subTest(market=market, side=side, point=point):
                b = self.model(market, side, point)
                self.assertTrue(b['available'])
                self.assertFalse(b['calibrated'])
                self.assertAlmostEqual(sum(b['hda'].values()), 100, delta=.02)
                self.assertAlmostEqual(sum(b['ou'].values()), 100, delta=.02)
                self.assertAlmostEqual(sum(b['selection'][k] for k in ['p_win', 'p_partial', 'p_loss']), 100, delta=.02)

    def test_b_missing_model_does_not_fill_probabilities(self):
        b = self.f['system_b_analysis'](None, None, None, [(2.5, 1)], 2.5)
        self.assertFalse(b['available'])
        self.assertNotIn('hda', b)
        self.assertNotIn('expected_goals', b)
        self.assertNotIn('selection', b)

    def test_system_a_signature_is_independent_of_model_and_ev(self):
        import inspect
        parameters = inspect.signature(self.f['system_a_analysis']).parameters
        self.assertNotIn('ev', parameters)
        self.assertNotIn('matrix', parameters)
        self.assertNotIn('metrics', parameters)
        before = self.evidence()
        self.model()
        self.assertEqual(before, self.evidence())

    def test_first_observed_price_is_not_opening_and_line_changes_block_delta(self):
        price = self.f['price_evidence'](dict(point=2.5, odds=1.8, captured_at='first'), 2.75, 2., 'now')
        self.assertEqual(price['label'], 'First Observed Price')
        self.assertFalse(price['opening_odds_available'])
        self.assertIsNone(price['odds_delta'])
        same = self.f['price_evidence'](dict(point=2.5, odds=1.8, captured_at='first'), 2.5, 2., 'now')
        self.assertEqual(same['odds_delta'], .2)

    def test_first_observed_database_lookup_is_partitioned_by_bookmaker(self):
        for book, odds in [('A', 1.8), ('B', 1.9), ('A', 2.)]:
            self.f['save_picks']([], [dict(event_id='e', market='Over/Under', side='over', bookmaker=book, point=2.5, odds=odds)])
        result = self.f['first_observed_quotes'](['e'])
        self.assertEqual(result['e', 'Over/Under', 'over', 'A']['odds'], 1.8)
        self.assertEqual(result['e', 'Over/Under', 'over', 'B']['odds'], 1.9)

    def test_actual_xg_missing_or_old_is_not_synthesized(self):
        kickoff = datetime(2026, 10, 20, tzinfo=timezone.utc)
        self.assertIsNone(self.f['actual_xg_evidence'](pd.DataFrame(), 'A', 'B', kickoff)['home'])
        history = pd.DataFrame([dict(Date_dt=pd.Timestamp('2023-01-01'), HomeTeam='A', AwayTeam='B', xG_home=1.2, xG_away=.8)])
        self.assertIsNone(self.f['actual_xg_evidence'](history, 'A', 'B', kickoff)['home'])
        history['Date_dt'] = pd.Timestamp('2026-10-01')
        current = self.f['actual_xg_evidence'](history, 'A', 'B', kickoff)
        self.assertEqual(current['home']['xg'], 1.2)
        self.assertEqual(current['away']['xga'], 1.2)

    def test_opponent_quality_uses_only_results_before_each_game(self):
        rows = [dict(Date_dt=pd.Timestamp(f'2026-09-{day:02}'), HomeTeam='C', AwayTeam='D', FTHG=1, FTAG=0) for day in [1, 2, 3]]
        rows.append(dict(Date_dt=pd.Timestamp('2026-09-04'), HomeTeam='A', AwayTeam='C', FTHG=0, FTAG=5))
        hist = pd.DataFrame(rows)
        kickoff = datetime(2026, 10, 20, tzinfo=timezone.utc)
        result = self.f['opponent_quality'](hist, 'A', kickoff, 'home')
        self.assertEqual(result['mean_ppg'], 3)
        self.assertEqual(result['sample'], 1)
        hist = pd.concat([hist, pd.DataFrame([dict(Date_dt=pd.Timestamp('2026-11-01'), HomeTeam='C', AwayTeam='D', FTHG=0, FTAG=5)])])
        self.assertEqual(result, self.f['opponent_quality'](hist, 'A', kickoff, 'home'))

    def test_mobile_card_contains_actual_probabilities_and_explicit_labels(self):
        a, b = self.evidence(), self.model()
        a['price'] = self.f['price_evidence'](None, 2.25, 1.95, 'now')
        row = dict(self.row, market='Over/Under', point=2.25, analysis_a_json=json.dumps(a), analysis_b_json=json.dumps(b))
        text = '\n'.join(self.f['mobile_card_lines'](row))
        for expected in ['ระบบ A', 'ระบบ B', 'ระบบ C', 'First Observed Price', 'Calibration', 'ประตูคาดการณ์', 'สูงชนะเต็ม', 'คะแนนหลักฐาน']:
            self.assertIn(expected, text)
        self.assertIn(f"{b['hda']['home']:.2f}%", text)

    def test_legacy_scores_are_not_relabeled_and_no_database_rows_change(self):
        self.save_bill()
        with self.f['db']() as con:
            con.execute('UPDATE picks SET confidence_score=99, recommendation=?, analysis_a_json=NULL, analysis_b_json=NULL', ('ตัวเลือกเด่น',))
            before_picks = [tuple(r) for r in con.execute('SELECT * FROM picks')]
            before_slips = [tuple(r) for r in con.execute('SELECT * FROM slips')]
        view = self.f['analysis_display_rows'](self.f['load_picks']())
        self.assertTrue(pd.isna(view.iloc[0].confidence_score))
        self.assertNotIn('ตัวเลือกเด่น', view.iloc[0].recommendation)
        with self.f['db']() as con:
            self.assertEqual(before_picks, [tuple(r) for r in con.execute('SELECT * FROM picks')])
            self.assertEqual(before_slips, [tuple(r) for r in con.execute('SELECT * FROM slips')])

    def test_scan_persists_independent_a_b_with_no_additional_odds_requests(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        event = dict(id='e', commence_time=kickoff.isoformat(), home_team='A', away_team='B', bookmakers=[dict(title='Book', markets=[
            dict(key='spreads', outcomes=[dict(name='A', point=-.25, price=1.95)]),
            dict(key='totals', outcomes=[dict(name='Over', point=2.25, price=1.9)])])])
        self.f['fetch_odds'] = Mock(return_value=([event], '99', '1'))
        score = pd.DataFrame([dict(Date_dt=pd.Timestamp(kickoff.replace(tzinfo=None)).normalize()-pd.Timedelta(days=i+1),
                                   HomeTeam='A', AwayTeam='B', FTHG=2, FTAG=1, xG_home=1.7, xG_away=1.1) for i in range(10)])
        self.f['fetch_history'] = Mock(return_value=score)
        self.f['fetch_understat_history'] = Mock(return_value=score)
        rows, snapshots, _, _ = self.f['scan_league']('soccer_epl', self.row['league'], 'E0', 24, 1.8, 2.05, 'uk', 'test')
        self.assertEqual(len(rows), 2)
        self.f['fetch_odds'].assert_called_once()
        self.f['save_picks'](rows, snapshots)
        for _, row in self.f['load_picks']().iterrows():
            self.assertIsNotNone(json.loads(row.analysis_a_json)['score'])
            self.assertTrue(json.loads(row.analysis_b_json)['available'])
            self.f['mobile_card_lines'](row)
        self.f['fetch_odds'].assert_called_once()

    def test_scan_with_stale_xg_keeps_a_and_suppresses_b(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        event = dict(id='e', commence_time=kickoff.isoformat(), home_team='A', away_team='B', bookmakers=[dict(title='Book', markets=[
            dict(key='totals', outcomes=[dict(name='Over', point=2.5, price=1.9)])])])
        self.f['fetch_odds'] = Mock(return_value=([event], '', ''))
        history = pd.DataFrame([dict(Date_dt=pd.Timestamp(kickoff.replace(tzinfo=None)).normalize()-pd.Timedelta(days=400+i),
                                     HomeTeam='A', AwayTeam='B', FTHG=2, FTAG=1, xG_home=1.7, xG_away=1.1) for i in range(10)])
        self.f['fetch_history'] = Mock(return_value=history)
        self.f['fetch_understat_history'] = Mock(return_value=history)
        rows, _, _, _ = self.f['scan_league']('soccer_epl', self.row['league'], 'E0', 24, 1.8, 2.05, 'uk', 'test')
        a, b = json.loads(rows[0]['analysis_a_json']), json.loads(rows[0]['analysis_b_json'])
        self.assertIsNotNone(a['score'])
        self.assertIsNone(a['xg']['home'])
        self.assertFalse(b['available'])
        self.assertIsNone(rows[0]['ev_pct'])
        self.assertNotIn('ตัวเลือกเด่น', rows[0]['recommendation'])

    def test_missing_history_and_missing_market_show_unavailable_not_fake_numbers(self):
        kickoff = datetime.now(timezone.utc) + timedelta(hours=3)
        event = dict(id='e', commence_time=kickoff.isoformat(), home_team='A', away_team='B', bookmakers=[])
        self.f['fetch_odds'] = Mock(return_value=([event], '', ''))
        self.f['fetch_history'] = Mock(return_value=pd.DataFrame())
        self.f['fetch_understat_history'] = Mock(return_value=pd.DataFrame())
        rows, snapshots, _, _ = self.f['scan_league']('soccer_epl', self.row['league'], 'E0', 24, 1.8, 2.05, 'uk', 'test')
        self.assertEqual(len(rows), 1)
        self.assertEqual(snapshots, [])
        self.assertIsNone(json.loads(rows[0]['analysis_a_json'])['score'])
        self.assertFalse(json.loads(rows[0]['analysis_b_json'])['available'])
        self.assertIsNone(rows[0]['p_win'])
        self.assertIsNone(rows[0]['ev_pct'])
        self.assertIn('ไม่มีข้อมูลโมเดล', '\n'.join(self.f['mobile_card_lines'](rows[0])))

    def test_b_does_not_change_when_a_form_changes(self):
        before = self.model()
        self.evidence()
        self.f['system_a_analysis']({'form_rate': 0, 'form_sample': 10}, {}, {}, {}, {}, {}, {}, {})
        self.assertEqual(before, self.model())

    def test_phase1_migration_does_not_rewrite_old_picks_snapshots_or_final_bills(self):
        self.save_bill()
        self.f['update_pick_result'](1, '0-0', 'แพ้ครึ่ง', -.5)
        self.f['refresh_slip_settlements']()
        self.f['save_picks']([], [dict(event_id='event', market='Asian Handicap', side='home', bookmaker='Book', point=-.25, odds=2)])
        with self.f['db']() as con:
            con.execute('ALTER TABLE picks DROP COLUMN analysis_a_json')
            con.execute('ALTER TABLE picks DROP COLUMN analysis_b_json')
            pick_columns = [r[1] for r in con.execute('PRAGMA table_info(picks)')]
            before_picks = [tuple(r) for r in con.execute('SELECT * FROM picks')]
            before_slips = [tuple(r) for r in con.execute('SELECT * FROM slips')]
            before_snapshots = [tuple(r) for r in con.execute('SELECT * FROM odds_snapshots')]
        self.f['init_db']()
        self.f['init_db']()
        with self.f['db']() as con:
            self.assertEqual(before_picks, [tuple(r) for r in con.execute('SELECT '+','.join(pick_columns)+' FROM picks')])
            self.assertEqual(before_slips, [tuple(r) for r in con.execute('SELECT * FROM slips')])
            self.assertEqual(before_snapshots, [tuple(r) for r in con.execute('SELECT * FROM odds_snapshots')])
