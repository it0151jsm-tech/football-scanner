"""Synthetic UCL routing acceptance checks; never call a football provider."""
import json
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import Mock

import pandas as pd
from domestic_data import season_for_kickoff
from market_recommendations import group_fixture_recommendations,recommended_pick_ids
from mobile_dashboard import fixture_dashboard_html,fixture_details_html
import test_regressions as regression


class UCLScanTests(unittest.TestCase):
    setUp=regression.Regressions.setUp

    def prepare(self, quotes=True):
        self.now=datetime.now(timezone.utc);self.kickoff=self.now+timedelta(hours=3)
        season=season_for_kickoff(self.kickoff)
        dates=pd.date_range(end=pd.Timestamp(self.kickoff.date())-pd.Timedelta(days=1),periods=10,freq='3D')
        def frame(team,away=False):
            rows=[dict(Date_dt=d,HomeTeam=f'Other {i}' if away else team,AwayTeam=team if away else f'Other {i}',
                       FTHG=1 if away else 2,FTAG=2 if away else 1,_history_season=season,xG_home=1.1 if away else 1.8,xG_away=1.4 if away else .9) for i,d in enumerate(dates)]
            out=pd.DataFrame(rows);out.attrs['history_diagnostics']={'status':'ok','sources':[{'url':'https://example.invalid/synthetic-only.csv','season':season,'status':'ok'}]};return out
        self.frames={'soccer_epl':frame('Arsenal',True),'soccer_germany_bundesliga':frame('Bayern Munich'),
                     'soccer_spain_la_liga':frame('Spain Test'),'soccer_italy_serie_a':frame('Italy Test'),'soccer_france_ligue_one':frame('France Test')}
        self.by_code={self.f['LEAGUES'][k][1]:v for k,v in self.frames.items()}
        markets=[dict(key='spreads',last_update=self.now.isoformat(),outcomes=[dict(name='Bayern Munich',point=-.25,price=2)])]
        if quotes:markets.append(dict(key='totals',last_update=self.now.isoformat(),outcomes=[dict(name='Over',point=3,price=1.95),dict(name='Under',point=3,price=1.95)]))
        self.event=dict(id='ucl-synthetic',commence_time=self.kickoff.isoformat(),home_team='Bayern Munich',away_team='Arsenal',_received_at=self.now.isoformat(),bookmakers=[dict(key='test-provider',title='Test provider',markets=markets)])
        self.f.update(fetch_odds=Mock(return_value=([self.event],'100','2')),
                      fetch_domestic_rosters=Mock(side_effect=lambda season:{k:v.copy() for k,v in self.frames.items()}),
                      fetch_history=Mock(side_effect=lambda code:self.by_code[code]),
                      fetch_understat_history=Mock(side_effect=lambda key:self.frames[key]),
                      fetch_understat_all_history=Mock(side_effect=AssertionError('no six-league Understat fanout')))

    def scan(self):
        return self.f['scan_league']('soccer_uefa_champs_league',self.f['LEAGUES']['soccer_uefa_champs_league'][0],None,24,1.8,2.1,'uk','MOCK')

    def test_bayern_arsenal_uses_two_domestic_sources_and_preserves_model_boundary(self):
        self.prepare();rows,snapshots,_,_=self.scan()
        row=next(r for r in rows if r['market']=='Asian Handicap');a=json.loads(row['analysis_a_json']);b=json.loads(row['analysis_b_json'])
        self.assertEqual((a['home_form']['sample5'],a['away_form']['sample5']),(5,5))
        self.assertEqual((a['home_form']['sample10'],a['away_form']['sample10']),(10,10))
        self.assertIsNotNone(a['score']);self.assertEqual(a['xg']['home']['sample'],10);self.assertEqual(a['xg']['away']['sample'],10)
        self.assertEqual(a['domestic_sources']['home']['csv_code'],'D1');self.assertEqual(a['domestic_sources']['away']['csv_code'],'E0')
        audit=a['form_audit']
        self.assertEqual(audit['home']['windows']['5']['sum_gf'],10)
        self.assertTrue(audit['home']['windows']['5']['matches_profile'])
        self.assertEqual(audit['away']['windows']['10']['count'],10)
        self.assertIsNotNone(datetime.fromisoformat(audit['recorded_at']).tzinfo)
        self.assertFalse(b['available']);self.assertEqual(b['scope'],'domestic_context_only');self.assertTrue(b['domestic_xg_available'])
        self.assertIn('มี xG ลีกในประเทศจริง',b['status']);self.assertIsNone(row['ev_pct']);self.assertNotIn('expected_goals',b)
        self.assertEqual({c.args[0] for c in self.f['fetch_history'].call_args_list},{'D1','E0'})
        self.assertEqual({c.args[0] for c in self.f['fetch_understat_history'].call_args_list},{'soccer_epl','soccer_germany_bundesliga'})
        self.f['fetch_odds'].assert_called_once();self.f['fetch_domestic_rosters'].assert_called_once();self.f['fetch_understat_all_history'].assert_not_called()
        self.f['save_picks'](rows,snapshots);stored=self.f['load_picks']().to_dict('records')
        fixture=group_fixture_recommendations(stored,self.f['choose_recommendation'])[0]
        self.assertEqual(fixture['ou_main']['line'],3);self.assertIsNone(fixture['ou_direction']);self.assertEqual(recommended_pick_ids(stored,self.f['choose_recommendation']),set())
        self.assertIn('ฟอร์มลีกในประเทศ',fixture_dashboard_html(fixture));self.assertIn('มี xG ลีกในประเทศจริง',fixture_dashboard_html(fixture))
        self.assertIn('1.80',fixture_details_html(fixture,'test'))

    def test_missing_xg_does_not_use_scores_as_replacement(self):
        self.prepare();self.f['fetch_understat_history']=Mock(return_value=pd.DataFrame())
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json']);b=json.loads(rows[0]['analysis_b_json'])
        self.assertEqual(a['home_form']['sample5'],5);self.assertIsNone(a['xg']['home']);self.assertIsNone(a['xg']['away'])
        self.assertIn('Understat โหลดไม่ได้',a['xg']['status']);self.assertFalse(b['domestic_xg_available']);self.assertIsNone(rows[0]['ev_pct'])

    def test_form_manifest_persists_without_rewriting_previous_saved_analysis(self):
        self.prepare();rows,snapshots,_,_=self.scan()
        self.f['save_picks'](rows,snapshots)
        saved=self.f['load_picks']().iloc[0].to_dict()
        audit=json.loads(saved['analysis_a_json'])['form_audit']
        self.assertEqual(audit['home']['windows']['5']['mean_gf'],2)
        self.assertEqual(audit['away']['windows']['5']['mean_gf'],2)
        self.assertEqual(audit['home']['windows']['10']['rows'][0]['season'],season_for_kickoff(self.kickoff))
        self.assertTrue(all(r['date']<self.kickoff.date().isoformat() for r in audit['home']['windows']['10']['rows']))

    def test_no_price_placeholder_keeps_form_evidence_without_ev(self):
        self.prepare();self.event['bookmakers']=[]
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json'])
        self.assertEqual(a['form_audit']['home']['windows']['5']['count'],5)
        self.assertEqual(rows[0]['market'],'ยังไม่มีราคา');self.assertIsNone(rows[0]['ev_pct'])

    def test_old_xg_is_reported_without_inventing_values(self):
        self.prepare()
        old={k:v.assign(Date_dt=v.Date_dt-pd.DateOffset(years=1),_history_season='2526') for k,v in self.frames.items()}
        self.f['fetch_understat_history']=Mock(side_effect=lambda key:old[key])
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json'])
        self.assertIsNone(a['xg']['home']);self.assertIn('เก่าเกิน 120 วัน',a['xg']['status']);self.assertIsNone(rows[0]['ev_pct'])
        self.assertIsNone(rows[0]['home_context_xg']);self.assertIsNone(rows[0]['away_context_xg'])
        self.assertIn('เก่าเกิน 120 วัน',rows[0]['context_status'])

    def test_unknown_name_and_partial_history_failure_keep_explicit_reasons(self):
        self.prepare();self.event['away_team']='Arsenal Similar Unverified'
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json'])
        self.assertEqual(a['home_form']['sample5'],5);self.assertEqual(a['away_form']['sample5'],0)
        self.assertIn('ยังสรุปว่าไม่พบทีมไม่ได้',a['away_form']['diagnostics']['message']);self.assertIsNone(rows[0]['ev_pct'])
        self.prepare();self.f['fetch_history']=Mock(side_effect=RuntimeError('synthetic source failure'))
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json']);self.assertEqual(a['home_form']['sample5'],0)
        self.assertIn('โหลดข้อมูลไม่สำเร็จ',a['home_form']['diagnostics']['message'])

    def test_repeated_events_share_rosters_and_only_two_matched_source_loads(self):
        self.prepare();other=json.loads(json.dumps(self.event));other['id']='second-synthetic'
        self.f['fetch_odds']=Mock(return_value=([self.event,other],'100','2'))
        rows,_,_,_=self.scan()
        self.assertEqual(len(rows),6);self.f['fetch_odds'].assert_called_once();self.f['fetch_domestic_rosters'].assert_called_once()
        self.assertEqual(self.f['fetch_history'].call_count,2);self.assertEqual(self.f['fetch_understat_history'].call_count,2)

    def test_understat_wrong_or_lossy_name_never_becomes_xg_evidence(self):
        self.prepare()
        original=self.frames['soccer_germany_bundesliga']
        self.frames['soccer_germany_bundesliga']=original.copy()
        self.frames['soccer_germany_bundesliga']['HomeTeam']='Báyern Munich'
        # Roster/score evidence remains the exact original club; only the xG source differs.
        roster={k:v.copy() for k,v in self.frames.items()};roster['soccer_germany_bundesliga']=original
        self.f['fetch_domestic_rosters']=Mock(return_value=roster)
        rows,_,_,_=self.scan();a=json.loads(rows[0]['analysis_a_json'])
        self.assertIsNone(a['xg']['home']);self.assertIn('จับคู่ชื่อกับ Understat',a['xg']['status'])
        with self.f['db']() as con:
            health=json.loads(con.execute('SELECT payload_json FROM league_health ORDER BY id DESC LIMIT 1').fetchone()[0])
        home=next(t for t in health['teams'] if t['api_name']=='Bayern Munich')
        self.assertEqual(home['understat']['status'],'unmatched')

    def test_roster_loader_uses_only_one_current_csv_per_supported_league(self):
        from types import SimpleNamespace
        season=season_for_kickoff(datetime.now(timezone.utc))
        content=b'Date,HomeTeam,AwayTeam,FTHG,FTAG\n01/10/2026,Synthetic A,Synthetic B,2,1\n'
        get=Mock(return_value=SimpleNamespace(status_code=200,content=content,url='https://example.invalid/synthetic.csv'))
        self.f.update(requests=SimpleNamespace(get=get),HISTORY_TIMEOUT=(3,8))
        rosters=self.f['fetch_domestic_rosters'](season)
        expected = len(self.f['domestic_league_definitions']())
        self.assertEqual(expected, 17)
        self.assertEqual(len(rosters), expected)
        self.assertEqual(get.call_count, expected)
        self.assertTrue(all('/'+season+'/' in call.args[0] for call in get.call_args_list))
        self.assertTrue(all('football-data.co.uk/mmz4281' in call.args[0] for call in get.call_args_list))

    def test_first_observed_price_does_not_claim_movement_from_one_quote(self):
        evidence = self.f['price_evidence'](None, 2.5, 1.92, '2026-10-22T10:00:00Z')
        self.assertIsNone(evidence['odds_delta'])
        self.assertIn('ยังยืนยันราคาไหลไม่ได้', evidence['status'])
        same_scan = {'point': 2.5, 'odds': 1.89, 'captured_at': '2026-10-22T12:29:55.100000Z'}
        near = self.f['price_evidence'](same_scan, 2.5, 1.90, '2026-10-22T12:29:55.544048Z')
        self.assertIsNone(near['odds_delta'])
        self.assertIn('ยังยืนยันราคาไหลไม่ได้', near['status'])
        earlier = {'point': 2.5, 'odds': 1.92, 'captured_at': '2026-10-22T09:00:00Z'}
        later = self.f['price_evidence'](earlier, 2.5, 1.84, '2026-10-22T10:00:00Z')
        self.assertAlmostEqual(later['odds_delta'], -0.08)
        changed_line = self.f['price_evidence'](earlier, 3.0, 1.84, '2026-10-22T10:00:00Z')
        self.assertIsNone(changed_line['odds_delta'])

    def test_single_ah_quote_never_manufactures_ou(self):
        self.prepare(quotes=False);rows,_,_,_=self.scan()
        self.assertEqual(len(rows),1);self.f['save_picks'](rows)
        fixture=group_fixture_recommendations(self.f['load_picks']().to_dict('records'),self.f['choose_recommendation'])[0]
        self.assertEqual(fixture['ou_main']['reason_code'],'missing_market');self.assertIsNone(fixture['ou_main']['line'])

    def test_cup_scan_skips_understat_for_unsupported_domestic_league(self):
        self.prepare()
        # Simulate a supported domestic score league without Understat coverage.
        self.f['UNDERSTAT_LEAGUES'].pop('soccer_germany_bundesliga', None)
        self.scan()
        requested = {call.args[0] for call in self.f['fetch_understat_history'].call_args_list}
        self.assertEqual(requested, {'soccer_epl'})

    def test_outside_24h_or_invalid_time_does_not_load_domestic_sources(self):
        for stamp in [(datetime.now(timezone.utc)+timedelta(hours=25)).isoformat(),'2026-10-22T02:00:00','invalid']:
            self.prepare();self.event['commence_time']=stamp;rows,_,_,_=self.scan()
            self.assertEqual(rows,[]);self.f['fetch_domestic_rosters'].assert_not_called();self.f['fetch_history'].assert_not_called();self.f['fetch_understat_history'].assert_not_called()

    def test_existing_finalized_bill_and_snapshots_are_untouched_by_cup_scan(self):
        self.prepare()
        with self.f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES (?,?,?,?)',('{bad','แพ้บิล',-1,'unchanged'))
            before=con.execute('SELECT * FROM slips').fetchall()
        rows,snaps,_,_=self.scan();self.f['save_picks'](rows,snaps)
        with self.f['db']() as con:self.assertEqual([tuple(r) for r in before],[tuple(r) for r in con.execute('SELECT * FROM slips')])

    def test_same_title_different_provider_keys_keep_separate_quote_evidence(self):
        self.prepare();first=self.event['bookmakers'][0];second=json.loads(json.dumps(first));second['key']='other-key'
        self.event['bookmakers'].append(second)
        rows,snaps,_,_=self.scan();self.f['save_picks'](rows,snaps)
        self.assertEqual(len(self.f['load_picks']()),6)
        with self.f['db']() as con:self.assertEqual(con.execute('SELECT COUNT(*) FROM odds_snapshots').fetchone()[0],6)
        for row in self.f['load_picks']().to_dict('records'):
            self.assertIsNotNone(self.f['lookup_quote_snapshot'](row))
        observed=self.f['first_observed_quotes'](['ucl-synthetic'])
        self.assertIn(('ucl-synthetic','Over/Under','over','Test provider','test-provider'),observed)
        self.assertIn(('ucl-synthetic','Over/Under','over','Test provider','other-key'),observed)
