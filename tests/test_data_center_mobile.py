"""Data Center UI acceptance uses authored facts and temporary SQLite only.

These tests establish software/render behavior, not live football coverage,
source entitlement, physical Safari compatibility or model performance.
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

ROOT=Path(__file__).resolve().parents[1]
TABS=['📊 ผลวิเคราะห์ราคา','🎟️ ตัวเลือกสเต็ป','📚 ราคาไหลและตรวจผล','ℹ️ วิธีใช้']


def authored_card(*, event_id='synthetic-dc-browser', missing=False, hostile=False):
    """Authored shape only: never a real provider observation."""
    at=datetime.now(timezone.utc)
    team='<img src=x onerror="window.PWNED=1">'+'LONGTEAM'*25 if hostile else 'Synthetic Home'
    book='Book '+('ABCDEFGHIJKLMNOPQRSTUVWXYZ'*8 if hostile else 'Fixture')
    quotes=[]
    if not missing:
        for market,side,line,odds in [('Asian Handicap','home',-.25,2.1),
                ('Asian Handicap','away',.25,1.9),('Over/Under','over',2.75,2.05),
                ('Over/Under','under',2.75,1.85)]:
            quotes.append(dict(market=market,side=side,point=line,odds=odds,
                bookmaker=book,bookmaker_key='fixture-book',received_at=at.isoformat(),
                source_updated_at=at.isoformat(),source_update_scope='bookmaker',
                first_observed=dict(label='First Observed Price',odds=odds-.05,point=line,
                    received_at=(at-timedelta(minutes=2)).isoformat(),line_changed=False),
                opening_odds=None,opening_status='unavailable'))
    card=dict(run_id='synthetic-browser-run',event_id=event_id,sport_key='soccer_epl',csv_code='E0',
        league='ลีกทดสอบ — ไม่ใช่ข้อมูลจริง',home_team=team,away_team='Synthetic Away',
        kickoff_utc=(at+timedelta(hours=4)).isoformat(),kickoff_th='25/10/2026 21:00',
        received_at=at.isoformat(),quotes=quotes,data_status='no_prices' if missing else 'available',
        statistics=None)
    if not missing:
        import pandas as pd
        from data_center_stats import collect_statistics
        rows=[]
        for index in range(10):
            home,away=(team,'Synthetic Away') if index%2==0 else ('Synthetic Away',team)
            day=(at-timedelta(days=3+index)).date()
            season_start=day.year if day.month>=7 else day.year-1
            rows.append(dict(Date_dt=pd.Timestamp(day),
                HomeTeam=home,AwayTeam=away,FTHG=2,FTAG=1,
                _history_season=f'{season_start%100:02}{(season_start+1)%100:02}'))
        scores=pd.DataFrame(rows)
        source=dict(status='ok',sources=[dict(url='https://example.invalid/'+('authored-source-'*20),
            received_at=(at-timedelta(minutes=5)).isoformat(),season=rows[0]['_history_season'])])
        scores.attrs['history_diagnostics']=source
        xg=scores.copy();xg['xG_home']=1.7;xg['xG_away']=.9
        xg.attrs['xg_diagnostics']=source
        card['statistics']=collect_statistics(card,fetch_history=lambda _:scores,
            fetch_xg=lambda _:xg,now=at)
    return card


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Streamlit unavailable locally')
class DataCenterStreamlitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'authored-data-center.db'
        env=patch.dict(os.environ,VALUE_BET_DB=str(self.path),
            VALUE_BET_API_LEDGER=str(Path(self.tmp.name)/'budget.db'),
            VALUE_BET_API_FOOTBALL_LEDGER=str(Path(self.tmp.name)/'fallback.db'),
            VALUE_BET_OBSERVATION_DB='',THE_ODDS_API_KEY='',API_FOOTBALL_KEY='')
        env.start();self.addCleanup(env.stop)
        self.f=regression.load_functions(ROOT/'app.py')
        self.f['DB_FILE']=self.path
        self.f['init_db']()

    def seed(self, *, markets=True, statistics=None):
        from data_center_store import initialize,save_batch,save_enrichment
        from scan_diagnostics import filter_scan_fixtures
        from scan_journal import begin,append
        now=datetime.now(timezone.utc)
        key='soccer_epl';league=self.f['LEAGUES'][key][0]
        home,away='Synthetic Data Home','Synthetic Data Away'
        books=[dict(key='authored-book',title='Authored bookmaker',last_update=now.isoformat(),markets=[
            dict(key='spreads',outcomes=[dict(name=home,point=-.25,price=2.1),dict(name=away,point=.25,price=1.9)]),
            dict(key='totals',outcomes=[dict(name='Over',point=2.75,price=2.0),dict(name='Under',point=2.75,price=1.9)])])]
        event=dict(id='synthetic-dc-mobile',sport_key=key,home_team=home,away_team=away,
            commence_time=(now+timedelta(hours=4)).isoformat(),_received_at=now.isoformat(),
            bookmakers=books if markets else [])
        _,report=filter_scan_fixtures([event],key,now,24)
        with self.f['db']() as con:
            initialize(con)
            run=begin(con,[key],'uk',24)
            save_batch(con,run,key,league,[event],now.isoformat(),report,remaining='498',used='2',region='uk')
            append(con,run,dict(sport_key=key,league=league,status='success',reason=None,
                api_returned=1,passed_filter=1,excluded_count=0,excluded_by_reason={},
                saved_fixture_count=1,market_rows=4 if markets else 1,snapshot_rows=4 if markets else 0,
                ah_fixtures=int(markets),ou_fixtures=int(markets),no_price_fixtures=int(not markets),
                api_received_at=now.isoformat(),provider_remaining=498,provider_used=2))
            if statistics is not None:
                save_enrichment(con,run,event['id'],statistics)
        return run,event

    def app(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=40).run()
        app.multiselect[0].set_value(['soccer_epl']).run()
        return app

    def assert_app(self,app):
        self.assertEqual(len(app.exception),0)
        self.assertEqual([tab.label for tab in app.tabs],TABS)

    def cards(self,app):
        return [item.value for item in app.tabs[0].markdown if 'data-center-fixture=' in item.value]

    def test_raw_fixture_prices_render_without_any_analysis_or_statistics(self):
        self.seed()
        with patch('requests.get',side_effect=AssertionError('view must remain offline')) as get:
            app=self.app();self.assert_app(app)
            toggle=next(item for item in app.toggle if item.label=='Football Data Center')
            self.assertTrue(toggle.value)
            markup='\n'.join(self.cards(app))
            self.assertIn('Synthetic Data Home',markup)
            self.assertIn('Synthetic Data Away',markup)
            self.assertIn('2.75',markup)
            self.assertIn('Authored bookmaker',markup)
            self.assertIn('ไม่มีข้อมูล',markup)
            self.assertNotIn('ตัวเลือกเด่น',markup)
            self.assertEqual(len(self.f['load_picks'](limit=None)),0)
            get.assert_not_called()

    def test_no_market_prices_still_show_fixture_and_explicit_missing_state(self):
        self.seed(markets=False)
        with patch('requests.get',side_effect=AssertionError('view must remain offline')) as get:
            app=self.app();self.assert_app(app)
            markup='\n'.join(self.cards(app))
            self.assertIn('Synthetic Data Home',markup)
            self.assertTrue(any(message in markup for message in ('ไม่มีราคา','ไม่มีข้อมูล')))
            self.assertNotIn('Opening Odds 2',markup)
            get.assert_not_called()

    def test_mode_view_and_scope_changes_do_not_spend_api_or_mutate_saved_data(self):
        self.seed()
        with self.f['db']() as con:
            before=[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')]
        with patch('requests.get',side_effect=AssertionError('view switch must not scan')) as get:
            app=self.app();self.assert_app(app)
            next(item for item in app.toggle if item.label=='Football Data Center').set_value(False).run()
            self.assert_app(app)
            next(item for item in app.toggle if item.label=='Football Data Center').set_value(True).run()
            self.assert_app(app)
            self.assertIn('Synthetic Data Home','\n'.join(self.cards(app)))
            app.radio[0].set_value('ตารางเดิม').run();self.assert_app(app)
            app.radio[0].set_value('การ์ดมือถือ').run();self.assert_app(app)
            next(item for item in app.selectbox if item.label=='ช่วงเวลาเตะข้างหน้า').set_value(48).run()
            self.assert_app(app)
            self.assertFalse(self.cards(app))
            get.assert_not_called()
        with self.f['db']() as con:
            self.assertEqual(before,[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')])

    def test_explicit_statistics_failure_never_removes_raw_fixture_or_echoes_error_body(self):
        self.seed()
        with patch('requests.get',side_effect=AssertionError('statistics fixture must not request real API')) as get, \
                patch('data_center_stats.collect_statistics',side_effect=RuntimeError('DO_NOT_ECHO_KEY')):
            app=self.app();self.assert_app(app)
            button=next(item for item in app.button if item.label=='โหลดสถิติที่มีสำหรับคู่ที่แสดง (ไม่เรียก Odds API)')
            button.click().run();self.assert_app(app)
            markup='\n'.join(self.cards(app))
            self.assertIn('Synthetic Data Home',markup)
            rendered='\n'.join(str(getattr(item,'value','')) for item in list(app.markdown)+list(app.warning)+list(app.error))
            self.assertNotIn('DO_NOT_ECHO_KEY',rendered)
            get.assert_not_called()


@unittest.skipUnless(importlib.util.find_spec('playwright'),'Playwright unavailable locally')
class DataCenterMobileBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_dashboard_browser as browser
        browser.DashboardBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close();cls.runtime.stop()

    def test_data_center_quotes_missing_statistics_and_api_text_fit_iphone_widths(self):
        from data_center_ui import DATA_CENTER_CSS,data_center_card_html
        cards=[authored_card(hostile=True),authored_card(event_id='synthetic-no-price',missing=True)]
        for width in (320,375,390,430):
            for dark in (False,True):
                with self.subTest(width=width,dark=dark):
                    page=self.browser.new_page(viewport={'width':width,'height':844},color_scheme='dark' if dark else 'light')
                    try:
                        colors='--background-color:#0e1117;--secondary-background-color:#262730;--text-color:#fafafa' if dark else '--background-color:#fff;--secondary-background-color:#f0f2f6;--text-color:#182332'
                        content='<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'+DATA_CENTER_CSS+'<style>body{margin:0;padding:10px;font-family:Arial,sans-serif;'+colors+';background:var(--background-color)}</style></head><body>'+''.join(data_center_card_html(card) for card in cards)+'</body></html>'
                        page.set_content(content)
                        self.assertEqual(page.locator('article.dc-card').count(),2)
                        self.assertEqual(page.locator('img,script').count(),0)
                        self.assertIsNone(page.evaluate('window.PWNED'))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        page.locator('details').evaluate_all('(items)=>items.forEach(item=>item.open=true)')
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
                        if width==375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                            directory=Path(os.environ['DASHBOARD_SCREENSHOTS']);directory.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(directory/f'data-center-{dark}.png'),full_page=True)
                    finally:
                        page.close()
