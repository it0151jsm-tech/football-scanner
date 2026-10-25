"""Actual cached request/parser -> Data Center -> SQLite -> Streamlit rendering.

HTTP responses and football facts are authored fixtures. All provider traffic is
mocked: these are software E2E tests, never live source/credit/coverage evidence.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
import uuid

import requests

from test_regressions import load_functions

ROOT=Path(__file__).resolve().parents[1]
UTC=timezone.utc


def quote_facts(quotes):
    """Compare persisted source facts, not the age that advances on every view."""
    keys=('market','side','point','odds','bookmaker','bookmaker_key','received_at',
          'source_updated_at','source_update_scope')
    return [tuple(quote.get(key) for key in keys) for quote in quotes]


class AuthoredResponse:
    def __init__(self, payload, *, status=200, used=2, remaining=498, cost=2):
        self.status_code=status
        self.headers={'x-requests-last':str(cost),'x-requests-used':str(used),
                      'x-requests-remaining':str(remaining)}
        self.payload=deepcopy(payload)
        self.content=json.dumps(payload,ensure_ascii=False).encode()
        self.text=self.content.decode()

    def json(self):
        return deepcopy(self.payload)


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Streamlit unavailable locally')
class DataCenterResponseTests(unittest.TestCase):
    def setUp(self):
        from streamlit import cache_data
        cache_data.clear()
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/'response-pilot.db'
        self.ledger=Path(self.tmp.name)/'response-budget.db'
        self.key='SYNTHETIC_RESPONSE_KEY_'+uuid.uuid4().hex
        env=patch.dict(os.environ,VALUE_BET_DB=str(self.db),VALUE_BET_API_LEDGER=str(self.ledger),
            VALUE_BET_API_FOOTBALL_LEDGER=str(Path(self.tmp.name)/'fallback.db'),
            VALUE_BET_OBSERVATION_DB='',THE_ODDS_API_KEY=self.key,API_FOOTBALL_KEY='',
            VALUE_BET_API_FOOTBALL_ENABLED='0',VALUE_BET_API_DAILY_REQUESTS='40',
            VALUE_BET_API_DAILY_CREDITS='80',VALUE_BET_API_MONTHLY_CREDITS='500',
            VALUE_BET_API_CREDIT_RESERVE='10',VALUE_BET_API_COOLDOWN_SECONDS='300')
        env.start();self.addCleanup(env.stop)
        network=patch('requests.sessions.Session.request',side_effect=AssertionError('real provider network forbidden'))
        self.network=network.start();self.addCleanup(network.stop)
        self.now=datetime.now(UTC)
        self.calls=[]
        self.failures={}
        self.f=load_functions(ROOT/'app.py')
        self.f['DB_FILE']=self.db
        self.f['init_db']()

    def tearDown(self):
        self.network.assert_not_called()

    def fixture(self, sport, *, hostile=False):
        home='<script>window.API_FIXTURE_EXECUTED=1</script>' if hostile else 'Response Fixture Home '+sport
        away='Response Fixture Away '+sport
        return {'id':'response-fixture-'+sport,'sport_key':sport,'home_team':home,'away_team':away,
            'commence_time':(self.now+timedelta(hours=4)).isoformat(),
            'bookmakers':[{'key':'response-fixture-book','title':'Response <b>Book</b>',
                'last_update':self.now.isoformat(),'markets':[
                    {'key':'spreads','outcomes':[{'name':home,'point':-.25,'price':2.1},
                        {'name':away,'point':.25,'price':1.9}]},
                    {'key':'totals','outcomes':[{'name':'Over','point':2.75,'price':2.0},
                        {'name':'Under','point':2.75,'price':1.9}]}]}]}

    def route(self, url, **kwargs):
        parsed=urlsplit(url)
        pieces=parsed.path.strip('/').split('/')
        if parsed.netloc!='api.the-odds-api.com' or not pieces or pieces[-1]!='odds':
            # Exercise real source failure handlers without any external request.
            raise requests.Timeout('PRIVATE_STATS_FAILURE_SENTINEL')
        sport=pieces[-2]
        self.calls.append(sport)
        params=kwargs.get('params',{})
        self.assertTrue(params.get('apiKey')==self.key,'only the authored key may be used')
        self.assertEqual(params.get('markets'),'spreads,totals')
        self.assertEqual(params.get('regions'),'uk')
        failure=self.failures.get(sport)
        if failure=='timeout':
            raise requests.Timeout('PRIVATE_ODDS_TIMEOUT_SENTINEL')
        if failure:
            return AuthoredResponse({'message':'PRIVATE_HTTP_BODY_SENTINEL'},status=failure,
                used=2*len(self.calls),remaining=500-2*len(self.calls))
        return AuthoredResponse([self.fixture(sport,hostile=getattr(self,'hostile',False))],
            used=2*len(self.calls),remaining=500-2*len(self.calls))

    def app(self, selected=None):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(ROOT/'app.py'),default_timeout=50).run()
        app.multiselect[0].set_value(selected or ['soccer_epl']).run()
        self.assert_app(app)
        self.assertTrue(next(item for item in app.toggle if item.label=='Football Data Center').value)
        return app

    def assert_app(self, app):
        self.assertEqual(len(app.exception),0)
        self.assertEqual(len(app.tabs),4)

    def scan(self, app):
        next(button for button in app.button if button.label=='🚀 สแกนราคาและวิเคราะห์').click().run()
        self.assert_app(app)

    def cards(self, app):
        return [item.value for item in app.tabs[0].markdown if 'data-center-fixture=' in item.value]

    def load_cards(self, selected=None):
        return self.f['load_data_center_cards'](selected or ['soccer_epl'],'uk',24)

    def test_one_response_uses_actual_parser_headers_budget_sqlite_and_escaped_card(self):
        self.hostile=True
        with patch('requests.get',side_effect=self.route) as get, \
                patch('data_center_stats.collect_statistics',side_effect=AssertionError('raw scan must not request statistics')) as stats:
            app=self.app();self.scan(app)
            get.assert_called_once();stats.assert_not_called()
            self.assertEqual(len(self.cards(app)),1)
            markup='\n'.join(self.cards(app))
            self.assertIn('&lt;script&gt;',markup)
            self.assertNotIn('<script>',markup)
            self.assertIn('Response &lt;b&gt;Book&lt;/b&gt;',markup)
            self.assertIn('2.75',markup)
            cards=self.load_cards()
            self.assertEqual(len(cards),1)
            self.assertEqual(len(cards[0]['quotes']),4)
            self.assertTrue(all(quote['source_update_scope']=='bookmaker' for quote in cards[0]['quotes']))
            self.assertTrue(cards[0]['received_at'])
            self.assertEqual(len(self.f['load_picks'](limit=None)),0)
        with sqlite3.connect(self.ledger) as con:
            self.assertEqual(con.execute('SELECT COUNT(*),SUM(credits) FROM calls').fetchone(),(1,2))
            self.assertEqual(con.execute('SELECT remaining,used FROM balances').fetchone(),(498,2))

    def test_eleven_actual_mock_responses_keep_all_leagues_and_charge_twenty_two_in_test_ledger(self):
        selected=list(self.f['LEAGUES'])[:11]
        with patch('requests.get',side_effect=self.route) as get, \
                patch('data_center_stats.collect_statistics',side_effect=AssertionError('no statistics gate')) as stats:
            app=self.app(selected);self.scan(app)
            self.assertEqual(get.call_count,11)
            self.assertEqual(self.calls,selected)
            stats.assert_not_called()
            self.assertEqual(len(self.cards(app)),11)
            cards=self.load_cards(selected)
            self.assertEqual(len(cards),11)
            self.assertEqual(sum(len(card['quotes']) for card in cards),44)
            report=self.f['latest_scan_report']()
            self.assertEqual(report['counts']['successful_leagues'],11)
            self.assertEqual(report['counts']['api_returned'],11)
        with sqlite3.connect(self.ledger) as con:
            self.assertEqual(con.execute('SELECT COUNT(*),SUM(credits) FROM calls').fetchone(),(11,22))
            self.assertEqual(con.execute('SELECT remaining,used FROM balances').fetchone(),(478,22))

    def test_mode_and_legacy_table_changes_do_not_call_provider_even_after_cache_is_cleared(self):
        from streamlit import cache_data
        with patch('requests.get',side_effect=self.route) as get:
            app=self.app();self.scan(app)
            self.assertEqual(get.call_count,1)
            cache_data.clear()
            next(item for item in app.toggle if item.label=='Football Data Center').set_value(False).run()
            self.assert_app(app)
            app.radio[0].set_value('ตารางเดิม').run();self.assert_app(app)
            app.radio[0].set_value('การ์ดมือถือ').run();self.assert_app(app)
            next(item for item in app.toggle if item.label=='Football Data Center').set_value(True).run()
            self.assert_app(app)
            self.assertEqual(len(self.cards(app)),1)
            self.assertEqual(get.call_count,1)

    def test_real_statistics_timeout_handling_preserves_already_displayed_response_prices(self):
        with patch('requests.get',side_effect=self.route) as get:
            app=self.app();self.scan(app)
            self.assertEqual(self.calls,['soccer_epl'])
            before=self.load_cards()[0]['quotes']
            with self.f['db']() as con:
                persisted_before=[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')]
            next(button for button in app.button if button.label=='โหลดสถิติที่มีสำหรับคู่ที่แสดง (ไม่เรียก Odds API)').click().run()
            self.assert_app(app)
            self.assertEqual(self.calls,['soccer_epl'])
            self.assertGreater(get.call_count,1)
            self.assertEqual(len(self.cards(app)),1)
            after=self.load_cards()[0]
            self.assertEqual(quote_facts(before),quote_facts(after['quotes']))
            with self.f['db']() as con:
                self.assertEqual(persisted_before,[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')])
            self.assertTrue(all(new['receipt_age_seconds']>=old['receipt_age_seconds']
                for old,new in zip(before,after['quotes'])))
            self.assertEqual(after['statistics']['teams']['home']['overall']['last5']['sample'],0)
            self.assertEqual(after['statistics']['teams']['home']['xg']['overall']['last5']['sample'],0)
            rendered='\n'.join(self.cards(app))+json.dumps(after['statistics'],ensure_ascii=False)
            self.assertNotIn('PRIVATE_STATS_FAILURE_SENTINEL',rendered)

    def test_http503_response_for_one_league_does_not_remove_other_league_responses(self):
        selected=['soccer_epl','soccer_spain_la_liga','soccer_italy_serie_a']
        self.failures[selected[1]]=503
        with patch('requests.get',side_effect=self.route) as get:
            app=self.app(selected);self.scan(app)
            self.assertEqual(get.call_count,3)
            self.assertEqual(self.calls,selected)
            self.assertEqual(len(self.cards(app)),2)
            report=self.f['latest_scan_report']()
            self.assertEqual(report['counts']['successful_leagues'],2)
            self.assertEqual(report['leagues'][1]['http_status'],503)
            self.assertEqual(report['leagues'][1]['status'],'league_error')
            rendered='\n'.join(self.cards(app))+'\n'.join(str(item.value) for item in app.warning)
            self.assertNotIn('PRIVATE_HTTP_BODY_SENTINEL',rendered)

    def test_http429_response_stops_remaining_real_parser_calls_and_keeps_first_fixture(self):
        selected=['soccer_epl','soccer_spain_la_liga','soccer_italy_serie_a']
        self.failures[selected[1]]=429
        with patch('requests.get',side_effect=self.route) as get:
            app=self.app(selected);self.scan(app)
            self.assertEqual(get.call_count,2)
            self.assertEqual(self.calls,selected[:2])
            self.assertEqual(len(self.cards(app)),1)
            report=self.f['latest_scan_report']()
            self.assertEqual([row['status'] for row in report['leagues']],
                ['success','safety_stop','skipped_safety_stop'])
            self.assertTrue(any('หยุดสแกน' in str(item.value) for item in app.warning))
        with sqlite3.connect(self.ledger) as con:
            self.assertTrue(con.execute('SELECT blocked FROM balances').fetchone()[0])

    def test_explicit_analysis_reuses_saved_http_response_after_odds_cache_expiry(self):
        from streamlit import cache_data
        with patch('requests.get',side_effect=self.route):
            app=self.app();self.scan(app)
            cache_data.clear()
            button=next(button for button in app.button if button.key=='dc_analyze')
            button.click().run();self.assert_app(app)
            self.assertEqual(self.calls,['soccer_epl'])
            self.assertEqual(len(self.cards(app)),1)
            saved=self.f['load_picks'](limit=None)
            self.assertEqual(len(saved),4)
            self.assertTrue(saved['ev_pct'].isna().all())
            self.assertTrue(all(not json.loads(raw)['available'] for raw in saved['analysis_b_json']))


if __name__=='__main__':
    unittest.main()
