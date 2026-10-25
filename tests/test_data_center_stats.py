"""Authored source fixtures verify descriptive data, never real model accuracy."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock

import pandas as pd

from data_center_stats import collect_statistics

UTC=timezone.utc
NOW=datetime(2026,10,25,12,tzinfo=UTC)


def fixture(**changes):
    event={'event_id':'authored-stats','sport_key':'soccer_epl','csv_code':'E0',
        'league':'Authored League','home_team':'Alpha United','away_team':'Beta Athletic',
        'kickoff_utc':(NOW+timedelta(hours=6)).isoformat()}
    event.update(changes)
    return event


def history(*,xg=False,count=10):
    rows=[]
    for day in range(1,count+1):
        rows.append({'Date_dt':pd.Timestamp(2026,10,day),
            'HomeTeam':'Alpha United' if day%2 else 'Beta Athletic',
            'AwayTeam':'Beta Athletic' if day%2 else 'Alpha United',
            'FTHG':2,'FTAG':1,'_history_season':'2627',
            '_history_received_at':(NOW-timedelta(hours=1)).isoformat(),
            '_history_source_url':'https://example.invalid/authored-history',
            **({'xG_home':1.5,'xG_away':.8} if xg else {})})
    frame=pd.DataFrame(rows)
    diagnostic={'status':'ok','sources':[{'season':'2627','url':'https://example.invalid/authored-history',
        'received_at':(NOW-timedelta(hours=1)).isoformat(),'http_status':200,'payload_sha256':'a'*64}]}
    frame.attrs['history_diagnostics']=diagnostic
    if xg:frame.attrs['xg_diagnostics']=deepcopy(diagnostic)
    return frame


class DataCenterStatsTests(unittest.TestCase):
    def collect(self,event=None,scores=None,xg=None,**kwargs):
        return collect_statistics(event or fixture(),fetch_history=Mock(return_value=history() if scores is None else scores),
            fetch_xg=Mock(return_value=history(xg=True) if xg is None else xg),now=NOW,**kwargs)

    def test_complete_source_data_has_actual_5_10_form_venue_and_xg(self):
        data=self.collect()
        self.assertEqual(data['status'],'complete')
        home,away=data['teams']['home'],data['teams']['away']
        self.assertEqual(home['overall']['last5']['sample'],5)
        self.assertEqual(home['overall']['last10']['sample'],10)
        self.assertEqual(home['venue']['last5']['sample'],5)
        self.assertEqual((home['venue']['last5']['gf_avg'],home['venue']['last5']['ga_avg']),(2,1))
        self.assertEqual((away['venue']['last5']['gf_avg'],away['venue']['last5']['ga_avg']),(1,2))
        self.assertEqual(home['xg']['venue']['last5']['xg_avg'],1.5)
        self.assertEqual(away['xg']['venue']['last5']['xg_avg'],.8)
        self.assertEqual(data['h2h']['sample'],10)
        self.assertIsNone(data['standings']['home'])

    def test_csv_exception_does_not_block_xg_or_drop_fixture(self):
        score=Mock(side_effect=TimeoutError('apiKey=DO_NOT_EXPOSE'))
        xg=Mock(return_value=history(xg=True))
        data=collect_statistics(fixture(),fetch_history=score,fetch_xg=xg,now=NOW)
        self.assertEqual(data['event_id'],'authored-stats')
        self.assertEqual(data['sources']['football_data']['status'],'error')
        self.assertEqual(data['teams']['home']['overall']['last5']['sample'],0)
        self.assertEqual(data['teams']['home']['xg']['overall']['last5']['sample'],5)
        self.assertNotIn('DO_NOT_EXPOSE',json.dumps(data))
        xg.assert_called_once()

    def test_xg_exception_does_not_block_form_or_h2h(self):
        score=Mock(return_value=history())
        xg=Mock(side_effect=RuntimeError('apiKey=DO_NOT_EXPOSE'))
        data=collect_statistics(fixture(),fetch_history=score,fetch_xg=xg,now=NOW)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],10)
        self.assertEqual(data['h2h']['sample'],10)
        self.assertIsNone(data['teams']['home']['xg']['overall']['last5']['xg_avg'])
        self.assertEqual(data['sources']['understat']['status'],'error')
        self.assertNotIn('DO_NOT_EXPOSE',json.dumps(data))

    def test_missing_sources_keep_empty_numbers_none(self):
        data=self.collect(scores=pd.DataFrame(),xg=pd.DataFrame())
        self.assertEqual(data['status'],'no_data')
        for side in ('home','away'):
            self.assertIsNone(data['teams'][side]['overall']['last5']['gf_avg'])
            self.assertIsNone(data['teams'][side]['xg']['overall']['last10']['xg_avg'])
        self.assertEqual(data['h2h']['sample'],0)

    def test_no_xg_columns_never_substitutes_actual_goals(self):
        data=self.collect(xg=history())
        self.assertEqual(data['sources']['understat']['quality']['status'],'missing_xg_columns')
        self.assertIsNone(data['teams']['home']['xg']['overall']['last5']['xg_avg'])
        self.assertEqual(data['teams']['home']['overall']['last5']['sample'],5)

    def test_two_xg_observations_display_sample_without_claiming_model_ready(self):
        data=self.collect(xg=history(xg=True,count=2))
        self.assertEqual(data['teams']['home']['xg']['overall']['last5']['sample'],2)
        self.assertNotIn('ev_pct',data)
        self.assertNotIn('probabilities',data)
        self.assertNotIn('model_ready',data)

    def test_unsupported_league_never_calls_xg_source(self):
        xg=Mock(side_effect=AssertionError('unsupported xg must not be requested'))
        score=Mock(return_value=history())
        data=collect_statistics(fixture(sport_key='soccer_england_league1',csv_code='E2'),
            fetch_history=score,fetch_xg=xg,now=NOW)
        self.assertEqual(data['sources']['understat']['status'],'unsupported')
        self.assertEqual(data['teams']['home']['overall']['last5']['sample'],5)
        xg.assert_not_called()

    def test_missing_csv_code_does_not_call_history_or_block_supported_xg(self):
        scores=Mock(side_effect=AssertionError('unsupported csv must not be requested'))
        xg=Mock(return_value=history(xg=True))
        data=collect_statistics(fixture(csv_code=None),fetch_history=scores,fetch_xg=xg,now=NOW)
        self.assertEqual(data['sources']['football_data']['status'],'unsupported')
        self.assertEqual(data['teams']['home']['xg']['overall']['last5']['sample'],5)
        scores.assert_not_called()

    def test_unmatched_team_name_has_reason_not_zero_statistic(self):
        data=self.collect(event=fixture(home_team='Different United'))
        self.assertEqual(data['teams']['home']['status'],'name_unmatched')
        self.assertIsNone(data['teams']['home']['overall']['last5']['gf_avg'])
        self.assertIsNone(data['teams']['home']['xg']['overall']['last5']['xg_avg'])
        self.assertEqual(data['teams']['away']['overall']['last5']['sample'],5)

    def test_reviewed_alias_works_without_fuzzy_matching(self):
        scores=history();xg=history(xg=True)
        for frame in (scores,xg):
            frame.replace({'Alpha United':'Man City','Beta Athletic':'Nott\'m Forest'},inplace=True)
        data=self.collect(event=fixture(home_team='Manchester City',away_team='Nottingham Forest'),scores=scores,xg=xg)
        self.assertEqual(data['teams']['home']['mapping'],'verified_alias')
        self.assertEqual(data['teams']['home']['matched_name'],'Man City')
        self.assertEqual(data['teams']['home']['overall']['last5']['sample'],5)

    def test_future_today_and_target_result_are_not_history(self):
        scores=history();xg=history(xg=True)
        for frame in (scores,xg):
            frame.loc[0,'Date_dt']=pd.Timestamp(2026,10,25)
            frame.loc[1,'Date_dt']=pd.Timestamp(2026,10,26)
        data=self.collect(scores=scores,xg=xg)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],8)
        self.assertEqual(data['teams']['home']['xg']['overall']['last10']['sample'],8)
        self.assertEqual(data['sources']['football_data']['quality']['late_rows'],2)

    def test_backdated_fixture_uses_earlier_kickoff_not_current_date(self):
        event=fixture(kickoff_utc='2026-10-06T19:00:00+07:00')
        data=self.collect(event=event)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],5)
        self.assertEqual(data['cutoff_utc'],'2026-10-06T12:00:00+00:00')

    def test_invalid_cutoff_fails_without_fetching_sources(self):
        for changes in ({'kickoff_utc':'2026-10-26'}, {'home_team':'Beta Athletic'}):
            with self.subTest(changes=changes):
                score,xg=Mock(),Mock()
                data=collect_statistics(fixture(**changes),fetch_history=score,fetch_xg=xg,now=NOW)
                self.assertEqual(data['reason'],'invalid_fixture_identity_or_time')
                score.assert_not_called();xg.assert_not_called()

    def test_conflicting_and_exact_duplicate_results_do_not_inflate_form(self):
        scores=history()
        scores=pd.concat([scores,scores.iloc[[0]],scores.iloc[[1]].assign(FTHG=9)],ignore_index=True)
        data=self.collect(scores=scores)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],9)
        self.assertEqual(data['sources']['football_data']['quality']['conflicting_rows'],2)
        self.assertEqual(data['sources']['football_data']['quality']['duplicate_rows'],1)

    def test_invalid_dates_goals_and_xg_are_excluded_without_filling(self):
        scores=history();xg=history(xg=True)
        scores.loc[0,'Date_dt']=pd.NaT
        scores.loc[1,'FTHG']=-1
        xg.loc[0,'xG_home']=-2
        xg.loc[1,'xG_home']=float('nan')
        data=self.collect(scores=scores,xg=xg)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],8)
        self.assertEqual(data['teams']['home']['xg']['overall']['last10']['sample'],8)
        self.assertEqual(data['sources']['understat']['quality']['invalid_xg_rows'],2)

    def test_actual_source_receipt_and_cache_status_preserved(self):
        scores=history()
        scores.attrs['cache_diagnostics']={'status':'cache'}
        data=self.collect(scores=scores)
        self.assertEqual(data['sources']['football_data']['cache_status'],'cache')
        self.assertEqual(data['sources']['football_data']['received_at'],['2026-10-25T11:00:00+00:00'])
        self.assertNotEqual(data['sources']['football_data']['received_at'],[data['as_of']])

    def test_missing_source_receipt_stays_unknown(self):
        scores=history().drop(columns=['_history_received_at']);scores.attrs={}
        data=self.collect(scores=scores)
        self.assertEqual(data['sources']['football_data']['receipt_status'],'unknown')
        self.assertIsNone(data['teams']['home']['overall']['last5']['matches'][0]['source_received_at'])

    def test_source_error_payload_and_credential_urls_are_not_returned(self):
        scores=history()
        scores['_history_source_url']='https://example.invalid/data?apiKey=DO_NOT_EXPOSE'
        scores.attrs['history_diagnostics']['sources'][0].update(url='https://example.invalid/?token=DO_NOT_EXPOSE',
            error='DO_NOT_EXPOSE',body='DO_NOT_EXPOSE')
        data=self.collect(scores=scores)
        self.assertNotIn('DO_NOT_EXPOSE',json.dumps(data))

    def test_optional_fallback_error_keeps_existing_csv_and_xg(self):
        fallback=Mock(side_effect=RuntimeError('DO_NOT_EXPOSE'))
        data=self.collect(optional_fallback=fallback)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],10)
        self.assertEqual(data['teams']['home']['xg']['overall']['last10']['sample'],10)
        self.assertEqual(data['sources']['api_football']['status'],'error')
        self.assertNotIn('DO_NOT_EXPOSE',json.dumps(data))

    def test_not_called_optional_source_does_not_claim_csv_receipt_as_its_own(self):
        candidate=history()
        candidate.attrs['api_football_diagnostics']={'state':'not_called','reason':'csv_sufficient','used_for_form':False}
        data=self.collect(optional_fallback=Mock(return_value=candidate))
        self.assertEqual(data['sources']['api_football']['status'],'not_called')
        self.assertEqual(data['sources']['api_football']['received_at'],[])
        self.assertEqual(data['sources']['football_data']['received_at'],['2026-10-25T11:00:00+00:00'])

    def test_optional_provider_backoff_keeps_safe_plan_reason_and_original_clock(self):
        candidate=history()
        candidate.attrs['api_football_diagnostics']={'state':'not_called','provider_state':'plan_unsupported',
            'status':'plan_unsupported','reason':'plan_backoff','used_for_form':False,'request_attempted':False,
            'backoff_active':True,'received_at':'2026-10-25T10:00:00Z',
            'next_retry_at':'2026-10-26T10:00:00Z','http_status':403,'body':'NEVER_EXPOSE'}
        data=self.collect(optional_fallback=Mock(return_value=candidate))
        source=data['sources']['api_football']
        self.assertEqual(source['status'],'not_called')
        self.assertEqual(source['provider_state'],'plan_unsupported')
        self.assertEqual(source['reason'],'plan_backoff')
        self.assertEqual(source['http_status'],403)
        self.assertEqual(source['received_at'],['2026-10-25T10:00:00+00:00'])
        self.assertEqual(source['next_retry_at'],'2026-10-26T10:00:00+00:00')
        self.assertNotIn('NEVER_EXPOSE',json.dumps(data))

    def test_unverified_provider_rows_do_not_replace_valid_csv(self):
        candidate=history()
        candidate['_provider_league_verified']=False
        candidate['_provider_identity_verified']=False
        candidate.attrs['api_football_diagnostics']={'state':'success','used_for_form':True}
        data=self.collect(optional_fallback=Mock(return_value=candidate))
        self.assertEqual(data['sources']['api_football']['status'],'unverified')
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],10)
        self.assertEqual(data['sources']['football_data']['provider'],'Football-Data')

    def test_future_or_invalid_receipt_rows_are_not_available_observations(self):
        scores,xg=history(),history(xg=True)
        for frame in (scores,xg):
            frame.loc[0,'_history_received_at']=(NOW+timedelta(minutes=1)).isoformat()
            frame.loc[1,'_history_received_at']='not-a-clock'
        data=self.collect(scores=scores,xg=xg)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],8)
        self.assertEqual(data['teams']['home']['xg']['overall']['last10']['sample'],8)
        self.assertEqual(data['sources']['football_data']['quality']['late_or_invalid_receipt_rows'],2)

    def test_malformed_one_source_rows_do_not_block_the_other_source(self):
        scores=history()
        # A duplicate numeric column is invalid provider content, not a reason
        # for the independently loaded actual xG source to disappear.
        scores=pd.concat([scores,scores[['FTHG']]],axis=1)
        data=self.collect(scores=scores)
        self.assertEqual(data['sources']['football_data']['quality']['status'],'invalid_response')
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],0)
        self.assertEqual(data['teams']['home']['xg']['overall']['last10']['sample'],10)

    def test_ambiguous_reviewed_aliases_do_not_duplicate_a_teams_games(self):
        scores=history();xg=history(xg=True)
        for frame in (scores,xg):
            frame.loc[frame.HomeTeam.eq('Alpha United'),'HomeTeam']='Man City'
            frame.loc[frame.AwayTeam.eq('Alpha United'),'AwayTeam']='Manchester City'
        data=self.collect(event=fixture(home_team='Manchester City'),scores=scores,xg=xg)
        self.assertEqual(data['teams']['home']['status'],'name_ambiguous')
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],0)
        self.assertIsNone(data['teams']['home']['xg']['overall']['last10']['xg_avg'])

    def test_verified_optional_provider_can_supply_form_without_xg(self):
        candidate=history()
        candidate['_provider_league_verified']=True
        candidate['_provider_identity_verified']=True
        candidate.attrs['api_football_diagnostics']={'state':'success','used_for_form':True}
        data=self.collect(scores=pd.DataFrame(),xg=pd.DataFrame(),optional_fallback=Mock(return_value=candidate))
        self.assertEqual(data['sources']['football_data']['provider'],'API-Football')
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],10)
        self.assertIsNone(data['teams']['home']['xg']['overall']['last5']['xg_avg'])

    def test_cups_need_verified_domestic_context_and_do_not_fetch_cup_xg(self):
        score,xg=Mock(),Mock()
        data=collect_statistics(fixture(sport_key='soccer_uefa_champs_league',csv_code=None),
            fetch_history=score,fetch_xg=xg,now=NOW)
        self.assertEqual(data['teams']['home']['context_status'],'unverified_or_unsupported')
        self.assertEqual(data['h2h']['status'],'unsupported')
        score.assert_not_called();xg.assert_not_called()

    def test_verified_domestic_context_uses_each_teams_own_evidence(self):
        cup=Mock(return_value={side:{'score_history':history(),'xg_history':history(xg=True),
            'context':{'status':'matched','sport_key':'soccer_epl'}} for side in ('home','away')})
        data=self.collect(event=fixture(sport_key='soccer_uefa_champs_league',csv_code=None),fetch_cup=cup)
        self.assertEqual(data['teams']['home']['overall']['last10']['sample'],10)
        self.assertEqual(data['teams']['home']['xg']['scope'],'domestic_context')
        self.assertEqual(data['h2h']['sample'],0)

    def test_descriptive_function_does_not_mutate_source_frames_or_event(self):
        scores,xg=history(),history(xg=True)
        event=fixture();before=deepcopy(event)
        score_before,xg_before=scores.copy(deep=True),xg.copy(deep=True)
        attrs=deepcopy(scores.attrs)
        data=self.collect(event=event,scores=scores,xg=xg)
        pd.testing.assert_frame_equal(scores,score_before)
        pd.testing.assert_frame_equal(xg,xg_before)
        self.assertEqual(scores.attrs,attrs);self.assertEqual(event,before)
        json.dumps(data,allow_nan=False)


if __name__=='__main__':
    unittest.main()
