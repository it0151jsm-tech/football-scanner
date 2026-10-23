"""Actual scan integration, using AST-loaded functions and disposable SQLite.

All football providers are mocked. Reviewed personal scores may support A only;
these tests never create an xG/Poisson model or issue an HTTP request.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import unittest
from unittest.mock import Mock, patch

import pandas as pd

from domestic_data import season_for_kickoff
from market_recommendations import recommended_pick_ids
from personal_stats import save_verified
import test_regressions as regression

UTC=timezone.utc


class PersonalScanTests(unittest.TestCase):
    setUp=regression.Regressions.setUp

    def prepare(self, *, cup=False, base=None):
        self.now=datetime.now(UTC);self.kickoff=self.now+timedelta(hours=6)
        self.home='Bayern Munich' if cup else 'Arsenal'
        self.away='Arsenal' if cup else 'Chelsea'
        self.home_league='soccer_germany_bundesliga' if cup else 'soccer_epl'
        self.away_league='soccer_epl';self.season=season_for_kickoff(self.kickoff)
        self.sport='soccer_uefa_champs_league' if cup else 'soccer_epl'
        self.event=dict(id='personal-future-target',commence_time=self.kickoff.isoformat(),
            home_team=self.home,away_team=self.away,_received_at=self.now.isoformat(),bookmakers=[dict(
                key='synthetic-book',title='Synthetic Book',markets=[
                    dict(key='spreads',last_update=self.now.isoformat(),outcomes=[dict(name=self.home,point=-.25,price=2)]),
                    dict(key='totals',last_update=self.now.isoformat(),outcomes=[dict(name='Over',point=3,price=1.95),dict(name='Under',point=3,price=1.95)])])])
        self.f.update(personal_pilot_enabled=True,
            fetch_odds=Mock(return_value=([self.event],'100','2')),
            fetch_history=Mock(return_value=pd.DataFrame() if base is None else base),
            fetch_understat_history=Mock(return_value=pd.DataFrame()),
            fetch_understat_all_history=Mock(side_effect=AssertionError('No broad source fanout')))
        if cup:
            def roster(team,away=False):
                return pd.DataFrame([dict(Date_dt=pd.Timestamp((self.now-timedelta(days=2)).date()),
                    HomeTeam='Roster Opponent' if away else team,AwayTeam=team if away else 'Roster Opponent',
                    FTHG=1,FTAG=1,_history_season=self.season)])
            self.f['fetch_domestic_rosters']=Mock(return_value={
                'soccer_germany_bundesliga':roster(self.home),
                'soccer_epl':roster(self.away,True)})

    def populate(self, *, observed=True):
        ids=[]
        with self.f['db']() as con:
            for i in range(5):
                day=(self.now-timedelta(days=2*i+2)).replace(hour=12,minute=0,second=0,microsecond=0)
                for side in ('home','away'):
                    home=self.home if side=='home' else f'Personal Away Opponent {i}'
                    away=f'Personal Home Opponent {i}' if side=='home' else self.away
                    ids.append(save_verified(con,match_key=f'personal-{side}-{i}',kickoff_utc=day.isoformat(),
                        home_team=home,away_team=away,home_goals=3 if side=='home' else 0,away_goals=0 if side=='home' else 2,
                        source='Synthetic user-reviewed evidence',source_url='https://example.invalid/synthetic-proof',
                        stats={'xG':{'home':2.4 if side=='home' else .6,'away':.7 if side=='home' else 1.9}},
                        league=self.home_league if side=='home' else self.away_league,season=self.season,
                        reviewed=observed,completed=observed,evidence_mode='observed' if observed else 'research'))
        return ids

    def scan(self):
        with patch('requests.get',side_effect=AssertionError('All scan providers must remain mocked')) as http:
            rows,snapshots,remaining,used=self.f['scan_league'](self.sport,self.f['LEAGUES'][self.sport][0],
                self.f['LEAGUES'][self.sport][1],24,1.8,2.1,'uk','SYNTHETIC-NO-HTTP')
        http.assert_not_called()
        return rows,snapshots

    def parsed(self, rows):
        row=next(r for r in rows if r['market']=='Asian Handicap')
        return row,json.loads(row['analysis_a_json']),json.loads(row['analysis_b_json'])

    def baseline(self):
        rows=[]
        for i in range(5):
            day=pd.Timestamp((self.now-timedelta(days=2*i+3)).date())
            rows.extend([dict(Date_dt=day,HomeTeam=self.home,AwayTeam=f'CSV H {i}',FTHG=0,FTAG=2,_history_season=self.season),
                         dict(Date_dt=day,HomeTeam=f'CSV A {i}',AwayTeam=self.away,FTHG=1,FTAG=0,_history_season=self.season)])
        frame=pd.DataFrame(rows);frame.attrs['history_diagnostics']={'status':'ok','sources':[{'source':'synthetic original CSV'}]}
        return frame

    def protect_finalized_bill(self):
        self.f['save_picks']([dict(event_id='finalized-legacy',market='Asian Handicap',side='home',
            league=self.f['LEAGUES']['soccer_epl'][0],bookmaker='Old Book',point=-.25,odds=2,
            selection='Old home (-0.25)',kickoff_utc=(self.now-timedelta(days=20)).isoformat())])
        pick_id=int(self.f['load_picks']().iloc[0]['id'])
        self.f['update_pick_result'](pick_id,'1-0','ชนะเต็ม',1,1)
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES (?,?,?,?)',
                ('{damaged legacy JSON','ชนะบิล',1,'original finalized outcome'))
            old_pick=tuple(con.execute('SELECT * FROM picks WHERE id=?',(pick_id,)).fetchone())
            old_slip=tuple(con.execute('SELECT * FROM slips WHERE id=1').fetchone())
        return pick_id,old_pick,old_slip

    def test_reviewed_personal_scores_fill_real_scan_a_and_manifest_but_never_b(self):
        self.prepare();pick_id,old_pick,old_slip=self.protect_finalized_bill();ids=self.populate()
        rows,snapshots=self.scan();row,a,b=self.parsed(rows)
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertEqual((a['home_form']['last5_gf'],a['away_form']['last5_gf']),(3,2))
        self.assertIsNotNone(a['score']);self.assertGreaterEqual(a['score'],55)
        manifest=a['personal_stats']
        self.assertTrue(manifest['available']);self.assertEqual(manifest['form_fallback_sides'],['home','away'])
        records=manifest['home']['records']+manifest['away']['records']
        self.assertEqual({r['revision_id'] for r in records},set(ids))
        self.assertTrue(all(r['source']=='Synthetic user-reviewed evidence' and r['source_url'].startswith('https://example.invalid/') for r in records))
        self.assertTrue(all(datetime.fromisoformat(r['received_at'])>=self.now for r in records))
        self.assertTrue(all(datetime.fromisoformat(r['reviewed_at'])<datetime.fromisoformat(manifest['analysis_at']) for r in records))
        self.assertFalse(b['available']);self.assertFalse(b['personal_stats']['model_enabled'])
        self.assertIsNone(row['ev_pct']);self.assertIsNone(row['lambda_home']);self.assertIsNone(row['xg_home_avg'])
        self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
        self.assertNotIn('ตัวเลือกเด่น',row['recommendation'])
        self.f['save_picks'](rows,snapshots)
        saved=self.f['load_picks'](limit=None)
        self.assertEqual(recommended_pick_ids(saved.to_dict('records'),self.f['choose_recommendation']),set())
        stored=json.loads(saved[saved.event_id=='personal-future-target'].iloc[0]['analysis_a_json'])['personal_stats']
        self.assertEqual({r['revision_id'] for r in stored['home']['records']+stored['away']['records']},set(ids))
        with self.f['db']() as con:
            self.assertEqual(tuple(con.execute('SELECT * FROM picks WHERE id=?',(pick_id,)).fetchone()),old_pick)
            self.assertEqual(tuple(con.execute('SELECT * FROM slips WHERE id=1').fetchone()),old_slip)
        self.f['fetch_odds'].assert_called_once()

    def test_disabled_flag_does_not_query_storage_and_keeps_existing_baseline(self):
        self.prepare();self.f['fetch_history'].return_value=self.baseline();self.populate()
        self.f['personal_pilot_enabled']=False
        with patch('personal_context.eligible_history',side_effect=AssertionError('disabled pilot cannot read personal storage')) as read:
            disabled,_=self.scan()
        read.assert_not_called();_,base_a,base_b=self.parsed(disabled)
        self.assertNotIn('personal_stats',base_a);self.assertNotIn('personal_stats',base_b)
        self.f['personal_pilot_enabled']=True
        enabled,_=self.scan();_,a,b=self.parsed(enabled)
        self.assertEqual(a['personal_stats']['form_fallback_sides'],[])
        self.assertEqual(a['personal_stats']['home']['form_status'],'existing_source_retained')
        self.assertEqual(a['home_form']['last5_gf'],0)
        self.assertEqual({k:v for k,v in a.items() if k!='personal_stats'},base_a)
        self.assertEqual({k:v for k,v in b.items() if k!='personal_stats'},base_b)

    def test_empty_personal_store_returns_no_invented_form_xg_or_ev(self):
        self.prepare();rows,_=self.scan();row,a,b=self.parsed(rows)
        self.assertFalse(a['personal_stats']['available'])
        self.assertEqual(a['home_form']['sample5'],0);self.assertIsNone(a['score'])
        self.assertFalse(b['available']);self.assertIsNone(row['ev_pct'])

    def test_research_imports_do_not_turn_into_observed_scan_form(self):
        self.prepare();self.populate(observed=False)
        rows,_=self.scan();row,a,b=self.parsed(rows)
        self.assertFalse(a['personal_stats']['available']);self.assertIsNone(a['score'])
        self.assertIsNone(row['ev_pct']);self.assertFalse(b['available'])

    def test_past_asof_cannot_see_evidence_received_today(self):
        self.prepare();self.populate()
        with patch('requests.get',side_effect=AssertionError('context lookup stays offline')) as http:
            manifest,frame=self.f['reviewed_personal_context'](self.home,self.away,self.kickoff,self.sport,
                self.event['id'],self.now-timedelta(minutes=1))
        self.assertTrue(frame.empty);self.assertFalse(manifest['available']);http.assert_not_called()
        self.f['fetch_odds'].assert_not_called()

    def test_cup_uses_proven_domestic_personal_form_without_enabling_cup_model(self):
        self.prepare(cup=True);self.populate()
        rows,_=self.scan();row,a,b=self.parsed(rows)
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertEqual(a['personal_stats']['home']['league'],'soccer_germany_bundesliga')
        self.assertEqual(a['personal_stats']['away']['league'],'soccer_epl')
        self.assertIsNotNone(a['score']);self.assertFalse(b['available'])
        self.assertEqual(b['scope'],'domestic_context_only');self.assertFalse(b['personal_stats']['model_enabled'])
        self.assertIsNone(row['ev_pct']);self.assertFalse(b['domestic_xg_available'])
        self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
        self.f['fetch_odds'].assert_called_once();self.f['fetch_understat_all_history'].assert_not_called()


if __name__=='__main__':unittest.main()
