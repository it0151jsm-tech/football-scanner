"""Synthetic, offline expectations against the protected original settlement callback."""
from copy import deepcopy
import json
import math
import unittest
from unittest.mock import Mock

from backtest.baseline import Baseline
from backtest.metrics import (
    calibration_metrics, report_by_market_and_league, settlement_detail,
    summarize_settlements, validate_probabilities,
)


class BacktestMetricsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = Baseline()

    def detail(self, market='AH', side='home', point=-.25, home=0, away=0,
               odds=2.0, stake=1.0, **extra):
        quote = dict(market=market, side=side, point=point, odds=odds, system='B',
                     league='synthetic-EPL', event_id='synthetic-event', **extra)
        return settlement_detail(quote, home, away, settle_pick=self.baseline.settle_pick, stake_units=stake)

    def prediction(self, probabilities, **kwargs):
        return self.detail(probabilities=dict(zip(('full_win', 'partial_or_push', 'full_loss'), probabilities)), **kwargs)

    def test_ah_minus_quarter_draw_loses_half(self):
        result = self.detail(point=-.25)
        self.assertEqual(result['outcome'], 'half_loss')
        self.assertEqual(result['profit_units'], -.5)
        self.assertEqual(result['calibration_class'], 'partial_or_push')

    def test_ah_plus_quarter_draw_wins_half_of_decimal_odds_profit(self):
        result = self.detail(side='away', point=.25, odds=1.9)
        self.assertEqual(result['outcome'], 'half_win')
        self.assertAlmostEqual(result['profit_units'], .45)
        self.assertEqual(result['calibration_class'], 'partial_or_push')

    def test_ah_minus_three_quarters_one_goal_win_is_half_win(self):
        result = self.detail(point=-.75, home=2, away=1, odds=2.1)
        self.assertEqual(result['outcome'], 'half_win')
        self.assertEqual(result['profit_units'], .55)

    def test_ah_plus_three_quarters_one_goal_loss_is_half_loss(self):
        result = self.detail(point=.75, home=0, away=1)
        self.assertEqual(result['outcome'], 'half_loss')
        self.assertEqual(result['profit_units'], -.5)

    def test_away_handicap_uses_away_score_direction(self):
        result = self.detail(side='away', point=-.75, home=1, away=2, odds=1.8)
        self.assertEqual(result['outcome'], 'half_win')
        self.assertEqual(result['profit_units'], .4)

    def test_integer_handicap_push_returns_full_stake(self):
        result = self.detail(point=-1, home=2, away=1)
        self.assertEqual(result['outcome'], 'push')
        self.assertEqual(result['profit_units'], 0)
        self.assertEqual(result['return_units'], 1)
        self.assertEqual(result['calibration_class'], 'partial_or_push')

    def test_all_ou_quarter_lines_have_independently_expected_partial_settlement(self):
        cases = [(2.25, 2, 'over', -.5, 'half_loss'),
                 (2.25, 2, 'under', .45, 'half_win'),
                 (2.75, 3, 'over', .45, 'half_win'),
                 (2.75, 3, 'under', -.5, 'half_loss'),
                 (3.25, 3, 'over', -.5, 'half_loss'),
                 (3.25, 3, 'under', .45, 'half_win'),
                 (3.75, 4, 'over', .45, 'half_win'),
                 (3.75, 4, 'under', -.5, 'half_loss')]
        for line, total, side, profit, outcome in cases:
            with self.subTest(line=line, side=side):
                detail = self.detail('OU', side, line, home=total, odds=1.9)
                self.assertTrue(detail['eligible'])
                self.assertEqual(detail['outcome'], outcome)
                self.assertEqual(detail['profit_units'], profit)
                self.assertEqual(detail['calibration_class'], 'partial_or_push')

    def test_ou_full_win_full_loss_and_push_are_distinct(self):
        for line, side, expected in ((2.5, 'over', ('full_win', 1)),
                                     (2.5, 'under', ('full_loss', -1)),
                                     (3, 'over', ('push', 0)), (3, 'under', ('push', 0))):
            with self.subTest(line=line, side=side):
                result = self.detail('OU', side, line, home=2, away=1)
                self.assertEqual((result['outcome'], result['profit_units']), expected)

    def test_callback_is_only_formula_and_called_once_with_validated_contract(self):
        callback = Mock(return_value=('ชนะเต็ม', 1., 1))
        detail = settlement_detail({'market': 'AH', 'side': 'home', 'point': -.5, 'odds': 2.},
                                   2, 1, settle_pick=callback)
        callback.assert_called_once_with({'market': 'Asian Handicap', 'side': 'home', 'point': -.5, 'odds': 2.}, 2, 1)
        self.assertTrue(detail['eligible'])

    def test_unknown_or_invalid_results_never_call_settlement_or_count_as_bets(self):
        for home in (None, '2', -1, 1.5, True, float('nan'), float('inf')):
            with self.subTest(home=home):
                callback = Mock()
                detail = settlement_detail({'market': 'AH', 'side': 'home', 'point': 0, 'odds': 2},
                                           home, 0, settle_pick=callback)
                self.assertFalse(detail['eligible'])
                self.assertEqual(detail['reason'], 'unknown_or_invalid_result')
                callback.assert_not_called()
                self.assertEqual(summarize_settlements([detail])['n'], 0)

    def test_malformed_contracts_are_excluded(self):
        for mutation in ({'market': '1X2'}, {'side': 'over'}, {'point': None}, {'point': 2.7},
                         {'point': float('inf')}, {'odds': None}, {'odds': True},
                         {'odds': 1}, {'odds': float('nan')}):
            with self.subTest(mutation=mutation):
                callback = Mock()
                detail = settlement_detail({'market': 'AH', 'side': 'home', 'point': 0, 'odds': 2, **mutation},
                                           1, 0, settle_pick=callback)
                self.assertFalse(detail['eligible'])
                callback.assert_not_called()
        self.assertFalse(self.detail('OU', 'over', -2.5)['eligible'])

    def test_baseline_callback_failure_or_nonfinite_profit_is_excluded(self):
        for callback in (Mock(side_effect=ValueError('test only')), Mock(return_value=None),
                         Mock(return_value=('ชนะเต็ม', float('nan'), 1)),
                         Mock(return_value=('made up', 1, 1))):
            detail = settlement_detail({'market': 'AH', 'side': 'home', 'point': 0, 'odds': 2},
                                       1, 0, settle_pick=callback)
            self.assertFalse(detail['eligible'])

    def test_roi_yield_denominator_includes_push_and_whole_half_bet_stake(self):
        rows = [self.detail(point=-.5, home=1), self.detail(point=-.25), self.detail(point=0)]
        summary = summarize_settlements(rows)
        self.assertEqual(summary['n'], 3)
        self.assertEqual(summary['stake_units'], 3)
        self.assertEqual(summary['profit_units'], .5)
        self.assertAlmostEqual(summary['roi_pct'], 100 / 6)
        self.assertEqual(summary['roi_pct'], summary['yield_pct'])
        self.assertEqual(summary['outcome_counts']['push'], 1)
        self.assertIn('including pushes', summary['denominator'])

    def test_stake_weights_change_profit_not_probability_calibration_weight(self):
        win = self.prediction((.6, .3, .1), point=-.5, home=1, stake=2)
        loss = self.prediction((.6, .3, .1), point=-.5, stake=1)
        push = self.detail(point=0)
        summary = summarize_settlements([win, loss, push])
        self.assertEqual(summary['stake_units'], 4)
        self.assertEqual(summary['profit_units'], 1)
        self.assertEqual(summary['roi_pct'], 25)
        duplicated_weight = calibration_metrics([win, loss])
        equal_stake = [dict(win, stake_units=1., profit_units=1.), loss]
        self.assertEqual(duplicated_weight['brier_multiclass'], calibration_metrics(equal_stake)['brier_multiclass'])

    def test_empty_or_all_unknown_result_metrics_are_none_not_zero_performance(self):
        summary = summarize_settlements([])
        calibration = calibration_metrics([])
        self.assertEqual(summary['status'], 'insufficient')
        self.assertIsNone(summary['roi_pct'])
        self.assertIsNone(summary['yield_pct'])
        self.assertIsNone(summary['expected_profit_units'])
        self.assertIsNone(calibration['brier_multiclass'])
        self.assertIsNone(calibration['log_loss'])
        self.assertIsNone(calibration['ece_macro'])
        self.assertEqual(calibration['n'], 0)

    def test_ev_is_explicit_percentage_units_never_guessed_from_decimal_value(self):
        row = self.detail(point=-.5, home=1, ev_pct=7.5, stake=2)
        summary = summarize_settlements([row])
        self.assertEqual(row['forecast_ev_pct'], 7.5)
        self.assertEqual(row['forecast_ev_unit'], .075)
        self.assertEqual(summary['expected_profit_units'], .15)
        tiny = self.detail(point=-.5, home=1, ev_pct=.05)
        self.assertEqual(tiny['forecast_ev_unit'], .0005)
        self.assertEqual(summarize_settlements([tiny])['expected_profit_units'], .0005)

    def test_ev_none_or_invalid_does_not_create_expected_profit(self):
        for ev in (None, float('nan'), float('inf'), 101, -101):
            row = self.detail(ev_pct=ev)
            summary = summarize_settlements([row])
            self.assertEqual(summary['n'], 1)
            self.assertEqual(summary['ev_n'], 0)
            self.assertIsNone(summary['expected_profit_units'])

    def test_perfect_three_class_predictions_have_zero_losses_including_both_halves(self):
        rows = [self.prediction((1., 0., 0.), point=-.5, home=1),
                self.prediction((0., 0., 1.), point=-.5),
                self.prediction((0., 1., 0.), market='OU', side='under', point=2.25, home=2),
                self.prediction((0., 1., 0.), market='OU', side='over', point=2.25, home=2),
                self.prediction((0., 1., 0.), point=0)]
        result = calibration_metrics(rows)
        self.assertEqual(result['n'], 5)
        self.assertEqual(result['brier_multiclass'], 0)
        self.assertEqual(result['log_loss'], 0)
        self.assertEqual(result['ece_macro'], 0)
        self.assertFalse(result['calibrated'])

    def test_known_multiclass_brier_logloss_and_per_class_ece(self):
        result = calibration_metrics([self.prediction((.6, .3, .1), point=-.5, home=1)])
        self.assertAlmostEqual(result['brier_multiclass'], .26)
        self.assertAlmostEqual(result['log_loss'], -math.log(.6))
        self.assertAlmostEqual(result['ece_by_class']['full_win'], .4)
        self.assertAlmostEqual(result['ece_by_class']['partial_or_push'], .3)
        self.assertAlmostEqual(result['ece_by_class']['full_loss'], .1)
        self.assertAlmostEqual(result['ece_macro'], .8 / 3)

    def test_probability_one_is_in_final_reliability_bin(self):
        result = calibration_metrics([self.prediction((1., 0., 0.), point=-.5, home=1)], bins=10)
        last = result['reliability_bins']['full_win'][-1]
        self.assertEqual(last['n'], 1)
        self.assertEqual(last['mean_probability'], 1)
        self.assertEqual(last['observed_frequency'], 1)
        self.assertTrue(last['upper_inclusive'])
        self.assertEqual(sum(row['n'] for row in result['reliability_bins']['full_win']), 1)

    def test_zero_truth_probability_is_finite_clipped_logloss_and_reported(self):
        result = calibration_metrics([self.prediction((0., 1., 0.), point=-.5, home=1)])
        self.assertEqual(result['zero_probability_truth_n'], 1)
        self.assertAlmostEqual(result['log_loss'], -math.log(1e-15))
        self.assertEqual(result['brier_multiclass'], 2)
        self.assertEqual(result['log_loss_epsilon'], 1e-15)

    def test_baseline_rounded_percentages_are_explicitly_normalized(self):
        row = self.detail(baseline_selection={'p_win': 33.33, 'p_partial': 33.33, 'p_loss': 33.33})
        result = calibration_metrics([row])
        self.assertEqual(result['n'], 1)
        self.assertEqual(result['normalized_rounded_prediction_n'], 1)
        self.assertAlmostEqual(result['brier_multiclass'], 2 / 3)

    def test_invalid_probability_sums_values_or_scale_are_excluded(self):
        for values in ((.8, .3, .1), (-.1, .5, .6), (80, 10, 10),
                       (float('nan'), 0, 1), (float('inf'), 0, 0), (True, 0, 0)):
            with self.subTest(values=values):
                result = calibration_metrics([self.prediction(values)])
                self.assertEqual(result['n'], 0)
                self.assertIsNone(result['log_loss'])
        self.assertFalse(validate_probabilities({'full_win': 1, 'partial_or_push': 0, 'full_loss': 0}, scale='guessed')['valid'])

    def test_a_c_scores_never_become_calibration_probabilities(self):
        a = self.detail()
        a['system'], a['score'] = 'A', 88
        c = self.detail()
        c['system'], c['score'] = 'C', 91
        b_missing = self.detail()
        b_missing['score'] = 99
        result = calibration_metrics([a, c, b_missing])
        self.assertEqual(result['n'], 0)
        self.assertEqual(result['excluded_reasons']['not_system_b'], 2)
        self.assertEqual(result['excluded_reasons']['missing_model_probabilities'], 1)
        self.assertIsNone(result['brier_multiclass'])

    def test_a_c_are_excluded_even_when_a_b_triad_was_attached(self):
        row = self.prediction((.6, .3, .1))
        row['system'] = 'A'
        self.assertEqual(calibration_metrics([row])['n'], 0)
        row['system'] = 'C'
        self.assertEqual(calibration_metrics([row])['n'], 0)

    def test_unavailable_model_probabilities_are_excluded(self):
        row = self.prediction((.6, .3, .1), model_available=False)
        result = calibration_metrics([row])
        self.assertEqual(result['n'], 0)
        self.assertEqual(result['excluded_reasons']['model_unavailable'], 1)

    def test_market_league_cohorts_and_event_counts_do_not_claim_independence(self):
        ah = self.prediction((.6, .3, .1), point=-.5, home=1)
        ou = self.prediction((.6, .3, .1), market='OU', side='over', point=2.5, home=2, away=1)
        la_liga = deepcopy(ah)
        la_liga.update(league='synthetic-LaLiga', event_id='synthetic-other-event')
        report = report_by_market_and_league([ah, ou, la_liga])
        self.assertEqual(report['overall']['settlement']['n'], 3)
        self.assertEqual(report['overall']['settlement']['unique_event_n'], 2)
        self.assertEqual(report['by_market']['Asian Handicap']['settlement']['n'], 2)
        self.assertEqual(report['by_market']['Over/Under']['settlement']['n'], 1)
        self.assertEqual(report['by_league']['synthetic-EPL']['overall']['settlement']['n'], 2)
        self.assertEqual(report['by_league']['synthetic-EPL']['overall']['settlement']['unique_event_n'], 1)
        self.assertIn('not independent', report['overall']['calibration']['correlation_warning'])

    def test_operational_sample_minimum_is_not_a_calibration_claim(self):
        rows = [self.prediction((.6, .3, .1)) for _ in range(3)]
        summary = summarize_settlements(rows, minimum_n_for_review=3)
        result = calibration_metrics(rows, minimum_n_for_review=3)
        self.assertTrue(summary['review_minimum_reached'])
        self.assertTrue(result['review_minimum_reached'])
        self.assertFalse(result['calibrated'])
        self.assertIn('not statistical significance', result['interpretation'])
        self.assertEqual(summarize_settlements(rows)['minimum_n_for_review'], 100)

    def test_tampered_unknown_result_is_excluded_and_report_is_json_finite(self):
        row = self.prediction((.6, .3, .1))
        row['home_goals'] = None
        summary = summarize_settlements([row])
        result = calibration_metrics([row])
        self.assertEqual(summary['n'], 0)
        self.assertEqual(result['n'], 0)
        json.dumps(report_by_market_and_league([row]), allow_nan=False)

    def test_finite_individual_stakes_that_overflow_aggregate_remain_json_safe(self):
        push = self.detail(point=0, stake=1e308)
        self.assertTrue(push['eligible'])
        summary = summarize_settlements([push, push])
        self.assertEqual(summary['n'], 2)
        self.assertEqual(summary['status'], 'numerical_error')
        self.assertTrue(summary['numerical_overflow'])
        self.assertIsNone(summary['stake_units'])
        self.assertIsNone(summary['roi_pct'])
        json.dumps(summary, allow_nan=False)

    def test_invalid_review_or_bin_configuration_fails_clearly(self):
        for minimum in (0, True, 1.5):
            with self.assertRaises(ValueError):
                summarize_settlements([], minimum_n_for_review=minimum)
        for bins in (0, True, 101, 2.5):
            with self.assertRaises(ValueError):
                calibration_metrics([], bins=bins)


if __name__ == '__main__':
    unittest.main()
