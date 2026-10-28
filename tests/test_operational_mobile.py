"""Operational UI regressions use authored prices and disposable SQLite only.

AppTest checks Streamlit execution and view switching; Chromium checks layout.
Neither is a claim about live source coverage or physical iPhone Safari.
"""
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import test_regressions as regression

ROOT = Path(__file__).resolve().parents[1]
TABS = ['📊 ผลวิเคราะห์ราคา', '🎟️ ตัวเลือกสเต็ป', '📚 ราคาไหลและตรวจผล', 'ℹ️ วิธีใช้']


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit unavailable locally')
class OperationalStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name)/'authored-ui.db'
        env = patch.dict(os.environ, VALUE_BET_DB=str(self.db_path),
            VALUE_BET_API_LEDGER=str(Path(self.tmp.name)/'authored-budget.db'),
            VALUE_BET_API_FOOTBALL_LEDGER=str(Path(self.tmp.name)/'authored-fallback.db'),
            THE_ODDS_API_KEY='', API_FOOTBALL_KEY='', VALUE_BET_OBSERVATION_DB='')
        env.start()
        self.addCleanup(env.stop)
        self.f = regression.load_functions(ROOT/'app.py')
        self.f['DB_FILE'] = self.db_path
        self.f['init_db']()

    def seed(self, count=11):
        from test_market_recommendations import quote
        chosen = list(self.f['LEAGUES'])[:count]
        now = datetime.now(timezone.utc)
        rows = []
        for index, key in enumerate(chosen):
            for side in ('over','under'):
                row = quote(event=f'authored-mobile-{index}', side=side,
                    pick=index*2+(side=='under')+1, odds=2.1 if side=='over' else 1.9)
                row.update(kickoff_utc=(now+timedelta(hours=3)).isoformat(),
                    kickoff_th='ข้อมูลทดสอบ · เวลาไทย', league=self.f['LEAGUES'][key][0],
                    match=f'Authored Home {index} vs Authored Away {index}',
                    home_team=f'Authored Home {index}', away_team=f'Authored Away {index}',
                    received_at=now.isoformat(), source_updated_at=now.isoformat())
                rows.append(row)
            self.f['record_api_receipt'](key, now.isoformat())
        self.f['save_picks'](rows)
        return chosen

    def assert_tabs(self, app):
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([tab.label for tab in app.tabs], TABS)

    def journal(self, chosen, *, failed=None, region='uk', hours=24):
        from scan_journal import begin, append
        with self.f['db']() as con:
            run_id=begin(con,chosen,region,hours)
            for key in chosen:
                league=self.f['LEAGUES'][key][0]
                row=con.execute('SELECT received_at FROM picks WHERE league=? ORDER BY id DESC LIMIT 1',
                    (league,)).fetchone()
                entry=dict(sport_key=key,league=league,status='league_error' if key==failed else 'success',
                    reason='http_503' if key==failed else None,
                    api_returned=None if key==failed else 1,passed_filter=None if key==failed else 1,
                    saved_fixture_count=0 if key==failed else 1,ah_fixtures=0,ou_fixtures=0 if key==failed else 1,
                    no_price_fixtures=0,api_received_at=None if key==failed else row['received_at'])
                append(con,run_id,entry)
        return run_id

    def test_latest_journal_reports_all_eleven_and_failed_league_cannot_revive_old_cards(self):
        from streamlit.testing.v1 import AppTest
        chosen=self.seed()
        self.journal(chosen,failed=chosen[3])
        original=self.f['load_picks'](limit=None).to_json()
        with patch('requests.get',side_effect=AssertionError('status and stored views must remain offline')) as get:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=40).run()
            app.multiselect[0].set_value(chosen).run()
            self.assert_tabs(app)
            cards=[item.value for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards),10)
            self.assertFalse(any('Authored Home 3 vs Authored Away 3' in card for card in cards))
            status=next(item for item in app.expander if item.label=='สถานะลีกจากรอบสแกนล่าสุด')
            statuses='\n'.join(item.value for item in status.markdown)
            for key in chosen:
                self.assertIn(self.f['LEAGUES'][key][0],statuses)
            self.assertIn('API/การประมวลผลผิดพลาด',statuses)
            self.assertTrue(any('เลือก 11 ลีก | ตรวจสำเร็จ 10 ลีก' in item.value for item in app.caption))
            app.radio[0].set_value('ตารางเดิม').run()
            self.assert_tabs(app)
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assert_tabs(app)
            get.assert_not_called()
        self.assertEqual(self.f['load_picks'](limit=None).to_json(),original)
        self.assertEqual(len(self.f['load_picks'](limit=None)),22)

    def test_changed_region_or_window_cannot_present_latest_scan_as_matching_scope(self):
        from streamlit.testing.v1 import AppTest
        chosen=self.seed(count=1)
        self.journal(chosen)
        original=self.f['load_picks'](limit=None).to_json()
        with patch('requests.get',side_effect=AssertionError('scope switch must not scan API')) as get:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=40).run()
            app.multiselect[0].set_value(chosen).run()
            self.assert_tabs(app)
            self.assertTrue(any('<article class="vbp-dashboard"' in item.value for item in app.tabs[0].markdown))
            next(item for item in app.selectbox if item.label=='ภูมิภาคราคา API').set_value('eu').run()
            self.assert_tabs(app)
            self.assertFalse(any('<article class="vbp-dashboard"' in item.value for item in app.tabs[0].markdown))
            next(item for item in app.selectbox if item.label=='ภูมิภาคราคา API').set_value('uk').run()
            next(item for item in app.selectbox if item.label=='ช่วงเวลาเตะข้างหน้า').set_value(48).run()
            self.assert_tabs(app)
            self.assertFalse(any('<article class="vbp-dashboard"' in item.value for item in app.tabs[0].markdown))
            get.assert_not_called()
        self.assertEqual(self.f['load_picks'](limit=None).to_json(),original)

    def test_eleven_saved_leagues_show_eleven_cards_and_switch_without_provider_calls(self):
        from streamlit.testing.v1 import AppTest
        chosen = self.seed()
        before = self.f['load_picks'](limit=None).to_json()
        with patch('requests.get', side_effect=AssertionError('view switching must remain offline')) as get:
            app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=40).run()
            app.multiselect[0].set_value(chosen).run()
            self.assert_tabs(app)
            cards = [item for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards), 11)
            self.assertTrue(any(metric.label=='คู่ที่ยังไม่เริ่ม' and str(metric.value)=='11' for metric in app.metric))
            app.radio[0].set_value('ตารางเดิม').run()
            self.assert_tabs(app)
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assert_tabs(app)
            get.assert_not_called()
        self.assertEqual(self.f['load_picks'](limit=None).to_json(), before)

    def test_saved_no_xg_preserves_four_tabs_and_never_displays_invented_model(self):
        from streamlit.testing.v1 import AppTest
        chosen = self.seed(count=1)
        with self.f['db']() as con:
            con.execute('UPDATE picks SET ev_pct=NULL,p_win=NULL,p_partial=NULL,p_loss=NULL,analysis_b_json=?',
                (json.dumps({'available':False,'status':'ข้อมูล xG ไม่เพียงพอ','calibrated':False}),))
        with patch('requests.get', side_effect=AssertionError('saved review must remain offline')) as get:
            app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=40).run()
            app.multiselect[0].set_value(chosen).run()
            self.assert_tabs(app)
            markup='\n'.join(item.value for item in app.tabs[0].markdown)
            self.assertIn('data-model="unavailable"',markup)
            self.assertNotIn('data-model="available"',markup)
            self.assertNotIn('ตัวเลือกเด่น',markup)
            get.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright unavailable locally')
class OperationalMobileBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_dashboard_browser as browser
        browser.DashboardBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def test_multiple_bookmakers_and_missing_xg_keep_existing_mobile_card_layout(self):
        import test_dashboard_browser as browser
        from test_dashboard import complete_row
        from test_market_recommendations import quote
        from market_recommendations import group_fixture_recommendations, RANKING
        from mobile_dashboard import fixture_dashboard_html, fixture_details_html
        f=regression.load_functions(ROOT/'app.py')
        rows=[]
        for event, xg in [('authored-complete',True),('authored-missing',False)]:
            for book in ('Book A','Book B'):
                for side in ('over','under'):
                    row=quote(event=event,book=book,side=side,pick=len(rows)+1,
                        odds=2.1 if side=='over' else 1.9,point=2.75)
                    if not xg:
                        row['analysis_b_json']=json.dumps({'available':False,'status':'xG ไม่เพียงพอ','calibrated':False})
                        row['ev_pct']=None
                    rows.append(row)
        fixtures=group_fixture_recommendations(rows,f['choose_recommendation'])
        self.assertEqual(len(fixtures),2)
        markup=''.join(fixture_dashboard_html(item)+'<details><summary>ดูรายละเอียดเพิ่มเติม</summary>'+
            fixture_details_html(item,RANKING)+'</details>' for item in fixtures)
        for width in (320,375,390,430):
            for dark in (False,True):
                with self.subTest(width=width,dark=dark):
                    page=self.browser.new_page(viewport={'width':width,'height':844},color_scheme='dark' if dark else 'light')
                    try:
                        page.set_content(browser.DashboardBrowserTests.page_content(self,complete_row(),dark))
                        page.locator('body').evaluate('(body,markup)=>body.innerHTML=markup',markup)
                        self.assertEqual(page.locator('article.vbp-dashboard').count(),2)
                        self.assertGreater(page.locator('[data-model="unavailable"]').count(),0)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        page.locator('details').evaluate_all('(items)=>items.forEach(e=>e.open=true)')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        if width==375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                            directory=Path(os.environ['DASHBOARD_SCREENSHOTS']);directory.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(directory/f'operational-mobile-{dark}.png'),full_page=True)
                    finally:
                        page.close()
