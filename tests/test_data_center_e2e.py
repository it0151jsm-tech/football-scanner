"""Mock provider -> real Data Center controller -> temporary SQLite -> reload.

These tests author football observations. They never demonstrate live provider
availability, actual match coverage, model accuracy, calibration or profit.
"""
from copy import deepcopy
from datetime import timedelta
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock

import pandas as pd

from bill_backup import backup_bytes, restore_new
from odds_budget import LeagueScanError, OddsBatch, ScanStopped
from test_operational_e2e import OperationalHarness, MAJOR

ROOT = Path(__file__).resolve().parents[1]
MATRIX = json.loads((ROOT/'fixtures/data-center/scan-matrix.json').read_text())


def quote_facts(quotes):
    """Persisted source facts stay fixed while computed age advances on reads."""
    keys=('market','side','point','odds','bookmaker','bookmaker_key','received_at',
          'source_updated_at','source_update_scope')
    return [tuple(quote.get(key) for key in keys) for quote in quotes]


def artifact(name, result, cards, **extra):
    folder = os.environ.get('DASHBOARD_SCREENSHOTS')
    if not folder:
        return
    path = Path(folder)
    path.mkdir(parents=True, exist_ok=True)
    value = {'data_mode':'synthetic_test_fixture','case':name,
        'real_provider_calls':0,'live_end_to_end_verified':False,
        'scope':'Data Center software acceptance with authored observations; not live coverage',
        'counts':result.get('counts',{}),'league_results':result.get('leagues',[]),
        'restored_fixture_n':len(cards),'restored_quote_n':sum(len(card.get('quotes',[])) for card in cards),
        **extra}
    (path/f'data-center-{name}.json').write_text(json.dumps(value, ensure_ascii=False, indent=2))


class DataCenterEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.h = OperationalHarness(self)
        self.selected = ['soccer_epl']
        self.h.events[self.selected[0]] = [self.h.event(self.selected[0])]

    def tearDown(self):
        self.h.http.assert_not_called()

    def scan(self, *, selected=None, hours=24, region='uk'):
        return self.h.f['execute_data_center_scan'](selected or self.selected,
            hours, 1.8, 2.15, region, 'SYNTHETIC_NO_CREDIT')

    def cards(self, *, selected=None, hours=24, region='uk'):
        return self.h.f['load_data_center_cards'](selected or self.selected, region, hours)

    def matrix(self):
        selected = list(self.h.f['LEAGUES'])[:11]
        self.assertTrue(MAJOR.issubset(selected))
        for sport in selected:
            self.h.events[sport] = [self.h.event(sport, row['suffix'], row['hours'],
                embedded_key=sport if row['embedded_key']=='selected' else 'soccer_wrong_unselected')
                for row in MATRIX['fixture_offsets']]
        return selected

    def test_one_league_raw_scan_never_waits_for_stats_or_models(self):
        self.h.f['fetch_history'] = Mock(side_effect=AssertionError('statistics must be optional'))
        self.h.f['fetch_understat_history'] = Mock(side_effect=AssertionError('xG must be optional'))
        self.h.f['scan_league'] = Mock(side_effect=AssertionError('legacy analysis must not gate fixtures'))
        self.h.f['estimate_goals'] = Mock(side_effect=AssertionError('no model on raw scan'))
        result = self.scan()
        cards = self.cards()
        self.assertEqual(result['counts']['successful_leagues'], 1)
        self.assertEqual(len(cards), 1)
        self.assertEqual(len(cards[0]['quotes']), 4)
        self.assertEqual(cards[0]['event_id'], self.h.events[self.selected[0]][0]['id'])
        self.h.f['fetch_history'].assert_not_called()
        self.h.f['fetch_understat_history'].assert_not_called()
        self.h.f['scan_league'].assert_not_called()
        self.h.f['estimate_goals'].assert_not_called()
        artifact('one-league-no-statistics', result, cards, model_calls=0, statistics_calls=0)

    def test_first_league_is_queryable_before_later_league_requests_finish(self):
        selected=['soccer_epl','soccer_spain_la_liga']
        for sport in selected:
            self.h.events[sport]=[self.h.event(sport)]
        observed=[]
        def progress(index,total,entry):
            cards=self.cards(selected=selected)
            observed.append((index,total,len(cards),self.h.f['fetch_odds'].call_count))
        self.h.f['execute_data_center_scan'](selected,24,1.8,2.15,'uk',
            'SYNTHETIC_NO_CREDIT',on_progress=progress)
        self.assertEqual(observed,[(1,2,1,1),(2,2,2,2)])
        self.h.f['fetch_history'].assert_not_called()

    def test_eleven_leagues24h_store_before_analysis_and_restore_all_fixtures(self):
        selected = self.matrix()
        result = self.scan(selected=selected)
        cards = self.cards(selected=selected)
        self.assertEqual(result['counts']['selected_leagues'], 11)
        self.assertEqual(result['counts']['successful_leagues'], 11)
        self.assertEqual(result['counts']['api_returned'], 55)
        self.assertEqual(result['counts']['passed_filter'], 11)
        self.assertEqual(len(cards), 11)
        self.assertEqual(len({row['event_id'] for row in cards}), 11)
        self.assertEqual(sum(len(row['quotes']) for row in cards), 44)
        self.assertEqual(self.h.f['fetch_odds'].call_count, 11)
        self.h.f['fetch_history'].assert_not_called()
        self.h.f['fetch_understat_history'].assert_not_called()
        self.assertEqual(len(self.h.f['load_picks'](limit=None)), 0)
        artifact('eleven-leagues24h', result, cards, statistics_calls=0, model_calls=0)

    def test_eleven_leagues48h_includes36_hour_games_and_thai_kickoff(self):
        selected = self.matrix()
        result = self.scan(selected=selected, hours=48)
        cards = self.cards(selected=selected, hours=48)
        self.assertEqual(result['counts']['api_returned'], 55)
        self.assertEqual(result['counts']['passed_filter'], 22)
        self.assertEqual(len(cards), 22)
        self.assertEqual(sum(len(row['quotes']) for row in cards), 88)
        for row in cards:
            self.assertTrue(row['kickoff_th'])
        artifact('eleven-leagues48h', result, cards, statistics_calls=0, model_calls=0)

    def test_all_configured_leagues_can_show_programs_without_supported_statistics(self):
        selected=list(self.h.f['LEAGUES'])
        self.assertEqual(len(selected),38)
        for sport in selected:
            self.h.events[sport]=[self.h.event(sport, markets=())]
        self.h.f['fetch_history']=Mock(side_effect=AssertionError('no score source needed for programs'))
        self.h.f['fetch_domestic_rosters']=Mock(side_effect=AssertionError('no domestic lookup needed for cup programs'))
        result=self.scan(selected=selected)
        cards=self.cards(selected=selected)
        self.assertEqual(result['counts']['successful_leagues'],38)
        self.assertEqual(len(cards),38)
        self.assertTrue(all(card['quotes']==[] for card in cards))
        self.h.f['fetch_history'].assert_not_called()
        self.h.f['fetch_domestic_rosters'].assert_not_called()
        artifact('all38-without-statistics',result,cards,statistics_calls=0,model_calls=0)

    def test_no_market_prices_does_not_hide_received_program(self):
        sport = self.selected[0]
        self.h.events[sport] = [self.h.event(sport, markets=())]
        result = self.scan()
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['quotes'], [])
        self.assertIn(cards[0]['data_status'], ('no_prices','no_market_prices'))
        self.assertEqual(result['counts']['successful_leagues'], 1)
        artifact('program-without-prices', result, cards)

    def test_ah_only_preserves_real_line_without_manufacturing_ou(self):
        sport = self.selected[0]
        self.h.events[sport] = [self.h.event(sport, markets=('spreads',))]
        self.scan()
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual({quote['market'] for quote in cards[0]['quotes']}, {'Asian Handicap'})
        self.assertEqual({quote['point'] for quote in cards[0]['quotes']}, {-.25,.25})

    def test_ou_only_preserves_quarter_line_without_manufacturing_ah(self):
        sport = self.selected[0]
        self.h.events[sport] = [self.h.event(sport, markets=('totals',))]
        self.scan()
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual({quote['market'] for quote in cards[0]['quotes']}, {'Over/Under'})
        self.assertEqual({quote['point'] for quote in cards[0]['quotes']}, {2.75})
        self.assertEqual({quote['side'] for quote in cards[0]['quotes']}, {'over','under'})

    def test_temporary_league_timeout_keeps_remaining_selected_leagues(self):
        selected = self.matrix()
        def fetch(sport, region, key):
            if sport == selected[2]:
                raise LeagueScanError('safe fixture timeout', reason='network_error')
            return self.h.fetch_odds(sport, region, key)
        self.h.f['fetch_odds'] = Mock(side_effect=fetch)
        result = self.scan(selected=selected)
        cards = self.cards(selected=selected)
        self.assertFalse(result['stopped'])
        self.assertEqual(result['counts']['successful_leagues'], 10)
        self.assertEqual(len(result['leagues']), 11)
        self.assertEqual(result['leagues'][2]['status'], 'league_error')
        self.assertEqual(self.h.f['fetch_odds'].call_count, 11)
        self.assertEqual(len(cards), 10)
        artifact('one-league-timeout', result, cards)

    def test_quota_stops_further_requests_but_preserves_already_received_cards(self):
        selected = self.matrix()
        def fetch(sport, region, key):
            if sport == selected[3]:
                raise ScanStopped('safe fixture quota stop', reason='provider_safety_stop', status=429)
            return self.h.fetch_odds(sport, region, key)
        self.h.f['fetch_odds'] = Mock(side_effect=fetch)
        result = self.scan(selected=selected)
        cards = self.cards(selected=selected)
        self.assertTrue(result['stopped'])
        self.assertEqual(self.h.f['fetch_odds'].call_count, 4)
        self.assertEqual(len(result['leagues']), 11)
        self.assertEqual(result['leagues'][3]['status'], 'safety_stop')
        self.assertTrue(all(row['status']=='skipped_safety_stop' for row in result['leagues'][4:]))
        self.assertEqual(len(cards), 3)
        artifact('quota-stop-after-three', result, cards)

    def test_fresh_old_cards_not_mixed_into_latest_failed_league(self):
        selected = ['soccer_epl','soccer_spain_la_liga']
        for sport in selected:
            self.h.events[sport] = [self.h.event(sport)]
        first = self.scan(selected=selected)
        self.assertEqual(len(self.cards(selected=selected)), 2)
        self.h.now += timedelta(seconds=1)
        def fetch(sport, region, key):
            if sport == selected[0]:
                raise LeagueScanError('safe HTTP503', reason='http_error', status=503)
            return self.h.fetch_odds(sport, region, key)
        self.h.f['fetch_odds'] = Mock(side_effect=fetch)
        second = self.scan(selected=selected)
        self.assertNotEqual(first['run_id'], second['run_id'])
        cards = self.cards(selected=selected)
        self.assertEqual({row['sport_key'] for row in cards}, {selected[1]})
        from data_center_store import load_batches
        with self.h.f['db']() as con:
            self.assertEqual(len(load_batches(con, first['run_id'])), 2)

    def test_region_or_window_change_does_not_claim_old_snapshot_matches_new_scope(self):
        self.scan()
        self.assertEqual(self.cards(region='eu'), [])
        self.assertEqual(self.cards(hours=48), [])
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(self.h.f['fetch_odds'].call_count, 1)

    def test_empty_successful_response_keeps_history_but_has_no_current_cards(self):
        first = self.scan()
        self.h.events[self.selected[0]] = []
        second = self.scan()
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(self.cards(), [])
        self.assertEqual(second['leagues'][0]['api_returned'], 0)
        self.assertNotEqual(second['leagues'][0]['status'], 'league_error')
        from data_center_store import load_batches
        with self.h.f['db']() as con:
            self.assertEqual(len(load_batches(con, first['run_id'])), 1)

    def test_reading_cards_repeatedly_and_opening_prices_never_refresh_odds(self):
        self.scan()
        before = self.h.f['fetch_odds'].call_count
        for _ in range(3):
            cards = self.cards()
            self.assertEqual(len(cards[0]['quotes']), 4)
        self.assertEqual(self.h.f['fetch_odds'].call_count, before)
        self.h.f['fetch_history'].assert_not_called()

    def test_optional_statistics_failure_cannot_erase_raw_prices(self):
        result = self.scan()
        before = self.cards()
        with self.h.f['db']() as con:
            stored_before=[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')]
        self.h.f['fetch_history'] = Mock(side_effect=RuntimeError('private provider failure body'))
        self.h.f['fetch_understat_history'] = Mock(side_effect=RuntimeError('private xG provider failure body'))
        self.h.f['collect_data_center_statistics'](result['run_id'])
        after = self.cards()
        self.assertEqual(len(after), 1)
        self.assertEqual(quote_facts(after[0]['quotes']), quote_facts(before[0]['quotes']))
        with self.h.f['db']() as con:
            self.assertEqual(stored_before,[tuple(row) for row in con.execute('SELECT * FROM dc_batches ORDER BY rowid')])
        for old,new in zip(before[0]['quotes'],after[0]['quotes']):
            self.assertGreaterEqual(new['receipt_age_seconds'],old['receipt_age_seconds'])
        self.assertTrue(after[0].get('statistics'))
        serialized = json.dumps(after[0]['statistics'], ensure_ascii=False)
        self.assertNotIn('private provider failure body', serialized)
        self.assertNotIn('private xG provider failure body', serialized)
        self.assertEqual(self.h.f['fetch_odds'].call_count, 1)

    def test_actual_xg_can_be_displayed_when_results_source_failed_without_running_models(self):
        result = self.scan()
        self.h.f['fetch_history'] = Mock(side_effect=RuntimeError('CSV unavailable'))
        self.h.f['estimate_goals'] = Mock(side_effect=AssertionError('no model needed to display actual xG'))
        self.h.f['collect_data_center_statistics'](result['run_id'])
        card = self.cards()[0]
        stats = card['statistics']
        self.assertTrue(stats)
        self.assertEqual(stats['teams']['home']['xg']['overall']['last5']['sample'], 5)
        self.assertAlmostEqual(stats['teams']['home']['xg']['overall']['last5']['xg_avg'], 1.8)
        self.h.f['estimate_goals'].assert_not_called()
        self.assertEqual(self.h.f['fetch_odds'].call_count, 1)

    def test_unmatched_api_team_stays_visible_and_no_synthetic_statistics_are_created(self):
        sport = self.selected[0]
        self.h.events[sport][0]['home_team'] = 'Unmatched Authored Club'
        self.h.events[sport][0]['bookmakers'][0]['markets'][0]['outcomes'][0]['name'] = 'Unmatched Authored Club'
        result = self.scan()
        self.h.f['collect_data_center_statistics'](result['run_id'])
        card = self.cards()[0]
        self.assertEqual(card['home_team'], 'Unmatched Authored Club')
        stats = card['statistics']
        self.assertEqual(stats['teams']['home']['overall']['last5']['sample'], 0)
        self.assertIsNone(stats['teams']['home']['overall']['last5']['gf_avg'])
        self.assertEqual(stats['teams']['home']['xg']['overall']['last5']['sample'], 0)
        self.assertIsNone(stats['teams']['home']['xg']['overall']['last5']['xg_avg'])

    def test_explicit_analysis_reuses_saved_response_without_new_odds_request(self):
        result = self.scan()
        self.h.f['fetch_odds'] = Mock(side_effect=AssertionError('analysis must reuse stored price evidence'))
        analyzed=self.h.f['analyze_data_center_run'](result['run_id'])
        self.assertEqual(analyzed['status'],'completed')
        self.assertEqual(analyzed['errors'],0)
        self.assertEqual(analyzed['leagues'],1)
        self.assertEqual(analyzed['saved_counts'][0],4)
        saved=self.h.f['load_picks'](limit=None)
        self.assertEqual(len(saved),4)
        self.assertTrue(all(json.loads(row)['available'] for row in saved['analysis_b_json']))
        self.assertTrue(all(json.loads(row)['score'] is not None for row in saved['analysis_a_json']))
        self.h.f['fetch_odds'].assert_not_called()
        self.assertEqual(len(self.cards()), 1)
        self.assertEqual(len(self.cards()[0]['quotes']), 4)

    def test_backup_restore_retains_center_cards_without_touching_existing_bills(self):
        with self.h.f['db']() as con:
            con.execute("INSERT INTO slips(selections_json,status) VALUES(?,?)", ('{damaged legacy bill','รอตรวจผล'))
        result = self.scan()
        source = self.h.f['DB_FILE']
        destination = Path(self.h.tmp.name)/'restored-data-center.db'
        restore_new(backup_bytes(source), destination)
        self.h.f['DB_FILE'] = destination
        cards = self.cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]['run_id'], result['run_id'])
        with self.h.f['db']() as con:
            row = con.execute('SELECT selections_json,status FROM slips').fetchone()
        self.assertEqual(tuple(row), ('{damaged legacy bill','รอตรวจผล'))

    def test_cached_payload_receipt_does_not_become_a_new_price_timestamp(self):
        first = self.scan()
        original = self.cards()[0]
        second = self.scan()
        current = self.cards()[0]
        self.assertNotEqual(first['run_id'], second['run_id'])
        self.assertEqual(original['received_at'], current['received_at'])
        # Operational run IDs may differ; source contract facts cannot refresh.
        keys=('market','side','point','odds','bookmaker','bookmaker_key',
              'source_updated_at','source_update_scope')
        signature=lambda card:[tuple(quote.get(key) for key in keys) for quote in card['quotes']]
        self.assertEqual(signature(original), signature(current))


if __name__=='__main__':
    unittest.main()
