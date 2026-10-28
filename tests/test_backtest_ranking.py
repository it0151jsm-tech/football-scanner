"""Ranking contract fixtures only: observed metadata below is mocked, not evidence.

These fabricated report attachments exercise gates; no metric in this file is a
real validation result, and no provider/API or bill database is accessed.
"""
from copy import deepcopy
import unittest

from backtest.protocol import default_protocol
from backtest.ranking import canonical_sha256, candidate_reasons, rank_candidates, validate_review_artifact


def protocol():
    value = default_protocol('unchanged-baseline-v1', frozen_at='2026-01-01T00:00:00Z')
    value['review_policy']['minimum_sample_count'] = 3
    value['holdout'] = {'training_end': '2026-01-31T00:00:00Z',
                        'start': '2026-02-01T00:00:00Z', 'end': '2026-02-10T00:00:00Z'}
    return value


def candidate(**changes):
    row = {'quality_status': 'eligible_observed', 'baseline_c_supported': True,
           'decision_at': '2026-03-01T00:00:00Z', 'kickoff_utc': '2026-03-02T00:00:00Z',
           'event_id': 'future-event', 'fixture_id': 'future-canonical-fixture',
           'market': 'Over/Under', 'league': 'soccer_epl', 'model_version': 'unchanged-baseline-v1',
           'ev_pct': 4.5}
    row.update(changes)
    return row


def bundle(row=None, rules=None):
    row, rules = row or candidate(), rules or protocol()
    report = {'schema_version': 1, 'protocol_sha256': canonical_sha256(rules),
              **{key: row[key] for key in ('model_version', 'league', 'market')},
              'data_mode': 'observed', 'result_split': 'holdout', 'system': 'C',
              'training_provenance_status': 'verified',
              'settlement_coverage': {'status': 'complete', 'selected_count': 3,
                                      'settled_count': 3, 'pending_count': 0},
              'training_end': '2026-01-31T00:00:00Z',
              'holdout_start': '2026-02-01T00:00:00Z',
              'evaluation_start': '2026-02-01T00:00:00Z',
              'evaluation_end': '2026-02-10T00:00:00Z',
              'protocol_frozen_at': rules['protocol_frozen_at'],
              'training_case_ids': ['train-case'], 'training_fixture_ids': ['train-fixture'],
              'results': [{'case_id': f'holdout-case-{i}', 'fixture_id': f'holdout-fixture-{i}',
                           'event_id': f'holdout-event-{i}',
                           'quality_status': 'eligible_observed', 'data_mode': 'observed',
                           'result_split': 'holdout', 'outcome': value, 'system': 'C',
                           **{key: row[key] for key in ('model_version', 'league', 'market')},
                           'decision_at': f'2026-02-0{i+2}T00:00:00Z',
                           'kickoff_utc': f'2026-02-0{i+2}T01:00:00Z',
                           'result_received_at': f'2026-02-0{i+2}T03:00:00Z'}
                          for i, value in enumerate(('win', 'loss', 'push'))],
              # Deliberately not consulted when deciding eligibility or order.
              'roi': -40, 'yield': -40, 'calibrated': False}
    manifest = {key: deepcopy(report[key]) for key in (
        'model_version', 'league', 'market', 'data_mode', 'result_split', 'system',
        'training_end', 'holdout_start', 'evaluation_start', 'evaluation_end', 'protocol_frozen_at')}
    manifest.update(schema_version=1, report_sha256=canonical_sha256(report),
                    protocol_sha256=canonical_sha256(rules), review_status='approved', reviewed_by='test-reviewer',
                    independent_holdout=True, reviewed_at='2026-02-11T00:00:00Z',
                    received_at='2026-02-12T00:00:00Z', sample_count=3)
    return {'manifest': manifest, 'report': report}


def rehash(value):
    value['manifest']['report_sha256'] = canonical_sha256(value['report'])
    return value


class BacktestRankingTests(unittest.TestCase):
    def validate(self, value, row=None, rules=None):
        return validate_review_artifact(value, row or candidate(), rules or protocol())

    def rejected(self, value, reason, row=None, rules=None):
        result = self.validate(value, row, rules)
        self.assertFalse(result['eligible'])
        self.assertIn(reason, result['reasons'])

    def test_valid_prior_review_exposes_history_only_not_profit_or_calibration(self):
        result = self.validate(bundle())
        self.assertTrue(result['eligible'])
        self.assertEqual(result['review']['sample_count'], 3)
        self.assertIn('not proof of profitability or calibration', result['review']['scope'])
        self.assertNotIn('roi', result['review'])

    def test_actual_report_attachment_hash_required_not_declared_roi(self):
        value = bundle()
        value['report']['results'][0]['outcome'] = 'loss'
        self.rejected(value, 'report_hash_mismatch')
        value = bundle()
        value['report']['roi'] = 999999
        self.rejected(value, 'report_hash_mismatch')

    def test_protocol_hash_binds_predeclared_sample_policy(self):
        changed = protocol()
        changed['review_policy']['minimum_sample_count'] = 4
        self.rejected(bundle(), 'protocol_hash_mismatch', rules=changed)

    def test_domain_must_match_model_league_and_market(self):
        for key, value in [('model_version', 'tuned-model-v2'), ('league', 'Bundesliga'),
                           ('market', 'Asian Handicap')]:
            with self.subTest(key=key):
                self.rejected(bundle(), 'review_domain_mismatch' if key == 'market' else 'candidate_protocol_domain_mismatch', row=candidate(**{key: value}))

    def test_synthetic_or_research_aggregate_cannot_be_validated(self):
        for mode in ('research', 'synthetic', 'sample', None):
            with self.subTest(mode=mode):
                value = bundle()
                value['manifest']['data_mode'] = value['report']['data_mode'] = mode
                self.rejected(rehash(value), 'observed_independent_holdout_required')

    def test_training_or_development_results_cannot_replace_holdout(self):
        value = bundle()
        value['manifest']['result_split'] = value['report']['result_split'] = 'training'
        self.rejected(rehash(value), 'observed_independent_holdout_required')

    def test_manual_approval_and_independent_holdout_required(self):
        for key, replacement in [('review_status', 'pending'), ('review_status', 'rejected'),
                                 ('independent_holdout', False), ('independent_holdout', 1)]:
            with self.subTest(key=key, value=replacement):
                value = bundle()
                value['manifest'][key] = replacement
                self.rejected(value, 'manual_independent_review_required')

    def test_future_review_receipt_or_results_cannot_rank_earlier_candidate(self):
        for field in ('reviewed_at', 'received_at'):
            with self.subTest(field=field):
                value = bundle()
                value['manifest'][field] = '2026-03-03T00:00:00Z'
                self.rejected(value, 'future_or_overlapping_review_period')
        value = bundle()
        value['manifest']['evaluation_end'] = value['report']['evaluation_end'] = '2026-03-03T00:00:00Z'
        self.rejected(rehash(value), 'future_or_overlapping_review_period')

    def test_manifest_cannot_backdate_actual_report_period(self):
        value = bundle()
        value['report']['evaluation_end'] = '2026-03-03T00:00:00Z'
        self.rejected(rehash(value), 'attachment_time_mismatch')

    def test_equal_end_review_boundary_is_not_prior_completed_evidence(self):
        value = bundle()
        value['manifest']['reviewed_at'] = value['report']['evaluation_end']
        self.rejected(value, 'future_or_overlapping_review_period')

    def test_training_and_holdout_intervals_cannot_overlap(self):
        value = bundle()
        value['manifest']['training_end'] = value['report']['training_end'] = value['report']['holdout_start']
        self.rejected(rehash(value), 'future_or_overlapping_review_period')

    def test_protocol_frozen_after_training_but_before_holdout_is_allowed(self):
        rules = protocol()
        rules['protocol_frozen_at'] = '2026-01-31T12:00:00Z'
        self.assertTrue(self.validate(bundle(rules=rules), rules=rules)['eligible'])

    def test_protocol_cannot_be_frozen_after_seeing_holdout(self):
        rules = protocol()
        rules['protocol_frozen_at'] = '2026-02-02T00:00:00Z'
        self.rejected(bundle(rules=rules), 'invalid_predeclared_protocol', rules=rules)

    def test_train_holdout_case_and_fixture_identity_overlap_rejected(self):
        for field, duplicate in [('training_case_ids', 'holdout-case-0'),
                                 ('training_fixture_ids', 'holdout-fixture-0')]:
            with self.subTest(field=field):
                value = bundle()
                value['report'][field].append(duplicate)
                self.rejected(rehash(value), 'train_holdout_identity_overlap')

    def test_target_candidate_must_not_be_in_evaluated_or_training_fixtures(self):
        for field, value in [('event_id', 'holdout-fixture-0'),
                             ('fixture_id', 'holdout-fixture-1'),
                             ('fixture_id', 'train-fixture'), ('case_id', 'train-case')]:
            with self.subTest(field=field, value=value):
                self.rejected(bundle(), 'candidate_in_review_data', row=candidate(**{field: value}))

    def test_duplicate_quotes_cannot_inflate_independent_sample_size(self):
        for field in ('case_id', 'fixture_id', 'event_id'):
            with self.subTest(field=field):
                value = bundle()
                value['report']['results'][1][field] = value['report']['results'][0][field]
                self.rejected(rehash(value), 'duplicate_or_missing_holdout_identity')

    def test_actual_observed_qualified_settled_results_required(self):
        for field, replacement, reason in [
            ('data_mode', 'research', 'unqualified_or_synthetic_holdout_result'),
            ('quality_status', 'quarantine', 'unqualified_or_synthetic_holdout_result'),
            ('result_split', 'training', 'unqualified_or_synthetic_holdout_result'),
            ('outcome', None, 'unsettled_holdout_result'),
            ('outcome', 'pending', 'unsettled_holdout_result')]:
            with self.subTest(field=field, replacement=replacement):
                value = bundle()
                value['report']['results'][0][field] = replacement
                self.rejected(rehash(value), reason)

    def test_missing_training_membership_empty_results_and_false_sample_counts_rejected(self):
        value = bundle()
        del value['report']['training_fixture_ids']
        self.rejected(rehash(value), 'training_membership_required')
        value = bundle()
        value['report']['results'] = []
        self.rejected(rehash(value), 'settled_holdout_results_required')
        for count in (0, 2, 4, True, 3.0):
            with self.subTest(count=count):
                value = bundle()
                value['manifest']['sample_count'] = count
                self.rejected(value, 'insufficient_or_inconsistent_review_sample')

    def test_ev_none_zero_negative_nonfinite_or_bool_never_value_bet_queue(self):
        for ev in (None, 0, -1, float('nan'), float('inf'), True, '50'):
            with self.subTest(ev=ev):
                result = rank_candidates([candidate(ev_pct=ev, a_score=100)], [bundle()], protocol=protocol())
                self.assertEqual(result['ranked'], [])
                self.assertEqual(result['research_queue'], [])
                self.assertIn('positive_model_ev_required', result['rejected'][0]['reasons'])

    def test_original_c_and_observed_data_gates_cannot_be_bypassed_with_high_ev(self):
        for values in ({'baseline_c_supported': False}, {'baseline_c_supported': 1},
                       {'quality_status': 'eligible_research'}, {'quality_status': 'quarantine'}):
            with self.subTest(values=values):
                result = rank_candidates([candidate(ev_pct=1000, **values)], [bundle()], protocol=protocol())
                self.assertEqual(len(result['rejected']), 1)
                self.assertEqual(result['ranked'], [])

    def test_missing_review_stays_in_separate_needs_validation_queue(self):
        result = rank_candidates([candidate()], protocol=protocol())
        self.assertEqual(result['ranked'], [])
        self.assertEqual(result['research_queue'][0]['validation_status'], 'needs_validation')
        self.assertEqual(result['research_queue'][0]['reasons'], ['no_reviewed_holdout_history'])
        self.assertNotIn('rank', result['research_queue'][0])

    def test_reviewed_history_precedes_unreviewed_high_ev(self):
        first = candidate(event_id='epl', ev_pct=1)
        unreviewed = candidate(event_id='spain', league='soccer_spain_la_liga', ev_pct=50)
        result = rank_candidates([unreviewed, first], [bundle(first)], protocol=protocol())
        self.assertEqual(result['ranked'][0]['candidate']['event_id'], 'epl')
        self.assertEqual(result['research_queue'][0]['candidate']['event_id'], 'spain')

    def test_same_quality_and_review_use_actual_ev_not_form_price_or_roi(self):
        rows = [candidate(event_id='large-price', ev_pct=1, odds=20, a_score=100),
                candidate(event_id='better-ev', ev_pct=5, odds=1.9, a_score=60)]
        review = bundle()
        review['report']['roi'] = -999
        result = rank_candidates(rows, [rehash(review)], protocol=protocol())
        self.assertEqual([entry['candidate']['event_id'] for entry in result['ranked']],
                         ['better-ev', 'large-price'])
        self.assertEqual([entry['rank'] for entry in result['ranked']], [1, 2])

    def test_newest_completed_review_selected_not_best_roi(self):
        old, new = bundle(), bundle()
        old['report']['roi'] = 9999
        new['report']['roi'] = -9999
        new['manifest']['reviewed_at'] = '2026-02-11T12:00:00Z'
        result = rank_candidates([candidate()], [rehash(old), rehash(new)], protocol=protocol())
        self.assertEqual(result['ranked'][0]['review']['report_sha256'], new['manifest']['report_sha256'])

    def test_future_high_roi_artifact_has_no_effect_on_current_ranking(self):
        good, future = bundle(), bundle()
        future['report']['roi'] = 9999999
        future['manifest']['received_at'] = '2026-03-05T00:00:00Z'
        original = rank_candidates([candidate()], [good], protocol=protocol())
        self.assertEqual(rank_candidates([candidate()], [good, future], protocol=protocol()), original)

    def test_target_kickoff_must_be_confirmed_future_of_decision(self):
        for stamp in ('2026-02-28T00:00:00Z', '2026-03-01T00:00:00Z',
                      '2026-03-02T00:00:00', None, 'bad'):
            with self.subTest(stamp=stamp):
                self.assertIn('prospective_aware_kickoff_required', candidate_reasons(candidate(kickoff_utc=stamp)))
        self.assertEqual(candidate_reasons(candidate(kickoff_utc='2026-03-02T07:00:00+07:00')), [])

    def test_invalid_domain_empty_and_missing_protocol_fail_closed(self):
        for market in (None, '1X2', [], {}):
            with self.subTest(market=market):
                self.assertIn('unsupported_market', candidate_reasons(candidate(market=market)))
        for rules in ({}, {'review_policy': {'minimum_sample_count': True}},
                      {'protocol_frozen_at': 'bad', 'review_policy': {'minimum_sample_count': 3}}):
            with self.subTest(rules=rules):
                with self.assertRaises(ValueError):
                    rank_candidates([], protocol=rules)
        result = rank_candidates([], protocol=protocol())
        self.assertEqual((result['ranked'], result['research_queue'], result['rejected']), ([], [], []))

    def test_review_must_be_for_c_not_a_or_b(self):
        for system in ('A', 'B', None, 'C-optimized'):
            with self.subTest(system=system):
                value = bundle()
                value['manifest']['system'] = value['report']['system'] = system
                self.rejected(rehash(value), 'review_strategy_mismatch')

    def test_unknown_training_provenance_never_becomes_reviewed_history(self):
        for status in (None, 'unknown', 'assumed', 'research'):
            with self.subTest(status=status):
                value = bundle()
                value['report']['training_provenance_status'] = status
                self.rejected(rehash(value), 'training_provenance_not_verified')

    def test_predeclared_holdout_required_for_validation(self):
        rules = protocol()
        rules['holdout'] = None
        self.rejected(bundle(rules=rules), 'predeclared_holdout_required', rules=rules)
        result = rank_candidates([candidate()], protocol=rules)
        self.assertEqual(result['ranked'], [])
        self.assertEqual(len(result['research_queue']), 1)

    def test_cannot_cherry_pick_smaller_profitable_window(self):
        for key, stamp in [('evaluation_start', '2026-02-02T00:00:00Z'),
                           ('evaluation_end', '2026-02-09T00:00:00Z'),
                           ('training_end', '2026-01-30T00:00:00Z'),
                           ('holdout_start', '2026-01-31T12:00:00Z')]:
            with self.subTest(key=key):
                value = bundle()
                value['report'][key] = value['manifest'][key] = stamp
                self.rejected(rehash(value), 'full_predeclared_holdout_window_required')

    def test_each_holdout_row_must_match_strategy_model_league_and_market(self):
        for key, replacement in [('system', 'B'), ('model_version', 'tuned'),
                                 ('league', 'soccer_spain_la_liga'), ('market', 'Asian Handicap')]:
            with self.subTest(key=key):
                value = bundle()
                value['report']['results'][0][key] = replacement
                self.rejected(rehash(value), 'holdout_result_domain_mismatch')

    def test_each_holdout_result_requires_aware_ordered_clocks(self):
        for key, stamp in [('decision_at', None), ('decision_at', '2026-02-02T02:00:00Z'),
                           ('decision_at', '2026-01-31T00:00:00Z'),
                           ('kickoff_utc', '2026-02-02T01:00:00'),
                           ('kickoff_utc', '2026-02-11T01:00:00Z'),
                           ('result_received_at', '2026-02-02T01:00:00Z'),
                           ('result_received_at', '2026-02-11T12:00:00Z')]:
            with self.subTest(key=key, stamp=stamp):
                value = bundle()
                value['report']['results'][0][key] = stamp
                self.rejected(rehash(value), 'holdout_result_time_invalid')

    def test_clockless_results_cannot_ride_on_valid_aggregate_period(self):
        value = bundle()
        for row in value['report']['results']:
            for key in ('decision_at', 'kickoff_utc', 'result_received_at'):
                del row[key]
        self.rejected(rehash(value), 'holdout_result_time_invalid')

    def test_canonical_full_win_and_full_loss_metrics_outcomes_supported(self):
        value = bundle()
        for row, label in zip(value['report']['results'], ('full_win', 'half_loss', 'full_loss')):
            row['outcome'] = label
        self.assertTrue(self.validate(rehash(value))['eligible'])

    def test_review_reviewer_required(self):
        value = bundle()
        del value['manifest']['reviewed_by']
        self.rejected(value, 'manual_independent_review_required')

    def test_event_identity_prevents_target_membership_with_different_fixture_alias(self):
        self.rejected(bundle(), 'candidate_in_review_data', row=candidate(event_id='holdout-event-0'))

    def test_wrong_protocol_domain_rejected_without_review_bundle(self):
        for values in ({'league': 'unsupported'}, {'model_version': 'tuned-model-v2'}):
            with self.subTest(values=values):
                result = rank_candidates([candidate(**values)], protocol=protocol())
                self.assertEqual(result['ranked'], [])
                self.assertEqual(result['research_queue'], [])
                self.assertIn('candidate_protocol_domain_mismatch', result['rejected'][0]['reasons'])

    def test_review_cannot_ignore_pending_or_missing_settlements(self):
        for coverage in (None, {'status': 'unknown'},
                         {'status': 'complete', 'selected_count': 100, 'settled_count': 3, 'pending_count': 97},
                         {'status': 'complete', 'selected_count': 3, 'settled_count': 3, 'pending_count': True}):
            with self.subTest(coverage=coverage):
                value = bundle()
                value['report']['settlement_coverage'] = coverage
                self.rejected(rehash(value), 'incomplete_or_unverified_settlement_coverage')

    def test_hashes_are_deterministic_strict_json_and_no_mutation(self):
        self.assertEqual(canonical_sha256({'x': 1, 'y': [2]}), canonical_sha256({'y': [2], 'x': 1}))
        with self.assertRaises(ValueError):
            canonical_sha256({'x': float('nan')})
        rows, reviews, rules = [candidate()], [bundle()], protocol()
        before = deepcopy((rows, reviews, rules))
        rank_candidates(rows, reviews, protocol=rules)
        self.assertEqual((rows, reviews, rules), before)


if __name__ == '__main__':
    unittest.main()
