"""P0 acceptance checks: all data is in memory or disposable SQLite files."""
import ast
import hashlib
import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
from bill_backup import backup_bytes, restore_new
from readiness import analysis_scope, quote_freshness, unique_result
from odds_budget import Budget, ScanStopped
from market_recommendations import assess, recommended_pick_ids, group_fixture_recommendations
from test_market_recommendations import quote
from test_regressions import load_functions

ROOT=Path(__file__).resolve().parents[1]
UTC=timezone.utc


class ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.f=load_functions(ROOT/'app.py');self.f['DB_FILE']=Path(self.tmp.name)/'bills.db';self.f['init_db']()
        self.now=datetime.now(UTC)

    def row(self, **changes):
        row=quote('Asian Handicap',-.25,'home','Book',odds=2.1)
        row.update(kickoff_utc=(self.now+timedelta(hours=6)).isoformat(),received_at=self.now.isoformat(),source_updated_at=self.now.isoformat(),bookmaker_key='book')
        row.update(changes)
        return row

    def test_scope_is_selected_leagues_future_and_24_hours_only(self):
        rows=[self.row(event_id=str(i),kickoff_utc=(self.now+timedelta(hours=h)).isoformat(),league=l)
              for i,(h,l) in enumerate([(1,'yes'),(24,'yes'),(24.01,'yes'),(-1,'yes'),(0,'yes'),(2,'no')])]
        view=analysis_scope(pd.DataFrame(rows),['yes'],self.now)
        self.assertEqual(view.event_id.tolist(),['0','1'])
        self.assertTrue(analysis_scope(pd.DataFrame(rows),[],self.now).empty)
        self.assertEqual(len(rows),6)

    def test_missing_naive_invalid_old_and_future_provenance_fail_closed(self):
        for changes in [dict(received_at=None),dict(source_updated_at=None),dict(source_updated_at='bad'),
                        dict(source_updated_at='2026-10-21T12:00:00'),
                        dict(received_at=(self.now-timedelta(minutes=16)).isoformat()),
                        dict(source_updated_at=(self.now-timedelta(minutes=16)).isoformat()),
                        dict(source_updated_at=(self.now+timedelta(minutes=1)).isoformat())]:
            with self.subTest(changes=changes):self.assertFalse(quote_freshness(self.row(**changes),self.now)[0])
        self.assertTrue(quote_freshness(self.row(),self.now)[0])

    def test_max_age_can_tighten_but_not_loosen_safety_limit(self):
        row=self.row(source_updated_at=(self.now-timedelta(minutes=5)).isoformat())
        self.assertFalse(quote_freshness(row,self.now,max_age=60)[0])
        self.assertFalse(quote_freshness(row,self.now,max_age=3600)[0])

    def test_cached_rescan_does_not_refresh_receipt_or_source_timestamp(self):
        row=self.row(received_at=(self.now-timedelta(hours=1)).isoformat(),source_updated_at=(self.now-timedelta(hours=1)).isoformat())
        self.f['save_picks']([row]);self.f['save_picks']([row])
        saved=self.f['load_picks']().iloc[0].to_dict()
        self.assertEqual(saved['received_at'],row['received_at'])
        self.assertEqual(saved['source_updated_at'],row['source_updated_at'])
        self.assertIsNone(assess(saved,self.f['choose_recommendation'])['ev'])

    def test_fresh_lower_ev_beats_stale_high_ev_and_legacy_without_receipt(self):
        stale=self.row(id=1,odds=4,received_at=(self.now-timedelta(days=1)).isoformat())
        fresh=self.row(id=2,bookmaker='Fresh')
        result=group_fixture_recommendations([stale,fresh],self.f['choose_recommendation'])[0]
        self.assertEqual(result['markets']['Asian Handicap']['row']['id'],2)
        legacy=self.row(id=3,received_at=None,source_updated_at=None)
        self.assertNotIn(3,recommended_pick_ids([legacy],self.f['choose_recommendation']))

    def test_new_response_removes_old_bookmaker_from_analysis_without_deleting(self):
        rows=[self.row(id=1,received_at=(self.now-timedelta(minutes=1)).isoformat()),self.row(id=2,bookmaker='new')]
        self.assertEqual(analysis_scope(pd.DataFrame(rows),[rows[0]['league']],self.now).id.tolist(),[2])
        self.assertEqual(len(rows),2)

    def test_ah_and_ou_require_supported_positive_ev(self):
        for market,side,line in [('Asian Handicap','home',-.25),('Over/Under','over',2.5)]:
            row=quote(market,line,side,'Book',odds=2.1)
            for ev in (None,-1,0):
                test=dict(row,ev_pct=ev)
                b=json.loads(test['analysis_b_json']);b['selection']['ev_pct']=ev;test['analysis_b_json']=json.dumps(b)
                self.assertNotEqual(assess(test,self.f['choose_recommendation'])['tier'],4)
                self.assertEqual(recommended_pick_ids([test],self.f['choose_recommendation']),set())

    def test_positive_ah_and_paired_ou_enter_same_eligibility_policy(self):
        ah=self.row(id=1,event_id='ah')
        ou=[quote(side=s,odds=1.95,pick=i,event='ou') for i,s in [(2,'over'),(3,'under')]]
        ids=recommended_pick_ids([ah]+ou,self.f['choose_recommendation'])
        self.assertEqual(ids,{1,2})

    def test_save_revalidates_price_freshness_scope_and_canonical_data(self):
        self.f['save_picks']([self.row()]);df=self.f['load_picks']()
        with self.assertRaises(ValueError):self.f['save_slip_record'](df,2.1,None,['different league'])
        tampered=df.copy();tampered['odds']=9
        with self.assertRaises(ValueError):self.f['save_slip_record'](tampered,9,None)
        with self.f['db']() as con:
            con.execute('UPDATE picks SET source_updated_at=?',((self.now-timedelta(hours=1)).isoformat(),))
        with self.assertRaises(ValueError):self.f['save_slip_record'](df,2.1,None)
        self.assertTrue(self.f['load_slips']().empty)

    def test_new_slip_locks_quote_and_keeps_provenance(self):
        row=self.row();self.f['save_picks']([row]);df=self.f['load_picks']()
        self.f['save_slip_record'](df,2.1,None)
        leg=json.loads(self.f['load_slips']().iloc[0]['selections_json'])[0]
        contract=json.loads((ROOT/'fixtures/readiness/pr2-contract.json').read_text())
        self.assertEqual(list(leg),contract['slip_leg_keys'])
        self.assertEqual(self.f['load_picks']().iloc[0]['source_updated_at'],row['source_updated_at'])
        self.f['save_picks']([dict(row,point=.25)])
        self.assertEqual(len(self.f['load_picks']()),2)

    def test_result_ambiguity_and_weak_evidence_never_settle(self):
        day=(self.now-timedelta(days=2)).replace(hour=12,minute=0,second=0,microsecond=0)
        record=dict(HomeTeam='A',AwayTeam='B',Date_dt=pd.Timestamp(day.date()),FTHG=2,FTAG=1)
        hist=pd.DataFrame([record]);self.assertIsNotNone(unique_result(hist,'A','B',day.isoformat()))
        for records in ([record,record],[record,dict(record,Date_dt=pd.Timestamp(day.date())+pd.Timedelta(days=1))],
                        [dict(record,Date_dt=pd.Timestamp(day.date())+pd.Timedelta(days=1))],
                        [dict(record,FTHG=None)],[dict(record,FTHG=-1)],[dict(record,FTHG=1.5)]):
            self.assertIsNone(unique_result(pd.DataFrame(records),'A','B',day.isoformat()))
        self.assertIsNone(unique_result(hist,'B','A',day.isoformat()))
        self.assertIsNone(unique_result(hist,'A','B','invalid'))

    def test_settled_pick_immutable_even_direct_sql_and_second_writer(self):
        self.f['save_picks']([self.row()]);self.f['update_pick_result'](1,'1-0','ชนะเต็ม',1,1)
        self.f['update_pick_result'](1,'0-1','แพ้เต็ม',-1,0)
        with self.f['db']() as con:
            with self.assertRaises(sqlite3.IntegrityError):con.execute("UPDATE picks SET profit_units=-1 WHERE id=1")
            row=con.execute('SELECT final_score,profit_units FROM picks WHERE id=1').fetchone()
            self.assertEqual(tuple(row),('1-0',1))

    def test_backup_includes_wal_and_restore_never_overwrites(self):
        source=Path(self.tmp.name)/'wal.db'
        with sqlite3.connect(source) as con:
            con.execute('PRAGMA journal_mode=WAL');con.execute('CREATE TABLE test(value)');con.execute('INSERT INTO test VALUES (42)');con.commit()
            payload=backup_bytes(source)
            restored=restore_new(payload,Path(self.tmp.name)/'restored.db')
            with sqlite3.connect(restored) as restored_con:
                self.assertEqual(restored_con.execute('SELECT value FROM test').fetchone(),(42,))
                self.assertEqual(restored_con.execute('PRAGMA integrity_check').fetchone(),('ok',))
            with self.assertRaises(FileExistsError):restore_new(payload,source)
        with self.assertRaises(sqlite3.DatabaseError):restore_new(b'broken',Path(self.tmp.name)/'bad.db')
        self.assertFalse((Path(self.tmp.name)/'bad.db').exists())

    def test_backup_restore_preserves_legacy_and_finalized_bills(self):
        self.f['save_picks']([self.row()])
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status,profit_units) VALUES (?,?,?)',('{bad','แพ้บิล',-1))
            before=[tuple(r) for r in con.execute('SELECT * FROM slips')]
        payload=backup_bytes(self.f['DB_FILE']);target=restore_new(payload,Path(self.tmp.name)/'restored.db')
        with sqlite3.connect(target) as con:self.assertEqual(con.execute('SELECT * FROM slips').fetchall(),before)

    def test_empty_response_invalidates_old_quotes_without_deleting_history(self):
        from odds_budget import OddsBatch
        row=self.row(received_at=(self.now-timedelta(minutes=1)).isoformat())
        self.f['save_picks']([row])
        self.f.update(fetch_odds=lambda *a:(OddsBatch([],self.now.isoformat()),'100','2'),
                      fetch_understat_history=lambda *a:pd.DataFrame(),fetch_history=lambda *a:pd.DataFrame())
        rows,_,_,_=self.f['scan_league']('soccer_epl',row['league'],'E0',24,1.8,2.1,'uk','mock')
        self.assertEqual(rows,[])
        stored=self.f['load_picks']()
        self.assertEqual(len(stored),1)
        view=analysis_scope(stored,[row['league']],self.now,latest_receipts=self.f['latest_api_receipts']())
        self.assertTrue(view.empty)
        self.f['record_api_receipt']('soccer_epl',(self.now-timedelta(minutes=2)).isoformat())
        self.assertEqual(self.f['latest_api_receipts']()[row['league']],self.now.isoformat())
        with self.assertRaises(ValueError):self.f['save_slip_record'](stored,2.1,None)

    def test_identical_price_new_response_keeps_new_evidence_cached_repeat_deduplicates(self):
        row=self.row();snapshot=dict(row,match_name=row['match'])
        self.assertEqual(self.f['save_picks']([row],[snapshot])[1],1)
        self.assertEqual(self.f['save_picks']([row],[snapshot])[1],0)
        newer=dict(snapshot,received_at=(self.now+timedelta(seconds=1)).isoformat())
        self.assertEqual(self.f['save_picks']([dict(row,received_at=newer['received_at'])],[newer])[1],1)
        with self.f['db']() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM odds_snapshots').fetchone()[0],2)
        lookup=self.f['lookup_quote_snapshot'](dict(row,received_at=newer['received_at']))
        self.assertEqual(lookup['received_at'],newer['received_at'])

    def test_unverifiable_book_identity_invalid_contract_or_configuration_cannot_bet(self):
        for changes in (dict(event_id=None),dict(bookmaker_key=None),dict(point=2.6),dict(odds=float('inf'))):
            self.assertFalse(quote_freshness(self.row(**changes),self.now)[0])
        with patch.dict(os.environ,VALUE_BET_QUOTE_MAX_AGE_SECONDS='invalid'):
            self.assertFalse(quote_freshness(self.row(),self.now)[0])

    def test_future_or_unknown_kickoff_cannot_enter_manual_result_flow(self):
        from readiness import result_time_ready
        for kickoff in [None,'invalid','2026-10-21T12:00:00',(self.now+timedelta(hours=1)).isoformat()]:
            self.assertFalse(result_time_ready({'kickoff_utc':kickoff},self.now))
        self.assertTrue(result_time_ready({'kickoff_utc':(self.now-timedelta(days=1)).isoformat()},self.now))

    def test_boolean_goals_are_not_full_time_scores(self):
        day=(self.now-timedelta(days=2)).replace(hour=12,minute=0,second=0,microsecond=0)
        row=dict(HomeTeam='A',AwayTeam='B',Date_dt=pd.Timestamp(day.date()),FTHG=True,FTAG=False)
        self.assertIsNone(unique_result(pd.DataFrame([row]),'A','B',day.isoformat()))

    def test_rescan_lock_prevents_bill_writer_from_racing_immutable_pick_check(self):
        original=self.f['db'];blocked=[]
        def connect():
            con=original()
            def trace(sql):
                if sql=='SELECT selections_json FROM slips':
                    second=sqlite3.connect(self.f['DB_FILE'],timeout=0)
                    try:
                        try:second.execute('BEGIN IMMEDIATE');blocked.append(False)
                        except sqlite3.OperationalError:blocked.append(True)
                    finally:second.close()
            con.set_trace_callback(trace)
            return con
        self.f['db']=connect
        self.f['save_picks']([self.row()])
        self.assertEqual(blocked,[True])
        with original() as con:con.execute('BEGIN IMMEDIATE')

    def test_ui_templates_widgets_labels_css_and_model_formulas_unchanged(self):
        contract=json.loads((ROOT/'fixtures/readiness/pr2-contract.json').read_text())
        # PR #6 authorizes diagnostics only inside the existing details expander.
        import mobile_dashboard
        mobile_source=(ROOT/'mobile_dashboard.py').read_text()
        self.assertEqual(hashlib.sha256(mobile_dashboard.DASHBOARD_CSS.encode()).hexdigest(),contract['mobile_css_sha256'])
        protected={n.name:n for n in ast.parse(mobile_source).body if isinstance(n,ast.FunctionDef)}
        for name,digest in contract['mobile_protected_source_sha256'].items():
            self.assertEqual(hashlib.sha256(ast.get_source_segment(mobile_source,protected[name]).encode()).hexdigest(),digest,name)
        source=(ROOT/'app.py').read_text()
        tree=ast.parse(source);ui=[]
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and isinstance(n.func.value,ast.Name) and n.func.value.id=='st':
                ui.append((n.lineno,n.func.attr,[v.value for v in ast.walk(n) if isinstance(v,ast.Constant) and isinstance(v.value,(str,int,float,bool,type(None)))]))
        self.assertEqual([list(x[1:]) for x in sorted(ui)],contract['ui_calls'])
        for n in tree.body:
            if isinstance(n,ast.FunctionDef) and n.name in contract['formula_source_sha256']:
                # Source hashes are stricter and portable across Python 3.11/3.12 AST changes.
                self.assertEqual(hashlib.sha256(ast.get_source_segment(source,n).encode()).hexdigest(),contract['formula_source_sha256'][n.name],n.name)

    def test_settled_slip_result_cannot_be_overwritten_by_sql(self):
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(status,profit_units,final_summary) VALUES (?,?,?)',('แพ้บิล',-1,'original'))
        with self.f['db']() as con:
            with self.assertRaises(sqlite3.IntegrityError):
                con.execute("UPDATE slips SET profit_units=10 WHERE id=1")
            self.assertEqual(tuple(con.execute('SELECT status,profit_units,final_summary FROM slips').fetchone()),('แพ้บิล',-1,'original'))

    def test_ambiguous_result_queue_is_append_only_and_available_for_manual_flow(self):
        self.f['save_picks']([self.row()])
        self.f['record_result_review'](1,'ambiguous',[dict(FTHG=1),dict(FTHG=2)])
        self.f['record_result_review'](1,'still ambiguous',[dict(FTHG=1)])
        self.assertEqual(self.f['manual_review_ids'](),{1})
        with self.f['db']() as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM result_reviews').fetchone()[0],2)
            self.assertEqual(con.execute('SELECT status FROM picks').fetchone()[0],'รอตรวจผล')

    def test_provider_update_survives_scan_and_snapshot_storage(self):
        received=self.now.isoformat();updated=(self.now-timedelta(minutes=2)).isoformat()
        event=dict(id='source',commence_time=(self.now+timedelta(hours=1)).isoformat(),home_team='A',away_team='B',
                   _received_at=received,bookmakers=[dict(key='real-book',title='Real Book',markets=[dict(key='totals',last_update=updated,outcomes=[dict(name='Over',point=2.75,price=1.95)])])])
        self.f.update(fetch_odds=lambda *a:([event],'100','2'),fetch_understat_history=lambda *a:pd.DataFrame(),
                      fetch_history=lambda *a:pd.DataFrame())
        rows,snapshots,_,_=self.f['scan_league']('soccer_epl',self.f['LEAGUES']['soccer_epl'][0],'E0',48,1.8,2.1,'uk','mock')
        self.f['save_picks'](rows,snapshots)
        row=self.f['load_picks']().iloc[0]
        self.assertEqual((row['received_at'],row['source_updated_at'],row['bookmaker_key']),(received,updated,'real-book'))
        with self.f['db']() as con:
            stored=con.execute('SELECT captured_at,received_at,source_updated_at FROM odds_snapshots').fetchone()
            self.assertEqual(tuple(stored),(received,received,updated))



class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.budget=Budget(Path(self.tmp.name)/'budget.db');self.now=datetime.now(UTC)
        self.limits=dict(requests=2,credits=4,reserve=1,cooldown=300)

    def test_request_credit_cap_and_cooldown_without_storing_keys(self):
        self.budget.reserve('SECRET','epl','uk',self.now,self.limits)
        with self.assertRaises(ScanStopped):self.budget.reserve('SECRET','epl','uk',self.now,self.limits)
        self.budget.reserve('SECRET','laliga','uk',self.now,self.limits)
        with self.assertRaises(ScanStopped):self.budget.reserve('SECRET','seriea','uk',self.now,self.limits)
        self.assertNotIn(b'SECRET',self.budget.path.read_bytes())

    def test_headers_and_provider_balance_stop_before_reserve_threshold(self):
        call=self.budget.reserve('key','epl','uk',self.now,self.limits)
        self.budget.complete(call,200,{'x-requests-last':'2','x-requests-remaining':'2','x-requests-used':'40'})
        with self.assertRaises(ScanStopped):self.budget.reserve('key','laliga','uk',self.now,self.limits)

    def test_401_429_or_unexpected_cost_blocks_remaining_scan(self):
        for status,headers in [(401,{}),(429,{}),(200,{'x-requests-last':'3'})]:
            key=str(status)+str(headers);call=self.budget.reserve(key,'epl','uk',self.now,self.limits)
            with self.assertRaises(ScanStopped):self.budget.complete(call,status,headers)
            with self.assertRaises(ScanStopped):self.budget.reserve(key,'laliga','uk',self.now,self.limits)

    def test_atomic_parallel_reservation_cannot_exceed_shared_ledger_cap(self):
        def run(i):
            try:self.budget.reserve('key',str(i),'uk',self.now,self.limits);return 1
            except ScanStopped:return 0
        with ThreadPoolExecutor(max_workers=8) as pool:self.assertEqual(sum(pool.map(run,range(8))),2)

    def test_failed_or_unknown_request_remains_conservatively_charged(self):
        call=self.budget.reserve('key','epl','uk',self.now,self.limits);self.budget.complete(call,'network_error',{})
        with self.budget.connect() as con:self.assertEqual(con.execute('SELECT credits FROM calls').fetchone(),(2,))

    def test_ledger_cannot_be_bill_database(self):
        with self.assertRaises(ScanStopped):Budget(self.budget.path,bill_path=self.budget.path)

    def test_fetch_error_redacts_key_and_records_without_external_request(self):
        f=load_functions(ROOT/'app.py');f.update(API_BASE='https://example.invalid',TIMEOUT=25,DB_FILE=Path(self.tmp.name)/'bills.db')
        class RequestException(Exception):pass
        req=SimpleNamespace(RequestException=RequestException,get=Mock(side_effect=RequestException('apiKey=DO_NOT_PRINT')))
        f['requests']=req
        with patch.dict(os.environ,VALUE_BET_API_LEDGER=str(Path(self.tmp.name)/'fetch.db')):
            with self.assertRaises(ScanStopped) as caught:f['fetch_odds']('epl','uk','DO_NOT_PRINT')
        self.assertNotIn('DO_NOT_PRINT',str(caught.exception));req.get.assert_called_once()

    def test_fetch_retains_original_receipt_and_real_market_update(self):
        f=load_functions(ROOT/'app.py');f.update(API_BASE='https://example.invalid',TIMEOUT=25,DB_FILE=Path(self.tmp.name)/'bills.db')
        payload=[dict(id='e',bookmakers=[dict(markets=[dict(last_update='2026-10-21T12:00:00Z')])])]
        response=SimpleNamespace(status_code=200,headers={'x-requests-last':'2','x-requests-remaining':'100','x-requests-used':'2'},json=lambda:payload)
        f['requests']=SimpleNamespace(get=Mock(return_value=response),RequestException=Exception)
        with patch.dict(os.environ,VALUE_BET_API_LEDGER=str(Path(self.tmp.name)/'fetch.db')):
            matches,remaining,used=f['fetch_odds']('epl','uk','test')
        self.assertIn('_received_at',matches[0]);self.assertEqual(remaining,'100')
        self.assertEqual(matches[0]['bookmakers'][0]['markets'][0]['last_update'],'2026-10-21T12:00:00Z')


class CredentialTests(unittest.TestCase):
    def test_logging_redacts_url_arguments_and_exception_trace(self):
        import io,logging
        from credential_redaction import protect
        protect('SENSITIVE_SENTINEL')
        stream=io.StringIO();handler=logging.StreamHandler(stream);logger=logging.getLogger('urllib3.readiness-test')
        logger.addHandler(handler);logger.setLevel(logging.DEBUG)
        try:
            logger.debug('GET %s','/odds?apiKey=SENSITIVE_SENTINEL')
            try:raise RuntimeError('request failed: SENSITIVE_SENTINEL')
            except RuntimeError:logger.exception('failed api_key=SENSITIVE_SENTINEL')
        finally:logger.removeHandler(handler)
        self.assertNotIn('SENSITIVE_SENTINEL',stream.getvalue());self.assertIn('[REDACTED]',stream.getvalue())
