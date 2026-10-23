"""24/48-hour scope and provider-count regressions with no provider requests."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

from readiness import analysis_scope
from scan_diagnostics import filter_scan_fixtures, finalize_scan_report, scan_report_text, scan_window
import test_regressions as regression

UTC = timezone.utc
TH_TZ = timezone(timedelta(hours=7))


class ScanWindowAuditTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 23, 16, 30, tzinfo=UTC)

    def fixture(self, hours=1, event='event', **updates):
        row = dict(id=event, sport_key='soccer_epl', home_team='Team A', away_team='Team B',
                   commence_time=(self.now + timedelta(hours=hours)).isoformat(), bookmakers=[])
        row.update(updates)
        return row

    def scoped_row(self, hours=1, event='event', **updates):
        row = dict(event_id=event, league='selected',
                   kickoff_utc=(self.now + timedelta(hours=hours)).isoformat(),
                   received_at=self.now.isoformat())
        row.update(updates)
        return row

    def test_24_and_48_are_elapsed_hours_with_strict_future_inclusive_end(self):
        rows = [self.fixture(h, str(i)) for i, h in enumerate((-1, 0, 1, 24, 24.01, 48, 48.01))]
        accepted24, report24 = filter_scan_fixtures(rows, 'soccer_epl', self.now, 24)
        accepted48, report48 = filter_scan_fixtures(rows, 'soccer_epl', self.now, 48)
        self.assertEqual([r['id'] for r in accepted24], ['2', '3'])
        self.assertEqual([r['id'] for r in accepted48], ['2', '3', '4', '5'])
        self.assertEqual(report24['excluded_by_reason'], {'already_started': 2, 'outside_window': 3})
        self.assertEqual((report48['api_returned'], report48['passed_filter'], report48['excluded_count']), (7, 4, 3))

    def test_thai_midnight_keeps_full48_hours_instead_of_calendar_day(self):
        thai_now = self.now.astimezone(TH_TZ)
        self.assertEqual(thai_now.isoformat(), '2026-10-23T23:30:00+07:00')
        rows, report = filter_scan_fixtures([self.fixture(47.9)], 'soccer_epl', thai_now, 48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(report['start_th'], '2026-10-23T23:30:00+07:00')
        self.assertEqual(report['end_th'], '2026-10-25T23:30:00+07:00')
        self.assertEqual(report['end_utc'], '2026-10-25T16:30:00+00:00')

    def test_kickoff_explicit_offset_compared_as_instant(self):
        at_end = (self.now + timedelta(hours=48)).astimezone(TH_TZ).isoformat()
        rows, _ = filter_scan_fixtures([self.fixture(commence_time=at_end)], 'soccer_epl', self.now, 48)
        self.assertEqual(len(rows), 1)

    def test_unknown_naive_invalid_missing_kickoff_not_assumed_utc(self):
        rows = [self.fixture(event=str(i), commence_time=stamp)
                for i, stamp in enumerate((None, '', 'invalid', '2026-10-24T12:00:00'))]
        accepted, report = filter_scan_fixtures(rows, 'soccer_epl', self.now, 48)
        self.assertEqual(accepted, [])
        self.assertEqual(report['excluded_by_reason'], {'invalid_kickoff': 4})

    def test_selected_endpoint_allows_absent_embedded_key_but_blocks_mismatch(self):
        rows = [self.fixture(event='wrong', sport_key='soccer_germany_bundesliga'),
                self.fixture(event='provided'), self.fixture(event='endpoint-only', sport_key=None)]
        accepted, report = filter_scan_fixtures(rows, 'soccer_epl', self.now, 48)
        self.assertEqual([row['id'] for row in accepted], ['provided', 'endpoint-only'])
        self.assertEqual(report['excluded_by_reason'], {'league_mismatch': 1})

    def test_incomplete_identity_and_non_object_rows_are_counted(self):
        rows = [None, 'bad', self.fixture(id=''), self.fixture(home_team=' '), self.fixture(away_team=None)]
        accepted, report = filter_scan_fixtures(rows, 'soccer_epl', self.now, 48)
        self.assertEqual(accepted, [])
        self.assertEqual(report['excluded_by_reason'], {'invalid_fixture': 2, 'missing_identity': 3})
        self.assertEqual(report['api_returned'], len(report['fixtures']))

    def test_identical_duplicate_is_reported_without_double_counting_fixture(self):
        row = self.fixture()
        accepted, report = filter_scan_fixtures([row, copy.deepcopy(row)], 'soccer_epl', self.now, 48)
        self.assertEqual(accepted, [row])
        self.assertEqual(report['excluded_by_reason'], {'duplicate_event': 1})

    def test_same_event_id_with_conflicting_teams_or_kickoff_quarantines_all(self):
        for updates in (dict(home_team='Other team'), dict(away_team='Other team'),
                        dict(commence_time=(self.now + timedelta(hours=2)).isoformat())):
            with self.subTest(updates=updates):
                accepted, report = filter_scan_fixtures(
                    [self.fixture(), self.fixture(**updates)], 'soccer_epl', self.now, 48)
                self.assertEqual(accepted, [])
                self.assertEqual(report['excluded_by_reason'], {'conflicting_event': 2})

    def test_conflicting_event_outside_window_cannot_hide_behind_scope_filter(self):
        accepted, report = filter_scan_fixtures(
            [self.fixture(), self.fixture(hours=72)], 'soccer_epl', self.now, 48)
        self.assertEqual(accepted, [])
        self.assertEqual(report['excluded_by_reason'], {'conflicting_event': 1, 'outside_window': 1})

    def test_source_payload_and_old_price_history_are_never_mutated(self):
        rows = [self.fixture(), self.fixture(-1, 'old')]
        saved = copy.deepcopy(rows)
        accepted, report = filter_scan_fixtures(rows, 'soccer_epl', self.now, 48)
        self.assertEqual(rows, saved)
        self.assertIs(accepted[0], rows[0])
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(report['excluded_by_reason'].values()) + report['passed_filter'], report['api_returned'])

    def test_report_text_explains_actual_counts_and_thai_bounds(self):
        _, report = filter_scan_fixtures([self.fixture(), self.fixture(-1, 'old')], 'soccer_epl', self.now, 48)
        text = scan_report_text(report)
        for fragment in ('API ส่งมา 2 คู่', 'ผ่านตัวกรอง 1 คู่', 'ตัดออก 1 คู่', '48 ชั่วโมง',
                         '2026-10-25T23:30:00+07:00', 'เริ่มแข่งขันแล้ว'):
            self.assertIn(fragment, text)

    def test_empty_response_has_zero_counts_and_full_window_evidence(self):
        accepted, report = filter_scan_fixtures([], 'soccer_epl', self.now, 48)
        self.assertEqual(accepted, [])
        self.assertEqual((report['api_returned'], report['passed_filter'], report['excluded_count']), (0, 0, 0))
        self.assertEqual(report['excluded_by_reason'], {})

    def test_mixed_aware_receipts_cannot_keep_a_removed_bookmaker(self):
        rows = [self.scoped_row(event='same', received_at='2026-10-23T16:20:00Z'),
                self.scoped_row(event='same', received_at='2026-10-23T16:25:00.000001+00:00')]
        view = analysis_scope(pd.DataFrame(rows), ['selected'], self.now, hours_limit=48)
        self.assertEqual(view.received_at.tolist(), ['2026-10-23T16:25:00.000001+00:00'])
        view = analysis_scope(pd.DataFrame(rows), ['selected'], self.now, hours_limit=48,
                              latest_receipts={'selected': '2026-10-23T23:26:00+07:00'})
        self.assertTrue(view.empty)

    def test_window_configuration_does_not_allow_accidental_extensions(self):
        for hours in (0, -1, 25, 72, True, '48', None):
            with self.subTest(hours=hours), self.assertRaises(ValueError):
                scan_window(self.now, hours)
        with self.assertRaises(ValueError):
            scan_window(self.now.replace(tzinfo=None), 48)

    def test_persisted_cards_and_slip_scope_keep_selected48_hours(self):
        frame = pd.DataFrame([self.scoped_row(h, str(i)) for i, h in enumerate((0, 1, 24, 36, 48, 48.01))])
        self.assertEqual(analysis_scope(frame, ['selected'], self.now).event_id.tolist(), ['1', '2'])
        self.assertEqual(analysis_scope(frame, ['selected'], self.now, hours_limit=48).event_id.tolist(), ['1', '2', '3', '4'])
        self.assertTrue(analysis_scope(frame, ['other'], self.now, hours_limit=48).empty)
        self.assertEqual(len(frame), 6)

    def test_48_hour_scope_does_not_restore_obsolete_bookmaker_response(self):
        old = (self.now - timedelta(minutes=1)).isoformat()
        frame = pd.DataFrame([self.scoped_row(36, 'one', received_at=old),
                              self.scoped_row(36, 'one'), self.scoped_row(36, 'two', received_at=old)])
        self.assertEqual(analysis_scope(frame, ['selected'], self.now, hours_limit=48).index.tolist(), [1, 2])
        self.assertEqual(analysis_scope(frame, ['selected'], self.now,
                                       latest_receipts={'selected': self.now.isoformat()}, hours_limit=48).index.tolist(), [1])

    def test_persisted_scope_accepts_mixed_aware_iso_formats_as_real_instants(self):
        frame = pd.DataFrame([self.scoped_row(event='utc', kickoff_utc='2026-10-24T12:00:00Z'),
                              self.scoped_row(event='fraction', kickoff_utc='2026-10-24T12:00:00.123456+00:00'),
                              self.scoped_row(event='thai', kickoff_utc='2026-10-24T19:00:00+07:00')])
        self.assertEqual(analysis_scope(frame, ['selected'], self.now, hours_limit=48).event_id.tolist(),
                         ['utc', 'fraction', 'thai'])

    def test_persisted_scope_rejects_unknown_timezone_and_bad_config(self):
        frame = pd.DataFrame([self.scoped_row(kickoff_utc='2026-10-24T12:00:00')])
        self.assertTrue(analysis_scope(frame, ['selected'], self.now, hours_limit=48).empty)
        valid = pd.DataFrame([self.scoped_row()])
        for hours in (72, 0, True, '48', None):
            self.assertTrue(analysis_scope(valid, ['selected'], self.now, hours_limit=hours).empty)
        self.assertTrue(analysis_scope(valid, ['selected'], self.now.replace(tzinfo=None), hours_limit=48).empty)

    def analysis_row(self, event='one', home='A', away='B', home_sample=5, away_sample=5, **updates):
        row = dict(event_id=event, home_team=home, away_team=away, market='Over/Under', side='over',
                   point=2.75, odds=1.95, bookmaker='Book', bookmaker_key='book',
                   received_at=self.now.isoformat(), source_updated_at=self.now.isoformat(),
                   analysis_a_json=json.dumps(dict(score=70, home_form=dict(sample5=home_sample, sample10=10),
                                                   away_form=dict(sample5=away_sample, sample10=8))),
                   analysis_b_json=json.dumps(dict(available=True)))
        row.update(updates)
        return row

    def test_coverage_counts_unique_fixture_and_team_not_market_or_book(self):
        _, report = filter_scan_fixtures([self.fixture()], 'soccer_epl', self.now, 48)
        rows = [self.analysis_row(), self.analysis_row(side='under'),
                self.analysis_row(market='Asian Handicap', side='home', point=-.25),
                self.analysis_row(bookmaker='Other', bookmaker_key='other')]
        result = finalize_scan_report(report, rows, self.now)
        self.assertEqual((result['analyzed_fixtures'], result['priced_fixtures'], result['no_price_fixtures']), (1, 1, 0))
        self.assertEqual((result['a_available_fixtures'], result['b_available_fixtures']), (1, 1))
        self.assertEqual((result['teams_checked'], result['teams_with_form5']), (2, 2))
        self.assertEqual((result['team_venue_checked'], result['team_venue_form5']), (2, 2))
        self.assertEqual((result['unique_quotes'], result['fresh_quotes'], result['rejected_quotes']), (4, 4, 0))
        self.assertNotIn('analyzed_fixtures', report)

    def test_high_a_never_counts_missing_b_as_available_and_no_price_is_explicit(self):
        _, report = filter_scan_fixtures([self.fixture()], 'soccer_epl', self.now, 48)
        row = self.analysis_row(market='ยังไม่มีราคา', odds=None, point=None,
                                analysis_b_json=json.dumps(dict(available=False)))
        result = finalize_scan_report(report, [row], self.now)
        self.assertEqual((result['a_available_fixtures'], result['b_available_fixtures']), (1, 0))
        self.assertEqual((result['priced_fixtures'], result['no_price_fixtures'], result['unique_quotes']), (0, 1, 0))

    def test_insufficient_and_conflicting_venue_samples_do_not_inflate_team_coverage(self):
        _, report = filter_scan_fixtures([self.fixture()], 'soccer_epl', self.now, 48)
        rows = [self.analysis_row(away_sample=4),
                self.analysis_row(away_sample=5, side='under'),
                self.analysis_row(event='two', home='C', away='A', home_sample=0, away_sample=3)]
        result = finalize_scan_report(report, rows, self.now)
        self.assertEqual((result['teams_checked'], result['teams_with_form5']), (3, 0))
        self.assertEqual((result['team_venue_checked'], result['team_venue_form5']), (4, 1))
        sample = next(row for row in result['team_venue_samples'] if row['api_name'] == 'B')
        self.assertEqual(sample['sample5'], 4)

    def test_stale_quotes_are_counted_with_reasons_and_duplicate_rows_once(self):
        _, report = filter_scan_fixtures([self.fixture()], 'soccer_epl', self.now, 48)
        stale = self.analysis_row(source_updated_at=(self.now - timedelta(hours=1)).isoformat())
        result = finalize_scan_report(report, [stale, copy.deepcopy(stale), self.analysis_row(side='under')], self.now)
        self.assertEqual((result['unique_quotes'], result['fresh_quotes'], result['rejected_quotes']), (2, 1, 1))
        self.assertEqual(sum(result['rejected_quotes_by_reason'].values()), 1)
        self.assertTrue(any('ราคาเก่า' in reason for reason in result['rejected_quotes_by_reason']))

    def test_empty_invalid_analysis_is_reported_as_unavailable(self):
        _, report = filter_scan_fixtures([], 'soccer_epl', self.now, 48)
        empty = finalize_scan_report(report, [], self.now)
        self.assertEqual((empty['analyzed_fixtures'], empty['teams_checked'], empty['b_available_fixtures']), (0, 0, 0))
        row = self.analysis_row(analysis_a_json='{broken', analysis_b_json='[]')
        result = finalize_scan_report(report, [row], self.now)
        self.assertEqual((result['a_available_fixtures'], result['b_available_fixtures'], result['teams_with_form5']), (0, 0, 0))


class ActualScanWindowTests(unittest.TestCase):
    setUp = regression.Regressions.setUp

    def prepare_scan(self, events):
        self.f.update(fetch_odds=Mock(return_value=(events, '100', '2')),
                      fetch_understat_with_diagnostics=Mock(return_value=pd.DataFrame()),
                      fetch_history=Mock(return_value=pd.DataFrame()),
                      optional_api_football_fallback=lambda key, frame, *args: frame,
                      st=SimpleNamespace(caption=Mock()))

    def event(self, hours, event='future', **updates):
        now = datetime.now(UTC)
        row = dict(id=event, sport_key='soccer_epl', home_team='Team A', away_team='Team B',
                   commence_time=(now + timedelta(hours=hours)).isoformat(),
                   _received_at=now.isoformat(), bookmakers=[])
        row.update(updates)
        return row

    def scan(self, hours):
        return self.f['scan_league']('soccer_epl', self.f['LEAGUES']['soccer_epl'][0],
                                     'E0', hours, 1.8, 2.1, 'uk', 'MOCK')

    def test_actual48_scan_saves36_hour_fixture_without_inventing_xg(self):
        self.prepare_scan([self.event(36)])
        rows, snapshots, _, _ = self.scan(48)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['event_id'], 'future')
        self.assertEqual(rows[0]['market'], 'ยังไม่มีราคา')
        self.assertIsNone(rows[0]['ev_pct'])
        self.assertFalse(json.loads(rows[0]['analysis_b_json'])['available'])
        self.f['save_picks'](rows, snapshots)
        stored = self.f['load_picks']()
        self.assertEqual(len(analysis_scope(stored, [rows[0]['league']], hours_limit=48)), 1)
        self.assertTrue(analysis_scope(stored, [rows[0]['league']], hours_limit=24).empty)
        self.f['fetch_odds'].assert_called_once()

    def test_no_in_window_fixtures_does_not_download_stats_sources(self):
        self.prepare_scan([self.event(36), self.event(-1, 'started')])
        rows, snapshots, _, _ = self.scan(24)
        self.assertEqual((rows, snapshots), ([], []))
        self.f['fetch_understat_with_diagnostics'].assert_not_called()
        self.f['fetch_history'].assert_not_called()
        self.f['fetch_odds'].assert_called_once()

    def test_actual_scan_persists_returned_filtered_and_excluded_evidence(self):
        events = [self.event(36), self.event(-1, 'started'), self.event(49, 'outside'),
                  self.event(2, 'wrong-league', sport_key='soccer_germany_bundesliga'),
                  self.event(2, 'no-time', commence_time='2026-10-24T12:00:00')]
        self.prepare_scan(events)
        rows, _, _, _ = self.scan(48)
        self.assertEqual(len(rows), 1)
        report = json.loads(rows[0]['analysis_a_json'])['scan_diagnostics']
        self.assertEqual((report['api_returned'], report['passed_filter'], report['excluded_count']), (5, 1, 4))
        self.assertEqual(report['excluded_by_reason'], dict(already_started=1, outside_window=1,
                         league_mismatch=1, invalid_kickoff=1))
        self.assertEqual((report['analyzed_fixtures'], report['b_available_fixtures']), (1, 0))
        with self.f['db']() as con:
            health = json.loads(con.execute('SELECT payload_json FROM league_health ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(health['scan_window'], report)

    def test_ucl36_hour_fixture_loads_domestic_form_in48_hour_scan(self):
        from test_ucl_scan import UCLScanTests
        UCLScanTests.prepare(self)
        self.kickoff = self.now + timedelta(hours=36)
        self.event['commence_time'] = self.kickoff.isoformat()
        self.f['st'] = SimpleNamespace(caption=Mock())
        rows, _, _, _ = self.f['scan_league']('soccer_uefa_champs_league',
                         self.f['LEAGUES']['soccer_uefa_champs_league'][0], None,
                         48, 1.8, 2.1, 'uk', 'MOCK')
        self.assertTrue(rows)
        a = json.loads(rows[0]['analysis_a_json'])
        self.assertEqual((a['home_form']['sample5'], a['away_form']['sample5']), (5, 5))
        self.assertEqual(a['scan_diagnostics']['teams_with_form5'], 2)
        self.assertFalse(json.loads(rows[0]['analysis_b_json'])['available'])
        self.assertIsNone(rows[0]['ev_pct'])
        self.f['fetch_odds'].assert_called_once()
        self.f['fetch_domestic_rosters'].assert_called_once()

    def test_canonical_bill_validation_uses_selected48_scope_and_still_checks_freshness(self):
        from test_market_recommendations import quote
        now = datetime.now(UTC)
        row = quote('Asian Handicap', -.25, 'home', 'Book', odds=2.0)
        row.update(kickoff_utc=(now + timedelta(hours=36)).isoformat(),
                   league=self.f['LEAGUES']['soccer_epl'][0],
                   received_at=now.isoformat(), source_updated_at=now.isoformat(), bookmaker_key='book')
        self.f['save_picks']([row])
        saved = self.f['load_picks']()
        with self.assertRaises(ValueError):
            self.f['save_slip_record'](saved, 2.0, None, hours_limit=24)
        self.assertEqual(self.f['save_slip_record'](saved, 2.0, None, hours_limit=48), 1)
        with self.f['db']() as con:
            con.execute('UPDATE picks SET source_updated_at=?', ((now - timedelta(hours=1)).isoformat(),))
        with self.assertRaises(ValueError):
            self.f['save_slip_record'](saved, 2.0, None, hours_limit=48)
        self.assertEqual(len(self.f['load_slips']()), 1)


if __name__ == '__main__':
    unittest.main()
