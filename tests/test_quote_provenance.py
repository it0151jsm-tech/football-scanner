"""Authored provider-schema fixtures, not captured live prices or time evidence."""
from copy import deepcopy
from datetime import datetime,timezone
import json
import unittest
from unittest.mock import patch

from quote_provenance import PROVIDER_DOCUMENTATION,quote_update
from readiness import quote_freshness

NOW=datetime(2026,10,24,12,tzinfo=timezone.utc)

# Minimal authored shape follows the publicly documented general GET /odds
# example. It does not claim these teams/prices were observed from the API.
DOCUMENTED_SHAPE_FIXTURE={
    'key':'authored_book','title':'Authored Book','last_update':'2026-10-24T11:59:00Z',
    'markets':[{'key':'totals','outcomes':[
        {'name':'Over','point':2.75,'price':1.95},
        {'name':'Under','point':2.75,'price':1.95}]}]}


class QuoteProvenanceTests(unittest.TestCase):
    def row(self,update):
        return dict(event_id='authored-event',bookmaker='Authored Book',bookmaker_key='authored_book',
            market='Over/Under',side='over',point=2.75,odds=1.95,
            received_at='2026-10-24T12:00:00Z',**update)

    def test_documented_bookmaker_level_update_is_used_when_market_field_absent(self):
        book=deepcopy(DOCUMENTED_SHAPE_FIXTURE)
        market=book['markets'][0]
        self.assertNotIn('last_update',market)
        update=quote_update(book,market)
        self.assertEqual(update,{'source_updated_at':'2026-10-24T11:59:00Z','source_update_scope':'bookmaker'})
        self.assertEqual(quote_freshness(self.row(update),now=NOW),(True,''))
        self.assertEqual(PROVIDER_DOCUMENTATION,'https://the-odds-api.com/liveapi/guides/v4/#get-odds')

    def test_explicit_market_timestamp_takes_precedence_over_bookmaker_timestamp(self):
        update=quote_update({'last_update':'2026-10-24T11:58:00Z'},
                            {'last_update':'2026-10-24T11:59:30Z'})
        self.assertEqual(update,{'source_updated_at':'2026-10-24T11:59:30Z','source_update_scope':'market'})

    def test_invalid_explicit_market_timestamp_never_falls_back_to_valid_bookmaker(self):
        for value in (None,'',False,123,{},[],'invalid','2026-10-24T11:59:00',
                      ' 2026-10-24T11:59:00Z','2026-10-24T11:59:00Z\n'):
            with self.subTest(value=value):
                update=quote_update({'last_update':'2026-10-24T11:59:00Z'},{'last_update':value})
                self.assertEqual(update,{'source_updated_at':None,'source_update_scope':'invalid_market'})
                self.assertFalse(quote_freshness(self.row(update),now=NOW)[0])

    def test_invalid_or_absent_bookmaker_clock_never_substitutes_receipt(self):
        for bookmaker in ({},{'last_update':None},{'last_update':'2026-10-24T11:59:00'},
                          {'last_update':'bad'},None,[],{'last_update':True}):
            with self.subTest(bookmaker=bookmaker):
                update=quote_update(bookmaker,{})
                self.assertEqual(update,{'source_updated_at':None,'source_update_scope':'missing'})
                self.assertFalse(quote_freshness(self.row(update),now=NOW)[0])

    def test_malformed_market_container_does_not_hide_behind_bookmaker_update(self):
        for market in (None,[],False,'totals'):
            with self.subTest(market=market):
                self.assertEqual(quote_update({'last_update':'2026-10-24T11:59:00Z'},market),
                    {'source_updated_at':None,'source_update_scope':'invalid_market'})

    def test_aware_source_offset_is_preserved_and_compared_as_same_instant(self):
        update=quote_update({'last_update':'2026-10-24T19:00:00+07:00'},{})
        self.assertEqual(update['source_updated_at'],'2026-10-24T19:00:00+07:00')
        self.assertTrue(quote_freshness(self.row(update),now=NOW)[0])

    def test_future_bookmaker_clock_fails_existing_ordering_gate(self):
        update=quote_update({'last_update':'2026-10-24T12:01:00Z'},{})
        self.assertEqual(update['source_update_scope'],'bookmaker')
        self.assertEqual(update['source_updated_at'],'2026-10-24T12:01:00Z')
        self.assertFalse(quote_freshness(self.row(update),now=NOW)[0])

    def test_future_market_clock_cannot_fall_back_to_older_valid_bookmaker(self):
        update=quote_update({'last_update':'2026-10-24T11:59:00Z'},
                            {'last_update':'2026-10-24T12:01:00Z'})
        self.assertEqual(update['source_update_scope'],'market')
        self.assertFalse(quote_freshness(self.row(update),now=NOW)[0])

    def test_stale_bookmaker_timestamp_stays_stale_despite_fresh_receipt(self):
        update=quote_update({'last_update':'2026-10-24T11:44:59Z'},{})
        fresh,reason=quote_freshness(self.row(update),now=NOW)
        self.assertFalse(fresh)
        self.assertIn('เก่า',reason)

    def test_stale_market_timestamp_cannot_fall_back_to_newer_bookmaker_clock(self):
        update=quote_update({'last_update':'2026-10-24T11:59:00Z'},
                            {'last_update':'2026-10-24T11:44:59Z'})
        self.assertEqual(update['source_update_scope'],'market')
        self.assertFalse(quote_freshness(self.row(update),now=NOW)[0])

    def test_existing_freshness_boundary_is_unchanged(self):
        at_boundary=quote_update({'last_update':'2026-10-24T11:45:00Z'},{})
        self.assertTrue(quote_freshness(self.row(at_boundary),now=NOW,max_age=900)[0])
        past_boundary=quote_update({'last_update':'2026-10-24T11:44:59Z'},{})
        self.assertFalse(quote_freshness(self.row(past_boundary),now=NOW,max_age=900)[0])

    def test_absent_bookmaker_does_not_discard_valid_market_timestamp(self):
        update=quote_update(None,{'last_update':'2026-10-24T11:59:00Z'})
        self.assertEqual(update['source_update_scope'],'market')
        # This helper is update evidence only, not proof of bookmaker identity.
        row=self.row(update)
        row['bookmaker_key']=None
        self.assertFalse(quote_freshness(row,now=NOW)[0])

    def test_helper_has_no_network_clock_or_input_mutation(self):
        book=deepcopy(DOCUMENTED_SHAPE_FIXTURE)
        original=deepcopy(book)
        with patch('requests.sessions.Session.request',side_effect=AssertionError('no provider request')) as request:
            first=quote_update(book,book['markets'][0])
            second=quote_update(book,book['markets'][0])
        self.assertEqual(first,second)
        self.assertEqual(book,original)
        request.assert_not_called()

    def scan_case(self):
        import test_issue9_scan
        case=test_issue9_scan.Issue9ScanTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare()
        case.event['bookmakers'][0]['last_update']=case.now.isoformat()
        for market in case.event['bookmakers'][0]['markets']:
            market.pop('last_update',None)
        return case

    def test_documented_bookmaker_clock_survives_scan_save_load_and_main_ou_gate(self):
        from market_recommendations import group_fixture_recommendations
        case=self.scan_case()
        with patch('requests.sessions.Session.request',side_effect=AssertionError('no live provider')) as request:
            rows,snapshots,_,_=case.scan()
            case.f['save_picks'](rows,snapshots)
            saved=case.f['load_picks'](limit=None)
            with case.f['db']() as con:
                stored_snapshots=list(con.execute('SELECT source_updated_at,source_update_scope FROM odds_snapshots'))
        self.assertTrue(rows)
        self.assertTrue(stored_snapshots)
        self.assertTrue(all(row['source_update_scope']=='bookmaker' for row in rows))
        self.assertTrue(all(row['source_updated_at']==case.now.isoformat() for row in rows))
        self.assertTrue(all(row['source_update_scope']=='bookmaker' for row in snapshots))
        self.assertTrue(saved.source_update_scope.eq('bookmaker').all())
        self.assertTrue(all(tuple(row)==(case.now.isoformat(),'bookmaker') for row in stored_snapshots))
        for row in saved.to_dict('records'):
            self.assertEqual(json.loads(row['analysis_a_json'])['price']['source_update_scope'],'bookmaker')
            self.assertTrue(quote_freshness(row)[0])
        grouped=group_fixture_recommendations(saved.to_dict('records'),case.f['choose_recommendation'])
        self.assertEqual(len(grouped),1)
        self.assertIsNotNone(grouped[0]['ou_main']['pair'])
        request.assert_not_called()

    def test_invalid_explicit_market_update_remains_missing_through_sqlite(self):
        case=self.scan_case()
        for market in case.event['bookmakers'][0]['markets']:
            market['last_update']=None
        rows,snapshots,_,_=case.scan()
        case.f['save_picks'](rows,snapshots)
        saved=case.f['load_picks'](limit=None)
        self.assertTrue(saved.source_update_scope.eq('invalid_market').all())
        self.assertTrue(saved.source_updated_at.isna().all())
        self.assertTrue(all(not quote_freshness(row)[0] for row in saved.to_dict('records')))


if __name__=='__main__':
    unittest.main()
