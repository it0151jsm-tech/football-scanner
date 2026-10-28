"""Streamlit integration smoke tests; CI installs the app requirements."""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from test_regressions import load_functions


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed locally')
class StreamlitSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(__file__).resolve().parents[1] / 'app.py'
        self.db_path = Path(self.tmp.name) / 'smoke.db'
        self.env = patch.dict(os.environ, VALUE_BET_DB=str(self.db_path))
        self.env.start()
        self.addCleanup(self.env.stop)

    def app(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_file(str(self.path), default_timeout=20).run()
        app.multiselect[0].set_value(['soccer_epl']).run()
        return app

    def test_existing_four_tabs_start_with_empty_database(self):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.tabs), 4)

    def test_damaged_legacy_bills_keep_four_tabs_and_original_results(self):
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        f['save_picks']([dict(event_id='old', market='Asian Handicap', side='home',
                             league=f['LEAGUES']['soccer_epl'][0], bookmaker='Book',
                             point=-.25, odds=2, selection='ต่อ A (-0.25)', match='A vs B')])
        with f['db']() as con:
            for payload in [None, '', '  ', '{bad', 'null', '{}', '[{}]', '[null]', '[{"pick_id":1},{}]']:
                for status in ['รอตรวจผล', 'แพ้บิล']:
                    con.execute('''INSERT INTO slips(selections_json,status,profit_units,final_summary)
                                   VALUES (?,?,?,?)''', (payload, status, -1, 'original result'))
            before = [tuple(r) for r in con.execute('SELECT * FROM slips ORDER BY id')]
        f['save_picks']([dict(event_id='new', market='Asian Handicap', side='home',
                             bookmaker='Book', point=0, odds=2)])
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([tab.label for tab in app.tabs],
                         ['📊 ผลวิเคราะห์ราคา', '🎟️ ตัวเลือกสเต็ป', '📚 ราคาไหลและตรวจผล', 'ℹ️ วิธีใช้'])
        with f['db']() as con:
            self.assertEqual(before, [tuple(r) for r in con.execute('SELECT * FROM slips ORDER BY id')])

    def test_legacy_unsupported_bill_can_be_manually_settled(self):
        import json
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        f['save_picks']([dict(event_id='legacy', market='Asian Handicap', side='home',
                             league=f['LEAGUES']['soccer_usa_mls'][0], bookmaker='Book',
                             kickoff_utc=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
                             home_team='A', away_team='B', match='A vs B',
                             point=-.25, odds=2, selection='ต่อ A (-0.25)')])
        with f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status) VALUES (?,?)',
                        (json.dumps([dict(pick_id=1, market='Asian Handicap', selection='ต่อ A (-0.25)', odds=2)]), 'รอตรวจผล'))
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == 'บันทึกผลที่ยืนยันแล้ว').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(f['load_slips']().iloc[0].profit_units, -.5)
        self.assertEqual(f['load_picks']().iloc[0].status, 'ตรวจแล้ว')

    def test_mobile_cards_and_table_switch_do_not_call_odds_api(self):
        import json
        from unittest.mock import Mock
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        form = dict(form_rate=80, form_sample=10)
        a = f['system_a_analysis'](form, form, form, {'summary': 'ฟอร์มเหย้า'},
                                   {'summary': 'ฟอร์มเยือน'}, {}, {'home': None, 'away': None}, {})
        b = f['system_b_analysis'](None, None, None, [(2.5, 1)], 2.5)
        f['save_picks']([dict(event_id='future', market='Over/Under', side='over',
                             league=f['LEAGUES']['soccer_epl'][0], bookmaker='Book',
                             kickoff_utc=(datetime.now(timezone.utc)+timedelta(hours=5)).replace(second=0,microsecond=0).isoformat(),
                             kickoff_th='เวลาไทยทดสอบ', home_team='A', away_team='B', match='A vs B',
                             point=2.5, odds=2, selection='สูง (Over 2.5)', confidence_score=80,
                             analysis_a_json=json.dumps(a), analysis_b_json=json.dumps(b))])
        # Every HTTP request is forbidden on initial render and all view reruns.
        with patch('requests.get', side_effect=AssertionError('view must not request API')) as get:
            app = self.app()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(app.radio[0].value, 'การ์ดมือถือ')
            self.assertTrue(any('ระบบ A:' in item.value for item in app.tabs[0].markdown))
            self.assertTrue(any('class="vbp-dashboard"' in item.value for item in app.tabs[0].markdown))
            self.assertTrue(any('ข้อมูลโมเดลไม่เพียงพอ' in item.value for item in app.tabs[0].markdown))
            self.assertTrue(any(item.label == 'ดูรายละเอียดเพิ่มเติม' for item in app.tabs[0].expander))
            self.assertTrue(any('ตัวเลือกตลาดที่กำลังประเมิน' in item.value for item in app.tabs[0].markdown))
            self.assertFalse(any('ตัวเลือกเด่น' in item.value for item in app.tabs[0].markdown))
            app.radio[0].set_value('ตารางเดิม').run()
            self.assertEqual(len(app.exception), 0)
            self.assertGreater(len(app.dataframe), 0)
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)
            get.assert_not_called()

    def test_new_analysis_migration_preserves_finalized_bills_on_render(self):
        import json
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        with f['db']() as con:
            # Simulate a database from the PR #1 schema, before Phase 1 columns.
            con.execute('ALTER TABLE picks DROP COLUMN analysis_a_json')
            con.execute('ALTER TABLE picks DROP COLUMN analysis_b_json')
            con.execute('''INSERT INTO slips(selections_json,status,profit_units,final_summary)
                           VALUES (?,?,?,?)''', (json.dumps([{'pick_id': 500}]), 'แพ้บิล', -1, 'keep finalized'))
            before = [tuple(r) for r in con.execute('SELECT * FROM slips')]
        with patch('requests.get', side_effect=AssertionError('render must be offline')) as get:
            app = self.app()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)
            get.assert_not_called()
        with f['db']() as con:
            self.assertEqual(before, [tuple(r) for r in con.execute('SELECT * FROM slips')])
            columns = {r[1] for r in con.execute('PRAGMA table_info(picks)')}
            self.assertTrue({'analysis_a_json', 'analysis_b_json'} <= columns)

    def test_complete_dashboard_renders_saved_model_without_http_or_data_changes(self):
        from test_market_recommendations import quote
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        row = quote()
        opposite = quote(side='under',odds=1.85,pick=2)
        row.update(event_id='complete', side='over', kickoff_utc=(datetime.now(timezone.utc)+timedelta(hours=5)).replace(second=0,microsecond=0).isoformat())
        opposite.update(event_id='complete',kickoff_utc=row['kickoff_utc'])
        f['save_picks']([row,opposite])
        with f['db']() as con:
            con.execute('''INSERT INTO slips(selections_json,status,profit_units,final_summary)
                           VALUES (?,?,?,?)''', ('[{"pick_id":1}]', 'แพ้บิล', -1, 'original final result'))
            before_picks = [tuple(r) for r in con.execute('SELECT * FROM picks')]
            before_bills = [tuple(r) for r in con.execute('SELECT * FROM slips')]
        with patch('requests.get', side_effect=AssertionError('presentation must stay offline')) as get:
            app = self.app()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)
            cards = [item.value for item in app.tabs[0].markdown if 'class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards), 1)
            self.assertIn('data-model="available"', cards[0])
            self.assertIn('class="vbp-bar"', cards[0])
            self.assertIn('เส้นตลาดจริง', cards[0])
            self.assertNotIn('FIRST-TIMESTAMP', cards[0])
            self.assertTrue(any(item.label == 'ดูรายละเอียดเพิ่มเติม' for item in app.tabs[0].expander))
            get.assert_not_called()
        with f['db']() as con:
            self.assertEqual(before_picks, [tuple(r) for r in con.execute('SELECT * FROM picks')])
            self.assertEqual(before_bills, [tuple(r) for r in con.execute('SELECT * FROM slips')])

    def test_grouped_ah_ou_multiple_books_and_view_switch_are_offline(self):
        from test_market_recommendations import quote
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        ah = quote('Asian Handicap', -.25, 'home', 'AH Book', pick=1)
        ou = quote(point=2.75, book='OU Book', pick=2)
        other = quote(point=3, book='Other Book', pick=3, odds=1.05)
        under = quote(point=2.75,side='under',odds=1.85,pick=4)
        for row in [ah, ou, other, under]:
            row['kickoff_utc'] = (datetime.now(timezone.utc)+timedelta(hours=5)).replace(second=0,microsecond=0).isoformat()
        f['save_picks']([ah, ou, other, under], [dict(event_id='event', market=ou['market'], side=ou['side'],
                                            point=ou['point'], odds=ou['odds'], bookmaker=ou['bookmaker'], bookmaker_key=ou['bookmaker_key'])])
        with f['db']() as con:
            before = {table: [tuple(r) for r in con.execute('SELECT * FROM '+table)] for table in ['picks', 'slips', 'odds_snapshots']}
        with patch('requests.get', side_effect=AssertionError('views must remain offline')) as get:
            app = self.app()
            self.assertEqual(len(app.exception), 0)
            cards = [item.value for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards), 1)
            self.assertIn('data-market="Asian Handicap"', cards[0])
            self.assertIn('data-market="Over/Under"', cards[0])
            self.assertIn('AH Book', cards[0])
            self.assertIn('OU Book', cards[0])
            self.assertNotIn('Other Book', cards[0])
            self.assertIn('Snapshot #', cards[0])
            app.radio[0].set_value('ตารางเดิม').run()
            self.assertEqual(len(app.exception), 0)
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)
            self.assertEqual(sum('<article class="vbp-dashboard"' in item.value for item in app.tabs[0].markdown), 1)
            get.assert_not_called()
        with f['db']() as con:
            after = {table: [tuple(r) for r in con.execute('SELECT * FROM '+table)] for table in before}
            self.assertEqual(before, after)

    def test_manual_form_submit_is_offline_and_preserves_existing_data(self):
        from test_market_recommendations import quote
        f=load_functions(self.path);f['DB_FILE']=self.db_path;f['init_db']()
        rows=[quote(side=s,odds=p,pick=i) for i,(s,p) in enumerate([('over',1.95),('under',1.95)],1)]
        for row in rows:
            row['kickoff_utc']=(datetime.now(timezone.utc)+timedelta(hours=5)).replace(second=0,microsecond=0).isoformat()
        f['save_picks'](rows)
        with f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES (?,?,?,?)',('{bad','แพ้บิล',-1,'unchanged'))
            before={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in ('picks','slips','odds_snapshots')}
        with patch('requests.get',side_effect=AssertionError('manual form must remain offline')) as get:
            app=self.app()
            next(w for w in app.text_input if w.label=='เว็บ/เจ้ามือ (Manual)').set_value('My Book')
            for label,value in [('เส้นสูง–ต่ำ (Manual)',3.25),('ราคาสูง (Manual)',1.98),('ราคาต่ำ (Manual)',1.91)]:
                next(w for w in app.number_input if w.label==label).set_value(value)
            next(w for w in app.button if w.label=='บันทึก Manual OU Quote').click().run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(len(app.tabs),4)
            self.assertEqual(len(f['load_manual_quotes']('event')),1)
            app.radio[0].set_value('ตารางเดิม').run()
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assertEqual(len(app.exception),0)
            self.assertEqual(len(f['load_manual_quotes']('event')),1)
            get.assert_not_called()
        with f['db']() as con:
            self.assertEqual(before,{t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in before})
