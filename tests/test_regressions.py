"""Offline regression tests: execute app functions without starting Streamlit."""
import ast
import json
import math
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
from pathlib import Path

import pandas as pd


def load_functions(path):
    tree = ast.parse(path.read_text())
    ns = dict(pd=pd, math=math, re=re, json=json, sqlite3=sqlite3,
              datetime=datetime, timedelta=timedelta, timezone=timezone,
              get_close_matches=get_close_matches, TH_TZ=timezone(timedelta(hours=7)),
              UTC=timezone.utc, MAX_XG_AGE_DAYS=120)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id in
                {'LEAGUES', 'EUROPEAN_CUPS', 'UNDERSTAT_LEAGUES', 'MAX_XG_AGE_DAYS'} for t in node.targets):
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), ns)
    return ns


class Regressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = load_functions(Path(__file__).resolve().parents[1] / 'app.py')
        self.f['DB_FILE'] = Path(self.tmp.name) / 'test.db'
        self.f['init_db']()
        self.row = dict(event_id='event', market='Asian Handicap', side='home',
                        bookmaker='Book', point=-0.25, odds=2.0, selection='home (-0.25)',
                        league=self.f['LEAGUES']['soccer_epl'][0])

    def pick(self):
        return self.f['load_picks']().iloc[0].to_dict()

    def save_bill(self):
        from test_market_recommendations import quote
        evidence = quote('Asian Handicap', -.25, 'home', 'Book', odds=2.0)
        self.row.update({k: v for k, v in evidence.items() if k not in ('id', 'event_id')})
        self.row['kickoff_utc'] = (datetime.now(timezone.utc)+timedelta(hours=12)).isoformat()
        self.f['save_picks']([self.row])
        return self.f['save_slip_record'](pd.DataFrame([self.pick()]), 2.0, 0.5)

    def test_saved_quote_survives_rescans_and_settles_idempotently(self):
        self.save_bill()
        self.row.update(point=0.25, odds=1.9, selection='home (+0.25)')
        self.f['save_picks']([self.row])
        self.f['save_picks']([self.row])
        picks = self.f['load_picks']()
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks.loc[picks.id == 1, 'point'].iloc[0], -0.25)
        self.f['update_pick_result'](1, '0-0', 'ชนะครึ่ง', 0.5)
        self.assertEqual(self.f['refresh_slip_settlements'](), 1)
        self.assertEqual(self.f['load_slips']().iloc[0].profit_units, -0.5)
        self.assertEqual(self.f['refresh_slip_settlements'](), 0)

    def test_legacy_bill_uses_original_selection_not_changed_pick(self):
        self.f['save_picks']([self.row])
        leg = dict(pick_id=1, market='Asian Handicap', selection='home (-0.25)', odds=2.0)
        with self.f['db']() as con:
            con.execute("INSERT INTO slips(selections_json,status) VALUES (?,?)", (json.dumps([leg]), 'รอตรวจผล'))
            con.execute('UPDATE picks SET point=0.25')
        self.f['update_pick_result'](1, '0-0', 'ชนะครึ่ง', 0.5)
        self.f['refresh_slip_settlements']()
        self.assertEqual(self.f['load_slips']().iloc[0].profit_units, -0.5)

    def test_all_history_includes_old_pending_and_old_profit(self):
        self.save_bill()
        with self.f['db']() as con:
            con.executemany('INSERT INTO picks(event_id) VALUES (?)', [(str(i),) for i in range(5001)])
        self.assertNotIn(1, self.f['load_picks']().id.tolist())
        self.assertIn(1, self.f['load_picks'](limit=None).id.tolist())
        self.f['update_pick_result'](1, '1-0', 'ชนะเต็ม', 1)
        all_picks = self.f['load_picks'](limit=None)
        self.assertEqual(all_picks[all_picks.status == 'ตรวจแล้ว'].profit_units.sum(), 1)
        self.f['refresh_slip_settlements']()
        self.assertEqual(self.f['load_slips']().iloc[0].profit_units, 1)

    def test_ou_sample_deduplicates_shared_fixture(self):
        hist = pd.DataFrame([dict(Date_dt=pd.Timestamp('2026-10-01'), HomeTeam='A', AwayTeam='B', FTHG=2, FTAG=1)])
        result = self.f['estimate_form_signal'](hist, 'A', 'B', datetime(2026, 10, 20, tzinfo=timezone.utc), 'totals', 'over', 2.5)
        self.assertEqual(result['form_sample'], 1)
        self.assertEqual(result['form_rate'], 100)

    def test_xg_freshness_requires_both_venue_samples(self):
        kickoff = datetime(2026, 10, 20, tzinfo=timezone.utc)
        def history(home_age, away_age):
            rows = []
            for home, away, age in [('A', 'C', home_age), ('D', 'B', away_age)]:
                for i in range(6):
                    rows.append(dict(Date_dt=pd.Timestamp(kickoff.replace(tzinfo=None)) - pd.Timedelta(days=age+i),
                                     HomeTeam=home, AwayTeam=away, xG_home=1.5, xG_away=1.0))
            return pd.DataFrame(rows).sort_values('Date_dt')
        self.assertIsNotNone(self.f['estimate_goals'](history(120, 120), 'A', 'B', kickoff))
        for ages in [(121, 1), (1, 121), (400, 400)]:
            self.assertIsNone(self.f['estimate_goals'](history(*ages), 'A', 'B', kickoff))
        self.assertIsNotNone(self.f['estimate_goals'](history(1, 1), 'A', 'B', kickoff))

    def test_unsupported_bill_rejected_without_losing_existing_data(self):
        self.save_bill()
        row = self.pick()
        row['league'] = self.f['LEAGUES']['soccer_usa_mls'][0]
        with self.assertRaises(ValueError):
            self.f['save_slip_record'](pd.DataFrame([row]), 2, None)
        self.assertEqual(len(self.f['load_slips']()), 1)
        self.f['init_db']()
        self.assertEqual(len(self.f['load_slips']()), 1)
        self.assertEqual(len(self.f['load_picks']()), 1)

    def test_schema_upgrade_preserves_legacy_rows(self):
        self.save_bill()
        before = self.f['load_slips']().iloc[0].selections_json
        with self.f['db']() as con:
            con.execute('DROP TRIGGER protect_settled_slip')
            con.execute('ALTER TABLE slips DROP COLUMN profit_units')
            con.execute('ALTER TABLE slips DROP COLUMN final_summary')
            con.execute('ALTER TABLE picks DROP COLUMN confidence_score')
        self.f['init_db']()
        self.f['init_db']()
        self.assertEqual(self.f['load_slips']().iloc[0].selections_json, before)
        self.assertEqual(self.pick()['point'], -.25)
        self.assertIn('profit_units', self.f['load_slips']().columns)
        self.assertIn('confidence_score', self.f['load_picks']().columns)

    def test_legacy_over_under_bill_keeps_its_original_line(self):
        self.row.update(market='Over/Under', side='over', point=2.75, selection='สูง (Over 2.75)')
        self.f['save_picks']([self.row])
        leg = dict(pick_id=1, market='Over/Under', selection='สูง (Over 2.25)', odds=2)
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status) VALUES (?,?)', (json.dumps([leg]), 'รอตรวจผล'))
        self.f['update_pick_result'](1, '2-1', 'ชนะครึ่ง', .5)
        self.f['refresh_slip_settlements']()
        self.assertEqual(self.f['load_slips']().iloc[0].profit_units, 1)

    def test_unreadable_legacy_line_is_not_guessed(self):
        self.f['save_picks']([self.row])
        leg = dict(pick_id=1, market='Asian Handicap', selection='unrecognized', odds=2)
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status) VALUES (?,?)', (json.dumps([leg]), 'รอตรวจผล'))
        self.f['update_pick_result'](1, '0-0', 'แพ้ครึ่ง', -.5)
        self.assertEqual(self.f['refresh_slip_settlements'](), 0)
        self.assertEqual(self.f['load_slips']().iloc[0].status, 'รอตรวจผล')

    def test_damaged_legacy_json_never_blocks_scans_or_rewrites_bills(self):
        payloads = [None, '', '   ', '[]', '{broken', 'null', '{}', '7', '"text"',
                    '[{}]', '[null]', '[4]', '[[]]', '[{"pick_id":null}]',
                    '[{"pick_id":true}]', '[{"pick_id":1.5}]',
                    '[{"pick_id":"bad"}]', '[{"pick_id":0}]',
                    '[{"pick_id":-1}]', '[{"pick_id":9223372036854775808}]',
                    '[{"pick_id":1},{}]', b'\xff']
        for index, payload in enumerate(payloads):
            with self.subTest(payload=payload):
                self.f['DB_FILE'] = Path(self.tmp.name) / f'damaged-{index}.db'
                self.f['init_db']()
                self.f['save_picks']([self.row])
                with self.f['db']() as con:
                    for status in ['รอตรวจผล', 'แพ้บิล']:
                        con.execute('''INSERT INTO slips
                            (selections_json,status,profit_units,final_summary)
                            VALUES (?,?,?,?)''', (payload, status, -1 if status == 'แพ้บิล' else None, 'original summary'))
                    before = [tuple(r) for r in con.execute('SELECT * FROM slips ORDER BY id')]
                changed = dict(self.row, point=.25, odds=1.9)
                snapshot = dict(event_id='event', market='Asian Handicap', side='home',
                                bookmaker='Book', point=.25, odds=1.9)
                self.assertEqual(self.f['save_picks']([changed], [snapshot]), (1, 1))
                # Every existing bill, including finalized bills, stays byte-for-byte intact.
                self.assertEqual(self.f['refresh_slip_settlements'](), 0)
                with self.f['db']() as con:
                    after = [tuple(r) for r in con.execute('SELECT * FROM slips ORDER BY id')]
                    self.assertEqual(before, after)
                    self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0], 1)
                    if payload not in [None, '', '   ', '[]']:
                        self.assertEqual(con.execute('SELECT point FROM picks WHERE id=1').fetchone()[0], -.25)

    def test_legacy_string_ids_still_lock_picks(self):
        self.f['save_picks']([self.row])
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json) VALUES (?)', ('[{"pick_id":" 1 "}]',))
        self.f['save_picks']([dict(self.row, point=.25)])
        picks = self.f['load_picks']()
        self.assertEqual(len(picks), 2)
        self.assertEqual(picks.loc[picks.id == 1, 'point'].iloc[0], -.25)

    def test_valid_pending_bill_settles_beside_damaged_and_finalized_bills(self):
        self.save_bill()
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status) VALUES (?,?)', ('[{"pick_id":1},{}]', 'รอตรวจผล'))
            con.execute('''INSERT INTO slips(selections_json,status,profit_units,final_summary)
                           VALUES (?,?,?,?)''', ('{bad', 'แพ้บิล', -1, 'keep final result'))
            protected = [tuple(r) for r in con.execute('SELECT * FROM slips WHERE id>1 ORDER BY id')]
        self.f['update_pick_result'](1, '0-0', 'แพ้ครึ่ง', -.5)
        self.assertEqual(self.f['refresh_slip_settlements'](), 1)
        self.assertEqual(self.f['load_slips']().query('id == 1').iloc[0].profit_units, -.5)
        self.assertEqual(self.f['refresh_slip_settlements'](), 0)
        with self.f['db']() as con:
            self.assertEqual(protected, [tuple(r) for r in con.execute('SELECT * FROM slips WHERE id>1 ORDER BY id')])

    def test_quarter_lines_expected_returns(self):
        for market, side, point, hg, ag, expected in [
            ('Asian Handicap', 'home', -.25, 0, 0, -.5),
            ('Asian Handicap', 'away', .25, 0, 0, .5),
            ('Asian Handicap', 'home', -.75, 1, 0, .5),
            ('Over/Under', 'over', 2.25, 1, 1, -.5),
            ('Over/Under', 'under', 2.25, 1, 1, .5),
            ('Over/Under', 'over', 2, 1, 1, 0),
        ]:
            with self.subTest(market=market, side=side, point=point):
                result = self.f['settle_pick'](dict(market=market, side=side, point=point, odds=2), hg, ag)
                self.assertEqual(result[1], expected)

    def test_ev_matches_weighted_settlement_for_92_markets(self):
        matrix = self.f['score_matrix'](1.7, 1.1)
        self.assertAlmostEqual(sum(p for _, _, p in matrix), 1)
        for market, sides in [('spreads', ('home', 'away')), ('totals', ('over', 'under'))]:
            for side in sides:
                for q in range(-12 if market == 'spreads' else 0, 17):
                    point = q / 4
                    with self.subTest(market=market, side=side, point=point):
                        actual = self.f['evaluate_market_poisson'](matrix, market, side, point, 1.95)['ev_pct']
                        row = dict(market='Asian Handicap' if market == 'spreads' else 'Over/Under', side=side, point=point, odds=1.95)
                        expected = sum(p*self.f['settle_pick'](row, h, a)[1] for h, a, p in matrix)*100
                        self.assertAlmostEqual(actual, expected, delta=.011)


if __name__ == '__main__':
    unittest.main()
