"""Synthetic score fixtures only; no football provider request or real DB access."""
import unittest
from datetime import datetime, timezone
from copy import deepcopy

import pandas as pd

from domestic_data import (resolve_domestic_sources, build_domestic_history,
                           season_for_kickoff, safe_match_team)
from history_data import team_history


LEAGUES = {
    'soccer_epl': dict(csv_code='E0',label='Premier League',understat='EPL'),
    'soccer_germany_bundesliga': dict(csv_code='D1',label='Bundesliga',understat='Bundesliga'),
}


def history(team, away=False, season='2627', dates=None):
    dates=dates or [f'2026-10-{day:02d}' for day in range(1,11)]
    rows=[]
    for i,date in enumerate(dates):
        rows.append(dict(Date_dt=pd.Timestamp(date),HomeTeam=f'Opponent {i}' if away else team,
                         AwayTeam=team if away else f'Opponent {i}',FTHG=i%3,FTAG=(i+1)%3,
                         _history_season=season))
    frame=pd.DataFrame(rows)
    frame.attrs['history_diagnostics']={'status':'ok','sources':[dict(url='https://example.invalid/synthetic.csv',season=season,status='ok')]}
    return frame


class DomesticDataTests(unittest.TestCase):
    def setUp(self):
        self.kickoff=datetime(2026,10,21,19,tzinfo=timezone.utc)
        self.frames={'soccer_epl':history('Arsenal',away=True),
                     'soccer_germany_bundesliga':history('Bayern Munich')}

    def resolve(self,names=('Bayern Munich','Arsenal'),frames=None):
        return resolve_domestic_sources(names,self.frames if frames is None else frames,self.kickoff,LEAGUES)

    def test_exact_bayern_arsenal_resolve_different_current_leagues(self):
        results=self.resolve()
        self.assertEqual(results['Bayern Munich']['sport_key'],'soccer_germany_bundesliga')
        self.assertEqual(results['Arsenal']['sport_key'],'soccer_epl')
        for value in results.values():
            self.assertEqual(value['status'],'matched');self.assertEqual(value['source_season'],'2627')
            self.assertTrue(value['coverage_complete'])
        self.assertEqual(results['Bayern Munich']['source_diagnostics']['soccer_germany_bundesliga']['mapping'],'exact')

    def test_season_boundary_and_explicit_timezone(self):
        self.assertEqual(season_for_kickoff(datetime(2026,6,30,12,tzinfo=timezone.utc)),'2526')
        self.assertEqual(season_for_kickoff(datetime(2026,7,1,tzinfo=timezone.utc)),'2627')
        self.assertIsNone(season_for_kickoff('2026-10-21T19:00:00'))
        out=resolve_domestic_sources(['Bayern Munich'],self.frames,datetime(2026,10,21),LEAGUES)
        self.assertEqual(out['Bayern Munich']['status'],'invalid_kickoff')

    def test_old_membership_does_not_identify_relegated_club_current_league(self):
        self.frames['soccer_epl']=history('Arsenal',season='2526',dates=['2026-05-01'])
        row=self.resolve()['Arsenal']
        self.assertEqual(row['status'],'stale');self.assertIsNone(row['sport_key'])

    def test_same_name_in_multiple_current_leagues_is_ambiguous(self):
        self.frames['soccer_epl']=history('Bayern Munich',away=True)
        result=self.resolve()['Bayern Munich']
        self.assertEqual(result['status'],'ambiguous');self.assertIsNone(result['sport_key'])

    def test_wrong_season_label_cannot_pass_even_with_current_match_date(self):
        self.frames['soccer_germany_bundesliga']=history('Bayern Munich',season='2526')
        result=self.resolve()['Bayern Munich']
        self.assertNotEqual(result['status'],'matched')
        self.assertGreater(result['source_diagnostics']['soccer_germany_bundesliga']['invalid_season_rows'],0)

    def test_no_season_is_not_guessed_from_match_dates(self):
        frame=history('Bayern Munich').drop(columns=['_history_season'])
        result=self.resolve(frames={'soccer_germany_bundesliga':frame})['Bayern Munich']
        self.assertEqual(result['status'],'unknown_season')

    def test_future_current_season_rows_cannot_prove_membership(self):
        self.frames['soccer_germany_bundesliga']=history('Bayern Munich',dates=['2026-10-22'])
        self.assertNotEqual(self.resolve()['Bayern Munich']['status'],'matched')

    def test_kickoff_day_is_excluded_conservatively(self):
        self.frames['soccer_germany_bundesliga']=history('Bayern Munich',dates=['2026-10-21'])
        self.assertNotEqual(self.resolve()['Bayern Munich']['status'],'matched')

    def test_lossy_unicode_normalization_and_similar_teams_never_match(self):
        self.assertEqual(safe_match_team('Málaga',['Mlaga']),(None,'lossy_normalization'))
        self.assertIsNone(safe_match_team('Bayern Munich',['Bayern Munich II'])[0])
        self.assertIsNone(safe_match_team('Arsenal',['Arsenal Women'])[0])
        self.assertIsNone(safe_match_team('Arsenal',['Arsenal U21'])[0])
        self.assertEqual(safe_match_team('BAYERN MUNICH',['Bayern Munich']),('Bayern Munich','exact'))
        self.assertEqual(safe_match_team('Oxford United',['Oxford']),('Oxford','verified_alias'))

    def test_name_changes_without_reviewed_alias_fail_closed(self):
        self.frames['soccer_germany_bundesliga']=history('FC Bayern München')
        self.assertEqual(self.resolve()['Bayern Munich']['status'],'unmatched')

    def test_partial_download_failure_preserves_supported_side_and_scope(self):
        failed=pd.DataFrame();failed.attrs['history_diagnostics']={'status':'http_error','sources':[dict(http_status=404)]}
        self.frames['soccer_epl']=failed
        out=self.resolve()
        self.assertEqual(out['Bayern Munich']['status'],'matched')
        self.assertFalse(out['Bayern Munich']['coverage_complete'])
        self.assertNotEqual(out['Arsenal']['status'],'matched')
        self.assertEqual(out['Arsenal']['source_diagnostics']['soccer_epl']['status'],'http_error')
        combined=build_domestic_history(out['Bayern Munich'],out['Arsenal'],self.frames)
        games,diag=team_history(combined,'Bayern Munich',self.kickoff,'home')
        self.assertEqual(len(games),10)
        self.assertEqual(len(combined[combined.AwayTeam=='Arsenal']),0)
        self.assertEqual(combined.attrs['domestic_sources']['away']['status'],out['Arsenal']['status'])

    def test_all_sources_failed_is_reported_separately_from_name_unmatched(self):
        self.assertEqual(self.resolve(frames={})['Arsenal']['status'],'source_error')
        self.assertEqual(self.resolve(names=['Unknown Club'])['Unknown Club']['status'],'unmatched')

    def test_current_membership_can_be_too_old(self):
        frames={k:history('Arsenal',dates=['2026-07-01']) for k in ('soccer_epl',)}
        kickoff=datetime(2027,1,15,tzinfo=timezone.utc)
        result=resolve_domestic_sources(['Arsenal'],frames,kickoff,LEAGUES)['Arsenal']
        self.assertEqual(result['status'],'stale')

    def test_full_histories_preserved_with_source_provenance(self):
        old=history('Bayern Munich',season='2526',dates=['2026-05-01'])
        self.frames['soccer_germany_bundesliga']=pd.concat([old,self.frames['soccer_germany_bundesliga']],ignore_index=True)
        self.frames['soccer_germany_bundesliga'].attrs=deepcopy(old.attrs)
        original=self.frames['soccer_germany_bundesliga'].copy(deep=True)
        out=self.resolve();combined=build_domestic_history(out['Bayern Munich'],out['Arsenal'],self.frames)
        self.assertEqual(len(combined),21)
        self.assertEqual(set(combined._domestic_sport_key),set(LEAGUES))
        self.assertIn('2526',set(combined._history_season))
        self.assertTrue(combined.attrs['history_diagnostics']['sources'])
        self.assertEqual(combined.attrs['domestic_sources']['home']['csv_code'],'D1')
        pd.testing.assert_frame_equal(self.frames['soccer_germany_bundesliga'],original)

    def test_foreign_names_are_namespaced_preventing_cross_league_contamination(self):
        # Arsenal appears as an EPL-selected target and an old Bundesliga label.
        # It must not contaminate Arsenal's selected EPL away history.
        foreign=history('Arsenal',away=True,season='2526',dates=['2026-05-01'])
        self.frames['soccer_germany_bundesliga']=pd.concat([self.frames['soccer_germany_bundesliga'],foreign],ignore_index=True)
        out=self.resolve();combined=build_domestic_history(out['Bayern Munich'],out['Arsenal'],self.frames)
        games,diag=team_history(combined,'Arsenal',self.kickoff,'away')
        self.assertEqual(len(games),10)
        self.assertEqual(set(games._domestic_sport_key),{'soccer_epl'})
        self.assertIn('__domestic__[soccer_germany_bundesliga]::Arsenal',set(combined.AwayTeam))

    def test_same_source_clubs_share_one_full_history_not_double_count(self):
        self.frames['soccer_epl']=history('Arsenal')
        self.frames['soccer_epl'].loc[0,'AwayTeam']='Chelsea'
        out=self.resolve(names=['Arsenal','Chelsea'])
        combined=build_domestic_history(out['Arsenal'],out['Chelsea'],self.frames)
        self.assertEqual(len(combined),10)
        self.assertEqual(combined.iloc[0]['AwayTeam'],'Chelsea')

    def test_same_club_on_both_sides_is_not_a_valid_fixture(self):
        out=self.resolve();row=out['Arsenal']
        combined=build_domestic_history(row,row,self.frames)
        self.assertTrue(combined.empty)
        self.assertEqual(combined.attrs['domestic_sources']['home']['status'],'ambiguous')

    def test_conflicting_scores_are_not_chosen_arbitrarily(self):
        frame=self.frames['soccer_germany_bundesliga']
        duplicate=frame.iloc[[0]].copy();duplicate['FTHG']=99
        self.frames['soccer_germany_bundesliga']=pd.concat([frame,duplicate],ignore_index=True)
        out=self.resolve();combined=build_domestic_history(out['Bayern Munich'],out['Arsenal'],self.frames)
        self.assertEqual(out['Bayern Munich']['source_diagnostics']['soccer_germany_bundesliga']['conflicting_result_rows'],2)
        self.assertNotIn(99,combined.FTHG.tolist())


if __name__=='__main__':unittest.main()
