"""Synthetic integration archives test replay mechanics, never real performance."""
from copy import deepcopy
import hashlib
import json
import unittest

from backtest.engine import run_backtest
from backtest.baseline import Baseline
from backtest.protocol import default_protocol


DECISION = '2020-10-20T12:00:00+00:00'
KICKOFF = '2020-10-20T15:00:00+00:00'
REPORT = '2020-10-21T12:00:00+00:00'


class AuthoredArchive:
    """Explicitly authored test observations; no provider or persisted DB."""
    def __init__(self, *, line=2.5, market='Over/Under', goals=(2, 1)):
        self.document = dict(schema_version=1, data_mode='synthetic_test',
            source_catalog={'fixture': dict(rights_status='approved', reviewed_by='test author',
                reviewed_at='2020-09-01T00:00:00+00:00',
                rights_evidence_url='https://example.invalid/authored-test-fixture')},
            evidence_payloads={}, events=[], decisions=[], score_history=[],
            xg_history=[], quotes=[], results=[])
        identity = dict(event_id='target', league='soccer_epl', season='2021', period='FT90',
            home_team='Synthetic Home', away_team='Synthetic Away', kickoff_utc=KICKOFF)
        self.document['events'] = [self.bound(dict(identity, received_at='2020-10-19T12:00:00+00:00'))]
        self.document['decisions'] = [self.bound(dict(decision_id='decision', event_id='target',
            decision_at=DECISION, received_at=DECISION))]
        for day in range(1, 11):
            history = dict(match_id='history-'+str(day), league='soccer_epl', season='2021',
                period='FT90', home_team='Synthetic Home', away_team='Synthetic Away',
                match_time_utc=f'2020-10-{day:02}T15:00:00+00:00', status='FT',
                home_goals=2, away_goals=1, received_at=f'2020-10-{day:02}T18:00:00+00:00')
            self.document['score_history'].append(self.bound(history))
            self.document['xg_history'].append(self.bound(dict(history, xg_home=1.7, xg_away=1.1)))
        sides = ('over', 'under') if market == 'Over/Under' else ('home', 'away')
        for side in sides:
            point = -line if market == 'Asian Handicap' and side == 'away' else line
            self.document['quotes'].append(self.bound(dict(identity, quote_id='quote-'+side,
                market=market, side=side, point=point, odds=1.95,
                bookmaker='Authored Book', bookmaker_key='authored-book', quote_kind='observed',
                odds_in_range=True, snapshot_group_id='entry',
                source_updated_at='2020-10-20T11:59:00+00:00',
                received_at=DECISION, captured_at=DECISION, scanned_at=DECISION)))
        self.document['results'] = [self.bound(dict(identity, status='FT',
            home_goals=goals[0], away_goals=goals[1], received_at='2020-10-20T18:00:00+00:00'))]

    def bound(self, row):
        row = deepcopy(row)
        row.pop('source_payload_sha256', None)
        row.setdefault('source', 'fixture')
        row.setdefault('data_mode', 'synthetic_test')
        body = json.dumps(row, sort_keys=True, separators=(',', ':'), allow_nan=False)
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.document['evidence_payloads'][digest] = dict(body=body,
            source=row['source'], received_at=row['received_at'])
        return dict(row, source_payload_sha256=digest)

    def replace(self, collection, index=0, **changes):
        self.document[collection][index] = self.bound(dict(self.document[collection][index], **changes))

    def report(self, **kwargs):
        return run_backtest(self.document, report_at=REPORT, allow_test_fixtures=True, **kwargs)


def selections(report, system):
    return [row for row in report['settled_records'] if row['system'] == system]


class BacktestEngineTests(unittest.TestCase):
    def test_prefrozen_holdout_is_separate_but_unknown_training_still_blocks_validation(self):
        fixture=AuthoredArchive()
        protocol=default_protocol(Baseline().version['bundle_sha256'],frozen_at='2020-09-20T00:00:00Z')
        protocol['holdout']={'training_end':'2020-09-01T00:00:00Z',
            'start':'2020-10-01T00:00:00Z','end':'2020-10-21T00:00:00Z'}
        report=fixture.report(protocol=protocol)
        self.assertTrue(all(row['result_split']=='holdout' for row in report['settled_records']))
        self.assertEqual(report['systems']['B']['by_split']['holdout']['n'],1)
        self.assertEqual(report['b_calibration_all_contracts']['by_split']['holdout']['overall']['calibration']['n'],2)
        self.assertEqual(report['training_provenance_status'],'unknown')
        self.assertFalse(report['systems']['B']['overall']['calibration']['calibrated'])

    def test_proven_capture_time_maps_to_original_runtime_without_guessing_scan_time(self):
        fixture=AuthoredArchive()
        for index,row in enumerate(list(fixture.document['quotes'])):
            value=dict(row)
            value.pop('scanned_at')
            fixture.document['quotes'][index]=fixture.bound(value)
        report=fixture.report()
        self.assertEqual(len(selections(report,'C')),1)
        fixture.replace('quotes',0,scanned_at='2020-10-20T11:00:00Z')
        rejected=fixture.report()
        self.assertEqual(rejected['exclusions']['scan_clock_contradicts_normalized_snapshot'],1)
        self.assertEqual(selections(rejected,'C'),[])

    def test_missing_historical_odds_range_cannot_default_to_c_eligibility(self):
        fixture=AuthoredArchive()
        for index,row in enumerate(list(fixture.document['quotes'])):
            value=dict(row)
            value.pop('odds_in_range')
            fixture.document['quotes'][index]=fixture.bound(value)
        report=fixture.report()
        self.assertEqual(selections(report,'C'),[])
        self.assertEqual(len(selections(report,'B')),1)

    def test_aliases_and_equivalent_utc_offsets_cannot_duplicate_canonical_fixtures(self):
        fixture=AuthoredArchive()
        # Historical team stats are irrelevant for proving fixture deduplication.
        fixture.replace('events',home_team='Manchester City')
        for group in ('quotes','results'):
            for index in range(len(fixture.document[group])):
                fixture.replace(group,index,home_team='Manchester City')
        second=dict(fixture.document['events'][0],event_id='target-second',home_team='Man City',
                    kickoff_utc='2020-10-20T22:00:00+07:00')
        fixture.document['events'].append(fixture.bound(second))
        fixture.document['decisions'].append(fixture.bound(dict(fixture.document['decisions'][0],
            decision_id='second',event_id='target-second')))
        report=fixture.report()
        self.assertEqual(report['evaluated_fixture_n'],1)
        self.assertEqual(report['exclusions']['duplicate_canonical_fixture'],1)

    def test_complete_archive_replays_original_a_b_c_and_never_claims_real_performance(self):
        report = AuthoredArchive().report()
        self.assertEqual(report['status'], 'synthetic_test_only_not_real_performance')
        self.assertEqual(report['qualified_real_fixture_n'], 0)
        self.assertEqual(report['evaluated_fixture_n'], 1)
        for system in ('A', 'B', 'C'):
            rows = selections(report, system)
            self.assertEqual(len(rows), 1, (system, report['event_diagnostics']))
            self.assertEqual((rows[0]['side'], rows[0]['point']), ('over', 2.5))
            self.assertEqual(rows[0]['profit_units'], .95)
            self.assertEqual(rows[0]['outcome'], 'full_win')
            self.assertEqual(rows[0]['result_split'], 'descriptive_replay')
        self.assertEqual(report['training_provenance_status'], 'unknown')
        self.assertEqual(report['b_calibration_contract_n'], 2)

    def test_ou_quarter_settlement_runs_existing_formula_at_its_actual_line(self):
        report = AuthoredArchive(line=2.75).report()
        rows = selections(report, 'A')
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]['side'], rows[0]['point']), ('over', 2.75))
        self.assertEqual(rows[0]['outcome'], 'half_win')
        self.assertEqual(rows[0]['profit_units'], .475)
        self.assertEqual(rows[0]['calibration_class'], 'partial_or_push')

    def test_ah_quarter_settlement_preserves_half_loss_on_draw(self):
        report = AuthoredArchive(line=-.25, market='Asian Handicap', goals=(1, 1)).report()
        row = selections(report, 'A')[0]
        self.assertEqual((row['side'], row['point']), ('home', -.25))
        self.assertEqual((row['outcome'], row['profit_units']), ('half_loss', -.5))

    def test_ah_three_quarter_settlement_preserves_half_win(self):
        report = AuthoredArchive(line=-.75, market='Asian Handicap').report()
        row = selections(report, 'A')[0]
        self.assertEqual((row['side'], row['point']), ('home', -.75))
        self.assertEqual((row['outcome'], row['profit_units']), ('half_win', .475))

    def test_missing_xg_keeps_form_a_without_b_ev_or_c_value_bets(self):
        fixture = AuthoredArchive()
        fixture.document['xg_history'] = []
        report = fixture.report()
        self.assertEqual(len(selections(report, 'A')), 1)
        self.assertEqual(selections(report, 'B'), [])
        self.assertEqual(selections(report, 'C'), [])
        self.assertEqual(report['b_calibration_contract_n'], 0)
        self.assertTrue(all(row['ev_pct'] is None and row['p_win'] is None for row in report['predictions']))
        self.assertEqual(report['event_diagnostics'][0]['xg_history_n'], 0)

    def test_actual_goals_do_not_replace_xg_for_model_b(self):
        fixture = AuthoredArchive()
        before = fixture.report()
        for index in range(10):
            fixture.replace('score_history', index, home_goals=0, away_goals=0)
        after = fixture.report()
        self.assertNotEqual([row['score'] for row in before['predictions']], [row['score'] for row in after['predictions']])
        self.assertEqual([(row['ev_pct'], row['p_win']) for row in before['predictions']],
                         [(row['ev_pct'], row['p_win']) for row in after['predictions']])

    def test_history_received_after_decision_cannot_change_features(self):
        fixture = AuthoredArchive()
        before = fixture.report()
        for collection in ('score_history', 'xg_history'):
            original = fixture.document[collection][0]
            fixture.document[collection].append(fixture.bound(dict(original, home_goals=99,
                received_at='2020-10-20T13:00:00+00:00')))
        after = fixture.report()
        self.assertEqual(before['predictions'], after['predictions'])
        self.assertEqual(after['history_exclusions']['history_late_receipt_or_wrong_league'], 2)

    def test_target_outcome_changes_settlement_but_not_predictions_or_picks(self):
        fixture = AuthoredArchive()
        before = fixture.report()
        fixture.replace('results', home_goals=0, away_goals=0)
        after = fixture.report()
        self.assertEqual(before['predictions'], after['predictions'])
        self.assertEqual([(row['system'], row['quote_id']) for row in before['settled_records']],
                         [(row['system'], row['quote_id']) for row in after['settled_records']])
        self.assertEqual(selections(after, 'B')[0]['outcome'], 'full_loss')

    def test_closing_quote_is_outcome_report_only_and_cannot_change_entry(self):
        fixture = AuthoredArchive()
        before = fixture.report()
        for original in list(fixture.document['quotes']):
            fixture.document['quotes'].append(fixture.bound(dict(original,
                quote_id='closing-'+original['side'], quote_kind='closing',
                odds=2.5 if original['side'] == 'under' else 1.6,
                source_updated_at='2020-10-20T14:55:00+00:00',
                received_at='2020-10-20T14:55:00+00:00',
                captured_at='2020-10-20T14:55:00+00:00',
                scanned_at='2020-10-20T14:55:00+00:00', snapshot_group_id='closing')))
        after = fixture.report()
        self.assertEqual(before['predictions'], after['predictions'])
        self.assertEqual([row['quote_id'] for row in before['settled_records']],
                         [row['quote_id'] for row in after['settled_records']])
        self.assertTrue(all(row['closing']['clv_decimal'] is not None for row in after['price_reports']))
        self.assertTrue(all(row['closing']['closing_label'] == 'last_observed_pre_kickoff' for row in after['price_reports']))

    def test_future_result_is_pending_and_no_profit_is_fabricated(self):
        fixture = AuthoredArchive()
        fixture.replace('results', received_at='2020-10-22T12:00:00+00:00')
        report = fixture.report()
        self.assertEqual(report['settled_records'], [])
        self.assertEqual(len(report['pending_records']), 3)
        for system in ('A', 'B', 'C'):
            summary = report['systems'][system]
            self.assertEqual(summary['pending_n'], 1)
            self.assertFalse(summary['coverage_complete'])
            self.assertIsNone(summary['overall']['settlement']['roi_pct'])
            self.assertIsNone(summary['overall']['settlement']['profit_units'])

    def test_missing_result_keeps_coverage_explicitly_incomplete(self):
        fixture = AuthoredArchive()
        fixture.document['results'] = []
        report = fixture.report()
        self.assertTrue(all(row['reason'] == 'result_not_available' for row in report['pending_records']))
        self.assertEqual(report['systems']['C']['selected_n'], 1)
        self.assertEqual(report['systems']['C']['pending_proposed_stake_units'], 1)

    def test_only_first_recorded_cutoff_counts_as_independent_decision(self):
        fixture = AuthoredArchive()
        fixture.document['decisions'].append(fixture.bound(dict(fixture.document['decisions'][0],
            decision_id='later', decision_at='2020-10-20T12:01:00+00:00',
            received_at='2020-10-20T12:01:00+00:00')))
        report = fixture.report()
        self.assertEqual(len(selections(report, 'C')), 1)
        self.assertEqual(report['exclusions']['later_decisions_not_independent_samples'], 1)
        self.assertTrue(all(row['decision_at'] == DECISION for row in report['predictions']))

    def test_conflicting_quote_identifiers_are_rejected_instead_of_choosing_high_price(self):
        fixture = AuthoredArchive()
        fixture.document['quotes'].append(fixture.bound(dict(fixture.document['quotes'][0], odds=2.8)))
        report = fixture.report()
        self.assertEqual(report['exclusions']['conflicting_quote_identity'], 2)
        self.assertFalse(any(row['side'] == 'over' for row in report['predictions']))
        self.assertEqual(selections(report, 'C'), [])

    def test_identical_quotes_and_events_do_not_inflate_sample_count(self):
        fixture = AuthoredArchive()
        fixture.document['events'].append(deepcopy(fixture.document['events'][0]))
        fixture.document['quotes'] += deepcopy(fixture.document['quotes'])
        report = fixture.report()
        self.assertEqual(len(report['predictions']), 2)
        self.assertEqual(report['b_calibration_contract_n'], 2)
        self.assertEqual(len(selections(report, 'A')), 1)

    def test_conflicting_event_identity_stops_evaluation(self):
        fixture = AuthoredArchive()
        fixture.document['events'].append(fixture.bound(dict(fixture.document['events'][0], away_team='Other Away')))
        report = fixture.report()
        self.assertEqual(report['predictions'], [])
        self.assertEqual(report['exclusions']['conflicting_event_identity'], 1)

    def test_calibration_includes_negative_ev_and_deduplicates_bookmakers(self):
        fixture = AuthoredArchive()
        for quote in list(fixture.document['quotes']):
            fixture.document['quotes'].append(fixture.bound(dict(quote,
                quote_id='second-'+quote['side'], bookmaker='Second Authored Book', bookmaker_key='second-book')))
        report = fixture.report()
        self.assertEqual(len(report['predictions']), 4)
        self.assertTrue(any(row['ev_pct'] < 0 for row in report['predictions']))
        self.assertEqual(report['b_calibration_contract_n'], 2)
        self.assertEqual(report['b_calibration_all_contracts']['by_market']['Over/Under']['calibration']['n'], 2)
        self.assertEqual(len(selections(report, 'B')), 1)

    def test_empty_real_archive_reports_zero_samples_and_null_performance(self):
        fixture = AuthoredArchive()
        document = dict(fixture.document, data_mode='observed', events=[], decisions=[],
            score_history=[], xg_history=[], quotes=[], results=[], evidence_payloads={})
        report = run_backtest(document, report_at=REPORT)
        self.assertEqual(report['status'], 'insufficient_real_data')
        self.assertEqual(report['qualified_real_fixture_n'], 0)
        for system in ('A', 'B', 'C'):
            cell = report['systems'][system]['overall']['settlement']
            self.assertEqual(cell['n'], 0)
            self.assertIsNone(cell['profit_units'])
            self.assertIsNone(cell['roi_pct'])
        self.assertEqual(report['b_calibration_contract_n'], 0)

    def test_replay_is_read_only_and_serializes_without_nan(self):
        fixture = AuthoredArchive()
        before = deepcopy(fixture.document)
        report = fixture.report()
        self.assertEqual(before, fixture.document)
        json.dumps(report, allow_nan=False)
        self.assertEqual(len(report['model_version']), 64)
        self.assertEqual(len(report['evaluator_sha256']), 64)
        self.assertEqual(set(report['baseline_manifest']['formula_sha256']), {
            'estimate_goals', 'estimate_form_signal', 'choose_recommendation', 'poisson_probs',
            'score_matrix', 'split_line', 'evaluate_market_poisson', 'actual_xg_evidence',
            'system_a_analysis', 'system_b_analysis', 'settle_pick'})


if __name__ == '__main__':
    unittest.main()
