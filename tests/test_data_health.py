import unittest
from datetime import datetime, timezone
from pathlib import Path
import pandas as pd
from data_health import check_source, observe_league, table_rows
from test_regressions import load_functions


class DataHealthTests(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,10,20,tzinfo=timezone.utc)
        self.frame=pd.DataFrame([dict(HomeTeam='Oxford',AwayTeam='Wigan',Date_dt=pd.Timestamp('2026-10-01'))])

    def test_exact_verified_alias_and_unmatched_are_distinct(self):
        self.assertEqual(check_source(self.frame,'Oxford United',True,self.now)['matched_name'],'Oxford')
        self.assertEqual(check_source(self.frame,'Wigan Athletic',True,self.now)['matched_name'],'Wigan')
        self.assertEqual(check_source(self.frame,'Oxford City',True,self.now)['status'],'unmatched')
        self.assertEqual(check_source(self.frame,'Wigan',True,self.now)['status'],'matched')

    def test_unsupported_download_failure_stale_and_future_dates(self):
        self.assertEqual(check_source(None,'A',False,self.now)['status'],'unsupported')
        self.assertEqual(check_source(None,'A',True,self.now)['status'],'load_error')
        for date in ('2020-01-01','2026-10-21','invalid'):
            frame=self.frame.copy();frame['Date_dt']=date
            self.assertEqual(check_source(frame,'Oxford',True,self.now)['status'],'stale')

    def test_counts_are_observed_unique_teams_not_claim_of_complete_roster(self):
        matches=[dict(home_team='Oxford United',away_team='Wigan Athletic')]*2+[dict(home_team='Unknown',away_team='Wigan Athletic')]
        r=observe_league('league','League','E2',False,self.frame,None,matches,self.now)
        self.assertEqual((r['teams_checked'],r['teams_with_problems']),(3,1))
        self.assertTrue(all(t['understat']['status']=='unsupported' for t in r['teams']))
        self.assertEqual(observe_league('league','League','E2',False,self.frame,None,[],self.now)['teams_checked'],0)

    def test_all_configured_leagues_shown_with_partial_scope_and_unresolved_counts(self):
        f=load_functions(Path(__file__).resolve().parents[1]/'app.py')
        report={'leagues':[dict(key='soccer_epl',cross_source_checked=20,cross_source_matched=16)]}
        rows=table_rows(f['LEAGUES'],f['UNDERSTAT_LEAGUES'],{},report)
        self.assertEqual(len(rows),38)
        epl=next(r for r in rows if r['ลีก']==f['LEAGUES']['soccer_epl'][0])
        self.assertEqual(epl['ยังจับคู่ข้ามแหล่งไม่ได้'],4)
        self.assertEqual(epl['ทีม API ที่ตรวจจริง'],0)
        self.assertIn('ยังไม่มีชื่อ API',epl['ขอบเขต'])
