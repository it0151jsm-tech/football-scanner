"""Authored offline source/identity regressions; never live coverage evidence."""
from copy import deepcopy
from datetime import datetime, timezone
from functools import lru_cache
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from history_data import match_team, team_history
from source_diagnostics import (summarize_export_readiness, xg_diagnostics,
    model_data_readiness, csv_form_ready)
from test_regressions import load_functions

ROOT = Path(__file__).resolve().parents[1]
KICKOFF = datetime(2026, 10, 24, 18, tzinfo=timezone.utc)


def exported(**changes):
    row={'ลีก':'EPL','คู่แข่งขัน':'Verified A vs Verified B','เวลาแข่ง (ไทย)':'25/10/2026 01:00',
         'คะแนนหลักฐาน A/100':60,'EV (%)':2.5}
    row.update(changes)
    return row


def history(*, home='Verified A',away='Verified B',xg=True):
    rows=[dict(Date_dt=pd.Timestamp(f'2026-10-{day:02}'),HomeTeam=home,AwayTeam=away,
        FTHG=2,FTAG=1,_history_season='2627') for day in range(1,11)]
    frame=pd.DataFrame(rows)
    if xg:
        frame['xG_home'],frame['xG_away']=1.8,.9
    frame.attrs['history_diagnostics']={'status':'ok','sources':[{'status':'ok',
        'http_status':200,'season':'2627','received_at':'2026-10-24T10:00:00Z',
        'valid_rows':len(rows),'url':'https://example.invalid/authored-fixture'}]}
    return frame


class StatsRecoveryTests(unittest.TestCase):
    def setUp(self):
        blocked=patch('requests.sessions.Session.request',side_effect=AssertionError('no live stats requests'))
        self.http=blocked.start()
        self.addCleanup(blocked.stop)

    def tearDown(self):
        self.http.assert_not_called()

    def test_event_id_keeps_same_display_labels_as_distinct_provider_fixtures(self):
        report=summarize_export_readiness(pd.DataFrame([
            exported(event_id='one'),exported(event_id='one'),exported(event_id='two')]))
        self.assertEqual(report['market_rows'],3)
        self.assertEqual(report['fixtures'],2)
        self.assertEqual(report['identity_basis'],'provider_event_id')
        self.assertEqual(report['a_scored_fixtures'],2)

    def test_legacy_exports_explicitly_mark_unverified_label_time_grouping(self):
        report=summarize_export_readiness(pd.DataFrame([exported(),exported()]))
        self.assertEqual(report['fixtures'],1)
        self.assertEqual(report['identity_basis'],'legacy_label_time_fallback_not_verified_event_id')

    def test_conflicting_identity_does_not_merge_independent_a_b_readiness(self):
        report=summarize_export_readiness(pd.DataFrame([
            exported(event_id='one'),exported(event_id='one',**{'คู่แข่งขัน':'Other A vs Other B'})]))
        self.assertEqual(report['conflicting_event_ids'],['one'])
        self.assertEqual(report['a_scored_fixtures'],0)
        self.assertEqual(report['b_ev_fixtures'],0)
        self.assertTrue(report['problem_fixtures'][0]['identity_conflict'])

    def test_missing_ids_in_id_export_stay_separate_not_joined_by_labels(self):
        report=summarize_export_readiness(pd.DataFrame([
            exported(event_id='one'),exported(event_id=None),exported(event_id='')]))
        self.assertEqual(report['fixtures'],3)
        self.assertEqual(report['missing_event_id_rows'],2)

    def test_a_score_requires_finite_non_boolean_in_original_zero_to_hundred_range(self):
        for score in (float('inf'),float('-inf'),float('nan'),True,-1,100.01,'bad'):
            with self.subTest(score=score):
                report=summarize_export_readiness(pd.DataFrame([exported(**{'คะแนนหลักฐาน A/100':score})]))
                self.assertEqual(report['a_scored_fixtures'],0)
        for score in (0,100,'55.25'):
            with self.subTest(score=score):
                report=summarize_export_readiness(pd.DataFrame([exported(**{'คะแนนหลักฐาน A/100':score})]))
                self.assertEqual(report['a_scored_fixtures'],1)

    def test_ev_presence_is_finite_availability_not_a_positive_value_claim(self):
        for ev,expected in ((None,0),(float('inf'),0),(float('nan'),0),(True,0),(-5,1),(0,1),('2.5',1)):
            with self.subTest(ev=ev):
                report=summarize_export_readiness(pd.DataFrame([exported(**{'EV (%)':ev})]))
                self.assertEqual(report['b_ev_fixtures'],expected)
                self.assertIn('not calibration',report['note'])

    def test_export_summary_preserves_input_values_and_nested_attrs(self):
        frame=pd.DataFrame([exported(event_id='one')])
        frame.attrs['owner']={'nested':['unchanged']}
        before,attrs=frame.copy(deep=True),deepcopy(frame.attrs)
        summarize_export_readiness(frame)
        pd.testing.assert_frame_equal(frame,before)
        self.assertEqual(frame.attrs,attrs)

    def test_reviewed_aliases_map_but_similar_clubs_remain_unmatched(self):
        self.assertEqual(match_team('Manchester City',['Man City']),('Man City','verified_alias'))
        self.assertEqual(match_team('Manchester United',['Man United']),('Man United','verified_alias'))
        self.assertEqual(match_team('Manchester City',['Man United'])[0],None)
        self.assertEqual(match_team('Nottingham Forest',['Notts County'])[0],None)
        self.assertEqual(match_team('Stuttgart',['Stuttgarter Kickers'])[0],None)
        self.assertEqual(match_team('Manchester City',['Manchester City','Man City'])[1],'name_ambiguous')

    def test_actual_five_ten_and_pre_cutoff_games_remain_exact(self):
        frame=history(home='Stuttgart',away="M'gladbach",xg=False)
        home,diag=team_history(frame,'VfB Stuttgart',KICKOFF,'home')
        away,_=team_history(frame,'Borussia Monchengladbach',KICKOFF,'away')
        self.assertEqual(len(home.tail(5)),5)
        self.assertEqual(len(home.tail(10)),10)
        self.assertEqual(len(away.tail(10)),10)
        self.assertEqual(diag['mapping'],'verified_alias')
        self.assertTrue((home.Date_dt<pd.Timestamp(KICKOFF.date())).all())

    def test_invalid_kickoff_has_diagnostics_instead_of_throwing(self):
        for kickoff in (None,'bad date','2026-10-24T18:00:00'):
            with self.subTest(kickoff=kickoff):
                report=xg_diagnostics(history(),'Verified A','Verified B',kickoff)
                self.assertEqual(report['status'],'invalid_cutoff')
                self.assertEqual(report['home']['sample'],0)
                self.assertIsNone(report['cutoff_date'])

    def test_unsupported_xg_never_becomes_available_from_unscoped_frame(self):
        report=xg_diagnostics(history(),'Verified A','Verified B',KICKOFF,supported=False)
        self.assertEqual(report['status'],'unsupported')
        self.assertEqual(report['home']['sample'],0)
        self.assertEqual(report['home']['status'],'source_unavailable')
        ready=model_data_readiness(report,None,{'available':False})
        self.assertEqual(ready['status'],'xg_unavailable')
        self.assertIn('ไม่มีแหล่ง xG',report['summary'])

    def test_invalid_xg_rows_are_explained_without_fabricated_sample(self):
        for value in (None,float('inf'),float('-inf'),float('nan'),True,-.1):
            with self.subTest(value=value):
                frame=history()
                frame['xG_home']=value
                report=xg_diagnostics(frame,'Verified A','Verified B',KICKOFF)
                self.assertEqual(report['home']['sample'],0)
                self.assertEqual(report['home']['invalid_xg_rows'],10)
                self.assertEqual(report['home']['status'],'invalid_xg')

    def test_xg_diagnostic_does_not_count_future_games_or_replace_missing_samples(self):
        frame=history().iloc[:4].copy()
        future=frame.iloc[[0]].copy()
        future['Date_dt']=pd.Timestamp('2026-10-25')
        frame=pd.concat([frame,future],ignore_index=True)
        report=xg_diagnostics(frame,'Verified A','Verified B',KICKOFF)
        self.assertEqual(report['home']['sample'],4)
        self.assertEqual(report['home']['status'],'insufficient_sample')

    def test_xg_calendar_strings_and_aware_dates_have_safe_diagnostic_comparison(self):
        frame=history()
        frame['Date_dt']=frame.Date_dt.map(lambda value:value.isoformat()+'Z')
        report=xg_diagnostics(frame,'Verified A','Verified B',KICKOFF)
        self.assertEqual(report['home']['sample'],10)
        self.assertEqual(report['home']['last_date'],'2026-10-10')

    def test_bad_source_dates_are_not_mislabeled_as_team_name_failure(self):
        frame=history()
        frame['Date_dt']='not-a-date'
        report=xg_diagnostics(frame,'Verified A','Verified B',KICKOFF)
        self.assertEqual(report['status'],'invalid_dates')
        self.assertEqual(report['invalid_date_rows'],10)
        self.assertEqual(report['home']['status'],'source_unavailable')
        self.assertIn('วันที่ xG',report['summary'])

    def test_missing_xg_columns_and_failed_source_remain_distinct(self):
        missing=xg_diagnostics(history(xg=False),'Verified A','Verified B',KICKOFF)
        self.assertEqual(missing['status'],'missing_columns')
        failed=pd.DataFrame()
        failed.attrs['xg_diagnostics']={'status':'http_error','sources':[{'status':'http_error','season':2026,'http_status':403}]}
        report=xg_diagnostics(failed,'Verified A','Verified B',KICKOFF)
        self.assertEqual(report['status'],'http_error')
        self.assertIn('403',report['summary'])
        self.assertEqual(report['home']['sample'],0)

    def test_csv_readiness_requires_every_fixture_and_real_venue_samples(self):
        frame=history(xg=False)
        good={'home_team':'Verified A','away_team':'Verified B','commence_time':KICKOFF.isoformat()}
        bad=dict(good,away_team='Unverified club')
        self.assertTrue(csv_form_ready(frame,[good]))
        self.assertFalse(csv_form_ready(frame,[good,bad]))
        self.assertFalse(csv_form_ready(frame.iloc[:4],[good]))

    def test_h2h_uses_actual_pre_fixture_results_and_does_not_create_xg(self):
        functions=load_functions(ROOT/'app.py')
        frame=history(xg=False)
        before=frame.copy(deep=True)
        result=functions['get_h2h_history'](frame,'Verified A','Verified B',KICKOFF,limit=5)
        self.assertEqual(result['h2h_sample'],5)
        self.assertIn('3.00/นัด',result['h2h_summary'])
        self.assertEqual(set(result),{'h2h_summary','h2h_detail','h2h_sample'})
        pd.testing.assert_frame_equal(frame,before)
        missing=functions['get_h2h_history'](frame,'Other A','Other B',KICKOFF)
        self.assertEqual(missing['h2h_sample'],0)

    def synthetic_scan(self):
        # Import the module, never an imported TestCase name that discovery could
        # accidentally count twice. This helper retains temporary SQLite only.
        import test_issue9_scan
        case=test_issue9_scan.Issue9ScanTests()
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.prepare(english=True,blocked=True)
        return case

    def test_one_unmatched_fixture_cannot_block_verified_provider_form_for_another(self):
        case=self.synthetic_scan()
        candidate=case.frame(*case.api)
        candidate['_provider_league_verified']=True
        candidate['_provider_identity_verified']=True
        diagnostic={'provider':'api-football','state':'success','status':'ok',
            'received_at':case.now.isoformat(),'usable_rows':len(candidate)}
        candidate.attrs['history_diagnostics']=dict(diagnostic)
        # The AST harness strips Streamlit decorators. Explicit mock cache below
        # models the existing per-league/season successful-response cache contract.
        provider=Mock(return_value=(candidate,diagnostic))
        @lru_cache(maxsize=4)
        def cached(sport,season,key):
            return provider(sport,season,key)
        case.f['fetch_api_football_with_diagnostics']=Mock(side_effect=cached)
        case.csv=pd.DataFrame()
        second=deepcopy(case.event)
        second.update(id='unmatched-fixture',home_team='Unverified Club')
        case.f['fetch_odds']=Mock(return_value=([case.event,second],'100','2'))
        with patch.dict(os.environ,API_FOOTBALL_KEY='synthetic-api-football-key',VALUE_BET_API_FOOTBALL_ENABLED='1'):
            rows,_,_,_=case.scan()
        first=next(row for row in rows if row['event_id']==case.event['id'] and row['market']=='Over/Under')
        second_row=next(row for row in rows if row['event_id']==second['id'] and row['market']=='Over/Under')
        a,b=case.analyses(first)
        other_a,other_b=case.analyses(second_row)
        self.assertEqual(a['home_form']['sample5'],5)
        self.assertEqual(a['away_form']['sample10'],10)
        self.assertTrue(a['home_form']['diagnostics']['api_football']['used_for_form'])
        self.assertEqual(other_a['home_form']['sample5'],0)
        self.assertFalse(other_a['home_form']['diagnostics']['api_football']['used_for_form'])
        self.assertFalse(b['available'])
        self.assertFalse(other_b['available'])
        self.assertIsNone(first['ev_pct'])
        self.assertIsNone(second_row['ev_pct'])
        self.assertEqual(case.f['fetch_api_football_with_diagnostics'].call_count,2)
        self.assertEqual(provider.call_count,1)

    def test_sufficient_csv_fixture_keeps_its_source_when_another_fixture_uses_fallback(self):
        case=self.synthetic_scan()
        original=deepcopy(case.csv)
        second=deepcopy(case.event)
        second.update(id='unmatched-fixture',home_team='Unverified Club')
        case.f['fetch_odds']=Mock(return_value=([case.event,second],'100','2'))
        provider=Mock(return_value=(pd.DataFrame(),{'provider':'api-football','state':'empty','status':'empty_history',
            'received_at':case.now.isoformat()}))
        case.f['fetch_api_football_with_diagnostics']=provider
        with patch.dict(os.environ,API_FOOTBALL_KEY='synthetic-api-football-key',VALUE_BET_API_FOOTBALL_ENABLED='1'):
            rows,_,_,_=case.scan()
        first=next(row for row in rows if row['event_id']==case.event['id'] and row['market']=='Over/Under')
        a,_=case.analyses(first)
        self.assertEqual(a['home_form']['sample5'],5)
        self.assertEqual(a['home_form']['diagnostics']['api_football']['reason_code'],'csv_sufficient')
        self.assertEqual(provider.call_count,1)
        pd.testing.assert_frame_equal(case.csv,original)


if __name__=='__main__':
    unittest.main()
