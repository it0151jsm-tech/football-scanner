"""Mock-provider -> actual scanner -> temporary SQLite -> original A/B/C -> UI.

Every football row in this module is authored synthetic test data. Provider
availability, live coverage, model accuracy, calibration and ROI are unmeasured.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

import pandas as pd

import test_regressions as regression
import test_issue9_scan as aliases
from bill_backup import backup_bytes, restore_new
from domestic_data import season_for_kickoff
from market_recommendations import group_fixture_recommendations
from market_summary import summary_market_rows
from mobile_dashboard import fixture_dashboard_html, fixture_details_html
from odds_budget import LeagueScanError, OddsBatch, ScanStopped
from readiness import analysis_scope
from scan_control import run_selected_leagues, scan_summary_text

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
MAJOR = {'soccer_epl','soccer_spain_la_liga','soccer_italy_serie_a',
         'soccer_germany_bundesliga','soccer_france_ligue_one'}
MATRIX = json.loads((ROOT/'fixtures/operational-recovery/scan-matrix.json').read_text())


class OperationalHarness:
    """No decorated cache, real network or user database is used."""
    def __init__(self, test):
        case=regression.Regressions('test_ou_sample_deduplicates_shared_fixture')
        case.setUp()
        test.addCleanup(case.doCleanups)
        self.f=case.f
        self.tmp=case.tmp
        self.now=datetime.now(UTC)
        self.current_sport=None
        self.reports={}
        self.events={}
        self.real_http=patch('requests.sessions.Session.request',side_effect=AssertionError('offline E2E must not request provider'))
        self.http=self.real_http.start()
        test.addCleanup(self.real_http.stop)
        self.no_optional=patch.dict(os.environ,VALUE_BET_API_FOOTBALL_ENABLED='0',API_FOOTBALL_KEY='',
            VALUE_BET_OBSERVATION_DB='')
        self.no_optional.start()
        test.addCleanup(self.no_optional.stop)
        self.f.update(fetch_odds=Mock(side_effect=self.fetch_odds),
            fetch_history=Mock(side_effect=self.score_history),
            fetch_understat_history=Mock(side_effect=self.xg_history),
            fetch_understat_all_history=Mock(side_effect=AssertionError('no xG fanout')),
            fetch_domestic_rosters=Mock(return_value={}))

    def teams(self,sport):
        return f'Synthetic Home {sport}',f'Synthetic Away {sport}'

    def frame(self,sport,*,xg=False):
        home,away=self.teams(sport)
        end=(self.now-timedelta(days=3)).replace(tzinfo=None,hour=0,minute=0,second=0,microsecond=0)
        dates=[pd.Timestamp(end-timedelta(days=3*(9-index))) for index in range(10)]
        frame=pd.DataFrame([dict(Date_dt=day,HomeTeam=home,AwayTeam=away,FTHG=2,FTAG=1,
            _history_season=season_for_kickoff(self.now)) for day in dates])
        if xg:
            frame['xG_home'],frame['xG_away']=1.8,.9
            frame['source_league']=self.f['UNDERSTAT_LEAGUES'].get(sport)
        diagnostic=dict(status='ok',provider='Understat' if xg else 'Football-Data',sources=[dict(
            status='ok',provider='Understat' if xg else 'Football-Data',
            url='https://example.invalid/authored-offline-fixture',received_at=self.now.isoformat(),
            season=2026 if xg else season_for_kickoff(self.now),accepted_rows=len(frame),http_status=200)])
        frame.attrs['history_diagnostics']=deepcopy(diagnostic)
        if xg:frame.attrs['xg_diagnostics']=deepcopy(diagnostic)
        return frame

    def score_history(self,code):
        if code is None:
            frame=pd.DataFrame()
            frame.attrs['history_diagnostics']=dict(status='unsupported_source',sources=[])
            return frame
        return self.frame(self.current_sport)

    def xg_history(self,sport):
        if sport not in self.f['UNDERSTAT_LEAGUES']:
            frame=pd.DataFrame()
            diagnostic=dict(status='unsupported_xg',provider='Understat',sources=[])
            frame.attrs['history_diagnostics']=deepcopy(diagnostic)
            frame.attrs['xg_diagnostics']=deepcopy(diagnostic)
            return frame
        return self.frame(sport,xg=True)

    def event(self,sport,suffix='only',hours=2,*,markets=('spreads','totals'),embedded_key=None):
        home,away=self.teams(sport)
        quoted=[]
        if 'spreads' in markets:
            quoted.append(dict(key='spreads',last_update=self.now.isoformat(),outcomes=[
                dict(name=home,point=-.25,price=2.1),dict(name=away,point=.25,price=1.9)]))
        if 'totals' in markets:
            quoted.append(dict(key='totals',last_update=self.now.isoformat(),outcomes=[
                dict(name='Over',point=2.75,price=2.1),dict(name='Under',point=2.75,price=1.9)]))
        return dict(id=f'{sport}-{suffix}',sport_key=embedded_key or sport,home_team=home,away_team=away,
            commence_time=(self.now+timedelta(hours=hours)).isoformat(),_received_at=self.now.isoformat(),
            bookmakers=[dict(key='synthetic-book',title='Synthetic book',markets=quoted)] if quoted else [])

    def populate_matrix(self,selected):
        for sport in selected:
            self.events[sport]=[self.event(sport,entry['suffix'],entry['hours'],
                embedded_key=sport if entry['embedded_key']=='selected' else 'soccer_wrong_unselected')
                for entry in MATRIX['fixture_offsets']]

    def fetch_odds(self,sport,regions,key):
        self.current_sport=sport
        if regions not in ('uk','eu','us'):raise AssertionError('unexpected region')
        batch=OddsBatch(deepcopy(self.events.get(sport,[])),self.now.isoformat())
        return batch,'498','2'

    def scan_one(self,sport,label,code,*,hours=24,region='uk'):
        self.current_sport=sport
        result=self.f['scan_league'](sport,label,code,hours,1.8,2.15,region,'SYNTHETIC_NO_CREDIT')
        # Real scanner persisted the complete current-response audit once.
        with self.f['db']() as con:
            row=con.execute('SELECT payload_json FROM league_health WHERE sport_key=? ORDER BY id DESC LIMIT 1',(sport,)).fetchone()
        if row:self.reports[sport]=json.loads(row[0])['scan_window']
        return result

    def run(self,selected,*,hours=24,region='uk',scan_one=None):
        scan=scan_one or (lambda sport,label,code:self.scan_one(sport,label,code,hours=hours,region=region))
        return run_selected_leagues(selected,self.f['LEAGUES'],scan,report_for=self.reports.get)

    def save_and_view(self,result,selected,*,hours=24):
        self.f['save_picks'](result['rows'],result['snapshots'])
        saved=self.f['load_picks'](limit=None)
        return analysis_scope(saved,[self.f['LEAGUES'][sport][0] for sport in selected],
            datetime.now(UTC),latest_receipts=self.f['latest_api_receipts'](),hours_limit=hours)


def write_acceptance(case_name,result,view,**extra):
    folder=os.environ.get('DASHBOARD_SCREENSHOTS')
    if not folder:return
    target=Path(folder)
    target.mkdir(parents=True,exist_ok=True)
    report=dict(data_mode='synthetic_test_fixture',case=case_name,real_provider_calls=0,
        live_end_to_end_verified=False,scope='Software fixture acceptance; not live football coverage or model performance',
        counts=result['counts'],league_results=result['leagues'],
        restored_fixture_n=int(view.event_id.nunique()) if not view.empty else 0,**extra)
    (target/f'operational-{case_name}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))


class OperationalEndToEndTests(unittest.TestCase):
    def setUp(self):
        self.h=OperationalHarness(self)

    def tearDown(self):
        self.h.http.assert_not_called()

    def eleven(self):
        selected=list(self.h.f['LEAGUES'])[:11]
        self.assertTrue(MAJOR.issubset(selected))
        self.h.populate_matrix(selected)
        return selected

    def test_actual_app_controller_persists_all_eleven_outcomes_and_reloads_selected_scope(self):
        selected=self.eleven()
        result=self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.assertEqual(result['saved_counts'],[44,44])
        self.assertEqual(result['counts']['successful_leagues'],11)
        report=self.h.f['latest_scan_report']()
        self.assertEqual(report['run_id'],result['run_id'])
        self.assertEqual(report['counts']['api_returned'],55)
        self.assertEqual(report['counts']['passed_filter'],11)
        self.assertEqual(len(report['leagues']),11)
        raw=self.h.f['load_picks'](limit=None)
        view=self.h.f['current_scan_rows'](raw,selected,'uk',24)
        self.assertEqual(view.event_id.nunique(),11)
        self.assertTrue(self.h.f['current_scan_rows'](raw,selected,'eu',24).empty)
        self.assertTrue(self.h.f['current_scan_rows'](raw,selected,'uk',48).empty)
        self.assertEqual(self.h.f['current_scan_rows'](raw,selected[:1],'uk',24).event_id.nunique(),1)
        with self.h.f['db']() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM scan_outcomes WHERE run_id=?',(result['run_id'],)).fetchone()[0],11)
        write_acceptance('app-controller-eleven',result,view)

    def test_failed_latest_league_cannot_revive_older_fresh_primary_quotes(self):
        selected=['soccer_epl','soccer_spain_la_liga']
        for sport in selected:self.h.events[sport]=[self.h.event(sport)]
        self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.h.now+=timedelta(seconds=1)
        for sport in selected:self.h.events[sport]=[self.h.event(sport)]
        def fetch(sport,region,key):
            if sport==selected[0]:raise LeagueScanError('safe temporary HTTP error',reason='provider_http_error',status=503)
            return self.h.fetch_odds(sport,region,key)
        self.h.f['fetch_odds']=Mock(side_effect=fetch)
        result=self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.assertFalse(result['stopped'])
        self.assertEqual(result['leagues'][0]['status'],'league_error')
        raw=self.h.f['load_picks'](limit=None)
        self.assertEqual(raw.event_id.nunique(),2)
        view=self.h.f['current_scan_rows'](raw,selected,'uk',24)
        self.assertEqual(set(view.league),{self.h.f['LEAGUES'][selected[1]][0]})
        self.assertEqual(view.event_id.nunique(),1)
        with self.h.f['db']() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0],12)
        report=self.h.f['latest_scan_report']()
        self.assertEqual(report['counts']['league_errors'],1)
        self.assertIsNone(report['leagues'][0]['api_returned'])

    def test_latest_incomplete_run_gates_primary_without_deleting_existing_records(self):
        selected=['soccer_epl']
        self.h.events[selected[0]]=[self.h.event(selected[0])]
        self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        raw=self.h.f['load_picks'](limit=None)
        from scan_journal import begin
        with self.h.f['db']() as con:
            run_id=begin(con,selected,'uk',24)
        report=self.h.f['latest_scan_report']()
        self.assertEqual(report['run_id'],run_id)
        self.assertEqual(report['leagues'][0]['status'],'incomplete')
        self.assertTrue(self.h.f['current_scan_rows'](raw,selected,'uk',24).empty)
        self.assertEqual(len(self.h.f['load_picks'](limit=None)),4)

    def test_journal_persistence_failure_halts_remaining_api_work_and_retains_prices(self):
        selected=self.eleven()
        import sqlite3
        with patch('scan_journal.append',side_effect=sqlite3.OperationalError('private error body not exported')):
            result=self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.assertTrue(result['stopped'])
        self.assertEqual(result['stop_reason'],'scan_journal_unavailable')
        self.assertEqual(self.h.f['fetch_odds'].call_count,1)
        self.assertEqual(sum(row['status']=='skipped_safety_stop' for row in result['leagues']),10)
        raw=self.h.f['load_picks'](limit=None)
        self.assertEqual(len(raw),4)
        self.assertTrue(self.h.f['current_scan_rows'](raw,selected,'uk',24).empty)
        self.assertNotIn('private error',json.dumps(result))

    def test_price_persistence_failure_is_a_global_safety_stop(self):
        selected=self.eleven()
        self.h.f['save_picks']=Mock(side_effect=RuntimeError('private persistence details'))
        result=self.h.f['execute_selected_scan'](selected,24,1.8,2.15,'uk','SYNTHETIC_NO_CREDIT')
        self.assertTrue(result['stopped'])
        self.assertEqual(result['stop_reason'],'price_persistence_failed')
        self.assertEqual(self.h.f['fetch_odds'].call_count,1)
        self.assertEqual(sum(row['status']=='skipped_safety_stop' for row in result['leagues']),10)
        self.assertEqual(self.h.f['latest_scan_report']()['leagues'][0]['status'],'safety_stop')
        self.assertTrue(self.h.f['load_picks'](limit=None).empty)
        self.assertNotIn('private persistence',json.dumps(result))

    def test_eleven_leagues24h_real_scan_save_restore_and_a_b_c(self):
        selected=self.eleven()
        result=self.h.run(selected)
        self.assertFalse(result['stopped'])
        self.assertEqual(result['counts']['selected_leagues'],11)
        self.assertEqual(result['counts']['successful_leagues'],11)
        self.assertEqual(result['counts']['api_returned'],55)
        self.assertEqual(result['counts']['passed_filter'],11)
        self.assertEqual(len(result['leagues']),11)
        self.assertTrue(all(row['status']=='success' for row in result['leagues']))
        self.assertEqual(len(result['rows']),44)
        self.assertEqual(len(result['snapshots']),44)
        self.assertEqual(self.h.f['fetch_odds'].call_count,11)
        view=self.h.save_and_view(result,selected)
        self.assertEqual(view.event_id.nunique(),11)
        summary=summary_market_rows(view.to_dict('records'),self.h.f['choose_recommendation'])
        self.assertEqual(len(summary),22)
        self.assertEqual(len({(row['event_id'],row['market']) for row in summary}),22)
        a={row['event_id'] for row in result['rows'] if json.loads(row['analysis_a_json']).get('score') is not None}
        b={row['event_id'] for row in result['rows'] if json.loads(row['analysis_b_json']).get('available') is True}
        self.assertEqual(len(a),10)
        self.assertEqual(len(b),5)
        for sport in MAJOR:
            row=next(row for row in result['rows'] if row['event_id']==sport+'-within24' and row['market']=='Over/Under')
            analysis=json.loads(row['analysis_a_json'])
            self.assertEqual(analysis['home_form']['sample5'],5)
            self.assertEqual(analysis['away_form']['sample10'],10)
            self.assertEqual(json.loads(row['analysis_b_json'])['ou_line'],2.75)
            self.assertEqual(row['h2h_sample'],5)
        with self.h.f['db']() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0],44)
            self.assertEqual(con.execute('SELECT count(*) FROM slips').fetchone()[0],0)
        write_acceptance('eleven-leagues24h',result,view,a_scored_fixture_n=len(a),b_ready_fixture_n=len(b))

    def test_eleven_leagues48h_keeps36h_games_in_thai_window(self):
        selected=self.eleven()
        result=self.h.run(selected,hours=48)
        view=self.h.save_and_view(result,selected,hours=48)
        self.assertEqual(result['counts']['api_returned'],55)
        self.assertEqual(result['counts']['passed_filter'],22)
        self.assertEqual(view.event_id.nunique(),22)
        self.assertEqual(len(result['snapshots']),88)
        self.assertEqual(sum(event.endswith('-within48') for event in view.event_id.unique()),11)
        self.assertTrue(all(report['hours_limit']==48 for report in self.h.reports.values()))
        for report in self.h.reports.values():
            self.assertEqual((datetime.fromisoformat(report['end_th'])-datetime.fromisoformat(report['start_th'])).total_seconds(),48*3600)
        write_acceptance('eleven-leagues48h',result,view)

    def test_all38_configured_leagues_are_attempted_and_empty_stats_do_not_hide_games(self):
        selected=list(self.h.f['LEAGUES'])
        self.assertEqual(len(selected),38)
        for sport in selected:self.h.events[sport]=[self.h.event(sport,markets=())]
        self.h.f['fetch_history']=Mock(return_value=pd.DataFrame())
        self.h.f['fetch_understat_history']=Mock(return_value=pd.DataFrame())
        result=self.h.run(selected)
        view=self.h.save_and_view(result,selected)
        self.assertEqual((result['counts']['successful_leagues'],result['counts']['api_returned'],
                          result['counts']['passed_filter']),(38,38,38))
        self.assertEqual(self.h.f['fetch_odds'].call_count,38)
        self.assertEqual(view.event_id.nunique(),38)
        self.assertEqual(len(result['snapshots']),0)
        self.assertTrue(all(row['market']=='ยังไม่มีราคา' and row['odds'] is None for row in result['rows']))
        self.assertTrue(all(row['ev_pct'] is None for row in result['rows']))
        self.assertEqual({row['sport_key'] for row in result['leagues']},set(selected))
        write_acceptance('all38-no-price',result,view)

    def test_temporary503_in_fourth_league_does_not_stop_last_seven(self):
        selected=self.eleven()
        def scan(sport,label,code):
            if sport==selected[3]:raise LeagueScanError('safe HTTP error',reason='provider_http_error',status=503)
            return self.h.scan_one(sport,label,code)
        result=self.h.run(selected,scan_one=scan)
        view=self.h.save_and_view(result,selected)
        self.assertFalse(result['stopped'])
        self.assertEqual(result['counts']['successful_leagues'],10)
        self.assertEqual(result['counts']['league_errors'],1)
        self.assertEqual(result['leagues'][3]['status'],'league_error')
        self.assertEqual(result['leagues'][-1]['status'],'success')
        self.assertIsNone(result['leagues'][3]['api_returned'])
        self.assertEqual(view.event_id.nunique(),10)
        self.assertEqual(result['counts']['api_returned'],50)
        write_acceptance('eleven-one503',result,view)

    def test_auth_quota_safety_stop_lists_every_unrequested_league(self):
        selected=self.eleven()
        def scan(sport,label,code):
            if sport==selected[3]:raise ScanStopped('safe stop',reason='provider_auth_or_quota',status=429)
            return self.h.scan_one(sport,label,code)
        result=self.h.run(selected,scan_one=scan)
        self.assertTrue(result['stopped'])
        self.assertEqual(len(result['leagues']),11)
        self.assertEqual(result['counts']['successful_leagues'],3)
        self.assertEqual(result['counts']['safety_stops'],1)
        self.assertEqual(result['counts']['skipped_safety_stop'],7)
        self.assertEqual(self.h.f['fetch_odds'].call_count,3)
        self.assertTrue(all(row['api_returned'] is None for row in result['leagues'][3:]))
        self.assertIn('ยังไม่ได้เรียก 7 ลีก',scan_summary_text(result))

    def test_api_fixtures_without_quotes_are_not_empty_schedule(self):
        sport='soccer_epl'
        self.h.events[sport]=[self.h.event(sport,markets=())]
        result=self.h.run([sport])
        view=self.h.save_and_view(result,[sport])
        self.assertEqual(result['leagues'][0]['status'],'success')
        self.assertEqual(result['leagues'][0]['no_price_fixtures'],1)
        self.assertEqual(result['leagues'][0]['api_returned'],1)
        self.assertEqual(view.event_id.nunique(),1)
        self.assertIsNone(view.iloc[0].odds)
        self.assertIsNone(view.iloc[0].ev_pct)
        self.assertIn('ไม่มีราคา',view.iloc[0].model_status)

    def test_ah_only_and_ou_only_restore_exact_missing_market_status(self):
        sport='soccer_epl'
        for markets,present,absent in [(('spreads',),'Asian Handicap','Over/Under'),
                                       (('totals',),'Over/Under','Asian Handicap')]:
            with self.subTest(markets=markets):
                self.h.events[sport]=[self.h.event(sport,suffix=markets[0],markets=markets)]
                result=self.h.run([sport])
                view=self.h.save_and_view(result,[sport])
                event_id=sport+'-'+markets[0]
                saved=view[view.event_id==event_id]
                summary=summary_market_rows(saved.to_dict('records'),self.h.f['choose_recommendation'])
                self.assertEqual(len(summary),2)
                self.assertEqual({row['market'] for row in result['rows']},{present})
                missing=next(row for row in summary if row['market']==absent)
                self.assertEqual(missing['summary_status'],'no_market_price')
                self.assertIsNone(missing['point']);self.assertIsNone(missing['odds'])
                self.assertIsNone(missing['ev_pct'])

    def test_unsupported_xg_keeps_a_and_no_fabricated_probability_or_ev(self):
        sport='soccer_efl_champ'
        self.h.events[sport]=[self.h.event(sport)]
        result=self.h.run([sport])
        self.assertTrue(any(json.loads(row['analysis_a_json']).get('score') is not None for row in result['rows']))
        for row in result['rows']:
            b=json.loads(row['analysis_b_json'])
            self.assertFalse(b['available'])
            self.assertIsNone(row['ev_pct'])
            self.assertIsNone(row['p_win'])
            self.assertNotIn('expected_goals',b)
        view=self.h.save_and_view(result,[sport])
        summary=summary_market_rows(view.to_dict('records'),self.h.f['choose_recommendation'])
        self.assertTrue(all(not row['summary_recommended'] for row in summary))

    def test_unsafe_alias_cannot_borrow_similar_club_form_or_xg(self):
        case=aliases.Issue9ScanTests('test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form')
        case.setUp();self.addCleanup(case.doCleanups)
        case.prepare(english=True)
        case.event['home_team']='Ipswich Town U21'
        case.event['bookmakers'][0]['markets'][0]['outcomes'][0]['name']='Ipswich Town U21'
        rows,_,_,_=case.scan()
        row=next(row for row in rows if row['market']=='Over/Under')
        a,b=json.loads(row['analysis_a_json']),json.loads(row['analysis_b_json'])
        self.assertEqual(a['home_form']['sample5'],0)
        self.assertFalse(b['available']);self.assertIsNone(row['ev_pct'])
        self.assertIsNone(a['xg']['home'])

    def test_rescan_cached_receipt_deduplicates_snapshots_but_retains_raw_history(self):
        selected=self.eleven()[:1]
        first=self.h.run(selected)
        self.h.save_and_view(first,selected)
        before=len(self.h.f['load_picks'](limit=None))
        self.h.f['save_picks'](first['rows'],first['snapshots'])
        self.assertEqual(len(self.h.f['load_picks'](limit=None)),before)
        with self.h.f['db']() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0],4)
        # A genuinely newer response may remove every market. Old raw records
        # remain stored while the current response's explicit placeholder wins.
        self.h.now+=timedelta(seconds=1)
        sport=selected[0]
        self.h.events[sport]=[self.h.event(sport,suffix='within24',markets=())]
        latest=self.h.run(selected)
        view=self.h.save_and_view(latest,selected)
        self.assertEqual(view.event_id.nunique(),1)
        self.assertEqual(set(view.market),{'ยังไม่มีราคา'})
        with self.h.f['db']() as con:
            self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0],4)
            self.assertGreater(con.execute('SELECT count(*) FROM picks').fetchone()[0],len(view))

    def test_current_empty_response_hides_previous_games_without_deleting_history(self):
        sport='soccer_epl'
        self.h.events[sport]=[self.h.event(sport)]
        first=self.h.run([sport]);self.h.save_and_view(first,[sport])
        self.h.now+=timedelta(seconds=1)
        self.h.events[sport]=[]
        second=self.h.run([sport])
        view=self.h.save_and_view(second,[sport])
        self.assertEqual(second['leagues'][0]['status'],'empty_api')
        self.assertEqual(second['leagues'][0]['api_returned'],0)
        self.assertTrue(view.empty)
        self.assertEqual(len(self.h.f['load_picks'](limit=None)),4)

    def test_original_quarter_settlement_and_probability_survive_real_save_load(self):
        sport='soccer_epl'
        self.h.events[sport]=[self.h.event(sport)]
        result=self.h.run([sport]);view=self.h.save_and_view(result,[sport])
        ah=next(row for row in view.to_dict('records') if row['market']=='Asian Handicap' and row['side']=='home')
        ou=next(row for row in view.to_dict('records') if row['market']=='Over/Under' and row['side']=='over')
        self.assertEqual(self.h.f['settle_pick'](ah,1,1),('แพ้ครึ่ง',-.5,None))
        self.assertEqual(self.h.f['settle_pick'](ou,2,1),('ชนะครึ่ง',.55,None))
        for row in (ah,ou):
            b=json.loads(row['analysis_b_json'])
            self.assertEqual(b['selection']['ev_pct'],row['ev_pct'])
            self.assertAlmostEqual(sum(b['selection'][name] for name in ('p_win','p_partial','p_loss')),100,delta=.03)

    def test_temporary_database_backup_restore_retains_quotes_and_finalized_legacy_bill(self):
        selected=self.eleven()[:1]
        result=self.h.run(selected);self.h.save_and_view(result,selected)
        with self.h.f['db']() as con:
            con.execute("INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES(?,?,?,?)",
                ('{damaged legacy bill','ชนะเต็มทุกคู่',1.2,'immutable original'))
            original=[tuple(row) for row in con.execute('SELECT * FROM slips')]
        blob=backup_bytes(self.h.f['DB_FILE'])
        restored=Path(self.h.tmp.name)/'restore-verification.db'
        restore_new(blob,restored)
        import sqlite3
        with sqlite3.connect(restored) as con:
            self.assertEqual(con.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(con.execute('SELECT count(*) FROM odds_snapshots').fetchone()[0],4)
            self.assertEqual(con.execute('SELECT * FROM slips').fetchall(),original)

    def test_saved_mobile_card_groups_bookmakers_and_escapes_api_labels_without_http(self):
        sport='soccer_epl'
        event=self.h.event(sport)
        event['bookmakers'].append(deepcopy(event['bookmakers'][0]))
        event['bookmakers'][1].update(key='second-book',title='<script>window.PWNED=1</script>')
        self.h.events[sport]=[event]
        result=self.h.run([sport]);view=self.h.save_and_view(result,[sport])
        grouped=group_fixture_recommendations(view.to_dict('records'),self.h.f['choose_recommendation'])
        self.assertEqual(len(grouped),1)
        rendered=fixture_dashboard_html(grouped[0])+fixture_details_html(grouped[0],'offline fixture')
        self.assertEqual(rendered.count('<article class="vbp-dashboard"'),1)
        self.assertNotIn('<script>',rendered)
        self.assertIn('&lt;script&gt;',rendered)
        self.assertEqual(len(result['snapshots']),8)
        self.assertEqual(len(summary_market_rows(view.to_dict('records'),self.h.f['choose_recommendation'])),2)

    def test_explicit_regions_uk_eu_us_are_forwarded_once_per_selected_league(self):
        sport='soccer_epl'
        self.h.events[sport]=[self.h.event(sport)]
        for region in ('uk','eu','us'):
            self.h.f['fetch_odds'].reset_mock()
            result=self.h.run([sport],region=region)
            self.assertEqual(result['counts']['successful_leagues'],1)
            self.h.f['fetch_odds'].assert_called_once_with(sport,region,'SYNTHETIC_NO_CREDIT')


if __name__=='__main__':
    unittest.main()
