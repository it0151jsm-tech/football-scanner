"""Offline OU quote diagnostics; synthetic prices are never production evidence."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import os
import unittest
from unittest.mock import patch

from ou_market import compare_sides, select_main_line


NOW = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
STAMP = NOW.isoformat()


def entry(side, odds=1.9, line=3.0, book='Test bookmaker', key='test-book', pick=1, **metadata):
    row = dict(id=pick, event_id='offline-event', market='Over/Under', side=side,
               point=line, odds=odds, bookmaker=book, bookmaker_key=key,
               scanned_at=STAMP, received_at=STAMP, source_updated_at=STAMP)
    row.update(metadata)
    # No fitted model or EV is invented for these informational price tests.
    return dict(row=row, model_valid=False, ev=None, tier=0, b={})


def quotes(over=1.9, under=1.9, **metadata):
    return [entry('over', over, pick=1, **metadata),
            entry('under', under, pick=2, **metadata)]


class OUQuoteDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'VALUE_BET_QUOTE_MAX_AGE_SECONDS': '900'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def main(self, choices):
        return select_main_line(choices, now=NOW)

    def test_decimal_price_math_distinguishes_implied_sum_from_true_overround(self):
        result = self.main(quotes(1.9, 1.9))
        pair = result['pair']
        self.assertEqual(result['status'], 'estimated')
        # Independent exact rational example: each reciprocal is 10/19.
        self.assertAlmostEqual(pair['implied_sum'], 1.0526315789473684)
        self.assertEqual(pair['margin'], pair['implied_sum'])
        self.assertAlmostEqual(pair['overround'], 0.05263157894736836)
        self.assertAlmostEqual(pair['overround_pct'], 5.263157894736836)
        self.assertEqual(pair['balance'], 0)
        self.assertEqual(pair['normalized_over'], .5)
        self.assertTrue(pair['numeric_eligible'])
        self.assertTrue(pair['fresh'])
        self.assertTrue(pair['eligible'])
        self.assertIn('S − 1', result['metric_definitions']['overround'])
        self.assertIn('ไม่ใช่โอกาสชนะ', result['metric_definitions']['interpretation'])

    def test_even_two_way_prices_have_zero_overround_without_model_probability(self):
        result = self.main(quotes(2.0, 2.0))
        self.assertEqual(result['pair']['implied_sum'], 1)
        self.assertEqual(result['pair']['overround'], 0)
        self.assertIsNone(compare_sides(result)[0])
        self.assertIn('EV สองฝั่งยังไม่ครบ', compare_sides(result)[1])
        self.assertTrue(all(result['pair'][side]['ev'] is None for side in ('over', 'under')))

    def test_four_complete_pairs_can_all_fail_without_relaxing_standards(self):
        specifications = [(2.5, 'A', 1.45, 2.80), (2.75, 'B', 1.50, 1.50),
                          (3.0, 'C', 2.50, 2.50), (3.25, 'D', 1.20, 4.00)]
        choices = []
        for line, book, over, under in specifications:
            choices += quotes(over, under, line=line, book=book, key=book.lower())
        result = self.main(choices)
        self.assertEqual(result['status'], 'unconfirmed')
        self.assertEqual(result['reason_code'], 'unbalanced_pairs')
        self.assertEqual(result['diagnostics']['complete_pairs'], 4)
        self.assertEqual(result['diagnostics']['eligible_pairs'], 0)
        self.assertIsNone(result['pair'])
        self.assertEqual([p['sum_passed'] for p in result['pairs']], [True, False, False, True])
        self.assertEqual([p['balance_passed'] for p in result['pairs']], [False, True, True, False])
        self.assertTrue(all(p['fresh'] and not p['numeric_eligible'] for p in result['pairs']))
        self.assertTrue(all(p['reasons'] and p['reason'] for p in result['pairs']))
        self.assertIn('0.98–1.20', result['reason'])

    def test_implied_sum_inclusive_boundaries_are_unchanged(self):
        for price, expected in [(100 / 49, .98), (5 / 3, 1.20)]:
            with self.subTest(expected=expected):
                result = self.main(quotes(price, price))
                self.assertAlmostEqual(result['pair']['implied_sum'], expected)
                self.assertTrue(result['pair']['sum_passed'])
        for price in (2.041, 1.666):
            with self.subTest(price=price):
                pair = self.main(quotes(price, price))['pairs'][0]
                self.assertFalse(pair['sum_passed'])
                self.assertFalse(pair['eligible'])

    def test_balance_on_either_side_of_original_threshold_is_not_hidden(self):
        # Both examples have a valid implied sum around 1.05, with balance near .08.
        inside = self.main(quotes(1.644, 2.268))['pairs'][0]
        outside = self.main(quotes(1.642, 2.270))['pairs'][0]
        self.assertAlmostEqual(inside['balance'], .07975460122699385)
        self.assertAlmostEqual(outside['balance'], .08026584869631848)
        self.assertTrue(inside['numeric_eligible'])
        self.assertFalse(outside['numeric_eligible'])
        self.assertIn('Balance สูงกว่า 0.08', outside['reasons'])

    def test_exact_balance_boundary_survives_only_binary_roundoff(self):
        # Exact reciprocals 29/50 and 21/50 give S=1, balance=0.08.
        pair = self.main(quotes(50 / 29, 50 / 21))['pairs'][0]
        self.assertAlmostEqual(pair['balance'], .08)
        self.assertTrue(pair['balance_passed'])
        self.assertTrue(pair['eligible'])
        self.assertIn('รวมค่าขอบเขต', pair['reason'])
        # Values outside by 1e-10 are real threshold failures, not float noise.
        pair = self.main(quotes(1 / (.58 + 1e-10), 1 / (.42 - 1e-10)))['pairs'][0]
        self.assertFalse(pair['balance_passed'])
        for implied_sum in (.98 - 1e-10, 1.20 + 1e-10):
            with self.subTest(implied_sum=implied_sum):
                pair = self.main(quotes(2 / implied_sum, 2 / implied_sum))['pairs'][0]
                self.assertFalse(pair['sum_passed'])
                self.assertFalse(pair['eligible'])

    def test_pair_diagnostic_keeps_bookmaker_line_and_all_three_times(self):
        result = self.main(quotes(1.95, 1.90, line=3.25, book='Book source'))
        pair = result['pair']
        self.assertEqual(pair['bookmaker'], 'Book source')
        self.assertEqual(pair['bookmaker_key'], 'test-book')
        self.assertEqual(pair['line'], 3.25)
        for field in ('scanned_at', 'received_at', 'source_updated_at'):
            self.assertEqual(pair[field], STAMP)
        self.assertEqual(pair['over']['row']['odds'], 1.95)
        self.assertEqual(pair['under']['row']['odds'], 1.90)
        self.assertIn('ผ่านเกณฑ์', pair['reason'])

    def test_every_quarter_integer_and_half_line_retains_its_real_quotes(self):
        for line in (2.5, 2.75, 3.0, 3.25, 3.5):
            with self.subTest(line=line):
                result = self.main(quotes(1.9, 1.95, line=line))
                self.assertEqual(result['line'], line)
                self.assertEqual(result['pair']['over']['row']['point'], line)
                self.assertEqual(result['pair']['under']['row']['point'], line)
                self.assertEqual(result['pair']['under']['row']['odds'], 1.95)

    def test_missing_side_is_preserved_as_unpaired_information(self):
        result = self.main(quotes()[:1])
        self.assertEqual(result['reason_code'], 'missing_side')
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['diagnostics']['unpaired_groups'], 1)
        unmatched = result['unpaired'][0]
        self.assertEqual(unmatched['reason_code'], 'missing_compatible_side')
        self.assertEqual(set(unmatched['sides']), {'over'})
        self.assertEqual(unmatched['sides']['over'][0]['odds'], 1.9)
        self.assertNotIn('under', unmatched['sides'])

    def test_different_event_book_key_line_or_snapshot_never_supplies_a_side(self):
        for field, value in [('event_id', 'other-event'), ('bookmaker', 'Other bookmaker'),
                             ('bookmaker_key', 'other-key'), ('point', 2.75),
                             ('scanned_at', (NOW - timedelta(seconds=1)).isoformat()),
                             ('received_at', (NOW - timedelta(seconds=1)).isoformat()),
                             ('source_updated_at', (NOW - timedelta(seconds=1)).isoformat())]:
            with self.subTest(field=field):
                choices = quotes()
                choices[1]['row'][field] = value
                result = self.main(choices)
                self.assertEqual(result['status'], 'unconfirmed')
                self.assertEqual(result['pairs'], [])
                self.assertEqual(len(result['unpaired']), 2)

    def test_equivalent_aware_timestamp_representations_share_one_snapshot(self):
        choices = quotes()
        for field in ('scanned_at', 'received_at', 'source_updated_at'):
            choices[0]['row'][field] = '2026-10-21T12:00:00Z'
            choices[1]['row'][field] = '2026-10-21T19:00:00+07:00'
        result = self.main(choices)
        self.assertEqual(result['status'], 'estimated')
        self.assertEqual(len(result['pairs']), 1)

    def test_stale_receipt_or_provider_update_stays_diagnostic_only(self):
        for fields in [('received_at', 'source_updated_at', 'scanned_at'), ('source_updated_at',)]:
            with self.subTest(fields=fields):
                choices = quotes()
                for choice in choices:
                    for field in fields:
                        choice['row'][field] = (NOW - timedelta(minutes=16)).isoformat()
                result = self.main(choices)
                self.assertEqual(result['reason_code'], 'stale_or_unverified_snapshots')
                pair = result['pairs'][0]
                self.assertTrue(pair['numeric_eligible'])
                self.assertFalse(pair['fresh'])
                self.assertFalse(pair['eligible'])
                self.assertEqual(pair['over']['row']['odds'], 1.9)
                self.assertTrue(any('ราคาเก่า' in reason for reason in pair['freshness_reasons']))
                self.assertIsNone(compare_sides(result)[0])

    def test_missing_provider_or_freshness_evidence_cannot_estimate_main(self):
        for field, value in [('bookmaker_key', None), ('event_id', None),
                             ('received_at', None), ('source_updated_at', None),
                             ('received_at', 'unparseable'),
                             ('source_updated_at', '2026-10-21T12:00:00'),
                             ('scanned_at', '2026-10-21T12:00:00')]:
            with self.subTest(field=field, value=value):
                choices = quotes()
                for choice in choices:
                    choice['row'][field] = value
                result = self.main(choices)
                self.assertIsNone(result['pair'])
                self.assertEqual(result['reason_code'], 'stale_or_unverified_snapshots')
                self.assertTrue(result['pairs'][0]['numeric_eligible'])
                self.assertFalse(result['pairs'][0]['fresh'])
                self.assertTrue(result['pairs'][0]['freshness_reasons'])

    def test_snapshot_save_before_receipt_or_in_future_cannot_estimate_main(self):
        for stamp in (NOW - timedelta(seconds=31), NOW + timedelta(seconds=31)):
            with self.subTest(stamp=stamp):
                choices = quotes(scanned_at=stamp.isoformat())
                result = self.main(choices)
                self.assertEqual(result['reason_code'], 'stale_or_unverified_snapshots')
                self.assertTrue(result['pairs'][0]['numeric_eligible'])
                self.assertFalse(result['pairs'][0]['fresh'])
                self.assertIn('ลำดับเวลาบันทึก', result['pairs'][0]['reason'])

    def test_freshness_age_includes_900_seconds_but_rejects_any_older_evidence(self):
        at_limit = (NOW - timedelta(seconds=900)).isoformat()
        result = self.main(quotes(scanned_at=at_limit, received_at=at_limit, source_updated_at=at_limit))
        self.assertEqual(result['status'], 'estimated')
        self.assertTrue(result['pair']['fresh'])
        for field in ('received_at', 'source_updated_at'):
            with self.subTest(field=field):
                choices = quotes(scanned_at=at_limit, received_at=at_limit, source_updated_at=at_limit)
                for choice in choices:
                    choice['row'][field] = (NOW - timedelta(seconds=900, microseconds=1)).isoformat()
                result = self.main(choices)
                self.assertEqual(result['reason_code'], 'stale_or_unverified_snapshots')
                self.assertFalse(result['pairs'][0]['fresh'])

    def test_scan_order_includes_30_seconds_but_not_beyond(self):
        for direction in (-1, 1):
            with self.subTest(direction=direction):
                exact = NOW + direction * timedelta(seconds=30)
                self.assertEqual(self.main(quotes(scanned_at=exact.isoformat()))['status'], 'estimated')
                outside = NOW + direction * timedelta(seconds=30, microseconds=1)
                result = self.main(quotes(scanned_at=outside.isoformat()))
                self.assertEqual(result['reason_code'], 'stale_or_unverified_snapshots')
                self.assertFalse(result['pairs'][0]['fresh'])

    def test_giant_point_is_invalid_diagnostic_without_overflow(self):
        for point in (1e308, 10 ** 400):
            with self.subTest(point=point):
                result = self.main(quotes(line=point))
                self.assertEqual(result['reason_code'], 'invalid_quotes')
                self.assertEqual(result['diagnostics']['invalid_quotes'], 2)
                self.assertEqual(result['pairs'], [])
                self.assertEqual(result['invalid'][0]['row']['point'], point)

    def test_equal_price_duplicate_is_deduplicated_without_extra_support(self):
        choices = quotes()
        duplicate = deepcopy(choices[0])
        duplicate['row']['id'] = 7
        result = self.main(choices + [duplicate])
        self.assertEqual(result['status'], 'estimated')
        self.assertEqual(result['pair']['quote_ids']['over'], [1, 7])
        self.assertEqual(result['pair']['over']['row']['id'], 7)
        self.assertEqual(result['diagnostics']['identical_duplicate_quotes'], 1)
        self.assertIn('1 เจ้ามือ', result['reason'])

    def test_conflicting_duplicate_side_never_uses_last_row_price(self):
        choices = quotes()
        conflict = deepcopy(choices[0])
        conflict['row'].update(id=999, odds=1.95)
        for ordered in (choices + [conflict], [conflict] + choices):
            with self.subTest(order=[e['row']['id'] for e in ordered]):
                result = self.main(ordered)
                self.assertEqual(result['reason_code'], 'conflicting_duplicates')
                self.assertEqual(result['pairs'], [])
                self.assertEqual(result['diagnostics']['conflicting_duplicate_groups'], 1)
                group = result['unpaired'][0]
                self.assertEqual(group['reason_code'], 'conflicting_duplicate_side')
                self.assertEqual({row['odds'] for row in group['sides']['over']}, {1.9, 1.95})

    def test_quote_and_snapshot_ids_are_preserved_without_creating_ids(self):
        choices = quotes()
        choices[0]['row']['snapshot_id'] = 21
        choices[1]['snapshot'] = {'id': 22}
        result = self.main(choices)
        self.assertEqual(result['pair']['quote_ids'], {'over': [1], 'under': [2]})
        self.assertEqual(result['pair']['snapshot_ids'], {'over': [21], 'under': [22]})
        self.assertEqual(self.main(quotes())['pair']['snapshot_ids'], {'over': [], 'under': []})
        result = self.main(choices[:1])
        self.assertEqual(result['unpaired'][0]['snapshot_ids'], [21])

    def test_invalid_quote_remains_available_for_diagnostics_without_a_fake_pair(self):
        choices = quotes()
        choices[1]['row']['odds'] = None
        result = self.main(choices)
        self.assertEqual(result['pairs'], [])
        self.assertEqual(result['diagnostics']['invalid_quotes'], 1)
        self.assertEqual(result['invalid'][0]['row']['id'], 2)
        self.assertEqual(result['invalid'][0]['reason_code'], 'invalid_quote')
        self.assertIsNone(result['invalid'][0]['row']['odds'])

    def test_multiple_bookmakers_never_lend_support_from_failed_or_stale_quotes(self):
        choices = quotes(1.9, 1.9, line=3, book='Fresh', key='fresh')
        choices += quotes(1.9, 1.9, line=2.5, book='Stale', key='stale',
                          source_updated_at=(NOW - timedelta(minutes=16)).isoformat())
        choices += quotes(1.5, 1.5, line=2.5, book='High overround', key='bad')
        result = self.main(choices)
        self.assertEqual(result['line'], 3)
        self.assertEqual(result['diagnostics']['complete_pairs'], 3)
        self.assertEqual(result['diagnostics']['eligible_pairs'], 1)
        self.assertEqual(result['pair']['bookmaker'], 'Fresh')


if __name__ == '__main__':
    unittest.main()
