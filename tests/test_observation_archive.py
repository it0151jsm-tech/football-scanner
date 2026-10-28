"""Authored source bytes and mocked clocks test forward capture, not real ROI."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from backtest.evidence import Archive
from bill_backup import backup_bytes, restore_new
from observation_archive import (ObservationArchive, capture_completed_scan,
    export_database, record_provider_response)

AT=datetime(2026,10,24,12,tzinfo=timezone.utc)
ROOT=Path(__file__).resolve().parents[1]


class ObservationArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'observer.db'
        self.bill=Path(self.tmp.name)/'bills.db'
        self.clock=patch('observation_archive._now',return_value=AT)
        self.clock.start();self.addCleanup(self.clock.stop)
        self.policy={'rights_status':'approved','reviewed_by':'authored fixture reviewer',
            'reviewed_at':(AT-timedelta(days=1)).isoformat(),
            'rights_evidence_url':'https://example.invalid/owned-test-license',
            'permissions':{'store_raw':True,'model_research':True}}
        self.archive=ObservationArchive(self.path,bill_db_path=self.bill,source_policies={'fixture':self.policy})

    def raw(self,body=b'{"fixture":"test bytes"}'):
        return self.archive.record_raw('fixture','https://example.invalid/data',body)

    def event(self,**changes):
        row={'event_id':'test-match','league':'soccer_epl','season':'2627','period':'FT90',
            'home_team':'Authored Home','away_team':'Authored Away','kickoff_utc':(AT+timedelta(hours=3)).isoformat()}
        row.update(changes)
        return row

    def quote(self,**changes):
        row=dict(self.event(),quote_id='test-quote',market='Over/Under',side='over',point=2.75,
            odds=1.95,bookmaker='Authored Book',bookmaker_key='authored-book',odds_in_range=True,
            source_updated_at=(AT-timedelta(minutes=1)).isoformat())
        row.update(changes)
        return row

    def test_opt_in_disabled_creates_no_files(self):
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':''}):
            self.assertIsNone(ObservationArchive.from_environment(bill_db_path=self.bill))
            self.assertEqual(record_provider_response('fixture','https://example.invalid/data',object()),{'status':'disabled'})
        self.assertFalse(self.bill.exists())

    def test_same_bill_path_symlink_and_hardlink_are_rejected_without_mutation(self):
        self.bill.write_bytes(b'private bill sentinel')
        before=self.bill.read_bytes()
        with self.assertRaisesRegex(ValueError,'separate_from_bills'):
            ObservationArchive(self.bill,bill_db_path=self.bill)
        alias=Path(self.tmp.name)/'hardlink.db';os.link(self.bill,alias)
        with self.assertRaisesRegex(ValueError,'separate_from_bills'):
            ObservationArchive(alias,bill_db_path=self.bill)
        link=Path(self.tmp.name)/'symlink.db';link.symlink_to(self.path)
        with self.assertRaisesRegex(ValueError,'symlink'):
            ObservationArchive(link)
        self.assertEqual(self.bill.read_bytes(),before)

    def test_unknown_rights_retain_only_digest_metadata(self):
        archive=ObservationArchive(Path(self.tmp.name)/'unknown.db')
        result=archive.record_raw('unknown','https://example.invalid/data',b'copyrighted fixture bytes')
        self.assertEqual(result['quality_status'],'quarantine')
        self.assertEqual(result['raw_sha256'],hashlib.sha256(b'copyrighted fixture bytes').hexdigest())
        with sqlite3.connect(archive.path) as con:
            self.assertIsNone(con.execute('SELECT body FROM observation_raw').fetchone()[0])

    def test_missing_permissions_and_future_rights_review_do_not_approve_storage(self):
        for changes in ({'permissions':[]},{'permissions':{'store_raw':True}},
                        {'reviewed_at':(AT+timedelta(days=1)).isoformat()}):
            with self.subTest(changes=changes):
                archive=ObservationArchive(Path(self.tmp.name)/str(len(list(Path(self.tmp.name).glob('*.db')))),
                    source_policies={'fixture':dict(self.policy,**changes)})
                self.assertEqual(archive.record_raw('fixture','https://example.invalid/data',b'fixture')['quality_status'],'quarantine')

    def test_approved_raw_original_bytes_have_actual_receipt_and_checksum(self):
        payload=b'{"bookmakers":[{"key":"authored-book"}]}'
        result=self.raw(payload)
        self.assertEqual(result['quality_status'],'observed_source')
        self.assertEqual(result['received_at'],AT.isoformat())
        with sqlite3.connect(self.path) as con:
            self.assertEqual(con.execute('SELECT body FROM observation_raw').fetchone()[0],payload)

    def test_credential_urls_and_payloads_are_never_stored(self):
        with self.assertRaises(ValueError):
            self.archive.record_raw('fixture','https://example.invalid/data?apiKey=do-not-store',b'fixture')
        result=self.raw(b'{"apiKey":"do-not-store"}')
        self.assertEqual(result['reason'],'source_payload_may_contain_credentials')
        with sqlite3.connect(self.path) as con:
            self.assertIsNone(con.execute('SELECT body FROM observation_raw').fetchone()[0])

    def test_provider_hook_uses_original_response_bytes_and_never_requests_network(self):
        class Response:
            content=b'original bytes'
            def json(self):raise AssertionError('do not reconstruct source bytes')
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path),
                'VALUE_BET_OBSERVATION_RIGHTS_JSON':json.dumps({'fixture':self.policy})}),patch('requests.get') as request:
            result=record_provider_response('fixture','https://example.invalid/data',Response(),bill_db_path=self.bill)
        self.assertEqual(result['quality_status'],'observed_source')
        request.assert_not_called()

    def test_source_clock_cannot_be_supplied_or_backdated_by_normalizer(self):
        raw=self.raw()
        with self.assertRaisesRegex(ValueError,'measured_here'):
            self.archive.record_normalized('events',dict(self.event(),received_at='1999-01-01T00:00:00Z'),raw['raw_id'])
        with patch('observation_archive._now',return_value=AT+timedelta(minutes=2)):
            result=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        exported=self.archive.export_document()['events'][0]
        self.assertEqual(result['received_at'],(AT+timedelta(minutes=2)).isoformat())
        self.assertEqual(exported['source_received_at'],AT.isoformat())

    def test_missing_season_period_identity_and_xg_are_quarantined(self):
        raw=self.raw()
        for kind,row,reason in [('events',self.event(season=None),'season_not_verified'),
            ('events',self.event(period=None),'period_not_verified_ft90'),
            ('events',self.event(away_team='Authored Home'),'team_identity_missing_or_self_match'),
            ('xg_history',dict(self.event(),match_id='past',status='FT',home_goals=2,away_goals=1,
                match_time_utc=(AT-timedelta(days=7)).isoformat()),'real_xg_missing')]:
            with self.subTest(reason=reason):
                result=self.archive.record_normalized(kind,row,raw['raw_id'])
                self.assertEqual(result['quality_status'],'quarantine')
                self.assertEqual(result['reason'],reason)
        self.assertEqual(self.archive.export_document()['events'],[])

    def test_batch_quote_sides_retain_one_snapshot_and_actual_persistence_time(self):
        raw=self.raw()
        self.archive.record_batch([('events',self.event()),('quotes',self.quote()),
            ('quotes',self.quote(quote_id='under',side='under'))],raw['raw_id'])
        document=self.archive.export_document()
        self.assertEqual(len(document['quotes']),2)
        self.assertEqual(document['quotes'][0]['captured_at'],document['quotes'][1]['captured_at'])
        for row in document['events']+document['quotes']:
            self.assertIsNone(Archive(document).proof(row))
            self.assertEqual(row['recorded_at'],AT.isoformat())

    def test_provider_update_after_receipt_is_quarantined(self):
        raw=self.raw()
        result=self.archive.record_normalized('quotes',self.quote(source_updated_at=(AT+timedelta(seconds=1)).isoformat()),raw['raw_id'])
        self.assertEqual(result['reason'],'provider_update_after_actual_receipt')

    def test_historical_match_timezone_is_not_invented(self):
        raw=self.raw()
        history=dict(self.event(),match_id='past',status='FT',home_goals=1,away_goals=0,
            match_time_utc='2026-10-01T15:00:00',source_date='2026-10-01')
        result=self.archive.record_normalized('score_history',history,raw['raw_id'])
        self.assertEqual(result['reason'],'historical_match_time_unverified')

    def test_log_decision_freezes_model_and_inputs_at_real_time(self):
        raw=self.raw()
        observed=self.archive.record_batch([('events',self.event()),('quotes',self.quote())],raw['raw_id'])
        with patch('observation_archive._now',return_value=AT+timedelta(seconds=1)):
            result=self.archive.log_decision('test-match','a'*64,[item['observation_id'] for item in observed])
        document=self.archive.export_document()
        self.assertEqual(result['decision_at'],(AT+timedelta(seconds=1)).isoformat())
        self.assertEqual(document['decisions'][0]['model_version'],'a'*64)
        self.assertEqual(len(document['decisions'][0]['input_normalized_sha256']),2)
        self.assertEqual(document['decisions'][0]['input_source_payload_sha256'],[
            document['events'][0]['source_payload_sha256'],document['quotes'][0]['source_payload_sha256']])
        self.assertNotEqual(document['decisions'][0]['input_source_payload_sha256'],
                            document['decisions'][0]['input_normalized_sha256'])
        self.assertIsNone(Archive(document).proof(document['decisions'][0]))

    def test_postkickoff_or_outcome_inputs_cannot_enter_a_decision(self):
        raw=self.raw()
        event=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        with patch('observation_archive._now',return_value=AT+timedelta(hours=4)),self.assertRaisesRegex(ValueError,'before_kickoff'):
            self.archive.log_decision('test-match','a'*64,[event['observation_id']])
        result=self.archive.record_normalized('results',self.event(kickoff_utc=(AT-timedelta(hours=3)).isoformat(),
            status='FT',home_goals=2,away_goals=1),raw['raw_id'])
        with self.assertRaisesRegex(ValueError,'outcome_data'):
            self.archive.log_decision('test-match','a'*64,[event['observation_id'],result['observation_id']])

    def test_future_clock_and_wrong_fixture_inputs_are_rejected(self):
        raw=self.raw()
        event=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        quote=self.archive.record_normalized('quotes',self.quote(event_id='other'),raw['raw_id'])
        with self.assertRaisesRegex(ValueError,'mismatch'):
            self.archive.log_decision('test-match','a'*64,[event['observation_id'],quote['observation_id']])
        with patch('observation_archive._now',return_value=AT-timedelta(seconds=1)),self.assertRaisesRegex(ValueError,'unverified'):
            self.archive.log_decision('test-match','a'*64,[event['observation_id']])

    def test_evidence_updates_and_deletes_are_blocked_at_database_level(self):
        raw=self.raw()
        self.archive.record_normalized('events',self.event(),raw['raw_id'])
        with sqlite3.connect(self.path) as con:
            for statement in ('UPDATE observation_raw SET source="tampered"','DELETE FROM observation_raw',
                              'UPDATE observation_rows SET quality_status="eligible_observed"','DELETE FROM observation_rows'):
                with self.subTest(statement=statement),self.assertRaisesRegex(sqlite3.IntegrityError,'append only'):
                    con.execute(statement)

    def test_quarantined_source_cannot_be_promoted_by_later_rights_change(self):
        self.archive.source_policies={}
        raw=self.raw()
        self.archive.source_policies={'fixture':self.policy}
        result=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        self.assertEqual(result['quality_status'],'quarantine')
        self.assertEqual(self.archive.export_document()['events'],[])

    def test_unknown_source_or_missing_raw_receipt_cannot_become_evidence(self):
        with self.assertRaisesRegex(ValueError,'not_found'):
            self.archive.record_normalized('events',self.event(),999)

    def test_backup_restore_preserves_exact_appendonly_evidence(self):
        raw=self.raw()
        self.archive.record_normalized('events',self.event(),raw['raw_id'])
        before=self.archive.export_document()
        restored=Path(self.tmp.name)/'restored.db'
        restore_new(backup_bytes(self.path),restored)
        self.assertEqual(export_database(restored),before)
        with self.assertRaises(FileExistsError):
            restore_new(backup_bytes(self.path),restored)
        self.assertFalse(self.bill.exists())

    def test_capture_scan_without_verified_context_reports_incomplete_without_inventing_rows(self):
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path)}):
            result=capture_completed_scan('soccer_epl',[{'id':'test-match'}],[],[],None,None,bill_db_path=self.bill)
        self.assertEqual(result['status'],'raw_captured_pending_verified_context')
        self.assertEqual(result['normalized_n'],0)
        self.assertEqual(self.archive.export_document()['events'],[])

    def test_complete_raw_bound_scan_records_actual_two_sided_quotes_and_decision(self):
        source={'id':'test-match','sport_key':'soccer_epl','home_team':'Authored Home',
            'away_team':'Authored Away','commence_time':self.event()['kickoff_utc'],
            'bookmakers':[{'key':'authored-book','title':'Authored Book','markets':[
                {'key':'totals','last_update':(AT-timedelta(minutes=1)).isoformat(),
                 'outcomes':[{'name':'Over','point':2.75,'price':1.95},{'name':'Under','point':2.75,'price':1.95}]}]}]}
        raw=self.raw(json.dumps([source]).encode())
        match=dict(source,_observation_raw_id=raw['raw_id'],_observation_context={
            'identity_verified':True,'season':'2627','period':'FT90','model_version':'a'*64,
            'season_evidence_url':'https://example.invalid/season',
            'identity_evidence_url':'https://example.invalid/fixture',
            'period_evidence_url':'https://example.invalid/market-definitions'})
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path)}):
            result=capture_completed_scan('soccer_epl',[match],[],[],None,None,bill_db_path=self.bill)
        self.assertEqual(result['status'],'captured')
        self.assertEqual((result['normalized_n'],result['decisions_n']),(3,1))
        self.assertEqual(result['events'][0]['feature_capture_status'],'missing_row_bound_feature_observations')
        document=self.archive.export_document()
        self.assertEqual(len(document['events']),1)
        self.assertEqual([(row['side'],row['point'],row['odds']) for row in document['quotes']],
            [('over',2.75,1.95),('under',2.75,1.95)])
        self.assertTrue(all('odds_in_range' not in row for row in document['quotes']))
        self.assertEqual(document['score_history'],[])
        self.assertEqual(document['xg_history'],[])
        self.assertTrue(all(Archive(document).proof(row) is None for kind in ('events','quotes','decisions') for row in document[kind]))

    def test_cached_raw_identity_mismatch_cannot_normalize_a_different_fixture(self):
        source={'id':'test-match','sport_key':'soccer_epl','home_team':'Authored Home',
            'away_team':'Authored Away','commence_time':self.event()['kickoff_utc'],'bookmakers':[]}
        raw=self.raw(json.dumps([source]).encode())
        match=dict(source,home_team='Other Team',_observation_raw_id=raw['raw_id'],_observation_context={
            'identity_verified':True,'season':'2627','period':'FT90',
            **{key:'https://example.invalid/proof' for key in
               ('season_evidence_url','identity_evidence_url','period_evidence_url')}})
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path)}):
            result=capture_completed_scan('soccer_epl',[match],[],[],None,None,bill_db_path=self.bill)
        self.assertEqual(result['normalized_n'],0)
        self.assertEqual(result['events'][0]['reason'],'raw_event_identity_mismatch')

    def test_collector_uses_documented_bookmaker_update_scope_without_hiding_invalid_market_clock(self):
        source={'id':'test-match','sport_key':'soccer_epl','home_team':'Authored Home',
            'away_team':'Authored Away','commence_time':self.event()['kickoff_utc'],
            'bookmakers':[{'key':'authored-book','title':'Authored Book',
                'last_update':(AT-timedelta(minutes=1)).isoformat(),'markets':[
                {'key':'totals','outcomes':[{'name':'Over','point':2.5,'price':1.95},
                                         {'name':'Under','point':2.5,'price':1.95}]}]}]}
        context={'identity_verified':True,'season':'2627','period':'FT90',
            **{key:'https://example.invalid/proof' for key in
               ('season_evidence_url','identity_evidence_url','period_evidence_url')}}
        raw=self.raw(json.dumps([source]).encode())
        match=dict(source,_observation_raw_id=raw['raw_id'],_observation_context=context)
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path)}):
            captured=capture_completed_scan('soccer_epl',[match],[],[],None,None,bill_db_path=self.bill)
        self.assertEqual(captured['normalized_n'],3)
        quotes=self.archive.export_document()['quotes']
        self.assertTrue(all(row['source_update_scope']=='bookmaker' for row in quotes))
        self.assertTrue(all(row['source_updated_at']==source['bookmakers'][0]['last_update'] for row in quotes))
        source['bookmakers'][0]['markets'][0]['last_update']=None
        raw=self.raw(json.dumps([source]).encode())
        match=dict(source,_observation_raw_id=raw['raw_id'],_observation_context=context)
        with patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path)}):
            rejected=capture_completed_scan('soccer_epl',[match],[],[],None,None,bill_db_path=self.bill)
        self.assertEqual(rejected['normalized_n'],1)
        self.assertIn('provider_update_time_missing',rejected['events'][0]['reasons'])

    def test_later_normalization_cannot_retroactively_enter_an_earlier_decision(self):
        raw=self.raw()
        event=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        decision=self.archive.log_decision('test-match','a'*64,[event['observation_id']])
        with patch('observation_archive._now',return_value=AT+timedelta(minutes=2)):
            self.archive.record_normalized('score_history',dict(self.event(),match_id='historic-fixture',
                status='FT',home_goals=2,away_goals=1,source_date='2026-10-01'),raw['raw_id'])
        document=self.archive.export_document()
        archive=Archive(document)
        frame=archive.history_frame(document['score_history'],document['events'][0],decision['decision_at'])
        self.assertTrue(frame.empty)
        self.assertEqual(archive.history_exclusions['history_late_receipt_or_wrong_league'],1)
        self.assertEqual(document['decisions'][0]['input_observation_ids'],[event['observation_id']])

    def test_corrupted_raw_bytes_are_rejected_even_if_normalized_proof_is_intact(self):
        raw=self.raw()
        self.archive.record_normalized('events',self.event(),raw['raw_id'])
        with sqlite3.connect(self.path) as con:
            # Simulates external file tampering, not a supported mutation API.
            con.execute('DROP TRIGGER protect_observation_raw_update')
            con.execute('UPDATE observation_raw SET body=?',(b'tampered bytes',))
        with self.assertRaisesRegex(ValueError,'original_source_evidence_checksum'):
            self.archive.export_document()

    def test_export_cli_uses_only_explicit_observer_and_refuses_existing_output(self):
        spec=importlib.util.spec_from_file_location('observer_export',ROOT/'scripts/export_observed_backtest.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        target=Path(self.tmp.name)/'export.json'
        with patch('sys.stdout',new_callable=io.StringIO),patch('sys.stderr',new_callable=io.StringIO):
            self.assertEqual(module.main(['--archive-db',str(self.path),'--output',str(target)]),0)
            self.assertEqual(module.main(['--archive-db',str(self.path),'--output',str(target)]),2)
        self.assertEqual(json.loads(target.read_text())['events'],[])
        self.assertFalse(self.bill.exists())

    def test_misconfigured_observer_hook_does_not_break_primary_provider_response(self):
        class Response:content=b'fixture bytes'
        for value in ('not-json','[]'):
            with self.subTest(value=value),patch.dict(os.environ,{'VALUE_BET_OBSERVATION_DB':str(self.path),
                    'VALUE_BET_OBSERVATION_RIGHTS_JSON':value}):
                result=record_provider_response('fixture','https://example.invalid/data',Response(),bill_db_path=self.bill)
                self.assertEqual(result['status'],'capture_error')
                self.assertNotIn('not-json',str(result))

    def test_source_policy_credentials_and_normalized_secret_urls_are_rejected(self):
        self.archive.source_policies={'fixture':dict(self.policy,api_key='never-store-this')}
        raw=self.raw()
        self.assertEqual(raw['quality_status'],'quarantine')
        self.archive.source_policies={'fixture':self.policy}
        raw=self.raw()
        with self.assertRaisesRegex(ValueError,'credential_fields'):
            self.archive.record_normalized('events',self.event(evidence_url='https://example.invalid/data?key=never-store'),raw['raw_id'])

    def test_normalization_failure_is_atomic_and_does_not_append_partial_rows(self):
        raw=self.raw()
        with self.assertRaises(ValueError):
            self.archive.record_batch([('events',self.event()),('invalid',{})],raw['raw_id'])
        self.assertEqual(self.archive.export_document()['events'],[])

    def test_empty_export_is_honest_about_missing_actual_cohort(self):
        document=self.archive.export_document()
        self.assertEqual(document['data_mode'],'observed')
        self.assertTrue(all(document[kind]==[] for kind in ('events','quotes','score_history','xg_history','results','decisions')))
        self.assertEqual(document['source_catalog'],{})
        self.assertEqual(document['evidence_payloads'],{})

    def test_missing_decision_input_export_mapping_fails_closed(self):
        raw=self.raw()
        rows=self.archive.record_batch([('events',self.event()),('quotes',self.quote())],raw['raw_id'])
        self.archive.log_decision('test-match','a'*64,[row['observation_id'] for row in rows])
        with sqlite3.connect(self.path) as con:
            con.execute('DROP TRIGGER protect_observation_rows_update')
            con.execute("UPDATE observation_rows SET quality_status='quarantine' WHERE kind='quotes'")
        with self.assertRaisesRegex(ValueError,'input_export_mapping_missing'):
            self.archive.export_document()

    def test_wrong_frozen_input_checksum_cannot_be_rebound_during_export(self):
        raw=self.raw()
        row=self.archive.record_normalized('events',self.event(),raw['raw_id'])
        decision=self.archive.log_decision('test-match','a'*64,[row['observation_id']])
        with sqlite3.connect(self.path) as con:
            con.execute('DROP TRIGGER protect_observation_rows_update')
            original=json.loads(con.execute('SELECT normalized_json FROM observation_rows WHERE id=?',
                (decision['observation_id'],)).fetchone()[0])
            original['input_normalized_sha256']=['f'*64]
            body=json.dumps(original,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
            con.execute('UPDATE observation_rows SET normalized_json=?,normalized_sha256=? WHERE id=?',
                (body,hashlib.sha256(body.encode()).hexdigest(),decision['observation_id']))
        with self.assertRaisesRegex(ValueError,'input_normalized_evidence_mismatch'):
            self.archive.export_document()


if __name__=='__main__':
    unittest.main()
