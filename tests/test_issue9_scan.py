"""Issue #9 synthetic source-contract regressions, not new production football data.

All scans use extracted real app functions, mocked source frames, and temporary
SQLite. No Odds API, CSV, Understat or personal database is contacted.
"""
import copy
import json
import math
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pandas as pd

import test_regressions as regression
from domestic_data import season_for_kickoff
from market_recommendations import group_fixture_recommendations, recommended_pick_ids
from mobile_dashboard import fixture_dashboard_html, fixture_details_html

UTC = timezone.utc
GERMAN_API = ('VfB Stuttgart', 'Borussia Monchengladbach')
GERMAN_CSV = ('Stuttgart', "M'gladbach")
GERMAN_XG = ('VfB Stuttgart', 'Borussia M.Gladbach')
ENGLISH_API = ('Ipswich Town', 'Nottingham Forest')
ENGLISH_CSV = ('Ipswich', "Nott'm Forest")
ENGLISH_XG = ('Ipswich', 'Nottingham Forest')
SOURCE_URL = 'https://understat.com/getLeagueData/Bundesliga/2026'


class Issue9ScanTests(unittest.TestCase):
    setUp = regression.Regressions.setUp

    def frame(self, home, away, *, xg=False):
        dates = pd.date_range(end=pd.Timestamp(self.kickoff.date())-pd.Timedelta(days=1), periods=10, freq='3D')
        rows = [dict(Date_dt=day, HomeTeam=home, AwayTeam=away, FTHG=2, FTAG=1,
                     _history_season=season_for_kickoff(self.kickoff)) for day in dates]
        frame = pd.DataFrame(rows)
        if xg:
            frame['xG_home'], frame['xG_away'] = 1.8, .9
            frame['source_league'] = 'Bundesliga' if self.sport == 'soccer_germany_bundesliga' else 'EPL'
        source = dict(status='ok', provider='Understat' if xg else 'Football-Data',
                      url=('https://understat.com/getLeagueData/EPL/2026' if self.sport=='soccer_epl' else SOURCE_URL) if xg else 'https://example.invalid/fixtures-only.csv',
                      season=2026 if xg else season_for_kickoff(self.kickoff),
                      http_status=200, received_at=self.now.isoformat(), accepted_rows=len(frame))
        report = dict(status='ok', provider=source['provider'], sources=[source])
        frame.attrs['history_diagnostics'] = report
        if xg:
            frame.attrs['xg_diagnostics'] = copy.deepcopy(report)
        return frame

    def prepare(self, *, english=False, blocked=False):
        self.now = datetime.now(UTC)
        self.kickoff = self.now + timedelta(hours=3)
        self.sport = 'soccer_epl' if english else 'soccer_germany_bundesliga'
        self.api = ENGLISH_API if english else GERMAN_API
        csv_names = ENGLISH_CSV if english else GERMAN_CSV
        xg_names = ENGLISH_XG if english else GERMAN_XG
        self.csv = self.frame(*csv_names)
        self.xg = self.frame(*xg_names, xg=True)
        if blocked:
            self.xg = pd.DataFrame()
            report = dict(status='http_error', provider='Understat', sources=[dict(
                status='http_error', reason_code='http_forbidden', http_status=403,
                url=SOURCE_URL, season=2026, provider='Understat', received_at=self.now.isoformat())])
            self.xg.attrs['history_diagnostics'] = copy.deepcopy(report)
            self.xg.attrs['xg_diagnostics'] = copy.deepcopy(report)
        self.event = dict(id='issue9-synthetic', commence_time=self.kickoff.isoformat(),
                          home_team=self.api[0], away_team=self.api[1], _received_at=self.now.isoformat(),
                          bookmakers=[dict(key='synthetic-quote', title='Synthetic quote', markets=[
                              dict(key='spreads', last_update=self.now.isoformat(), outcomes=[
                                  dict(name=self.api[0],point=-.25,price=2.1)]),
                              dict(key='totals', last_update=self.now.isoformat(), outcomes=[
                                  dict(name='Over',point=2.5,price=2.1),dict(name='Under',point=2.5,price=1.9)])])])
        self.f.update(fetch_odds=Mock(return_value=([self.event], '100', '2')),
                      fetch_history=Mock(side_effect=lambda code:self.csv),
                      fetch_understat_history=Mock(side_effect=lambda key:self.xg),
                      fetch_understat_all_history=Mock(side_effect=AssertionError('no global xG fanout')))

    def scan(self, *, cup=False):
        key = 'soccer_uefa_champs_league' if cup else self.sport
        league, code = self.f['LEAGUES'][key]
        return self.f['scan_league'](key,league,code,24,1.8,2.15,'uk','SYNTHETIC_NO_HTTP')

    def over(self, rows):
        return next(row for row in rows if row['market'] == 'Over/Under' and row['side'] == 'over')

    def analyses(self, row):
        return json.loads(row['analysis_a_json']), json.loads(row['analysis_b_json'])

    def seed_legacy_bills(self):
        with self.f['db']() as con:
            con.execute("INSERT INTO picks(event_id,market,point,odds,status,final_score,profit_units) VALUES(?,?,?,?,?,?,?)",
                        ('issue9-legacy','Asian Handicap',-.25,1.95,'ตรวจแล้ว','2-1',.95))
            for payload,status,profit in [('{damaged old selections_json','แพ้บิล',-1),('', 'รอตรวจผล',None),('[{"pick_id":1}]','ชนะเต็มทุกคู่',.95)]:
                con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES(?,?,?,?)',
                            (payload,status,profit,'original result'))
        return self.legacy_snapshot()

    def legacy_snapshot(self):
        with self.f['db']() as con:
            return dict(picks=[tuple(row) for row in con.execute("SELECT * FROM picks WHERE event_id='issue9-legacy'")],
                        slips=[tuple(row) for row in con.execute('SELECT * FROM slips ORDER BY id')])

    def test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form(self):
        self.prepare(); rows,_,_,_ = self.scan(); a,_ = self.analyses(self.over(rows))
        for side in ('home','away'):
            self.assertEqual(a[side+'_form']['sample5'],5)
            self.assertEqual(a[side+'_form']['sample10'],10)
            self.assertEqual(a[side+'_form']['diagnostics']['mapping'],'verified_alias')
        self.assertEqual(a['home_form']['last5_gf'],2)
        self.assertEqual(a['away_form']['last5_gf'],1)
        self.assertIsNotNone(a['score'])
        self.f['fetch_odds'].assert_called_once()
        self.f['fetch_history'].assert_called_once_with('D1')
        self.f['fetch_understat_history'].assert_called_once_with(self.sport)
        self.f['fetch_understat_all_history'].assert_not_called()

    def test_domestic_b_uses_mock_xg_only_and_actual_quote_contract(self):
        self.prepare(); rows,_,_,_ = self.scan(); row=self.over(rows); a,b=self.analyses(row)
        self.assertTrue(b['available'])
        self.assertEqual(a['xg']['home']['sample'],10)
        self.assertEqual(a['xg']['away']['sample'],10)
        self.assertEqual(b['ou_line'],2.5)
        self.assertGreater(row['ev_pct'],0)
        self.assertTrue(all(math.isfinite(value) for value in b['expected_goals']))
        self.assertAlmostEqual(sum(b['hda'].values()),100,delta=.03)
        expected=self.f['estimate_goals'](self.xg,*self.api,self.kickoff)
        self.assertEqual(b['expected_goals'],[expected['home_lambda'],expected['away_lambda']])
        self.assertEqual(row['received_at'],self.now.isoformat())
        self.assertEqual(row['source_updated_at'],self.now.isoformat())
        # Different actual-score evidence can change A, but cannot produce/alter xG lambdas.
        self.csv = self.csv.assign(FTHG=8,FTAG=7)
        rerun,_,_,_=self.scan(); _,again=self.analyses(self.over(rerun))
        self.assertEqual(again['expected_goals'],b['expected_goals'])
        self.assertEqual(again['selection']['ev_pct'],b['selection']['ev_pct'])

    def test_poisson_requires_at_least_five_current_xg_rows_for_both_venues(self):
        self.prepare()
        original=self.xg.copy()
        self.xg=original.tail(5).copy()
        rows,_,_,_=self.scan();a,b=self.analyses(self.over(rows))
        self.assertEqual((a['home_form']['sample10'],a['away_form']['sample10']),(10,10))
        self.assertEqual((a['xg']['home']['sample'],a['xg']['away']['sample']),(5,5))
        self.assertTrue(b['available']);self.assertIsNotNone(self.over(rows)['ev_pct'])
        self.xg=original.tail(4).copy()
        rows,_,_,_=self.scan();a,b=self.analyses(self.over(rows))
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertFalse(b['available']);self.assertIsNone(self.over(rows)['ev_pct'])
        self.assertNotIn('expected_goals',b)

    def test_ipswich_and_nottingham_aliases_recover_both_venues_without_fuzzy(self):
        self.prepare(english=True);rows,_,_,_=self.scan();a,b=self.analyses(self.over(rows))
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertEqual((a['home_form']['sample10'],a['away_form']['sample10']),(10,10))
        self.assertEqual(a['home_form']['diagnostics']['matched_name'],'Ipswich')
        self.assertEqual(a['away_form']['diagnostics']['matched_name'],"Nott'm Forest")
        self.assertTrue(b['available'])
        self.event['home_team']='Ipswich Town U21'
        self.event['bookmakers'][0]['markets'][0]['outcomes'][0]['name']='Ipswich Town U21'
        rerun,_,_,_=self.scan();a,b=self.analyses(self.over(rerun))
        self.assertEqual(a['home_form']['sample5'],0)
        self.assertFalse(b['available'])
        self.assertIsNone(self.over(rerun)['ev_pct'])

    def test_unreviewed_accented_or_similar_team_name_cannot_borrow_verified_data(self):
        self.prepare()
        self.event['away_team']='Borussia Mönchengladbach'
        rows,_,_,_=self.scan();row=self.over(rows);a,b=self.analyses(row)
        self.assertEqual(a['home_form']['sample5'],5)
        self.assertEqual(a['away_form']['sample5'],0)
        self.assertFalse(b['available']);self.assertIsNone(row['ev_pct'])
        self.assertIsNone(a['xg']['away'])

    def test_blocked_understat_preserves_a_and_precise_no_bet_reason(self):
        self.prepare(blocked=True);before=self.seed_legacy_bills()
        rows,snapshots,_,_=self.scan();row=self.over(rows);a,b=self.analyses(row)
        self.assertIsNotNone(a['score'])
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertFalse(b['available']);self.assertNotIn('expected_goals',b)
        self.assertIsNone(row['ev_pct']);self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
        diagnostic=json.dumps(a['xg'].get('diagnostics'),ensure_ascii=False)
        self.assertIn('403',diagnostic);self.assertIn(SOURCE_URL,diagnostic)
        self.assertIn('403',a['xg']['status']);self.assertIn('403',b['status'])
        self.f['save_picks'](rows,snapshots)
        saved=self.f['load_picks'](limit=None)
        saved=saved[saved['event_id']=='issue9-synthetic'].to_dict('records')
        self.assertEqual(recommended_pick_ids(saved,self.f['choose_recommendation']),set())
        fixture=group_fixture_recommendations(saved,self.f['choose_recommendation'])[0]
        rendered=fixture_dashboard_html(fixture)+fixture_details_html(fixture,'offline diagnostic')
        self.assertIn('403',rendered);self.assertIn('Understat',rendered)
        self.assertNotIn('ตัวเลือกเด่น',rendered)
        self.assertEqual(self.legacy_snapshot(),before)

    def test_old_xg_does_not_hide_current_csv_form_or_create_model(self):
        self.prepare()
        self.xg['Date_dt']=self.xg.Date_dt-pd.DateOffset(years=1)
        self.xg['_history_season']='2526'
        rows,_,_,_=self.scan();row=self.over(rows);a,b=self.analyses(row)
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertIsNotNone(a['score']);self.assertFalse(b['available'])
        self.assertIsNone(row['ev_pct']);self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
        self.assertIn('เก่า',a['xg']['status'])

    def test_same_day_and_future_rows_do_not_enter_form_or_xg(self):
        self.prepare();before,_,_,_=self.scan();old_a,old_b=self.analyses(self.over(before))
        for attr in ('csv','xg'):
            frame=getattr(self,attr);attrs=copy.deepcopy(frame.attrs)
            extra=[]
            for offset in (0,1):
                row=frame.iloc[-1].to_dict();row.update(Date_dt=pd.Timestamp(self.kickoff.date())+pd.Timedelta(days=offset),FTHG=9,FTAG=9)
                if attr=='xg':row.update(xG_home=40,xG_away=40)
                extra.append(row)
            out=pd.concat([frame,pd.DataFrame(extra)],ignore_index=True);out.attrs=attrs;setattr(self,attr,out)
        after,_,_,_=self.scan();a,b=self.analyses(self.over(after))
        self.assertEqual(a['home_form']['last5_gf'],old_a['home_form']['last5_gf'])
        self.assertEqual(a['away_form']['sample10'],old_a['away_form']['sample10'])
        self.assertEqual(b['expected_goals'],old_b['expected_goals'])
        self.assertEqual(b['selection']['ev_pct'],old_b['selection']['ev_pct'])

    def test_promoted_current_membership_routes_cup_to_current_domestic_league(self):
        self.prepare(english=True)
        old=self.csv.copy();old['Date_dt']=old.Date_dt-pd.DateOffset(years=1);old['_history_season']='2526'
        self.f['fetch_domestic_rosters']=Mock(return_value={'soccer_epl':self.csv,'soccer_efl_champ':old})
        self.f['fetch_history']=Mock(side_effect=lambda code:self.csv if code=='E0' else old)
        rows,_,_,_=self.scan(cup=True);a,b=self.analyses(self.over(rows))
        self.assertEqual(a['domestic_sources']['home']['sport_key'],'soccer_epl')
        self.assertEqual(a['domestic_sources']['home']['csv_code'],'E0')
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertEqual({call.args[0] for call in self.f['fetch_history'].call_args_list},{'E0'})
        self.assertFalse(b['available']);self.assertEqual(b['scope'],'domestic_context_only')
        self.assertIsNone(self.over(rows)['ev_pct'])

    def test_relegated_current_membership_never_borrows_old_top_tier_xg(self):
        self.prepare(english=True)
        old=self.csv.copy();old['Date_dt']=old.Date_dt-pd.DateOffset(years=1);old['_history_season']='2526'
        self.f['fetch_domestic_rosters']=Mock(return_value={'soccer_epl':old,'soccer_efl_champ':self.csv})
        self.f['fetch_history']=Mock(side_effect=lambda code:self.csv if code=='E1' else old)
        self.f['fetch_understat_history']=Mock(side_effect=AssertionError('secondary division has no supported xG connector'))
        rows,_,_,_=self.scan(cup=True);a,b=self.analyses(self.over(rows))
        self.assertEqual(a['domestic_sources']['home']['sport_key'],'soccer_efl_champ')
        self.assertEqual(a['domestic_sources']['home']['csv_code'],'E1')
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.f['fetch_understat_history'].assert_not_called()
        self.assertFalse(b['available']);self.assertIsNone(self.over(rows)['ev_pct'])
        self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
