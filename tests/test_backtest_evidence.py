"""Synthetic archive fixtures exercise provenance gates, never real performance."""
from copy import deepcopy
import hashlib
import json
import unittest

from backtest.evidence import Archive

DECISION = '2020-10-20T12:00:00+00:00'
KICKOFF = '2020-10-20T15:00:00+00:00'
REPORT = '2020-10-21T12:00:00+00:00'


class BacktestEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.document = {
            'schema_version': 1, 'data_mode': 'synthetic_test',
            'source_catalog': {'fixture': {'rights_status': 'approved',
                'reviewed_by': 'test author', 'reviewed_at': '2020-10-01T00:00:00+00:00',
                'rights_evidence_url': 'https://example.test/fixture-license'}},
            'evidence_payloads': {}, 'events': [], 'decisions': [],
            'score_history': [], 'xg_history': [], 'quotes': [], 'results': []}
        self.event = self.bound(dict(event_id='fixture-target', league='soccer_epl',
            season='2021', period='FT90', home_team='Synthetic Home', away_team='Synthetic Away',
            kickoff_utc=KICKOFF, received_at='2020-10-19T12:00:00+00:00'))

    def bound(self, row):
        row = deepcopy(row)
        row.pop('source_payload_sha256', None)
        row.setdefault('source', 'fixture')
        row.setdefault('data_mode', 'synthetic_test')
        body = json.dumps(row, sort_keys=True, separators=(',', ':'))
        digest = hashlib.sha256(body.encode()).hexdigest()
        self.document['evidence_payloads'][digest] = {
            'body': body, 'source': row['source'], 'received_at': row.get('received_at')}
        return dict(row, source_payload_sha256=digest)

    def archive(self):
        return Archive(self.document, allow_test_fixtures=True)

    def history(self, **changes):
        row = dict(match_id='fixture-history', league='soccer_epl', season='2021',
            home_team='Synthetic Home', away_team='Synthetic Away', period='FT90', status='FT',
            home_goals=2, away_goals=1, match_time_utc='2020-10-10T15:00:00+00:00',
            received_at='2020-10-11T12:00:00+00:00', xg_home=1.7, xg_away=1.1)
        row.update(changes)
        return self.bound(row)

    def quote(self, **changes):
        row = {key: value for key, value in self.event.items()
               if key not in ('source_payload_sha256', 'received_at')}
        row.update(quote_id='fixture-quote', market='Over/Under', side='over', point=2.75,
            odds=1.95, bookmaker='Fixture Book', bookmaker_key='fixture-book',
            source_updated_at='2020-10-20T11:58:00+00:00',
            received_at='2020-10-20T11:59:00+00:00', captured_at=DECISION)
        row.update(changes)
        return self.bound(row)

    def result(self, **changes):
        row = {key: value for key, value in self.event.items()
               if key not in ('source_payload_sha256', 'received_at')}
        row.update(status='FT', home_goals=2, away_goals=1,
                   received_at='2020-10-20T18:00:00+00:00')
        row.update(changes)
        return self.bound(row)

    def test_synthetic_fixtures_require_explicit_test_permission(self):
        self.assertEqual(Archive(self.document).proof(self.event), 'research_or_unverified_availability')
        self.assertIsNone(self.archive().proof(self.event))

    def test_research_cannot_be_promoted_with_test_flag(self):
        self.document['data_mode'] = 'research'
        self.assertEqual(self.archive().proof(self.event), 'research_or_unverified_availability')

    def test_observed_archive_does_not_accept_mixed_test_row(self):
        self.document['data_mode'] = 'observed'
        self.assertEqual(self.archive().proof(self.event), 'mixed_or_mislabeled_data_mode')

    def test_source_rights_and_review_evidence_required(self):
        for patch, reason in [({'rights_status': 'unknown'}, 'source_rights_not_reviewed'),
                              ({'reviewed_by': ''}, 'source_rights_not_reviewed'),
                              ({'rights_evidence_url': ''}, 'source_rights_evidence_missing'),
                              ({'reviewed_at': '2020-01-01'}, 'source_rights_evidence_missing')]:
            with self.subTest(patch=patch):
                document = deepcopy(self.document)
                document['source_catalog']['fixture'].update(patch)
                self.assertEqual(Archive(document, allow_test_fixtures=True).proof(self.event), reason)

    def test_checksum_and_observation_binding_reject_tampering(self):
        archive = self.archive()
        changed = dict(self.event, home_team='Other Team')
        self.assertEqual(archive.proof(changed), 'source_observation_not_bound_to_evidence')
        self.document['evidence_payloads'][self.event['source_payload_sha256']]['body'] += ' '
        self.assertEqual(archive.proof(self.event), 'source_payload_checksum_mismatch')

    def test_source_and_digest_bad_types_do_not_crash(self):
        self.assertEqual(self.archive().proof(dict(self.event, source=[])), 'source_rights_not_reviewed')
        self.assertEqual(self.archive().proof(dict(self.event, source_payload_sha256=[])), 'source_payload_missing')

    def test_receipt_must_have_explicit_timezone(self):
        event = self.bound(dict(self.event, received_at='2020-10-19T12:00:00'))
        self.assertEqual(self.archive().proof(event), 'source_receipt_unverified')

    def test_credentials_in_fields_and_urls_rejected_without_echo(self):
        for change in ({'api_key': 'never-echo-me'},
                       {'source_url': 'https://example.test/data?apiKey=never-echo-me'},
                       {'source_url': 'https://user:never-echo-me@example.test/data'},
                       {'source_url': 'https://['}):
            with self.subTest(keys=list(change)):
                document = dict(self.document, export_metadata=change)
                with self.assertRaisesRegex(ValueError, '^credential_fields_or_urls_present_export_must_be_redacted$'):
                    Archive(document)

    def test_valid_fixture_and_quarter_quote_pass(self):
        self.assertIsNone(self.archive().event_reason(self.event, DECISION))
        self.assertIsNone(self.archive().quote_reason(self.quote(), self.event, DECISION))

    def test_event_not_known_before_decision_or_with_wrong_season_rejected(self):
        late = self.bound(dict(self.event, received_at='2020-10-20T13:00:00+00:00'))
        wrong = self.bound(dict(self.event, season='1920'))
        self.assertEqual(self.archive().event_reason(late, DECISION), 'event_not_known_before_decision_or_kickoff')
        self.assertEqual(self.archive().event_reason(wrong, DECISION), 'event_league_or_season_unverified')
        self.assertEqual(self.archive().event_reason(self.event, KICKOFF), 'event_not_known_before_decision_or_kickoff')

    def test_same_reviewed_alias_cannot_play_itself(self):
        event = self.bound(dict(self.event, home_team='Manchester City', away_team='Man City'))
        self.assertEqual(self.archive().event_reason(event, DECISION), 'event_identity_invalid')

    def test_quote_identity_timestamps_and_staleness_fail_closed(self):
        mutations = [({'event_id': 'other'}, 'quote_fixture_identity_mismatch'),
            ({'source_updated_at': None}, 'quote_clocks_unverified_or_after_cutoff'),
            ({'source_updated_at': '2020-10-20T12:01:00+00:00'}, 'quote_clocks_unverified_or_after_cutoff'),
            ({'received_at': '2020-10-20T12:01:00+00:00'}, 'quote_clocks_unverified_or_after_cutoff'),
            ({'captured_at': KICKOFF}, 'quote_clocks_unverified_or_after_cutoff'),
            ({'source_updated_at': '2020-10-20T11:44:59+00:00'}, 'stale_decision_price')]
        for changes, reason in mutations:
            with self.subTest(changes=changes):
                self.assertEqual(self.archive().quote_reason(self.quote(**changes), self.event, DECISION), reason)

    def test_invalid_quotes_never_enter_baseline(self):
        for changes in ({'point': 2.7}, {'point': -2.5}, {'point': True}, {'point': 1e308},
                        {'odds': float('inf')}, {'odds': 10**400}, {'odds': 1}, {'odds': True}):
            with self.subTest(changes=changes):
                self.assertEqual(self.archive().quote_reason(self.quote(**changes), self.event, DECISION), 'invalid_market_contract')

    def test_valid_scores_and_real_xg_remain_distinct(self):
        no_xg = self.history(xg_home=None, xg_away=None)
        archive = self.archive()
        frame = archive.history_frame([no_xg], self.event, DECISION)
        self.assertEqual(len(frame), 1)
        self.assertTrue(frame['xG_home'].isna().all())
        self.assertTrue(archive.history_frame([no_xg], self.event, DECISION, xg=True).empty)
        self.assertEqual(archive.history_exclusions['real_xg_missing_or_invalid'], 1)
        self.assertEqual(len(archive.history_frame([self.history()], self.event, DECISION, xg=True)), 1)

    def test_history_before_match_receipt_rejected(self):
        archive = self.archive()
        row = self.history(received_at='2020-10-09T15:00:00+00:00')
        self.assertTrue(archive.history_frame([row], self.event, DECISION).empty)
        self.assertEqual(archive.history_exclusions['history_received_before_match'], 1)

    def test_late_receipt_target_and_wrong_league_are_not_features(self):
        archive = self.archive()
        rows = [self.history(received_at='2020-10-21T00:00:00+00:00'),
                self.history(match_id=self.event['event_id']),
                self.history(league='soccer_france_ligue_one')]
        self.assertTrue(archive.history_frame(rows, self.event, DECISION).empty)
        self.assertEqual(archive.history_exclusions['history_late_receipt_or_wrong_league'], 2)
        self.assertEqual(archive.history_exclusions['target_result_is_not_a_feature'], 1)

    def test_invalid_explicit_time_never_falls_back_to_source_date(self):
        for when in ('not-a-date', '2020-10-10T15:00:00', ''):
            with self.subTest(when=when):
                archive = self.archive()
                row = self.history(match_time_utc=when, source_date='2020-10-10')
                self.assertTrue(archive.history_frame([row], self.event, DECISION).empty)
                self.assertEqual(archive.history_exclusions['history_time_unverified'], 1)

    def test_date_only_has_conservative_bound_without_invented_kickoff(self):
        archive = self.archive()
        old = self.history(match_time_utc=None, source_date='2020-10-10')
        recent = self.history(match_time_utc=None, source_date='2020-10-19')
        frame = archive.history_frame([old, recent], self.event, DECISION)
        self.assertEqual(len(frame), 1)
        self.assertEqual(str(frame.iloc[0]['Date_dt'].date()), '2020-10-10')
        self.assertEqual(archive.history_exclusions['history_after_decision_or_unknown_calendar_bound'], 1)
        self.assertNotIn('match_time_utc', frame.columns)

    def test_invalid_date_season_status_and_scores_quarantined(self):
        for changes in ({'match_time_utc': None, 'source_date': '2020-02-30'},
                        {'season': '1920'}, {'status': 'HT'}, {'home_goals': None},
                        {'away_goals': True}, {'home_goals': -1}, {'home_goals': 2.5}):
            with self.subTest(changes=changes):
                self.assertTrue(self.archive().history_frame([self.history(**changes)], self.event, DECISION).empty)

    def test_exact_and_reviewed_alias_duplicates_count_once(self):
        first = self.history(home_team='Manchester City', away_team='Manchester United')
        second = self.history(match_id='other-provider-id', home_team='Man City', away_team='Man United')
        archive = self.archive()
        frame = archive.history_frame([first, second, first], self.event, DECISION)
        self.assertEqual(len(frame), 1)
        self.assertEqual(archive.history_exclusions['duplicate_history_rows'], 2)

    def test_conflicting_scores_and_xg_reject_all_ambiguous_rows(self):
        first = self.history(home_team='Manchester City', away_team='Manchester United')
        second = self.history(match_id='other-provider-id', home_team='Man City', away_team='Man United', home_goals=3)
        self.assertTrue(self.archive().history_frame([first, second], self.event, DECISION).empty)
        first, second = self.history(), self.history(xg_home=2.1)
        self.assertTrue(self.archive().history_frame([first, second], self.event, DECISION, xg=True).empty)

    def test_match_id_reused_across_different_days_is_ambiguous(self):
        first = self.history()
        second = self.history(match_time_utc='2020-10-09T15:00:00+00:00')
        self.assertTrue(self.archive().history_frame([first, second], self.event, DECISION).empty)

    def test_future_correction_does_not_change_earlier_asof_history(self):
        original = self.history()
        correction = self.history(home_goals=9, received_at='2020-10-21T00:00:00+00:00')
        frame = self.archive().history_frame([original, correction], self.event, DECISION)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]['FTHG'], 2)

    def test_missing_result_and_future_result_stay_pending(self):
        self.assertEqual(self.archive().result_for(self.event, REPORT), (None, 'result_not_available'))
        self.document['results'] = [self.result(received_at='2020-10-22T00:00:00+00:00')]
        self.assertEqual(self.archive().result_for(self.event, REPORT), (None, 'result_not_available'))

    def test_identical_results_deduplicate_but_conflicts_stop_settlement(self):
        first, second = self.result(), self.result(received_at='2020-10-20T19:00:00+00:00')
        self.document['results'] = [first, second]
        row, reason = self.archive().result_for(self.event, REPORT)
        self.assertEqual(row, first)
        self.assertIsNone(reason)
        self.document['results'].append(self.result(home_goals=3))
        self.assertEqual(self.archive().result_for(self.event, REPORT), (None, 'ambiguous_conflicting_result'))

    def test_future_conflicting_result_ignored_until_its_actual_receipt(self):
        first = self.result()
        second = self.result(home_goals=3, received_at='2020-10-22T00:00:00+00:00')
        self.document['results'] = [first, second]
        self.assertEqual(self.archive().result_for(self.event, REPORT), (first, None))
        self.assertEqual(self.archive().result_for(self.event, '2020-10-23T00:00:00Z'), (None, 'ambiguous_conflicting_result'))

    def test_result_identity_or_invalid_final_state_fail_closed(self):
        for changes, reason in [({'away_team': 'Other'}, 'result_identity_or_evidence_unverified'),
                                ({'status': 'HT'}, 'result_not_final_or_clock_invalid'),
                                ({'received_at': KICKOFF}, 'result_not_final_or_clock_invalid'),
                                ({'home_goals': True}, 'result_not_final_or_clock_invalid')]:
            with self.subTest(changes=changes):
                self.document['results'] = [self.result(**changes)]
                self.assertEqual(self.archive().result_for(self.event, REPORT), (None, reason))

    def test_full_time_90_minute_period_must_be_evidenced(self):
        for period in (None, 'ET', '1H'):
            with self.subTest(period=period):
                event = self.bound(dict(self.event, period=period))
                archive = self.archive()
                self.assertEqual(archive.event_reason(event, DECISION), 'unsupported_or_unverified_period')
                quote = self.quote(period=period)
                self.assertEqual(archive.quote_reason(quote, self.event, DECISION), 'unsupported_or_unverified_period')
                row = self.history(period=period)
                self.assertTrue(archive.history_frame([row], self.event, DECISION).empty)
                self.document['results'] = [self.result(period=period)]
                self.assertEqual(self.archive().result_for(self.event, REPORT), (None, 'unsupported_or_unverified_period'))

    def test_invalid_report_clock_cannot_resolve_outcomes(self):
        self.document['results'] = [self.result()]
        self.assertEqual(self.archive().result_for(self.event, '2020-10-21'), (None, 'result_report_clock_unverified'))


if __name__ == '__main__':
    unittest.main()
