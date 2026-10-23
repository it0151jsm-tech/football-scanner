"""Point-in-time personal A-context checks: synthetic rows and temporary SQLite only."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import unittest
from unittest.mock import patch

import pandas as pd

from domestic_data import season_for_kickoff
from history_data import team_history
from personal_context import fixture_context, supplement_form_history
from personal_stats import save_verified

UTC=timezone.utc


class PersonalContextTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime.now(UTC)
        self.target=self.now+timedelta(days=2)
        self.season=season_for_kickoff(self.target)
        self.db=sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)

    def record(self, **changes):
        row=dict(revision_id=1,match_key='historical-1',source_match_id='provider-old-1',
                 kickoff_utc=(self.now-timedelta(days=10)).isoformat(),
                 home_team='Arsenal',away_team='Other Club',home_goals=2,away_goals=1,
                 league='soccer_epl',season=self.season,source='synthetic user-reviewed source',
                 source_url='https://example.invalid/synthetic-evidence',
                 recorded_at=(self.now-timedelta(hours=2)).isoformat(),
                 received_at=(self.now-timedelta(hours=2)).isoformat(),
                 reviewed_at=(self.now-timedelta(hours=1)).isoformat(),
                 evidence_mode='observed',reviewed=True,completed=True,verified=True,
                 stats_json=json.dumps({'xG':{'home':1.8,'away':.9}}))
        row.update(changes)
        return row

    def context(self, records=(), *, away_records=(), **changes):
        kwargs=dict(home='Arsenal',away='Chelsea',kickoff=self.target.isoformat(),
                    sport_key='soccer_epl',event_id='target-fixture',analysis_at=self.now.isoformat())
        kwargs.update(changes)
        def fetch(*args,**kw):
            return deepcopy(list(records if kw['team']==kwargs['home'] else away_records))
        with patch('personal_context.eligible_history',side_effect=fetch) as query:
            result=fixture_context(self.db,**kwargs)
        return (*result,query)

    def base(self, **changes):
        row=dict(Date_dt=pd.Timestamp((self.now-timedelta(days=5)).date()),HomeTeam='Arsenal',
                 AwayTeam='CSV Opponent',FTHG=1,FTAG=0,
                 _domestic_sport_key='soccer_epl',_history_season=self.season)
        row.update(changes)
        frame=pd.DataFrame([row]);frame.attrs={'history_diagnostics':{'status':'ok','sources':[{'source':'existing-csv'}]}}
        return frame

    def test_empty_history_returns_explicit_unavailable_context_without_model(self):
        ctx,frame,query=self.context()
        self.assertFalse(ctx['available']);self.assertTrue(frame.empty)
        self.assertEqual(ctx['status'],'no_eligible_history')
        self.assertFalse(ctx['model_enabled']);self.assertFalse(ctx['calibrated'])
        self.assertEqual(ctx['form_fallback_sides'],[])
        self.assertEqual(query.call_count,2)

    def test_unsupported_league_never_queries_or_guesses_team_membership(self):
        ctx,frame,query=self.context([self.record()],sport_key='soccer_uefa_champs_league')
        query.assert_not_called();self.assertTrue(frame.empty)
        self.assertEqual(ctx['home']['status'],'unsupported_league')
        self.assertFalse(ctx['model_enabled'])

    def test_explicit_cup_team_sources_route_each_club_to_its_proven_league(self):
        home=self.record(home_team='Bayern Munich',league='soccer_germany_bundesliga')
        away=self.record(home_team='Domestic Opponent',away_team='Arsenal',match_key='away-past')
        ctx,frame,query=self.context([home],away_records=[away],home='Bayern Munich',away='Arsenal',
            sport_key='soccer_uefa_champs_league',team_sources={'home':'soccer_germany_bundesliga','away':'soccer_epl'})
        self.assertEqual([call.kwargs['league'] for call in query.call_args_list],['soccer_germany_bundesliga','soccer_epl'])
        self.assertEqual(set(frame._domestic_sport_key),{'soccer_germany_bundesliga','soccer_epl'})
        self.assertFalse(ctx['model_enabled']);self.assertNotIn('ev_pct',ctx)

    def test_invalid_target_or_identical_teams_do_not_query(self):
        for changes in (dict(kickoff='2026-10-25T19:00:00'),dict(kickoff='invalid'),dict(away='Arsenal')):
            with self.subTest(changes=changes):
                ctx,frame,query=self.context([self.record()],**changes)
                self.assertTrue(frame.empty);self.assertEqual(ctx['status'],'invalid_fixture');query.assert_not_called()

    def test_only_exact_team_identity_is_accepted_without_fuzzy_alias(self):
        for name in ('Arsenal Women','Arsenal U21','ARSENAL','Arsenall'):
            with self.subTest(name=name):
                ctx,frame,_=self.context([self.record(home_team=name)])
                self.assertTrue(frame.empty);self.assertFalse(ctx['available'])

    def test_wrong_league_and_wrong_season_are_rejected_at_context_boundary(self):
        for changes in (dict(league='soccer_germany_bundesliga'),dict(season='2526'),dict(league=None),dict(season=None)):
            with self.subTest(changes=changes):
                ctx,frame,_=self.context([self.record(**changes)])
                self.assertTrue(frame.empty);self.assertFalse(ctx['available'])

    def test_unknown_unreviewed_research_or_unfinished_rows_are_not_form_evidence(self):
        for changes in (dict(evidence_mode='research'),dict(reviewed=False),dict(completed=False),dict(verified=False)):
            with self.subTest(changes=changes):
                ctx,frame,_=self.context([self.record(**changes)])
                self.assertTrue(frame.empty);self.assertFalse(ctx['available'])

    def test_receipt_and_review_must_both_precede_analysis_cutoff(self):
        for field in ('received_at','reviewed_at'):
            for value in (self.now.isoformat(),(self.now+timedelta(seconds=1)).isoformat(),None,'invalid','2026-10-22T12:00:00'):
                with self.subTest(field=field,value=value):
                    ctx,frame,_=self.context([self.record(**{field:value})])
                    self.assertTrue(frame.empty);self.assertFalse(ctx['available'])

    def test_analysis_after_target_uses_target_as_the_hard_cutoff(self):
        ctx,frame,query=self.context([self.record()],analysis_at=(self.target+timedelta(days=1)).isoformat())
        self.assertEqual(ctx['as_of'],self.target.isoformat())
        self.assertEqual(query.call_args_list[0].kwargs['as_of'],self.target)

    def test_review_cannot_precede_receipt(self):
        ctx,frame,_=self.context([self.record(received_at=(self.now-timedelta(minutes=20)).isoformat(),
                                              reviewed_at=(self.now-timedelta(minutes=40)).isoformat())])
        self.assertTrue(frame.empty)

    def test_target_self_match_is_excluded_by_local_or_provider_identifier(self):
        for changes in (dict(match_key='target-fixture'),dict(source_match_id='target-fixture')):
            with self.subTest(changes=changes):
                ctx,frame,query=self.context([self.record(**changes)])
                self.assertTrue(frame.empty)
                self.assertEqual(query.call_args_list[0].kwargs['exclude_match_key'],'target-fixture')

    def test_same_utc_day_is_excluded_even_when_receipt_and_review_precede_target(self):
        target=self.now.replace(hour=20,minute=0,second=0,microsecond=0)-timedelta(days=1)
        record=self.record(kickoff_utc=(target-timedelta(hours=6)).isoformat(),
                           received_at=(target-timedelta(hours=3)).isoformat(),
                           reviewed_at=(target-timedelta(hours=2)).isoformat(),
                           recorded_at=(target-timedelta(hours=3)).isoformat())
        ctx,frame,_=self.context([record],kickoff=target.isoformat(),analysis_at=(target-timedelta(hours=1)).isoformat())
        self.assertTrue(frame.empty)

    def test_post_analysis_or_future_results_are_excluded(self):
        for stamp in (self.now.isoformat(),(self.now+timedelta(hours=1)).isoformat(),self.target.isoformat()):
            with self.subTest(stamp=stamp):
                ctx,frame,_=self.context([self.record(kickoff_utc=stamp)])
                self.assertTrue(frame.empty)

    def test_incomplete_boolean_negative_or_fractional_scores_are_not_synthesized(self):
        for value in (None,True,-1,1.5,'2',float('nan')):
            with self.subTest(value=value):
                ctx,frame,_=self.context([self.record(home_goals=value)])
                self.assertTrue(frame.empty)

    def test_duplicate_source_records_count_once_for_form(self):
        row=self.record();ctx,frame,_=self.context([row,dict(row,revision_id=2)])
        self.assertEqual(len(frame),1);self.assertTrue(ctx['available'])
        self.assertEqual(ctx['excluded_duplicates'],1)

    def test_conflicting_scores_are_quarantined_not_arbitrarily_selected(self):
        row=self.record();ctx,frame,_=self.context([row,dict(row,match_key='another-source',home_goals=3)])
        self.assertTrue(frame.empty);self.assertFalse(ctx['available'])
        self.assertEqual(ctx['excluded_conflicts'],2)

    def test_personal_xg_remains_recorded_context_without_poisson_ev_or_xg_frame(self):
        ctx,frame,_=self.context([self.record()])
        self.assertTrue(ctx['available']);self.assertEqual(ctx['home']['sample'],1)
        self.assertEqual(json.loads(ctx['home']['records'][0]['stats_json'])['xG']['home'],1.8)
        self.assertTrue({'xG_home','xG_away','ev_pct','p_win','lambda_home','lambda_away'}.isdisjoint(frame.columns))
        self.assertFalse(ctx['model_enabled']);self.assertFalse(ctx['calibrated'])
        self.assertIn('ไม่สร้าง EV',ctx['model_reason'])

    def test_valid_existing_source_and_attributes_are_preserved_without_pooling(self):
        ctx,personal,_=self.context([self.record()]);base=self.base();before=base.copy(deep=True)
        result=supplement_form_history(base,personal,ctx)
        pd.testing.assert_frame_equal(base,before);pd.testing.assert_frame_equal(result,before)
        self.assertEqual(result.attrs,before.attrs)
        self.assertEqual(ctx['form_fallback_sides'],[])
        self.assertEqual(ctx['home']['form_status'],'existing_source_retained')

    def test_absent_source_can_fill_only_form_from_reviewed_scores(self):
        ctx,personal,_=self.context([self.record()]);result=supplement_form_history(pd.DataFrame(),personal,ctx)
        self.assertEqual(len(result),1);self.assertEqual(ctx['form_fallback_sides'],['home'])
        self.assertEqual(ctx['home']['form_status'],'reviewed_personal_scores_only')
        games,_=team_history(result,'Arsenal',self.target,'home');self.assertEqual(len(games),1)
        self.assertTrue({'xG_home','xG_away','ev_pct','p_win'}.isdisjoint(result.columns))

    def test_future_only_base_cannot_prevent_valid_historical_form_fallback(self):
        ctx,personal,_=self.context([self.record()]);base=self.base(Date_dt=pd.Timestamp(self.target.date())+pd.Timedelta(days=1))
        result=supplement_form_history(base,personal,ctx)
        self.assertEqual(ctx['form_fallback_sides'],['home'])
        games,_=team_history(result,'Arsenal',self.target,'home')
        self.assertEqual(len(games),1);self.assertEqual(games.iloc[0]['FTHG'],2)

    def test_base_rows_without_usable_scores_do_not_block_form_fallback(self):
        ctx,personal,_=self.context([self.record()]);base=self.base(FTHG=None,FTAG=None)
        before=base.copy(deep=True);result=supplement_form_history(base,personal,ctx)
        pd.testing.assert_frame_equal(base,before)
        self.assertEqual(ctx['form_fallback_sides'],['home'])
        games,_=team_history(result,'Arsenal',self.target,'home')
        self.assertEqual(len(games),1);self.assertEqual(games.iloc[0]['FTHG'],2)

    def test_wrong_league_base_does_not_block_or_contaminate_personal_form(self):
        ctx,personal,_=self.context([self.record()]);base=self.base(_domestic_sport_key='soccer_germany_bundesliga')
        before=base.copy(deep=True);result=supplement_form_history(base,personal,ctx)
        pd.testing.assert_frame_equal(base,before)
        self.assertEqual(ctx['form_fallback_sides'],['home'])
        games,_=team_history(result,'Arsenal',self.target,'home')
        self.assertEqual(len(games),1)
        self.assertEqual(set(games._domestic_sport_key),{'soccer_epl'})

    def test_existing_club_not_accidentally_pooled_from_other_club_fallback(self):
        # Arsenal retains CSV; Chelsea needs personal evidence. A Chelsea result
        # against Arsenal must not silently add uploaded data to Arsenal's sample.
        row=self.record(home_team='Arsenal',away_team='Chelsea',match_key='chelsea-away')
        ctx,personal,_=self.context([],away_records=[row]);base=self.base()
        result=supplement_form_history(base,personal,ctx)
        self.assertEqual(ctx['form_fallback_sides'],['away'])
        home,_=team_history(result,'Arsenal',self.target,'home')
        away,_=team_history(result,'Chelsea',self.target,'away')
        self.assertEqual(len(home),1);self.assertEqual(home.iloc[0]['FTHG'],1)
        self.assertEqual(len(away),1)

    def test_observed_storage_round_trip_uses_actual_receipt_and_review_times(self):
        save_verified(self.db,match_key='actual-timestamp-test',
            kickoff_utc=(self.now-timedelta(days=3)).isoformat(),home_team='Arsenal',away_team='Other Club',
            home_goals=2,away_goals=1,source='Synthetic manually reviewed evidence',stats={'xG':{'home':1.5,'away':.7}},
            league='soccer_epl',season=self.season,reviewed=True,completed=True,evidence_mode='observed')
        analysis=datetime.now(UTC)
        ctx,frame=fixture_context(self.db,home='Arsenal',away='Chelsea',kickoff=self.target.isoformat(),
            sport_key='soccer_epl',event_id='future-target',analysis_at=analysis.isoformat())
        self.assertEqual(len(frame),1);self.assertTrue(ctx['available']);self.assertFalse(ctx['model_enabled'])
        record=ctx['home']['records'][0]
        self.assertGreaterEqual(datetime.fromisoformat(record['received_at']),self.now)
        self.assertLess(datetime.fromisoformat(record['reviewed_at']),analysis)
        prior,prior_frame=fixture_context(self.db,home='Arsenal',away='Chelsea',kickoff=self.target.isoformat(),
            sport_key='soccer_epl',analysis_at=(self.now-timedelta(minutes=1)).isoformat())
        self.assertTrue(prior_frame.empty);self.assertFalse(prior['available'])


if __name__=='__main__':unittest.main()
