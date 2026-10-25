"""Synthetic operational persistence: no real football data/provider/bill DB."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
import json
import sqlite3
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

import data_center_store as store
import scan_journal
from scan_diagnostics import filter_scan_fixtures

UTC=timezone.utc


class DataCenterStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'synthetic.db'
        self.con=sqlite3.connect(self.path);self.con.row_factory=sqlite3.Row
        self.addCleanup(self.con.close)
        scan_journal.initialize(self.con);store.initialize(self.con)
        self.now=datetime(2026,10,25,12,tzinfo=UTC)
        self.sport='soccer_epl';self.label='Synthetic League'
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        guard=patch('requests.get',side_effect=AssertionError('store cannot use HTTP'))
        self.http=guard.start();self.addCleanup(guard.stop)

    def tearDown(self):
        self.http.assert_not_called()

    def event(self,*,event_id='synthetic-event',hours=3,line=2.5,odds=1.95,receipt=None):
        receipt=receipt or self.now.isoformat()
        return {'id':event_id,'sport_key':self.sport,'home_team':'Synthetic Home','away_team':'Synthetic Away',
            'commence_time':(self.now+timedelta(hours=hours)).isoformat(),'_received_at':receipt,
            'bookmakers':[{'key':'fixture-book','title':'Synthetic Book','last_update':receipt,
                'markets':[{'key':'totals','outcomes':[{'name':'Over','point':line,'price':odds},
                    {'name':'Under','point':line,'price':1.95}]}]}]}

    def save(self,matches=None,*,run=None,receipt=None,now=None,region='uk'):
        matches=[self.event()] if matches is None else matches
        run=run or self.run
        hours=self.con.execute('SELECT hours_limit FROM scan_runs WHERE id=?',(run,)).fetchone()[0]
        _,report=filter_scan_fixtures(matches,self.sport,now or self.now,hours)
        return store.save_batch(self.con,run,self.sport,self.label,matches,
            self.now.isoformat() if receipt is None else receipt,report,'498','2',region)

    def cards(self,*,hours=24,region='uk',selected=None,now=None):
        return store.load_latest(self.con,selected or [self.sport],region,hours,now or self.now)

    def test_price_and_fixture_are_saved_without_a_b_c_xg_or_ev(self):
        result=self.save()
        self.assertEqual((result['fixtures'],result['quotes']),(1,2))
        self.assertEqual(len(result['rows']),2)
        self.assertTrue(all(row['ev_pct'] is None for row in result['rows']))
        card=self.cards()[0]
        self.assertEqual(card['data_status'],'available')
        self.assertIsNone(card['statistics'])
        self.assertEqual(card['kickoff_th'],'25/10/2026 22:00')
        self.assertEqual(card['quotes'][0]['source_update_scope'],'bookmaker')

    def test_program_without_prices_remains_visible(self):
        event=self.event();event['bookmakers']=[]
        result=self.save([event]);card=self.cards()[0]
        self.assertEqual(card['data_status'],'no_prices')
        self.assertEqual(card['quotes'],[])
        self.assertEqual(result['rows'][0]['market'],'ยังไม่มีราคา')
        self.assertIsNone(result['rows'][0]['point'])
        self.assertEqual(result['snapshots'],[])

    def test_ah_only_and_ou_only_do_not_invent_missing_market(self):
        event=self.event();market=event['bookmakers'][0]['markets'][0]
        market.update(key='spreads',outcomes=[{'name':'Synthetic Home','point':-.25,'price':2.0},
            {'name':'Synthetic Away','point':.25,'price':1.9}])
        self.save([event]);quotes=self.cards()[0]['quotes']
        self.assertEqual({q['market'] for q in quotes},{'Asian Handicap'})
        self.assertEqual({q['side'] for q in quotes},{'home','away'})

    def test_unknown_receipt_never_becomes_storage_clock(self):
        event=self.event();event.pop('_received_at')
        _,report=filter_scan_fixtures([event],self.sport,self.now,24)
        store.save_batch(self.con,self.run,self.sport,self.label,[event],None,report)
        card=self.cards()[0]
        self.assertIsNone(card['received_at'])
        self.assertEqual(card['data_status'],'missing_timestamp')
        self.assertIsNone(card['quotes'][0]['first_observed'])

    def test_cached_response_original_receipt_survives_later_storage(self):
        self.save()
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        source_now=self.now
        class WriteClock(datetime):
            @classmethod
            def now(cls,tz=None):return source_now+timedelta(minutes=3)
        with patch('data_center_store.datetime',WriteClock):self.save()
        self.assertEqual(self.cards()[0]['received_at'],self.now.isoformat())
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM dc_batches').fetchone()[0],2)
        self.assertEqual(self.con.execute('SELECT stored_at FROM dc_batches ORDER BY id DESC LIMIT 1').fetchone()[0],
                         (self.now+timedelta(minutes=3)).isoformat())

    def test_latest_failed_run_does_not_revive_old_fixture(self):
        self.save()
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        scan_journal.append(self.con,self.run,{'sport_key':self.sport,'status':'league_error','reason':'http_error'})
        self.assertEqual(self.cards(),[])
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM dc_batches').fetchone()[0],1)

    def test_latest_empty_success_does_not_mix_old_matches(self):
        self.save()
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        self.save([])
        self.assertEqual(self.cards(),[])
        self.assertEqual(len(store.load_batches(self.con,self.run)[self.sport]['matches']),0)

    def test_region_hours_and_selected_league_scope_are_exact(self):
        self.save()
        self.assertEqual(self.cards(region='us'),[])
        self.assertEqual(self.cards(hours=48),[])
        self.assertEqual(self.cards(selected=['soccer_other']),[])
        with self.assertRaisesRegex(ValueError,'scope_mismatch'):
            self.save(region='us')

    def test_48_hour_fixture_is_not_limited_to_24(self):
        event=self.event(hours=36)
        self.run=scan_journal.begin(self.con,[self.sport],'uk',48)
        result=self.save([event]);self.assertEqual(result['fixtures'],1)
        self.assertEqual(len(self.cards(hours=48)),1)
        self.assertEqual(self.cards(hours=24),[])

    def test_started_and_outside_window_and_naive_fixtures_are_not_invented(self):
        events=[self.event(event_id='past',hours=-1),self.event(event_id='far',hours=49),self.event(event_id='naive')]
        events[2]['commence_time']='2026-10-25T15:00:00'
        self.save(events)
        self.assertEqual(self.cards(),[])
        self.assertEqual(len(store.load_batches(self.con,self.run)[self.sport]['matches']),3)

    def test_duplicate_event_once_but_conflicting_identity_quarantined(self):
        event=self.event();self.save([event,deepcopy(event)])
        self.assertEqual(len(self.cards()),1)
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        bad=deepcopy(event);bad['away_team']='Other synthetic club'
        self.save([event,bad]);self.assertEqual(self.cards(),[])

    def test_same_team_fixture_is_not_presented(self):
        event=self.event();event['away_team']=event['home_team']
        self.save([event]);self.assertEqual(self.cards(),[])

    def test_all_real_bookmakers_lines_and_sides_kept(self):
        event=self.event()
        alt=deepcopy(event['bookmakers'][0]);alt.update(key='other',title='Other synthetic book')
        alt['markets'][0]['outcomes'][0].update(point=2.75,price=2.1)
        alt['markets'][0]['outcomes'][1].update(point=2.75,price=1.8)
        event['bookmakers'].append(alt)
        self.save([event]);quotes=self.cards()[0]['quotes']
        self.assertEqual(len(quotes),4)
        self.assertEqual({(q['bookmaker_key'],q['point']) for q in quotes},{('fixture-book',2.5),('other',2.75)})

    def test_first_observed_is_not_opening_and_line_change_is_explicit(self):
        self.save()
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        event=self.event(line=2.75,odds=2.05,receipt=(self.now+timedelta(minutes=1)).isoformat())
        self.save([event],receipt=event['_received_at'])
        over=next(q for q in self.cards()[0]['quotes'] if q['side']=='over')
        self.assertEqual(over['first_observed']['odds'],1.95)
        self.assertEqual(over['first_observed']['point'],2.5)
        self.assertTrue(over['first_observed']['line_changed'])
        self.assertEqual(over['first_observed']['label'],'First Observed Price')
        self.assertIsNone(over['opening_odds'])
        self.assertEqual(over['first_observed_scope'],'data_center_records_only')

    def test_unverified_opening_flags_are_not_trusted(self):
        event=self.event();event.update(opening_verified=True,opening_odds=8)
        event['bookmakers'][0]['markets'][0]['outcomes'][0]['opening_price']=9
        self.save([event]);quote=self.cards()[0]['quotes'][0]
        self.assertIsNone(quote['opening_odds'])
        payload=store.load_batches(self.con,self.run)[self.sport]['matches'][0]
        self.assertNotIn('opening_odds',payload)

    def test_first_simultaneous_alternative_lines_are_not_arbitrarily_ranked(self):
        event=self.event();outcomes=event['bookmakers'][0]['markets'][0]['outcomes']
        outcomes.extend([{'name':'Over','point':2.75,'price':1.8},{'name':'Under','point':2.75,'price':2.1}])
        self.save([event])
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        changed=self.event(line=3.0,receipt=(self.now+timedelta(minutes=1)).isoformat())
        self.save([changed],receipt=changed['_received_at'])
        for quote in self.cards()[0]['quotes']:
            self.assertIsNone(quote['first_observed'])
            self.assertEqual(quote['first_observed_status'],'ambiguous_first_snapshot')

    def test_first_same_contract_line_is_selected_from_simultaneous_alternatives(self):
        event=self.event();outcomes=event['bookmakers'][0]['markets'][0]['outcomes']
        outcomes.extend([{'name':'Over','point':2.75,'price':1.8},{'name':'Under','point':2.75,'price':2.1}])
        self.save([event]);quotes=self.cards()[0]['quotes']
        self.assertEqual(len(quotes),4)
        for quote in quotes:self.assertEqual(quote['first_observed']['point'],quote['point'])

    def test_conflicting_first_prices_are_unavailable_not_highest_price(self):
        event=self.event();event['bookmakers'][0]['markets'][0]['outcomes'].append({'name':'Over','point':2.5,'price':2.8})
        self.save([event]);over=[q for q in self.cards()[0]['quotes'] if q['side']=='over']
        self.assertEqual(len(over),2)
        self.assertTrue(all(q['first_observed'] is None for q in over))

    def test_reused_event_identity_cannot_mix_older_other_teams_first_price(self):
        self.save()
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24)
        event=self.event(odds=2.2);event['home_team']='Different Home'
        self.save([event]);over=next(q for q in self.cards()[0]['quotes'] if q['side']=='over')
        self.assertEqual(over['first_observed']['odds'],2.2)

    def test_old_prices_remain_visible_but_are_explicitly_not_recent(self):
        self.save();quote=self.cards(now=self.now+timedelta(minutes=16))[0]['quotes'][0]
        self.assertEqual(quote['odds'],1.95)
        self.assertEqual(quote['freshness_status'],'unverified_or_stale')
        self.assertEqual(quote['receipt_age_seconds'],960)

    def test_older_cached_current_receipt_does_not_compare_future_first_observation(self):
        later=self.event(receipt=(self.now+timedelta(minutes=1)).isoformat())
        self.save([later],receipt=later['_received_at'])
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24);self.save()
        quote=self.cards()[0]['quotes'][0]
        self.assertIsNone(quote['first_observed'])
        self.assertEqual(quote['first_observed_status'],'first_observation_after_current_receipt')

    def test_invalid_market_clock_never_hides_behind_bookmaker_clock(self):
        event=self.event();event['bookmakers'][0]['markets'][0]['last_update']=None
        self.save([event]);quote=self.cards()[0]['quotes'][0]
        self.assertIsNone(quote['source_updated_at'])
        self.assertEqual(quote['source_update_scope'],'invalid_market')
        self.assertEqual(self.cards()[0]['data_status'],'missing_timestamp')

    def test_bad_prices_do_not_hide_valid_fixture_or_generate_price(self):
        event=self.event();outcomes=event['bookmakers'][0]['markets'][0]['outcomes']
        outcomes[0]['price']=float('nan');outcomes[1]['point']=2.6
        self.save([event]);card=self.cards()[0]
        self.assertEqual(card['quotes'],[])
        self.assertEqual(card['data_status'],'no_prices')
        self.assertNotIn('NaN',self.con.execute('SELECT payload_json FROM dc_batches').fetchone()[0])

    def test_snapshot_is_append_only_and_cached_idempotent(self):
        self.save();self.save()
        self.assertEqual(self.con.execute('SELECT COUNT(*) FROM dc_batches').fetchone()[0],1)
        for sql in ('DELETE FROM dc_batches','UPDATE dc_batches SET received_at=NULL'):
            with self.assertRaises(sqlite3.IntegrityError):self.con.execute(sql)
        event=self.event(odds=2.1)
        with self.assertRaisesRegex(ValueError,'already_recorded'):self.save([event])

    def test_sources_failure_enrichment_does_not_remove_fixture(self):
        self.save()
        payload={'status':'partial','event_id':'synthetic-event','sources':{'understat':{'status':'http_error'}},'xg':None}
        store.save_enrichment(self.con,self.run,'synthetic-event',payload)
        card=self.cards()[0]
        self.assertEqual(card['statistics'],payload)
        self.assertEqual(len(card['quotes']),2)
        self.assertEqual(card['data_status'],'available')

    def test_enrichment_latest_revision_only_same_run(self):
        self.save()
        store.save_enrichment(self.con,self.run,'synthetic-event',{'status':'error'})
        store.save_enrichment(self.con,self.run,'synthetic-event',{'status':'ready','form5_n':5})
        self.assertEqual(self.cards()[0]['statistics']['form5_n'],5)
        self.run=scan_journal.begin(self.con,[self.sport],'uk',24);self.save()
        self.assertIsNone(self.cards()[0]['statistics'])

    def test_enrichment_identity_or_credentials_never_overwrite_fixture(self):
        self.save()
        for event,payload in [('missing',{}),('synthetic-event',{'away_team':'other'}),
                              ('synthetic-event',{'api_key':'not-real-do-not-store'})]:
            with self.subTest(event=event,payload=payload),self.assertRaises(ValueError):
                store.save_enrichment(self.con,self.run,event,payload)
        self.assertIsNone(self.cards()[0]['statistics'])

    def test_unknown_secret_fields_are_not_persisted(self):
        event=self.event();event['apiKey']='NOT_A_REAL_KEY_SENTINEL'
        event['bookmakers'][0]['headers']={'Authorization':'NOT_A_REAL_KEY_SENTINEL'}
        self.save([event])
        stored=self.con.execute('SELECT payload_json FROM dc_batches').fetchone()[0]
        self.assertNotIn('NOT_A_REAL_KEY_SENTINEL',stored)
        self.assertEqual(len(self.cards()),1)

    def test_new_data_tables_leave_bills_and_prior_prices_unchanged(self):
        self.con.execute('CREATE TABLE slips(id INTEGER,selections_json TEXT,status TEXT)')
        self.con.execute('CREATE TABLE picks(id INTEGER,odds REAL,status TEXT)')
        self.con.execute("INSERT INTO slips VALUES(1,'{broken','ตรวจแล้ว')")
        self.con.execute("INSERT INTO picks VALUES(1,1.9,'ตรวจแล้ว')")
        before=[tuple(row) for row in self.con.execute('SELECT * FROM slips')]
        picks=[tuple(row) for row in self.con.execute('SELECT * FROM picks')]
        self.save();store.save_enrichment(self.con,self.run,'synthetic-event',{'status':'missing'})
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM slips')],before)
        self.assertEqual([tuple(row) for row in self.con.execute('SELECT * FROM picks')],picks)

    def test_operational_payload_replay_retains_actual_fields_and_receipts(self):
        event=self.event();event['_observation_raw_id']=42
        self.save([event]);batch=store.load_batches(self.con,self.run)[self.sport]
        self.assertEqual(batch['matches'][0]['_received_at'],self.now.isoformat())
        self.assertEqual(batch['matches'][0]['_observation_raw_id'],42)
        self.assertEqual(batch['matches'][0]['bookmakers'][0]['last_update'],self.now.isoformat())
        self.assertEqual(batch['matches'][0]['bookmakers'][0]['markets'][0]['outcomes'][0],{'name':'Over','point':2.5,'price':1.95})
        self.assertIn('not_original_wire_bytes',batch['storage_kind'])


if __name__=='__main__':unittest.main()
