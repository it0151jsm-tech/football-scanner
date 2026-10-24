"""Synthetic readiness acceptance; never evidence of live EPL/provider coverage."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scan_diagnostics import (filter_scan_fixtures, finalize_scan_report,
                              fixture_readiness_report, readiness_table_rows, scan_report_text)
from test_market_recommendations import quote
from test_regressions import load_functions

UTC = timezone.utc


class Issue15ReadinessTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(UTC)
        self.kickoff = self.now + timedelta(hours=6)
        self.choose_c = load_functions(Path(__file__).resolve().parents[1] / 'app.py')['choose_recommendation']

    def report(self, count=1):
        fixtures = [dict(id=f'fixture-{index}', sport_key='soccer_epl', home_team=f'Synthetic Home {index}',
                         away_team=f'Synthetic Away {index}', commence_time=self.kickoff.isoformat(),
                         _received_at=self.now.isoformat()) for index in range(count)]
        return filter_scan_fixtures(fixtures, 'soccer_epl', self.now, 24)[1]

    def profile(self, sample=5, *, code=None, mapping='exact', current=5, prior=5, full=10):
        return dict(sample5=sample, sample10=min(full, 10), diagnostics=dict(
            status='ok' if code is None else code, mapping=mapping,
            requested_season='2627', season_counts={'2627': current, '2526': prior},
            venue_before_count=full, membership_status='current_source_matched',
            source=dict(provider='Football-Data', status='ok', sources=[dict(
                url='https://example.invalid/synthetic.csv', received_at=self.now.isoformat(), status='ok')]),
            readiness=dict(status='available' if code is None else 'unavailable', mapping=mapping,
                reason_codes=[code] if code else [], current_season_venue_count=current,
                prior_season_venue_count=prior, full_history_venue_count=full,
                current_membership='current_source_matched', provider='Football-Data')))

    def row(self, index=0, *, a_score=70, b_ready=True, code=None, sample=5,
            market='Asian Handicap', side='home', point=-.25, odds=2.1):
        row = quote(market, point, side, 'Synthetic Book', event=f'fixture-{index}', pick=index+1, odds=odds)
        row.update(home_team=f'Synthetic Home {index}', away_team=f'Synthetic Away {index}',
                   league='Premier League', kickoff_utc=self.kickoff.isoformat(),
                   received_at=self.now.isoformat(), source_updated_at=self.now.isoformat(),
                   scanned_at=self.now.isoformat())
        a = json.loads(row['analysis_a_json'])
        a.update(score=a_score, home_form=self.profile(sample, code=code), away_form=self.profile(sample))
        if sample < 5:
            for key in ('form5', 'form10'):
                a[key]['form_sample'] = sample
        if not b_ready:
            row.update(analysis_b_json=json.dumps(dict(available=False, calibrated=False,
                       status='Synthetic xG unavailable')), ev_pct=None)
            a['xg'] = dict(diagnostics=dict(status='forbidden'))
        row['analysis_a_json'] = json.dumps(a)
        return row

    def ready(self, report, rows, **updates):
        return fixture_readiness_report(report, rows, self.now,
                                        choose_c=self.choose_c, current_scan_rows=True, **updates)

    def test_ten_mock_epl_fixtures_keep_distinct_missing_reasons_not_fake_coverage(self):
        # Intentionally 3 A / 4 B / 1 C here; these are synthetic contracts and
        # are never the live counts or identities described in Issue #15.
        rows = [self.row(0), self.row(1, a_score=52, sample=4),
                self.row(2, a_score=None, code='name_unmatched'),
                self.row(3, a_score=None, b_ready=False, code='no_current_season'),
                self.row(4, a_score=None, b_ready=False, code='after_cutoff'),
                self.row(5, a_score=None, b_ready=False, code='provider_failure'),
                self.row(6, a_score=None, b_ready=False, code='invalid_history'),
                self.row(7, a_score=None, code='no_decisive_results'),
                self.row(8, a_score=None, b_ready=False, code='no_valid_quote'),
                self.row(9, a_score=60, b_ready=False)]
        rows[8].update(market='ยังไม่มีราคา', point=None, odds=None)
        result = self.ready(self.report(10), rows)
        self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (3, 4, 1))
        self.assertEqual(len(result['per_fixture_readiness']), 10)
        for index, reason in ((2, 'name_unmatched'), (3, 'no_current_season'), (4, 'after_cutoff'),
                              (5, 'provider_failure'), (6, 'invalid_history'),
                              (7, 'no_decisive_results'), (8, 'no_valid_quote')):
            item = result['per_fixture_readiness'][index]
            self.assertIn(reason, item['A_reason_codes'])
            self.assertFalse(item['A_ready'])
        self.assertEqual(result['missing_market_counts'], {'Asian Handicap': 1, 'Over/Under': 10})

    def test_form_five_is_caution_not_a_scoring_requirement(self):
        row = self.row(a_score=52, sample=4)
        result = self.ready(self.report(), [row])
        item = result['per_fixture_readiness'][0]
        self.assertTrue(item['A_ready'])
        self.assertFalse(item['C_supported'])
        self.assertIn('insufficient_venue_history', item['teams']['home']['reason_codes'])
        self.assertIn('not_A_scoring_requirement', item['teams']['home']['form5_gate'])

    def test_current_and_prior_season_context_are_explicit_without_changing_counts_or_score(self):
        row = self.row()
        a = json.loads(row['analysis_a_json'])
        a['home_form'] = self.profile(mapping='verified_alias', current=2, prior=9, full=11)
        row['analysis_a_json'] = json.dumps(a)
        original = copy.deepcopy(row)
        result = self.ready(self.report(), [row])
        team = result['per_fixture_readiness'][0]['teams']['home']
        self.assertEqual((team['current_season_venue_count'], team['prior_season_venue_count'],
                          team['full_history_venue_count']), (2, 9, 11))
        self.assertEqual(team['mapping'], 'verified_alias')
        self.assertTrue(result['per_fixture_readiness'][0]['A_ready'])
        self.assertEqual(row, original)

    def test_fallback_saved_diagnostics_preserve_exact_alias_and_name_failure(self):
        row = self.row(a_score=None)
        a = json.loads(row['analysis_a_json'])
        for side, mapping in (('home', 'verified_alias'), ('away', 'exact')):
            a[side + '_form']['diagnostics'].pop('readiness')
            a[side + '_form']['diagnostics']['mapping'] = mapping
        a['home_form']['diagnostics'].update(status='name_ambiguous', membership_status='current_membership_unverified')
        row['analysis_a_json'] = json.dumps(a)
        result = self.ready(self.report(), [row])['per_fixture_readiness'][0]
        self.assertEqual(result['teams']['home']['mapping'], 'verified_alias')
        self.assertEqual(result['teams']['away']['mapping'], 'exact')
        self.assertIn('name_ambiguous', result['A_reason_codes'])
        self.assertIn('no_current_season', result['A_reason_codes'])

    def test_high_a_cannot_create_c_when_b_or_ev_is_missing(self):
        row = self.row(a_score=99, b_ready=False)
        result = self.ready(self.report(), [row])
        self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (1, 0, 0))
        row = self.row(a_score=99)
        b = json.loads(row['analysis_b_json'])
        b['selection']['ev_pct'] = None
        row.update(ev_pct=None, analysis_b_json=json.dumps(b))
        self.assertEqual(self.ready(self.report(), [row])['C_supported'], 0)

    def test_negative_ev_is_not_c_support_even_with_real_model_fields(self):
        row = self.row(odds=1.1)
        self.assertLess(row['ev_pct'], 0)
        result = self.ready(self.report(), [row])
        self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (1, 1, 0))

    def test_b_available_uncalibrated_is_visible_without_claiming_calibration(self):
        result = self.ready(self.report(), [self.row()])['per_fixture_readiness'][0]
        self.assertTrue(result['B_ready'])
        self.assertEqual(result['B_status'], 'available_uncalibrated')
        self.assertEqual(result['C_status'], 'supported_uncalibrated')

    def test_boolean_available_without_model_numbers_is_not_b_ready(self):
        row = self.row()
        row['analysis_b_json'] = json.dumps(dict(available=True, calibrated=False))
        result = self.ready(self.report(), [row])
        self.assertEqual(result['B_ready'], 0)
        self.assertEqual(result['C_supported'], 0)

    def test_root_model_readiness_reasons_are_preserved_per_fixture(self):
        row = self.row(a_score=None, b_ready=False)
        b = json.loads(row['analysis_b_json'])
        b['data_readiness'] = dict(status='xg_unavailable', reason_codes=['home_insufficient_sample', 'away_stale'],
                                  provider='Understat', minimum_venue_samples=5, max_xg_age_days=120,
                                  calibration_status='not_validated')
        row['analysis_b_json'] = json.dumps(b)
        item = self.ready(self.report(), [row])['per_fixture_readiness'][0]
        self.assertEqual(item['B_reason_codes'], ['away_stale', 'home_insufficient_sample'])
        self.assertEqual(item['B_data_readiness'], [b['data_readiness']])
        self.assertIn('b_unavailable', item['C_reason_codes'])

    def test_root_form_reason_variants_and_date_exclusions_remain_auditable(self):
        row = self.row(a_score=None)
        a = json.loads(row['analysis_a_json'])
        d = a['home_form']['diagnostics']['readiness']
        d.update(reason_codes=['no_current_season_results', 'insufficient_venue_samples', 'historical_source_invalidity'],
                 after_cutoff_count=2, current_season_team_count=0, unknown_season_venue_count=1)
        row['analysis_a_json'] = json.dumps(a)
        team = self.ready(self.report(), [row])['per_fixture_readiness'][0]['teams']['home']
        self.assertEqual(team['source_reason_codes'], d['reason_codes'])
        self.assertIn('no_current_season', team['reason_codes'])
        self.assertIn('insufficient_venue_history', team['reason_codes'])
        self.assertIn('invalid_history', team['reason_codes'])
        self.assertEqual(team['date_exclusions']['after_cutoff_count'], 2)
        self.assertEqual(team['unknown_season_venue_count'], 1)

    def test_all_push_results_explain_missing_a_without_changing_formula(self):
        row = self.row(a_score=None)
        a = json.loads(row['analysis_a_json'])
        for key in ('form5', 'form10', 'form_all'):
            a[key] = dict(form_rate=None, form_sample=5, form_summary='ตัวอย่าง 5 นัด แต่ผลเทียบเส้นนี้เป็นคืนทุนทั้งหมด')
        row['analysis_a_json'] = json.dumps(a)
        item = self.ready(self.report(), [row])['per_fixture_readiness'][0]
        self.assertFalse(item['A_ready'])
        self.assertIn('no_decisive_results', item['A_reason_codes'])

    def test_stale_book_update_blocks_c_while_current_form_and_model_remain_visible(self):
        row = self.row()
        row['source_updated_at'] = (self.now - timedelta(hours=1)).isoformat()
        result = self.ready(self.report(), [row])
        self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (1, 1, 0))
        self.assertIn('stale_or_unverified_quote', result['per_fixture_readiness'][0]['markets']['Asian Handicap']['reason_codes'])

    def test_historical_receipt_and_settled_rows_cannot_count_as_current_readiness(self):
        for changes, reason in ((dict(received_at=(self.now-timedelta(days=1)).isoformat()), 'historical_receipt'),
                                (dict(status='ตรวจแล้ว'), 'settled_record')):
            with self.subTest(reason=reason):
                row = self.row()
                row.update(changes)
                result = self.ready(self.report(), [row])
                self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (0, 0, 0))
                self.assertEqual((result['A_unknown'], result['B_unknown'], result['C_unknown']), (1, 1, 1))
                self.assertEqual(result['readiness_excluded_rows'], {reason: 1})

    def test_archive_event_or_wrong_identity_cannot_leak_into_current_fixture(self):
        for changes, reason in ((dict(event_id='archive'), 'outside_current_scan'),
                                (dict(home_team='Similar Unverified Team'), 'fixture_identity_conflict'),
                                (dict(kickoff_utc=(self.now-timedelta(hours=1)).isoformat()), 'outside_current_window')):
            with self.subTest(reason=reason):
                row = self.row()
                row.update(changes)
                result = self.ready(self.report(), [row])
                self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (0, 0, 0))
                self.assertEqual(result['readiness_excluded_rows'], {reason: 1})

    def test_missing_receipt_or_mismatched_api_snapshot_is_explicit_unknown(self):
        for value, reason in ((None, 'unverified_receipt'),
                              ((self.now-timedelta(minutes=1)).isoformat(), 'different_snapshot')):
            row = self.row()
            row['received_at'] = value
            result = self.ready(self.report(), [row])
            self.assertEqual(result['A_ready'], 0)
            self.assertEqual(result['readiness_excluded_rows'], {reason: 1})
            self.assertEqual(result['missing_market_counts'], {})
            self.assertEqual(result['unknown_market_counts'], {'Asian Handicap': 1, 'Over/Under': 1})
            self.assertEqual(readiness_table_rows(result)[0]['AH'], 'ยังยืนยันราคาปัจจุบันไม่ได้')

    def test_single_ou_quote_is_never_c_support_without_confirmed_main_pair(self):
        row = self.row(market='Over/Under', side='over', point=2.5)
        result = self.ready(self.report(), [row])
        self.assertEqual((result['A_ready'], result['B_ready'], result['C_supported']), (1, 1, 0))

    def test_same_book_line_snapshot_ou_pair_uses_existing_direction_policy(self):
        rows = [self.row(market='Over/Under', side='over', point=2.5, odds=2.1),
                self.row(market='Over/Under', side='under', point=2.5, odds=1.85)]
        result = self.ready(self.report(), rows)
        self.assertEqual(result['C_supported'], 1)
        self.assertTrue(result['per_fixture_readiness'][0]['markets']['Over/Under']['C_supported'])
        rows[1]['bookmaker_key'] = 'different-book'
        self.assertEqual(self.ready(self.report(), rows)['C_supported'], 0)

    def test_new_rows_without_sqlite_ids_use_actual_assessment_time_only_in_memory(self):
        rows = [self.row(market='Over/Under', side='over', point=2.5, odds=2.1),
                self.row(market='Over/Under', side='under', point=2.5, odds=1.85)]
        for row in rows:
            row.pop('id')
            row.pop('scanned_at')
        saved = copy.deepcopy(rows)
        result = self.ready(self.report(), rows)
        self.assertEqual(result['C_supported'], 1)
        self.assertEqual(rows, saved)
        self.assertEqual(result['readiness_checked_at'], self.now.isoformat())

    def test_missing_saved_analysis_and_absent_policy_are_unknown_not_fabricated(self):
        row = self.row()
        row.update(analysis_a_json='{broken', analysis_b_json='')
        result = fixture_readiness_report(self.report(), [row], self.now, current_scan_rows=True)
        self.assertEqual((result['A_unknown'], result['B_unknown'], result['C_unknown']), (1, 1, 1))
        self.assertFalse(result['C_policy_evaluated'])

    def test_historical_clock_never_claims_existing_c_policy_ran_asof_past(self):
        past = self.now - timedelta(days=30)
        kickoff = past + timedelta(hours=6)
        row = self.row()
        row.update(kickoff_utc=kickoff.isoformat(), received_at=past.isoformat(),
                   source_updated_at=past.isoformat(), scanned_at=past.isoformat())
        fixture = dict(id=row['event_id'], sport_key='soccer_epl', home_team=row['home_team'],
                       away_team=row['away_team'], commence_time=kickoff.isoformat(), _received_at=past.isoformat())
        report = filter_scan_fixtures([fixture], 'soccer_epl', past, 24)[1]
        result = fixture_readiness_report(report, [row], past,
                    choose_c=self.choose_c, current_scan_rows=True)
        self.assertEqual((result['A_ready'], result['B_ready']), (1, 1))
        self.assertEqual(result['C_unknown'], 1)
        self.assertFalse(result['C_policy_evaluated'])
        self.assertEqual(result['C_policy_error'], 'historical_clock_not_supported')
        item = result['per_fixture_readiness'][0]
        self.assertIsNone(item['C_supported'])
        self.assertIn('historical_clock_not_supported', item['C_reason_codes'])

    def test_quote_at900_second_boundary_cannot_become_fresh_in_policy_after_assessment(self):
        row = self.row()
        received = self.now - timedelta(seconds=900)
        row.update(received_at=received.isoformat(), source_updated_at=received.isoformat())
        report = self.report()
        report['api_received_at'] = received.isoformat()
        result = self.ready(report, [row])
        self.assertTrue(result['C_policy_evaluated'])
        # The pure as-of scope may retain the exact boundary, but the unchanged
        # wall-clock policy runs later and must fail closed rather than re-date.
        self.assertEqual(result['C_supported'], 0)
        self.assertFalse(result['per_fixture_readiness'][0]['C_supported'])

    def test_existing_summary_and_data_health_values_use_same_report(self):
        result = finalize_scan_report(self.report(), [self.row()], self.now,
                                      choose_c=self.choose_c, current_scan_rows=True)
        text = scan_report_text(result)
        self.assertIn('A 1 / B 1 / C ผ่านเกณฑ์ 1 คู่', text)
        self.assertIn('ยังไม่ผ่าน Calibration', text)
        table = readiness_table_rows(result)
        self.assertEqual(len(table), 1)
        self.assertEqual(table[0]['A'], 'available')
        self.assertEqual(table[0]['B'], 'available_uncalibrated')
        self.assertEqual(table[0]['AH'], 'มีราคา')
        self.assertEqual(table[0]['OU'], 'ไม่มีราคาตลาด')


if __name__ == '__main__':
    unittest.main()
