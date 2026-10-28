"""Explicit coverage for every selected league, using mock scan callables only."""
from copy import deepcopy
import unittest

from odds_budget import LeagueScanError,ScanStopped
from scan_control import run_selected_leagues,scan_summary_text


class ScanControlTests(unittest.TestCase):
    def setUp(self):
        self.config={f'league-{n}':(f'League {n}',None) for n in range(38)}
        self.selected=list(self.config)
        self.calls=[]
        self.evidence={key:dict(api_returned=3,passed_filter=2,excluded_count=1,
            excluded_by_reason={'outside_window':1},api_received_at='2026-10-24T12:00:00Z') for key in self.selected}

    def scanner(self,key,name,csv_code):
        self.calls.append(key)
        rows=[dict(event_id=key+'-event-'+str(n),market=market) for n in range(2)
              for market in ('Asian Handicap','Over/Under')]
        return rows,[dict(event_id=row['event_id']) for row in rows],400,100

    def run_scan(self,selected=None,scanner=None,**kwargs):
        return run_selected_leagues(selected if selected is not None else self.selected,
            self.config,scanner or self.scanner,report_for=lambda key:self.evidence[key],**kwargs)

    def test_eleven_selected_all_checked_without_equating_market_rows_to_matches(self):
        result=self.run_scan(self.selected[:11])
        self.assertEqual(len(self.calls),11)
        self.assertEqual(result['counts']['selected_leagues'],11)
        self.assertEqual(result['counts']['successful_leagues'],11)
        self.assertEqual(result['counts']['api_returned'],33)
        self.assertEqual(result['counts']['passed_filter'],22)
        self.assertEqual(result['counts']['saved_fixture_count'],22)
        self.assertEqual(result['counts']['market_rows'],44)
        self.assertEqual(result['counts']['snapshot_rows'],44)

    def test_all_thirty_eight_configured_leagues_receive_a_result(self):
        result=self.run_scan()
        self.assertEqual(self.calls,self.selected)
        self.assertEqual(len(result['leagues']),38)
        self.assertEqual(result['counts']['api_returned'],114)
        self.assertEqual(result['counts']['passed_filter'],76)
        self.assertTrue(result['counts']['response_counts_complete'])

    def test_transient_error_at_fourth_league_does_not_stop_eight_later_leagues(self):
        def scanner(key,*args):
            if key=='league-3':
                self.calls.append(key)
                raise LeagueScanError('API temporary',reason='http_error',status=503)
            return self.scanner(key,*args)
        result=self.run_scan(self.selected[:11],scanner)
        self.assertEqual(self.calls,self.selected[:11])
        self.assertEqual(result['counts']['successful_leagues'],10)
        self.assertEqual(result['leagues'][3]['status'],'league_error')
        self.assertEqual(result['leagues'][3]['http_status'],503)
        self.assertIsNone(result['leagues'][3]['api_returned'])
        self.assertFalse(result['counts']['response_counts_complete'])
        self.assertFalse(result['stopped'])
        self.assertEqual(result['leagues'][-1]['status'],'success')

    def test_unexpected_error_does_not_leak_exception_or_drop_other_leagues(self):
        def scanner(key,*args):
            if key=='league-1':raise RuntimeError('apiKey=DO_NOT_PRINT')
            return self.scanner(key,*args)
        result=self.run_scan(self.selected[:3],scanner)
        self.assertEqual(result['leagues'][1]['reason'],'unexpected_scan_error')
        self.assertEqual(result['leagues'][2]['status'],'success')
        self.assertNotIn('DO_NOT_PRINT',str(result))

    def test_cooldown_skips_only_affected_league(self):
        def scanner(key,*args):
            if key=='league-0':raise LeagueScanError('recent',reason='cooldown')
            return self.scanner(key,*args)
        result=self.run_scan(self.selected[:3],scanner)
        self.assertEqual(result['counts']['cooldown_leagues'],1)
        self.assertEqual(result['counts']['successful_leagues'],2)
        self.assertFalse(result['stopped'])

    def test_auth_budget_stop_lists_every_remaining_unrequested_league(self):
        def scanner(key,*args):
            if key=='league-3':
                self.calls.append(key)
                raise ScanStopped('safety',reason='provider_safety_stop',status=429)
            return self.scanner(key,*args)
        progress=[]
        result=self.run_scan(self.selected[:11],scanner,on_progress=lambda index,total,entry:progress.append((index,total,entry)))
        self.assertEqual(self.calls,self.selected[:4])
        self.assertEqual(len(result['leagues']),11)
        self.assertEqual(len(progress),11)
        self.assertTrue(result['stopped'])
        self.assertEqual(result['counts']['skipped_safety_stop'],7)
        self.assertFalse(result['counts']['response_counts_complete'])
        self.assertTrue(all(row['status']=='skipped_safety_stop' and row['api_returned'] is None
            for row in result['leagues'][4:]))

    def test_empty_api_is_distinguished_from_filtering_and_unknown_response(self):
        self.evidence['league-0'].update(api_returned=0,passed_filter=0,excluded_count=0)
        self.evidence['league-1'].update(api_returned=3,passed_filter=0,excluded_count=3)
        self.evidence['league-2']={}
        result=self.run_scan(self.selected[:3],lambda *args:([],[],400,100))
        self.assertEqual([row['status'] for row in result['leagues']],['empty_api','filtered_out','success_no_rows'])
        self.assertFalse(result['counts']['response_counts_complete'])
        self.assertIn('เฉพาะลีกที่มีหลักฐาน',scan_summary_text(result))

    def test_api_fixtures_without_saved_rows_are_diagnosed_not_called_no_schedule(self):
        result=self.run_scan(self.selected[:1],lambda *args:([],[],400,100))
        self.assertEqual(result['leagues'][0]['status'],'fixtures_without_rows')
        self.assertEqual(result['leagues'][0]['passed_filter'],2)

    def test_missing_prices_preserves_fixture_placeholder_and_market_coverage(self):
        def scanner(*args):
            return [dict(event_id='a',market='Asian Handicap'),dict(event_id='b',market='Over/Under'),
                    dict(event_id='c',market='ยังไม่มีราคา')],[],None,None
        result=self.run_scan(self.selected[:1],scanner)
        row=result['leagues'][0]
        self.assertEqual((row['saved_fixture_count'],row['ah_fixtures'],row['ou_fixtures'],row['no_price_fixtures']),(3,1,1,1))

    def test_duplicate_and_unknown_selection_do_not_make_requests(self):
        result=self.run_scan(['league-0','league-0','not-configured','league-1'])
        self.assertEqual(self.calls,['league-0','league-1'])
        self.assertEqual([row['status'] for row in result['leagues']],['success','skipped_duplicate','invalid_league','success'])

    def test_reporting_failure_keeps_rows_and_continues_other_leagues(self):
        result=run_selected_leagues(self.selected[:2],self.config,self.scanner,
            report_for=lambda key:(_ for _ in ()).throw(ValueError('secret')))
        self.assertEqual(len(result['rows']),8)
        self.assertEqual(result['counts']['successful_leagues'],2)
        self.assertFalse(result['counts']['response_counts_complete'])
        self.assertNotIn('secret',str(result))

    def test_nonfatal_progress_failure_cannot_silently_abort_loop(self):
        result=self.run_scan(self.selected[:2],on_progress=lambda *args:(_ for _ in ()).throw(ValueError('private')))
        self.assertEqual(len(self.calls),2)
        self.assertTrue(all(row['progress_callback_error'] for row in result['leagues']))
        self.assertNotIn('private',str(result))

    def test_storage_callback_can_stop_before_spending_more_credits(self):
        def persist(*args):raise ScanStopped('storage unavailable',reason='scan_journal_unavailable')
        result=self.run_scan(self.selected[:3],on_progress=persist)
        self.assertEqual(self.calls,['league-0'])
        self.assertEqual(result['counts']['skipped_safety_stop'],2)
        self.assertEqual(result['stop_reason'],'scan_journal_unavailable')

    def test_no_input_mutation_or_quote_dedup_that_would_lose_raw_prices(self):
        raw=[dict(event_id='same',market='Over/Under',bookmaker=book,point=line)
             for book in ('one','two') for line in (2.5,2.75,3.0)]
        before=deepcopy(raw)
        selected=self.selected[:1]
        result=self.run_scan(selected,lambda *args:(raw,raw,None,None))
        self.assertEqual(len(result['rows']),6)
        self.assertEqual(len(result['snapshots']),6)
        self.assertEqual(result['counts']['saved_fixture_count'],1)
        self.assertEqual(raw,before)
        self.assertEqual(selected,self.selected[:1])

