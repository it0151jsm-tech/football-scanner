"""Protocol is frozen before a holdout; it cannot tune the original baseline."""
from copy import deepcopy
import unittest

from backtest.protocol import canonical_sha256, default_protocol, utc, validate_protocol


class BacktestProtocolTests(unittest.TestCase):
    def setUp(self):
        self.protocol = default_protocol('model', frozen_at='2026-10-24T12:00:00+00:00')

    def test_default_descriptive_protocol_is_reproducible(self):
        self.assertIs(validate_protocol(self.protocol,'model'),self.protocol)
        self.assertIsNone(self.protocol['holdout'])
        self.assertEqual(canonical_sha256(self.protocol),canonical_sha256(deepcopy(self.protocol)))

    def test_original_strategy_and_quote_age_cannot_be_relaxed(self):
        for key,value in [('quote_max_age_seconds',3600),('unit_stake',2),('strategies',{'A':'best_roi'}),
                          ('selection_rule','highest_odds'),('model_version','other')]:
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol,**{key:value}),'model')

    def test_bad_review_policies_fail_safely(self):
        for policy in (None,[],{'minimum_sample_count':True},{'minimum_sample_count':0}):
            with self.subTest(policy=policy),self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol,review_policy=policy),'model')

    def test_league_scope_is_explicit_and_unique(self):
        for leagues in ([],['soccer_uefa_champs_league'],[{}],['soccer_epl','soccer_epl']):
            with self.subTest(leagues=leagues),self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol,leagues=leagues),'model')

    def test_future_holdout_requires_both_training_and_freeze_before_start(self):
        holdout = {'training_end':'2026-10-23T00:00:00Z','start':'2026-11-01T00:00:00Z','end':'2026-12-01T00:00:00Z'}
        self.assertEqual(validate_protocol(dict(self.protocol,holdout=holdout),'model')['holdout'],holdout)
        for changes in ({'start':'2026-10-01T00:00:00Z'},{'training_end':holdout['start']},
                        {'end':'2026-10-01T00:00:00Z'},{'start':'2026-11-01'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol,holdout=dict(holdout,**changes)),'model')

    def test_naive_or_invalid_freeze_and_bins_rejected(self):
        for key,value in [('schema_version',True),('protocol_frozen_at','2026-10-24'),
                          ('calibration_bins',True),('calibration_bins',1),('calibration_bins',21)]:
            with self.subTest(key=key,value=value),self.assertRaises(ValueError):
                validate_protocol(dict(self.protocol,**{key:value}),'model')
        self.assertIsNone(utc('not-a-date'))

    def test_hash_rejects_nonfinite_instead_of_serializing_nan(self):
        with self.assertRaises(ValueError):
            canonical_sha256({'roi':float('nan')})
