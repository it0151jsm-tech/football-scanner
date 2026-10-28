"""Operational safety on authored data and disposable SQLite files only.

No assertions below constitute live-provider availability or predictive skill.
"""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

import pandas as pd

from bill_backup import backup_bytes,restore_new
from market_recommendations import recommended_pick_ids
from odds_budget import LeagueScanError,OddsBatch,ScanStopped
import scan_journal
from test_market_recommendations import quote
from test_operational_e2e import OperationalHarness
import test_regressions as regression

ROOT=Path(__file__).resolve().parents[1]
UTC=timezone.utc


class OperationalSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name)
        self.environment=patch.dict(os.environ,VALUE_BET_API_LEDGER=str(self.folder/'budget.db'),
            VALUE_BET_OBSERVATION_DB='',VALUE_BET_API_DAILY_REQUESTS='40',
            VALUE_BET_API_DAILY_CREDITS='80',VALUE_BET_API_MONTHLY_CREDITS='500',
            VALUE_BET_API_CREDIT_RESERVE='10',VALUE_BET_API_COOLDOWN_SECONDS='300')
        self.environment.start();self.addCleanup(self.environment.stop)
        self.network=patch('requests.sessions.Session.request',side_effect=AssertionError('Offline safety test'))
        self.http=self.network.start();self.addCleanup(self.network.stop)
        self.f=regression.load_functions(ROOT/'app.py')
        self.f['DB_FILE']=self.folder/'bills.db'
        self.f['API_BASE']='https://example.invalid/v4';self.f['TIMEOUT']=25
        self.f['init_db']()
        self.now=datetime.now(UTC)
        self.key='soccer_epl';self.label=self.f['LEAGUES'][self.key][0]

    def tearDown(self):
        self.http.assert_not_called()

    def row(self,**changes):
        row=quote('Asian Handicap',-.25,'home','Authored Book',odds=2.1)
        row.update(league=self.label,kickoff_utc=(self.now+timedelta(hours=6)).isoformat(),
            received_at=self.now.isoformat(),source_updated_at=self.now.isoformat(),
            bookmaker_key='authored-book',source_update_scope='market')
        row.update(changes)
        return row

    def saved(self):
        self.f['save_picks']([self.row()])
        return self.f['load_picks'](limit=None)

    def journal(self,status='success',receipt=None,*,regions='uk',hours=24):
        with self.f['db']() as con:
            run=scan_journal.begin(con,[self.key],regions,hours)
            scan_journal.append(con,run,dict(sport_key=self.key,league=self.label,status=status,
                reason='http_error' if status=='league_error' else None,api_received_at=receipt,
                api_returned=1 if status=='success' else None,passed_filter=1 if status=='success' else None,
                saved_fixture_count=1 if status=='success' else 0))
        return run

    def test_additive_journal_initialization_preserves_legacy_bills_and_existing_evidence(self):
        self.saved()
        with self.f['db']() as con:
            con.execute("INSERT INTO slips(selections_json,status) VALUES (?,?)",('broken legacy JSON','ตรวจแล้ว'))
        run=self.journal(receipt=self.now.isoformat())
        self.f['init_db']();self.f['init_db']()
        with self.f['db']() as con:
            self.assertEqual(con.execute('SELECT selections_json,status FROM slips').fetchone()[:],('broken legacy JSON','ตรวจแล้ว'))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM picks').fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM scan_runs WHERE id=?',(run,)).fetchone()[0],1)
            self.assertEqual(con.execute('SELECT COUNT(*) FROM scan_outcomes').fetchone()[0],1)

    def test_scan_runs_and_outcomes_are_append_only_not_scope_rewritable(self):
        run=self.journal(receipt=self.now.isoformat())
        for statement,parameters in [
            ("UPDATE scan_runs SET regions='us' WHERE id=?",(run,)),
            ('DELETE FROM scan_runs WHERE id=?',(run,)),
            ("UPDATE scan_outcomes SET payload_json='{}' WHERE run_id=?",(run,)),
            ('DELETE FROM scan_outcomes WHERE run_id=?',(run,))]:
            with self.subTest(statement=statement),self.f['db']() as con,self.assertRaises(sqlite3.IntegrityError):
                con.execute(statement,parameters)

    def test_consistent_backup_restore_keeps_journal_quotes_bills_and_triggers(self):
        saved=self.saved()
        self.f['save_slip_record'](saved,2.1,None)
        run=self.journal(receipt=self.now.isoformat())
        self.f['update_pick_result'](1,'1-0','ชนะเต็ม',1.1,1)
        payload=backup_bytes(self.f['DB_FILE'])
        restored=restore_new(payload,self.folder/'restored.db')
        with sqlite3.connect(restored) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertEqual(con.execute('SELECT final_score,profit_units FROM picks').fetchone(),('1-0',1.1))
            self.assertEqual(con.execute('SELECT COUNT(*) FROM slips').fetchone(),(1,))
            self.assertEqual(con.execute('SELECT id FROM scan_runs').fetchone(),(run,))
            with self.assertRaises(sqlite3.IntegrityError):con.execute("UPDATE scan_outcomes SET payload_json='{}'")

    def test_preupgrade_no_journal_keeps_original_fresh_quote_flow(self):
        saved=self.saved()
        self.assertIsNone(self.f['latest_scan_report']())
        current=self.f['current_scan_rows'](saved,[self.key],'uk',24)
        self.assertEqual(current.id.tolist(),saved.id.tolist())
        self.assertIsInstance(self.f['save_slip_record'](saved,2.1,None),int)

    def test_failed_latest_scan_rejects_old_canonical_slip_even_while_price_fresh(self):
        saved=self.saved()
        self.journal(receipt=self.now.isoformat())
        old_bill=self.f['save_slip_record'](saved,2.1,None,regions='uk')
        before=self.f['load_slips']().to_dict('records')
        self.journal('league_error')
        self.assertTrue(self.f['current_scan_rows'](saved,[self.key],'uk',24).empty)
        with self.assertRaises(ValueError):self.f['save_slip_record'](saved,2.1,None,regions='uk')
        self.assertEqual(self.f['load_slips']().to_dict('records'),before)
        self.assertEqual(int(self.f['load_slips']().iloc[0]['id']),old_bill)
        self.assertEqual(len(self.f['load_picks'](limit=None)),1)

    def test_success_receipt_scope_and_region_hours_cannot_be_crossed(self):
        saved=self.saved()
        self.journal(receipt=self.now.isoformat(),regions='eu',hours=48)
        self.assertEqual(len(self.f['current_scan_rows'](saved,[self.key],'eu',48)),1)
        for region,hours in [('uk',48),('us',48),('eu',24)]:
            with self.subTest(region=region,hours=hours):
                self.assertTrue(self.f['current_scan_rows'](saved,[self.key],region,hours).empty)
                with self.assertRaises(ValueError):self.f['save_slip_record'](saved,2.1,None,hours_limit=hours,regions=region)
        self.assertIsInstance(self.f['save_slip_record'](saved,2.1,None,hours_limit=48,regions='eu'),int)

    def test_unknown_cached_receipt_cannot_be_forged_into_current_or_bettable_quote(self):
        harness=OperationalHarness(self)
        event=harness.event('soccer_epl')
        event.pop('_received_at')
        harness.f['fetch_odds']=Mock(return_value=(OddsBatch([event],None),'498','2'))
        harness.current_sport='soccer_epl'
        rows,snapshots,*_=harness.f['scan_league']('soccer_epl',harness.f['LEAGUES']['soccer_epl'][0],
            'E0',24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.assertTrue(rows)
        self.assertTrue(all(row['received_at'] is None for row in rows+snapshots))
        harness.f['save_picks'](rows,snapshots)
        saved=harness.f['load_picks'](limit=None)
        self.assertTrue(saved['received_at'].isna().all())
        self.assertEqual(recommended_pick_ids(saved.to_dict('records'),harness.f['choose_recommendation']),set())

    def test_nonfatal_http_errors_and_timeout_do_not_become_global_stop(self):
        class RequestException(Exception):pass
        for status in (404,503):
            response=SimpleNamespace(status_code=status,headers={},json=lambda:[])
            self.f['requests']=SimpleNamespace(RequestException=RequestException,get=Mock(return_value=response))
            with self.subTest(status=status),self.assertRaises(LeagueScanError) as caught:
                self.f['fetch_odds']('sport-'+str(status),'uk','SYNTHETIC_NO_CREDIT')
            self.assertFalse(caught.exception.halt_all)
            self.assertEqual(caught.exception.status,status)
        self.f['requests']=SimpleNamespace(RequestException=RequestException,
            get=Mock(side_effect=RequestException('apiKey=DO_NOT_ECHO_THIS')))
        with self.assertRaises(LeagueScanError) as caught:self.f['fetch_odds']('timeout','uk','SYNTHETIC_NO_CREDIT')
        self.assertFalse(caught.exception.halt_all)
        self.assertNotIn('DO_NOT_ECHO_THIS',str(caught.exception))

    def test_auth_and_quota_http_errors_remain_global_safety_stop(self):
        for status in (401,429):
            self.f['requests']=SimpleNamespace(RequestException=Exception,
                get=Mock(return_value=SimpleNamespace(status_code=status,headers={},json=lambda:[])))
            with self.subTest(status=status),self.assertRaises(ScanStopped) as caught:
                self.f['fetch_odds']('auth-'+str(status),'uk','SYNTHETIC_KEY_'+str(status))
            self.assertNotIsInstance(caught.exception,LeagueScanError)
            self.assertTrue(caught.exception.halt_all)

    def test_invalid_json_is_league_error_with_sanitized_message(self):
        self.f['requests']=SimpleNamespace(RequestException=Exception,get=Mock(return_value=
            SimpleNamespace(status_code=200,headers={},json=Mock(side_effect=ValueError('apiKey=DO_NOT_ECHO_THIS')))))
        with self.assertRaises(LeagueScanError) as caught:self.f['fetch_odds']('bad-json','uk','SYNTHETIC_NO_CREDIT')
        self.assertEqual(caught.exception.reason,'invalid_json')
        self.assertNotIn('DO_NOT_ECHO_THIS',str(caught.exception))

    def test_bookmaker_update_timestamp_survives_actual_scan_save_and_snapshot(self):
        harness=OperationalHarness(self)
        event=harness.event('soccer_epl')
        stamp=(harness.now-timedelta(minutes=2)).isoformat()
        event['bookmakers'][0]['last_update']=stamp
        for market in event['bookmakers'][0]['markets']:market.pop('last_update')
        harness.events['soccer_epl']=[event]
        result=harness.run(['soccer_epl'])
        self.assertEqual(result['counts']['successful_leagues'],1)
        self.assertTrue(all(row['source_update_scope']=='bookmaker' and row['source_updated_at']==stamp
            for row in result['rows']+result['snapshots']))
        harness.f['save_picks'](result['rows'],result['snapshots'])
        with harness.f['db']() as con:
            for table in ('picks','odds_snapshots'):
                rows=con.execute(f'SELECT source_updated_at,source_update_scope FROM {table}').fetchall()
                self.assertTrue(rows)
                self.assertTrue(all(tuple(row)==(stamp,'bookmaker') for row in rows))

    def test_invalid_explicit_market_update_does_not_fall_back_to_bookmaker_clock(self):
        harness=OperationalHarness(self)
        event=harness.event('soccer_epl')
        event['bookmakers'][0]['last_update']=harness.now.isoformat()
        for market in event['bookmakers'][0]['markets']:market['last_update']='timezone-missing-invalid'
        harness.events['soccer_epl']=[event]
        result=harness.run(['soccer_epl'])
        self.assertTrue(result['rows'])
        self.assertTrue(all(row['source_updated_at'] is None and row['source_update_scope']=='invalid_market'
            for row in result['rows']+result['snapshots']))
        self.assertEqual(recommended_pick_ids(result['rows'],harness.f['choose_recommendation']),set())

    def test_malformed_bookmakers_markets_outcomes_cannot_drop_following_valid_fixture(self):
        harness=OperationalHarness(self)
        malformed=harness.event('soccer_epl','malformed')
        malformed['bookmakers']=[None,{'key':'bad','markets':None},
            {'key':'bad','markets':[None,{'key':'totals','outcomes':None},
                {'key':'spreads','outcomes':[None,{},dict(name='bad',price=float('nan'),point=0),
                    dict(name='bad',price=True,point=0),dict(name='bad',price=2,point=2.6)]}]}]
        valid=harness.event('soccer_epl','valid')
        harness.events['soccer_epl']=[malformed,valid]
        result=harness.run(['soccer_epl'])
        self.assertEqual(result['counts']['successful_leagues'],1)
        self.assertEqual({row['event_id'] for row in result['rows']},{malformed['id'],valid['id']})
        self.assertTrue(any(row['market']=='ยังไม่มีราคา' for row in result['rows'] if row['event_id']==malformed['id']))
        self.assertTrue(any(row['market']=='Asian Handicap' for row in result['rows'] if row['event_id']==valid['id']))

    def test_cloud_secrets_budget_values_are_read_without_requiring_environment_changes(self):
        settings_values={'VALUE_BET_API_DAILY_REQUESTS':'18','VALUE_BET_API_DAILY_CREDITS':'36',
            'VALUE_BET_API_MONTHLY_CREDITS':'450','VALUE_BET_API_CREDIT_RESERVE':'20',
            'VALUE_BET_API_COOLDOWN_SECONDS':'600'}
        self.f['runtime_setting']=lambda name,default:settings_values.get(name,default)
        self.assertEqual(self.f['api_budget_settings'](),dict(requests=18,credits=36,monthly_credits=450,reserve=20,cooldown=600))
        settings_values['VALUE_BET_API_DAILY_CREDITS']='DO_NOT_ECHO_THIS'
        with self.assertRaises(ScanStopped) as caught:self.f['api_budget_settings']()
        self.assertNotIn('DO_NOT_ECHO_THIS',str(caught.exception))

    def test_cached_response_resave_deduplicates_snapshots_without_rewriting_provenance(self):
        harness=OperationalHarness(self)
        harness.events['soccer_epl']=[harness.event('soccer_epl')]
        result=harness.run(['soccer_epl'])
        first=harness.f['save_picks'](result['rows'],result['snapshots'])
        second=harness.f['save_picks'](result['rows'],result['snapshots'])
        self.assertEqual(first[1],4)
        self.assertEqual(second[1],0)
        with harness.f['db']() as con:
            rows=con.execute('SELECT received_at,source_updated_at,source_update_scope FROM odds_snapshots').fetchall()
            self.assertEqual(len(rows),4)
            self.assertTrue(all(tuple(row)==(harness.now.isoformat(),harness.now.isoformat(),'market') for row in rows))
