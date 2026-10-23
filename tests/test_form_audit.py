"""Replay synthetic evidence only; no provider calls or databases."""
import copy
import unittest
from datetime import datetime, timezone

import pandas as pd

from form_audit import audit_fixture_forms


KICKOFF=datetime(2026,10,21,19,tzinfo=timezone.utc)


def matches(team='Bayern Munich',away=False,gf=None):
    gf=gf or [2]*10
    rows=[]
    sport='soccer_epl' if away else 'soccer_germany_bundesliga'
    for i,value in enumerate(gf):
        day=pd.Timestamp('2026-09-20')+pd.Timedelta(days=i*3)
        rows.append(dict(Date_dt=day,HomeTeam=f'__domestic__[{sport}]::Opponent {i}' if away else team,
                         AwayTeam=team if away else f'__domestic__[{sport}]::Opponent {i}',
                         FTHG=1 if away else value,FTAG=value if away else 1,
                         _domestic_sport_key=sport,_history_season='2627',
                         _history_source_url=f'https://example.invalid/{sport}/2627.csv',
                         _history_received_at='2026-10-21T12:00:00+00:00',
                         _history_payload_sha256='a'*64))
    return pd.DataFrame(rows)


def fixture(home=None,away=None):
    frame=pd.concat([matches() if home is None else home,matches('Arsenal',True) if away is None else away],ignore_index=True)
    defs={'home':('soccer_germany_bundesliga','D1','Bundesliga','Bayern Munich'),
          'away':('soccer_epl','E0','Premier League','Arsenal')}
    frame.attrs['domestic_sources']={side:dict(sport_key=sport,csv_code=code,input_name=name,csv_name=name,
                                             source_diagnostics={sport:dict(label=label,csv_code=code)}) for side,(sport,code,label,name) in defs.items()}
    frame.attrs['domestic_source_reports']={sport:dict(source={'quality_audit':{'duplicate_rows':2,'conflicting_rows':0,'invalid_season_rows':0,'details':[]}}) for sport,_,_,_ in defs.values()}
    return frame


class FormAuditTests(unittest.TestCase):
    def test_personal_fallback_keeps_its_own_provider_and_revision(self):
        frame=fixture()
        frame['_personal_source']='owner supplied screenshot'
        frame['_personal_revision_id']=123
        frame['AwayTeam']=frame.AwayTeam.str.replace('__domestic__','__personal__',regex=False)
        result=audit_fixture_forms(frame,'Bayern Munich','Arsenal',KICKOFF)['home']['windows']['5']['rows'][0]
        self.assertEqual(result['source'],'Personal reviewed: owner supplied screenshot')
        self.assertEqual(result['personal_revision_id'],123)
        self.assertIsNone(result['csv_code'])
        self.assertEqual(result['opponent'],'Opponent 5')

    def audit(self,frame=None,expected=None,kickoff=KICKOFF):
        return audit_fixture_forms(fixture() if frame is None else frame,'Bayern Munich','Arsenal',kickoff,expected)

    def test_exact_five_point_two_is_twenty_six_divided_by_five_and_not_trimmed(self):
        frame=fixture(matches(gf=[1,1,1,1,1,10,7,4,3,2]))
        result=self.audit(frame,{'home':{'last5_gf':5.2,'last5_ga':1,'sample5':5}})['home']['windows']['5']
        self.assertEqual(result['sum_gf'],26);self.assertEqual(result['count'],5);self.assertEqual(result['mean_gf'],5.2)
        self.assertEqual([r['gf'] for r in result['rows']],[10,7,4,3,2]);self.assertTrue(result['matches_profile'])
        self.assertIn('high_mean_gf_review_only',result['warnings'])
        self.assertIn('high_single_match_gf_review_only',result['rows'][0]['review_flags'])

    def test_home_and_away_goal_orientation_five_and_ten(self):
        frame=fixture(matches(gf=list(range(1,11))),matches('Arsenal',True,list(range(10,0,-1))))
        result=self.audit(frame)
        self.assertEqual(result['home']['windows']['5']['mean_gf'],8)
        self.assertEqual(result['away']['windows']['5']['mean_gf'],3)
        self.assertEqual(result['home']['windows']['10']['mean_gf'],5.5)
        self.assertEqual(result['away']['windows']['10']['mean_ga'],1)
        row=result['away']['windows']['5']['rows'][0]
        self.assertEqual(row['score'],'1-5');self.assertEqual(row['gf'],5);self.assertEqual(row['opponent'],'Opponent 5')

    def test_rows_expose_date_opponent_score_league_season_and_real_receipt_fields(self):
        result=self.audit()['home']['windows']['5']['rows'][0]
        self.assertEqual(result['date'],'2026-10-05');self.assertEqual(result['league'],'Bundesliga')
        self.assertEqual(result['season'],'2627');self.assertEqual(result['csv_code'],'D1')
        self.assertEqual(result['source_url'],'https://example.invalid/soccer_germany_bundesliga/2627.csv')
        self.assertEqual(result['payload_sha256'],'a'*64);self.assertEqual(result['received_at'],'2026-10-21T12:00:00+00:00')
        self.assertEqual(result['home_team'],'Bayern Munich');self.assertEqual(result['away_team'],'Opponent 5')

    def test_duplicates_do_not_inflate_sample_and_are_reported(self):
        home=matches();home=pd.concat([home,home.tail(2)],ignore_index=True)
        result=self.audit(fixture(home))['home']
        self.assertEqual(result['excluded']['duplicate_rows'],2)
        self.assertEqual(result['windows']['10']['count'],10);self.assertEqual(result['windows']['10']['sum_gf'],20)
        self.assertEqual(result['upstream_quality']['duplicate_rows'],2)

    def test_post_mapping_quality_is_kept_separate_from_source_quality(self):
        frame=fixture()
        frame.attrs['domestic_source_reports']['soccer_germany_bundesliga']['post_mapping_quality']={'conflicting_rows':2,'details':[{'original_home':'Bayern  Munich'}]}
        audit=self.audit(frame)['home']
        self.assertEqual(audit['upstream_quality']['duplicate_rows'],2)
        self.assertEqual(audit['post_mapping_quality']['conflicting_rows'],2)
        self.assertEqual(audit['post_mapping_quality']['details'][0]['original_home'],'Bayern  Munich')

    def test_conflicting_scores_for_same_fixture_are_excluded_not_first_wins(self):
        home=matches();other=home.iloc[[-1]].copy();other['FTHG']=8
        result=self.audit(fixture(pd.concat([home,other],ignore_index=True)))['home']
        self.assertEqual(result['excluded']['conflicting_rows'],2)
        self.assertEqual(result['windows']['10']['count'],9)
        self.assertNotIn('2026-10-17',[r['date'] for r in result['windows']['10']['rows']])

    def test_wrong_and_missing_seasons_are_excluded(self):
        home=matches();home.loc[0,'_history_season']='2526';home.loc[1,'_history_season']=None
        result=self.audit(fixture(home))['home']
        self.assertEqual(result['excluded']['invalid_season_rows'],2);self.assertEqual(result['windows']['10']['count'],8)

    def test_kickoff_day_and_future_rows_are_not_used(self):
        home=matches();home.loc[8,'Date_dt']=pd.Timestamp('2026-10-21');home.loc[9,'Date_dt']=pd.Timestamp('2026-10-22')
        result=self.audit(fixture(home))['home']
        self.assertEqual(result['excluded']['on_or_after_kickoff_day'],2)
        self.assertTrue(all(r['date']<'2026-10-21' for r in result['windows']['10']['rows']))

    def test_invalid_dates_scores_and_boolean_results_report_invalid_rows(self):
        home=matches();home['FTHG']=home.FTHG.astype(object)
        home.loc[0,'FTHG']=True;home.loc[1,'FTHG']=-1;home.loc[2,'FTHG']=1.5;home.loc[3,'Date_dt']=pd.NaT
        result=self.audit(fixture(home))['home']
        self.assertEqual(result['excluded']['invalid_rows'],4);self.assertEqual(result['windows']['10']['count'],6)

    def test_wrong_venue_does_not_enter_selected_sample(self):
        home=matches();additional=matches('Bayern Munich',True)
        additional['_domestic_sport_key']='soccer_germany_bundesliga'
        result=self.audit(fixture(pd.concat([home,additional],ignore_index=True)))['home']
        self.assertEqual(result['excluded']['other_venue_rows'],10);self.assertEqual(result['windows']['10']['count'],10)

    def test_saved_mean_or_count_mismatch_is_explicit(self):
        result=self.audit(expected={'home':{'last5_gf':5.2,'last5_ga':1,'sample5':5}})['home']['windows']['5']
        self.assertFalse(result['matches_profile']);self.assertIn('saved_profile_mismatch',result['warnings'])
        self.assertEqual(result['mean_gf'],2);self.assertEqual(result['expected_mean_gf'],5.2)

    def test_no_expected_profile_does_not_claim_verified_agreement(self):
        result=self.audit()['home']['windows']['5']
        self.assertIsNone(result['matches_profile']);self.assertIsNone(result['expected_mean_gf'])

    def test_source_namespaces_do_not_mix_same_club_label_from_another_league(self):
        home=matches();wrong=matches(gf=[15]*10);wrong['_domestic_sport_key']='soccer_epl'
        result=self.audit(fixture(pd.concat([home,wrong],ignore_index=True)))['home']['windows']['10']
        self.assertEqual(result['sum_gf'],20);self.assertEqual(result['count'],10)

    def test_ambiguous_source_without_resolution_is_not_averaged(self):
        frame=fixture();wrong=matches(gf=[15]*10);wrong['_domestic_sport_key']='soccer_epl'
        frame=pd.concat([frame,wrong],ignore_index=True)
        frame.attrs={}
        result=self.audit(frame)['home']
        self.assertEqual(result['status'],'ambiguous_sources');self.assertIsNone(result['windows']['5']['mean_gf'])

    def test_empty_and_invalid_timezone_evidence_never_gets_invented_values(self):
        empty=self.audit(pd.DataFrame())
        self.assertEqual(empty['status'],'no_saved_evidence');self.assertIsNone(empty['home']['windows']['5']['mean_gf'])
        for kickoff in ['invalid','2026-10-21T19:00:00',None]:
            result=self.audit(kickoff=kickoff);self.assertEqual(result['status'],'invalid_kickoff')

    def test_missing_source_receipt_is_visible_not_assigned_an_observation_time(self):
        frame=fixture().drop(columns=['_history_received_at','_history_payload_sha256','_history_source_url'])
        row=self.audit(frame)['home']['windows']['5']['rows'][0]
        self.assertIsNone(row['received_at']);self.assertIsNone(row['payload_sha256'])
        self.assertEqual(row['provenance_status'],'source_url_not_recorded')

    def test_audit_never_mutates_source_data_profiles_or_attrs(self):
        frame=fixture();before=frame.copy(deep=True);attrs=copy.deepcopy(frame.attrs);expected={'home':{'last5_gf':2}}
        expected_before=copy.deepcopy(expected);self.audit(frame,expected)
        pd.testing.assert_frame_equal(frame,before);self.assertEqual(frame.attrs,attrs);self.assertEqual(expected,expected_before)

    def test_partial_sample_keeps_actual_denominator(self):
        result=self.audit(fixture(matches(gf=[7,3])))['home']['windows']['5']
        self.assertEqual(result['count'],2);self.assertEqual(result['sum_gf'],10);self.assertEqual(result['mean_gf'],5)
        self.assertIn('insufficient_sample',result['warnings'])
