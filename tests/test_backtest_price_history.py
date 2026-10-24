"""Synthetic authored price contracts; no live prices, provider, API, or DB."""
import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from backtest.price_history import closing_line_value, select_price_history

UTC = timezone.utc


class BacktestPriceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.kickoff = datetime(2026, 10, 25, 12, tzinfo=UTC)
        self.decision = self.kickoff - timedelta(hours=2)

    def quote(self, quote_id='entry', *, minutes=125, point=2.75, odds=2.1,
              market='Over/Under', side='over', **updates):
        updated = self.kickoff - timedelta(minutes=minutes)
        row = dict(quote_id=quote_id, event_id='synthetic-event', league='EPL', season='2627',
                   home_team='Synthetic Home', away_team='Synthetic Away', kickoff_utc=self.kickoff.isoformat(),
                   market=market, side=side, point=point, odds=odds, bookmaker='Synthetic Book',
                   bookmaker_key='synthetic-book', source_updated_at=updated.isoformat(),
                   received_at=(updated+timedelta(seconds=1)).isoformat(),
                   captured_at=(updated+timedelta(seconds=2)).isoformat(), source='authored-test-fixture',
                   source_payload_sha256=('a' if minutes >= 120 else 'b')*64,
                   quote_kind='observed', opening_verified=False, closing_verified=False)
        row.update(updates)
        return row

    def evidence(self, *, minutes=4):
        return dict(url='https://example.invalid/provider-evidence/fixture', sha256='c'*64,
                    received_at=(self.kickoff-timedelta(minutes=minutes)).isoformat(),
                    provider='authored-test-fixture')

    def context(self, rows, selection=None, **options):
        return select_price_history(rows, selection or self.quote(), self.decision, **options)

    def close(self, rows, entry=None, **options):
        return closing_line_value(entry or self.quote(), rows, self.decision, **options)

    def paired_rows(self, *, actual_closing=False):
        rows = [self.quote(), self.quote('entry-under', side='under', odds=1.9),
                self.quote('close', minutes=5, odds=1.9),
                self.quote('close-under', minutes=5, side='under', odds=2.1)]
        if actual_closing:
            for row in rows[2:]:
                row.update(quote_kind='closing', closing_verified=True, closing_evidence=self.evidence())
        return rows

    def test_first_observed_is_not_opening_without_provider_proof(self):
        first, entry = self.quote('first', minutes=1440), self.quote()
        result = self.context([entry, first])
        self.assertEqual(result['decision_quote']['quote_id'], 'entry')
        self.assertEqual(result['first_observed_quote']['quote_id'], 'first')
        self.assertEqual(result['first_observed_label'], 'First Observed Price')
        self.assertFalse(result['opening_verified'])
        self.assertIsNone(result['opening_quote'])
        self.assertIsNone(result['opening_label'])

    def test_verified_opening_keeps_actual_receipt_separate_from_provider_update(self):
        opened = self.quote('real-opening', minutes=300, quote_kind='opening', opening_verified=True,
                            received_at=(self.decision-timedelta(minutes=30)).isoformat(),
                            captured_at=(self.decision-timedelta(minutes=29)).isoformat(),
                            opening_evidence=self.evidence(minutes=140))
        first = self.quote('observed-earlier', minutes=180)
        result = self.context([first, opened, self.quote()])
        self.assertTrue(result['opening_verified'])
        self.assertEqual(result['opening_label'], 'Opening Odds')
        self.assertEqual(result['opening_quote']['quote_id'], 'real-opening')
        self.assertEqual(result['first_observed_quote']['quote_id'], 'observed-earlier')
        self.assertEqual(result['opening_quote']['received_at'], opened['received_at'])
        self.assertNotEqual(result['opening_quote']['received_at'], opened['source_updated_at'])

    def test_opening_boolean_or_quote_kind_alone_cannot_establish_opening(self):
        for updates in (dict(opening_verified=True), dict(quote_kind='opening'),
                        dict(quote_kind='opening', opening_verified=True)):
            with self.subTest(updates=updates):
                result = self.context([self.quote(**updates)])
                self.assertFalse(result['opening_verified'])
                self.assertIsNone(result['opening_quote'])

    def test_opening_evidence_received_after_decision_is_not_a_historical_feature(self):
        row = self.quote(quote_kind='opening', opening_verified=True, opening_evidence=self.evidence())
        result = self.context([row])
        self.assertFalse(result['opening_verified'])
        self.assertEqual(result['diagnostics']['opening_rejected'], {'evidence_after_cutoff': 1})
        self.assertIsNotNone(result['decision_quote'])

    def test_conflicting_verified_opening_prices_remain_unconfirmed(self):
        opening = self.quote('opening', minutes=180, quote_kind='opening', opening_verified=True,
                             opening_evidence=self.evidence(minutes=140))
        conflict = self.quote('conflicting-opening', minutes=170, odds=2.2, quote_kind='opening',
                              opening_verified=True, opening_evidence=self.evidence(minutes=140))
        result = self.context([opening, conflict, self.quote()])
        self.assertFalse(result['opening_verified'])
        self.assertEqual(result['diagnostics']['opening_rejected'], {'conflicting_verified_opening': 1})

    def test_all_three_quote_times_must_be_available_at_decision(self):
        for field in ('source_updated_at', 'received_at', 'captured_at'):
            with self.subTest(field=field):
                row = self.quote()
                row[field] = (self.decision+timedelta(seconds=1)).isoformat()
                result = self.context([row])
                self.assertIsNone(result['decision_quote'])

    def test_naive_missing_and_invalid_times_never_assume_utc(self):
        for field in ('source_updated_at', 'received_at', 'captured_at', 'kickoff_utc'):
            for value in (None, 'bad', '2026-10-25T09:55:00'):
                with self.subTest(field=field, value=value):
                    row = self.quote()
                    row[field] = value
                    self.assertIsNone(self.context([row])['decision_quote'])
        self.assertIsNone(select_price_history([self.quote()], self.quote(), '2026-10-25T10:00:00')['decision_quote'])

    def test_aware_representations_of_same_instant_are_compatible(self):
        row = self.quote()
        for field in ('source_updated_at', 'received_at', 'captured_at', 'kickoff_utc'):
            row[field] = datetime.fromisoformat(row[field]).astimezone(timezone(timedelta(hours=7))).isoformat()
        self.assertEqual(self.context([row])['status'], 'available')

    def test_bad_timestamp_order_and_post_kickoff_records_are_quarantined(self):
        for changes in (dict(source_updated_at=(self.decision-timedelta(minutes=1)).isoformat()),
                        dict(received_at=self.kickoff.isoformat(), captured_at=self.kickoff.isoformat()),
                        dict(captured_at=(self.kickoff+timedelta(seconds=1)).isoformat())):
            with self.subTest(changes=changes):
                row = self.quote(**changes)
                self.assertIsNone(self.context([row])['decision_quote'])
                self.assertIsNone(self.close([row])['closing_quote'])

    def test_contracts_never_mix_book_team_season_league_market_or_side(self):
        changes = [dict(bookmaker='Other'), dict(bookmaker_key='other-key'), dict(event_id='other-event'),
                   dict(home_team='Other Home'), dict(away_team='Other Away'), dict(season='2526'),
                   dict(league='Bundesliga'), dict(side='under'), dict(market='Asian Handicap', side='home')]
        for update in changes:
            with self.subTest(update=update):
                self.assertIsNone(self.context([self.quote(**update)])['decision_quote'])
                self.assertIsNone(self.close([self.quote(minutes=5, **update)])['closing_quote'])

    def test_all_tested_quarter_half_and_integer_lines_retain_exact_actual_line(self):
        for point in (2.5, 2.75, 3.0, 3.25, 3.5):
            with self.subTest(point=point):
                row = self.quote(point=point)
                self.assertEqual(self.context([row], selection=row)['decision_quote']['point'], point)
        for point in (2.6, True, float('nan'), float('inf'), -2.5):
            self.assertIsNone(self.context([self.quote(point=point)])['decision_quote'])

    def test_latest_snapshot_cannot_resurrect_a_removed_selected_line(self):
        first = self.quote(point=2.5)
        newer = self.quote('newer-line', minutes=122, point=3.0)
        result = self.context([first, newer], selection=first)
        self.assertIsNone(result['decision_quote'])
        self.assertEqual(result['reason'], 'selected_line_absent_latest_snapshot')
        self.assertEqual(result['first_observed_quote']['point'], 2.5)

    def test_alternative_lines_need_explicit_selection_not_highest_odds(self):
        rows = [self.quote(point=2.5, odds=1.8), self.quote('alternative', point=3.0, odds=2.9)]
        selection = self.quote()
        selection.pop('point')
        self.assertIsNone(self.context(rows, selection=selection)['decision_quote'])
        self.assertEqual(self.context(rows, selection=rows[0])['decision_quote']['odds'], 1.8)

    def test_decisions_at_or_after_kickoff_and_unapproved_max_age_fail_closed(self):
        for decision in (self.kickoff, self.kickoff+timedelta(seconds=1)):
            self.assertIsNone(select_price_history([self.quote()], self.quote(), decision)['decision_quote'])
        for limit in (0, -1, True, 901, '900'):
            self.assertIsNone(self.context([self.quote()], max_age_seconds=limit)['decision_quote'])
            self.assertIsNone(self.close([self.quote(minutes=5)], max_close_age_seconds=limit)['closing_quote'])

    def test_stale_current_quote_is_unavailable_but_first_observation_survives(self):
        row = self.quote(minutes=150)
        result = self.context([row])
        self.assertEqual(result['reason'], 'stale_decision_quote')
        self.assertIsNone(result['decision_quote'])
        self.assertEqual(result['first_observed_quote']['quote_id'], row['quote_id'])

    def test_same_line_decimal_clv_has_explicit_formula_and_report_only_use(self):
        result = self.close(self.paired_rows())
        self.assertAlmostEqual(result['clv_decimal'], 2.1/1.9-1)
        self.assertEqual(result['closing_label'], 'last_observed_pre_kickoff')
        self.assertFalse(result['closing_verified'])
        self.assertEqual(result['feature_use'], 'outcome_report_only')
        self.assertIn('identical line only', result['clv_definition'])

    def test_changed_ah_lines_show_selected_team_favorability_without_equivalent_clv(self):
        for side, entry_point, close_point, delta in (('home', -.25, -.5, -.25), ('away', .25, .5, .25)):
            with self.subTest(side=side):
                entry = self.quote(market='Asian Handicap', side=side, point=entry_point)
                close = self.quote('closing', minutes=5, market='Asian Handicap', side=side, point=close_point)
                result = self.close([close], entry=entry)
                self.assertIsNone(result['clv_decimal'])
                self.assertIsNone(result['no_vig'])
                self.assertEqual(result['line_change']['closing_line_favorability_delta'], delta)
                self.assertEqual(result['line_change']['entry_line_advantage'], -delta)

    def test_changed_ou_line_favorability_respects_over_and_under_orientation(self):
        for side, favorable in (('over', -.25), ('under', .25)):
            entry = self.quote(side=side, point=2.5)
            result = self.close([self.quote('closing', minutes=5, side=side, point=2.75)], entry=entry)
            self.assertEqual(result['line_change']['point_delta'], .25)
            self.assertEqual(result['line_change']['closing_line_favorability_delta'], favorable)
            self.assertIsNone(result['clv_decimal'])
            self.assertEqual(result['reason'], 'line_changed_no_equivalent_odds_clv')

    def test_no_vig_uses_both_prices_from_each_matching_snapshot_with_caveat(self):
        result = self.close(self.paired_rows())
        no_vig = result['no_vig']
        self.assertAlmostEqual(no_vig['entry_implied_share'], (1/2.1)/(1/2.1+1/1.9))
        self.assertAlmostEqual(no_vig['closing_implied_share'], (1/1.9)/(1/1.9+1/2.1))
        self.assertIn('not win probabilities or model EV', no_vig['definition'])
        self.assertIn('quarter', no_vig['definition'])

    def test_ah_pair_requires_opposite_side_and_opposite_signed_quarter_line(self):
        rows = [self.quote(market='Asian Handicap', side='home', point=-.25),
                self.quote('entry-away', market='Asian Handicap', side='away', point=.25, odds=1.9),
                self.quote('close-home', minutes=5, market='Asian Handicap', side='home', point=-.25, odds=1.9),
                self.quote('close-away', minutes=5, market='Asian Handicap', side='away', point=.25, odds=2.1)]
        self.assertIsNotNone(self.close(rows, entry=rows[0])['no_vig'])
        rows[-1]['point'] = -.25
        self.assertIsNone(self.close(rows, entry=rows[0])['no_vig'])

    def test_unpaired_quotes_can_report_observed_decimal_clv_but_not_actual_closing_or_no_vig(self):
        result = self.close([self.quote('closing', minutes=5, odds=1.9)])
        self.assertIsNotNone(result['clv_decimal'])
        self.assertIsNone(result['no_vig'])
        self.assertFalse(result['closing_verified'])

    def test_actual_closing_requires_provider_proof_for_both_compatible_sides(self):
        rows = self.paired_rows(actual_closing=True)
        result = self.close(rows)
        self.assertTrue(result['closing_verified'])
        self.assertEqual(result['closing_label'], 'verified_closing')
        self.assertIsNotNone(result['no_vig'])
        for row in rows[2:]:
            self.assertLess(datetime.fromisoformat(row['closing_evidence']['received_at']), self.kickoff)

    def test_single_verified_side_or_postkickoff_evidence_does_not_prove_actual_closing(self):
        for case in ('single', 'late-proof', 'absent-counterpart'):
            rows = self.paired_rows(actual_closing=True)
            if case == 'single':
                rows[-1]['closing_verified'] = False
            elif case == 'late-proof':
                rows[-1]['closing_evidence']['received_at'] = (self.kickoff+timedelta(seconds=1)).isoformat()
            else:
                rows.pop()
            result = self.close(rows)
            self.assertFalse(result['closing_verified'])
            self.assertEqual(result['closing_label'], 'last_observed_pre_kickoff')

    def test_no_vig_never_pairs_different_book_line_or_snapshot(self):
        for changes in (dict(bookmaker_key='another'), dict(point=3.0),
                        dict(captured_at=(self.kickoff-timedelta(minutes=4)).isoformat()),
                        dict(source_payload_sha256='d'*64)):
            with self.subTest(changes=changes):
                rows = self.paired_rows()
                rows[-1].update(changes)
                self.assertIsNone(self.close(rows)['no_vig'])

    def test_explicit_snapshot_group_allows_only_coherent_times_and_source(self):
        rows = self.paired_rows()
        for index, row in enumerate(rows):
            row['snapshot_group_id'] = 'entry-batch' if index < 2 else 'closing-batch'
        for row in (rows[1], rows[3]):
            for field in ('source_updated_at', 'received_at', 'captured_at'):
                row[field] = (datetime.fromisoformat(row[field])+timedelta(seconds=5)).isoformat()
            row['source_payload_sha256'] = 'e'*64
        self.assertIsNotNone(self.close(rows, entry=rows[0])['no_vig'])
        rows[-1]['captured_at'] = (datetime.fromisoformat(rows[-1]['captured_at'])+timedelta(seconds=40)).isoformat()
        self.assertIsNone(self.close(rows, entry=rows[0])['no_vig'])

    def test_explicit_group_conflict_cannot_be_hidden_by_identical_timestamps(self):
        rows = self.paired_rows()
        rows[2]['snapshot_group_id'], rows[3]['snapshot_group_id'] = 'one', 'two'
        self.assertIsNone(self.close(rows)['no_vig'])

    def test_duplicate_ids_or_conflicting_same_snapshot_prices_are_quarantined(self):
        first = self.quote()
        conflict = self.quote(odds=2.2)
        self.assertIsNone(self.context([first, conflict])['decision_quote'])
        conflict['quote_id'] = 'different-id'
        self.assertIsNone(self.context([first, conflict])['decision_quote'])
        result = self.context([first, copy.deepcopy(first)])
        self.assertEqual(result['decision_quote']['quote_id'], first['quote_id'])
        self.assertEqual(result['diagnostics']['rejected']['identical_duplicate'], 1)

    def test_last_snapshot_old_source_update_does_not_fall_back_to_earlier_prices(self):
        close = self.quote('last', minutes=60,
            received_at=(self.kickoff-timedelta(minutes=5)).isoformat(),
            captured_at=(self.kickoff-timedelta(minutes=4)).isoformat())
        result = self.close([self.quote('earlier', minutes=10), close])
        self.assertIsNone(result['closing_quote'])
        self.assertEqual(result['reason'], 'closing_observation_too_old')

    def test_closing_data_is_never_returned_as_decision_features_even_when_timestamp_is_earlier(self):
        row = self.quote(quote_kind='closing', closing_verified=True,
                         closing_evidence=self.evidence(minutes=126))
        result = self.context([row])
        self.assertIsNone(result['decision_quote'])
        self.assertEqual(result['diagnostics']['rejected']['closing_outcome_only'], 1)

    def test_pre_kickoff_close_received_after_decision_is_outcome_only(self):
        rows = self.paired_rows()
        result = self.context(rows)
        self.assertEqual(result['decision_quote']['quote_id'], 'entry')
        self.assertEqual(result['diagnostics']['rejected']['unavailable_at_decision'], 1)
        self.assertEqual(self.close(rows)['closing_quote']['quote_id'], 'close')

    def test_missing_source_checksum_produces_no_verified_price_or_clv(self):
        for field, value in (('source', ''), ('source_payload_sha256', None), ('source_payload_sha256', 'bad')):
            with self.subTest(field=field):
                row = self.quote(**{field: value})
                self.assertIsNone(self.context([row])['decision_quote'])
                self.assertIsNone(self.close([self.quote('close', minutes=5)], entry=row)['clv_decimal'])

    def test_reports_are_json_serializable_and_inputs_are_unchanged(self):
        rows = self.paired_rows(actual_closing=True)
        for row in rows:
            for field in ('source_updated_at', 'received_at', 'captured_at', 'kickoff_utc'):
                row[field] = datetime.fromisoformat(row[field])
        original = copy.deepcopy(rows)
        context = self.context(rows, selection=rows[0])
        closing = self.close(rows, entry=rows[0])
        json.dumps(context, allow_nan=False)
        json.dumps(closing, allow_nan=False)
        self.assertEqual(rows, original)

    def test_arbitrary_secret_fields_and_unsafe_evidence_urls_are_not_copied_to_reports(self):
        row = self.quote(api_key='DO_NOT_PRINT', headers={'x-apisports-key': 'DO_NOT_PRINT'},
                         quote_kind='opening', opening_verified=True,
                         opening_evidence=dict(url='https://example.invalid/path?api_key=DO_NOT_PRINT',
                                               sha256='f'*64, received_at=self.decision.isoformat()))
        report = self.context([row])
        self.assertNotIn('DO_NOT_PRINT', json.dumps(report))
        self.assertFalse(report['opening_verified'])

    def test_closing_snapshot_cannot_precede_the_original_entry(self):
        entry = self.quote('late-entry', minutes=124)
        close = self.quote('before-entry', minutes=125)
        result = self.close([close], entry=entry)
        # This old quote also fails the final15-minute closing evidence window.
        self.assertIsNone(result['clv_decimal'])

    def test_later_correction_cannot_retroactively_remove_an_entry_quote(self):
        entry = self.quote()
        # A later observation reuses the identifier with a changed quote. It
        # was unknown at this decision and cannot influence duplicate checks.
        later = self.quote(minutes=119, odds=2.4)
        result = self.context([entry, later])
        self.assertEqual(result['decision_quote']['odds'], entry['odds'])
        self.assertEqual(result['diagnostics']['rejected']['unavailable_at_decision'], 1)
        self.assertNotIn('conflicting_quote_id', result['diagnostics']['rejected'])

    def test_closing_record_with_reused_identifier_cannot_poison_decision_evidence(self):
        entry = self.quote()
        outcome = self.quote(quote_kind='closing', odds=2.4)
        result = self.context([entry, outcome])
        self.assertEqual(result['decision_quote']['odds'], entry['odds'])
        self.assertEqual(result['diagnostics']['rejected']['closing_outcome_only'], 1)

    def test_unrelated_contract_cannot_change_selected_contract_duplicate_checks(self):
        entry = self.quote()
        unrelated = self.quote(bookmaker='Another Book', bookmaker_key='another', odds=2.4)
        result = self.context([entry, unrelated])
        self.assertEqual(result['decision_quote']['odds'], entry['odds'])
        self.assertEqual(result['diagnostics']['rejected']['contract_mismatch'], 1)


if __name__ == '__main__':
    unittest.main()
