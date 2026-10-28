"""Synthetic Understat transport fixtures; no HTTP, API keys or invented model output."""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import unittest
from unittest.mock import Mock

import pandas as pd

from understat_history import (UnderstatUnavailable, eligible_xg_history,
                               load_understat_history, source_status_message)

NOW = pd.Timestamp('2026-10-23T12:00:00+00:00')


def match(**changes):
    item = dict(id='1001', isResult=True, datetime='2026-10-15 18:00:00',
                h={'id': '1', 'title': 'Synthetic Home'}, a={'id': '2', 'title': 'Synthetic Away'},
                goals={'h': '2', 'a': '1'}, xG={'h': '1.25', 'a': '0.75'})
    item.update(changes)
    return item


class Response:
    def __init__(self, items=None, status=200, content=None, payload=None, url=None):
        self.status_code, self.url = status, url
        self.content = (json.dumps(payload if payload is not None else {'dates': items or []}).encode('utf-8')
                        if content is None else content)


def annotated(day='2026-10-15', quality='date_only_timezone_unknown', received='2026-10-23T11:59:59+00:00', match_utc=None):
    return pd.DataFrame([dict(Date_dt=pd.Timestamp(day), HomeTeam='H', AwayTeam='A', FTHG=2, FTAG=1,
                              xG_home=1.25, xG_away=.75, _xg_time_quality=quality,
                              _xg_match_utc=match_utc, _history_received_at=received)])


class UnderstatLoaderTests(unittest.TestCase):
    def load(self, items=None, *, response=None, seasons=(2026,), league='EPL', get=None):
        self.get = get or Mock(return_value=response or Response(items or [match()]))
        return load_understat_history('soccer_epl', league, seasons, self.get, now=NOW)

    def test_explicit_completed_match_preserves_real_xg_without_goal_substitution(self):
        frame = self.load()
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0].FTHG, 2)
        self.assertEqual(frame.iloc[0].xG_home, 1.25)
        self.assertEqual(frame.iloc[0].xG_away, .75)
        self.assertEqual(frame.iloc[0]._history_season, '2627')
        self.assertEqual(frame.attrs['xg_diagnostics']['status'], 'ok')
        self.assertEqual(frame.attrs['history_diagnostics'], frame.attrs['xg_diagnostics'])
        self.get.assert_called_once()
        self.assertIn('/getLeagueData/EPL/2026', self.get.call_args.args[0])
        self.assertEqual(self.get.call_args.kwargs['timeout'], (3, 8))

    def test_provenance_hash_url_and_receipt_are_actual_download_evidence(self):
        source = Response([match()])
        before = datetime.now(timezone.utc)
        frame = self.load(response=source)
        after = datetime.now(timezone.utc)
        report = frame.attrs['xg_diagnostics']['sources'][0]
        self.assertEqual(report['payload_sha256'], hashlib.sha256(source.content).hexdigest())
        self.assertEqual(report['http_status'], 200)
        self.assertEqual(report['season'], 2026)
        self.assertEqual(report['url'], 'https://understat.com/getLeagueData/EPL/2026')
        received = datetime.fromisoformat(report['received_at'])
        self.assertLessEqual(before, received)
        self.assertLessEqual(received, after)
        self.assertEqual(frame.iloc[0]._history_received_at, report['received_at'])
        self.assertEqual(frame.iloc[0]._history_payload_sha256, report['payload_sha256'])
        self.assertEqual(frame.iloc[0]._xg_data_mode, 'research_not_observed')

    def test_naive_datetime_is_calendar_only_without_fabricated_utc_time(self):
        frame = self.load()
        row = frame.iloc[0]
        self.assertEqual(row.Date_dt, pd.Timestamp('2026-10-15'))
        self.assertEqual(row._xg_time_quality, 'date_only_timezone_unknown')
        self.assertEqual(row._xg_timezone_status, 'unspecified_source_timezone')
        self.assertIsNone(row._xg_match_utc)
        self.assertEqual(row._xg_source_datetime, '2026-10-15 18:00:00')

    def test_explicit_offset_is_converted_from_evidence_not_league_country(self):
        frame = self.load([match(datetime='2026-10-15T23:00:00+07:00')])
        row = frame.iloc[0]
        self.assertEqual(row.Date_dt, pd.Timestamp('2026-10-15T16:00:00'))
        self.assertEqual(row._xg_match_utc, '2026-10-15T16:00:00+00:00')
        self.assertEqual(row._xg_time_quality, 'aware_timestamp')

    def test_missing_false_numeric_and_string_isresult_never_become_results(self):
        for flag in (None, False, 1, 'true'):
            with self.subTest(flag=flag):
                item = match(isResult=flag)
                if flag is None:
                    item.pop('isResult')
                frame = self.load([item])
                self.assertTrue(frame.empty)
                report = frame.attrs['xg_diagnostics']['sources'][0]
                if flag is False:
                    self.assertEqual(report['scheduled_rows'], 1)
                    self.assertEqual(report['status'], 'empty_history')
                else:
                    self.assertEqual(report['rejection_counts']['missing_or_invalid_result_flag'], 1)

    def test_valid_upcoming_scheduled_match_is_skipped_without_false_error(self):
        item = match(isResult=False, datetime='2026-11-01 16:00:00', goals={'h': None, 'a': None}, xG={'h': None, 'a': None})
        frame = self.load([item])
        source = frame.attrs['xg_diagnostics']['sources'][0]
        self.assertEqual(source['status'], 'empty_history')
        self.assertEqual(source['scheduled_rows'], 1)
        self.assertEqual(source['rejected_rows'], 0)

    def test_nonnegative_finite_integral_scores_and_real_xg_are_required(self):
        for field, values in [('goals', {'h': '-1', 'a': '1'}), ('goals', {'h': '1.5', 'a': '1'}),
                              ('goals', {'h': True, 'a': '1'}), ('goals', {'h': '101', 'a': '1'}),
                              ('xG', {'h': '-.1', 'a': '.7'}), ('xG', {'h': None, 'a': '0.7'}),
                              ('xG', {'h': 'NaN', 'a': '0.7'}), ('xG', {'h': 'Infinity', 'a': '0.7'}),
                              ('xG', {'h': True, 'a': '0.7'}), ('xG', {'h': 1e308, 'a': .7})]:
            with self.subTest(field=field, values=values):
                frame = self.load([match(**{field: values})])
                self.assertTrue(frame.empty)
                source = frame.attrs['xg_diagnostics']['sources'][0]
                self.assertEqual(source['status'], 'invalid_rows')
                self.assertEqual(source['rejected_rows'], 1)
                self.assertIn('invalid_scores' if field == 'goals' else 'invalid_xg', source['rejection_counts'])

    def test_bad_missing_same_team_and_fixture_identifiers_are_rejected(self):
        for changes in (dict(id=None), dict(id=True), dict(h={}),
                        dict(h={'title': ' '}, a={'title': 'A'}),
                        dict(h={'title': 'Same'}, a={'title': 'same'}),
                        dict(h={'id': '2', 'title': 'H'}, a={'id': '2', 'title': 'A'})):
            with self.subTest(changes=changes):
                self.assertTrue(self.load([match(**changes)]).empty)

    def test_dates_seasons_and_claimed_future_results_are_quarantined(self):
        for changes, reason in [(dict(datetime='23/10/2026'), 'invalid_dates'),
                                (dict(datetime='2026-02-30'), 'invalid_dates'),
                                (dict(datetime=None), 'invalid_dates'),
                                (dict(datetime='2026-06-30'), 'date_season_mismatch'),
                                (dict(datetime='2027-07-01'), 'date_season_mismatch'),
                                (dict(season='2025'), 'date_season_mismatch'),
                                (dict(datetime='2026-10-24'), 'future_dates'),
                                (dict(datetime='2026-10-23T12:00:01Z'), 'future_dates')]:
            with self.subTest(changes=changes):
                frame = self.load([match(**changes)])
                self.assertTrue(frame.empty)
                self.assertIn(reason, frame.attrs['xg_diagnostics']['sources'][0]['rejection_counts'])

    def test_403_429_and_401_remain_explicit_and_stop_extra_requests(self):
        for status, reason in [(403, 'http_forbidden'), (401, 'http_forbidden'), (429, 'rate_limited')]:
            with self.subTest(status=status):
                frame = self.load(response=Response(status=status, content=b'not a success payload'), seasons=(2026, 2025))
                self.get.assert_called_once()
                report = frame.attrs['xg_diagnostics']
                self.assertEqual(report['status'], 'http_error')
                self.assertEqual(report['sources'][0]['http_status'], status)
                self.assertEqual(report['sources'][0]['reason_code'], reason)
                self.assertEqual(report['sources'][1]['status'], 'not_requested')
                self.assertIn(str(status), source_status_message(report))

    def test_other_http_failure_does_not_claim_all_seasons_are_unavailable(self):
        old = match(id='1002', datetime='2025-10-15 18:00:00')
        get = Mock(side_effect=[Response(status=404, content=b'not found'), Response([old])])
        frame = self.load(seasons=(2026, 2025), get=get)
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.attrs['xg_diagnostics']['status'], 'partial')
        self.assertEqual(frame.attrs['xg_diagnostics']['sources'][0]['http_status'], 404)
        self.assertEqual(frame.attrs['xg_diagnostics']['sources'][1]['accepted_rows'], 1)
        self.assertEqual(get.call_count, 2)

    def test_download_error_records_type_without_exception_body_or_fallback(self):
        get = Mock(side_effect=RuntimeError('PRIVATE BODY apiKey=secret-value'))
        frame = self.load(get=get)
        report = frame.attrs['xg_diagnostics']
        self.assertEqual(report['status'], 'download_error')
        self.assertEqual(report['sources'][0]['error'], 'RuntimeError')
        self.assertNotIn('PRIVATE BODY', json.dumps(report))
        self.assertNotIn('secret-value', str(UnderstatUnavailable(frame)))
        self.assertEqual(get.call_count, 1)

    def test_malformed_empty_duplicate_json_and_changed_ajax_schema_are_distinct(self):
        valid = json.dumps({'dates': [match()]}).replace('"isResult": true', '"isResult": true, "isResult": false').encode()
        for source, status in [(Response(content=b''), 'empty_payload'),
                               (Response(content=b'<html>blocked</html>'), 'invalid_payload'),
                               (Response(payload={}), 'invalid_payload'),
                               (Response(payload={'dates': {}}), 'invalid_payload'),
                               (Response(content=valid), 'invalid_payload'),
                               (Response(items=[]), 'empty_history')]:
            with self.subTest(status=status):
                frame = self.load(response=source)
                self.assertTrue(frame.empty)
                self.assertEqual(frame.attrs['xg_diagnostics']['status'], status)

    def test_unverified_redirect_is_not_accepted_as_understat_history(self):
        for url in ('https://unrelated.invalid/getLeagueData/EPL/2026',
                    'https://understat.com/getLeagueData/EPL/2025',
                    'https://understat.com/getLeagueData/EPL/2026?token=private'):
            with self.subTest(url=url):
                frame = self.load(response=Response([match()], url=url))
                self.assertTrue(frame.empty)
                report = frame.attrs['xg_diagnostics']['sources'][0]
                self.assertEqual(report['reason_code'], 'unverified_redirect')
                self.assertNotIn('private', json.dumps(report))

    def test_exact_duplicates_count_once_but_keep_quality_audit(self):
        frame = self.load([match(), match()])
        self.assertEqual(len(frame), 1)
        quality = frame.attrs['xg_diagnostics']['quality_audit']
        self.assertEqual(quality['duplicate_rows'], 1)
        self.assertEqual(quality['conflicting_rows'], 0)
        self.assertEqual(frame.iloc[0]._understat_row_index, 0)

    def test_conflicting_scores_xg_or_fixture_ids_are_all_quarantined_before_dedup(self):
        for changed in (match(goals={'h': '3', 'a': '1'}), match(xG={'h': '2.25', 'a': '0.75'}),
                        match(id='1002'), match(a={'id': '3', 'title': 'Another opponent'})):
            with self.subTest(changed=changed):
                frame = self.load([match(), changed, match()])
                self.assertTrue(frame.empty)
                report = frame.attrs['xg_diagnostics']
                self.assertEqual(report['status'], 'conflicting_fixtures')
                self.assertEqual(report['quality_audit']['conflicting_rows'], 3)
                self.assertEqual(report['quality_audit']['duplicate_rows'], 0)
                self.assertEqual(report['sources'][0]['status'], 'conflicting_fixtures')

    def test_invalid_contradictory_row_cannot_be_removed_to_accept_other_version(self):
        bad = match(xG={'h': '-1', 'a': '0.75'})
        frame = self.load([match(), bad])
        self.assertTrue(frame.empty)
        report = frame.attrs['xg_diagnostics']
        self.assertEqual(report['quality_audit']['rejected_rows'], 1)
        self.assertEqual(report['quality_audit']['conflicting_rows'], 1)

    def test_independent_good_fixture_survives_with_partial_diagnostics(self):
        good = match(id='1002', datetime='2026-10-16', a={'id': '3', 'title': 'Other Away'})
        frame = self.load([match(), match(xG={'h': '2.25', 'a': '0.75'}), good])
        self.assertEqual(len(frame), 1)
        self.assertEqual(frame.iloc[0]._understat_id, '1002')
        self.assertEqual(frame.attrs['xg_diagnostics']['status'], 'partial')
        self.assertEqual(frame.attrs['xg_diagnostics']['quality_audit']['conflicting_rows'], 2)

    def test_request_configuration_is_explicit_and_duplicate_seasons_not_downloaded_again(self):
        get = Mock(return_value=Response([match()]))
        frame = self.load(get=get, seasons=(2026, 2026))
        get.assert_called_once()
        self.assertEqual(frame.attrs['xg_diagnostics']['duplicate_season_requests_avoided'], 1)
        for league, seasons in [(None, (2026,)), ('UCL', (2026,)), ('Bundesliga', (2026,)), ('EPL', (2027,)), ('EPL', ('2026',)), ('EPL', ())]:
            with self.subTest(league=league, seasons=seasons):
                get = Mock()
                frame = self.load(get=get, league=league, seasons=seasons)
                self.assertTrue(frame.empty)
                get.assert_not_called()

    def test_failure_exception_exposes_only_structured_diagnostics_and_safe_message(self):
        frame = self.load(response=Response(status=403, content=b'PRIVATE BLOCK PAGE'))
        error = UnderstatUnavailable(frame)
        self.assertEqual(error.report['sources'][0]['http_status'], 403)
        self.assertTrue(error.frame.empty)
        self.assertNotIn('PRIVATE BLOCK PAGE', str(error))
        self.assertNotIn('PRIVATE BLOCK PAGE', json.dumps(error.report))
        self.assertIsNot(error.frame, frame)


class UnderstatEligibilityTests(unittest.TestCase):
    def eligible(self, frame, kickoff='2026-10-24T12:00:00Z', analysis_at=NOW):
        return eligible_xg_history(frame, kickoff, analysis_at)

    def test_unknown_timezone_needs_full_conservative_upper_bound_before_analysis(self):
        frame = annotated(day='2026-10-21')
        at_boundary = self.eligible(frame, analysis_at='2026-10-23T00:00:00Z')
        self.assertTrue(at_boundary.empty)
        # Receipt must independently precede the historical analysis time.
        frame['_history_received_at'] = '2026-10-22T23:00:00Z'
        self.assertTrue(self.eligible(frame, analysis_at='2026-10-23T00:00:00Z').empty)
        safe = self.eligible(frame, analysis_at='2026-10-23T00:00:00.000001Z')
        self.assertEqual(len(safe), 1)
        self.assertEqual(safe.attrs['xg_eligibility']['status'], 'ok')
        self.assertEqual(safe.iloc[0].xG_home, 1.25)

    def test_today_yesterday_and_future_unknown_timezone_do_not_enter_baseline(self):
        for day in ('2026-10-22', '2026-10-23', '2026-10-24'):
            with self.subTest(day=day):
                result = self.eligible(annotated(day=day))
                self.assertTrue(result.empty)
                self.assertEqual(result.attrs['xg_eligibility']['excluded_unknown_time'], 1)

    def test_aware_timestamp_must_precede_both_cutoffs_strictly(self):
        for match_utc, expected in [('2026-10-23T11:59:59Z', 1), ('2026-10-23T12:00:00Z', 0), ('2026-10-23T12:00:01Z', 0)]:
            with self.subTest(match_utc=match_utc):
                frame = annotated(day=pd.Timestamp(match_utc).tz_localize(None), quality='aware_timestamp', match_utc=match_utc)
                self.assertEqual(len(self.eligible(frame)), expected)
        frame = annotated(day='2026-10-23T11:00:00', quality='aware_timestamp', match_utc='2026-10-23T11:00:00Z')
        self.assertTrue(self.eligible(frame, kickoff='2026-10-23T10:00:00Z').empty)

    def test_source_received_after_analysis_or_unknown_receipt_cannot_prove_availability(self):
        for received in ('2026-10-23T12:00:00.000001Z', None, '2026-10-23 11:00:00'):
            with self.subTest(received=received):
                result = self.eligible(annotated(received=received))
                self.assertTrue(result.empty)
        self.assertEqual(len(self.eligible(annotated(received=NOW.isoformat()))), 1)

    def test_missing_timezone_cutoffs_and_bad_metadata_fail_closed(self):
        self.assertTrue(self.eligible(annotated(), analysis_at='2026-10-23 12:00:00').empty)
        for frame in (annotated(quality='unrecognized'), annotated(day='2026-10-15T18:00:00'),
                      annotated(day='2026-10-15T10:00:00', quality='aware_timestamp', match_utc='2026-10-15T11:00:00Z')):
            with self.subTest(quality=frame.iloc[0]._xg_time_quality):
                self.assertTrue(self.eligible(frame).empty)

    def test_legacy_frames_remain_compatible_without_new_unproved_metadata_claims(self):
        frame = annotated().drop(columns=['_xg_time_quality', '_xg_match_utc', '_history_received_at'])
        frame.attrs['history_diagnostics'] = {'status': 'synthetic_fixture'}
        result = self.eligible(frame)
        pd.testing.assert_frame_equal(frame, result)
        self.assertEqual(result.attrs['xg_eligibility']['status'], 'legacy_unannotated')
        self.assertEqual(result.attrs['history_diagnostics'], frame.attrs['history_diagnostics'])

    def test_filter_preserves_source_failure_diagnostics_without_mutating_input(self):
        frame = annotated(day='2026-10-23')
        original = {'status': 'partial', 'sources': [{'status': 'http_error', 'http_status': 403, 'season': 2026}]}
        frame.attrs['history_diagnostics'] = deepcopy(original)
        frame.attrs['xg_diagnostics'] = deepcopy(original)
        result = self.eligible(frame)
        self.assertTrue(result.empty)
        self.assertEqual(result.attrs['history_diagnostics']['sources'], original['sources'])
        self.assertEqual(result.attrs['history_diagnostics']['eligibility']['excluded_unknown_time'], 1)
        self.assertEqual(frame.attrs['history_diagnostics'], original)


if __name__ == '__main__':
    unittest.main()
