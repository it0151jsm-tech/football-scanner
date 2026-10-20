import json
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd

import test_regressions as regression
from history_data import load_history, parse_csv, match_team, team_history
from mobile_dashboard import form_tile, details_html


HEADER = 'Date,HomeTeam,AwayTeam,FTHG,FTAG\n'


class HistoryDataTests(unittest.TestCase):
    def setUp(self):
        self.kickoff = datetime(2026, 10, 21, tzinfo=timezone.utc)
        self.f = regression.load_functions(Path(__file__).resolve().parents[1] / 'app.py')

    def history(self, lines):
        return parse_csv((HEADER + '\n'.join(lines)).encode())[0]

    def test_verified_oxford_wigan_aliases_without_fuzzy_wrong_teams(self):
        self.assertEqual(match_team('Oxford United', ['Oxford','Oxford City'])[0], 'Oxford')
        self.assertEqual(match_team('Wigan Athletic', ['Wigan'])[0], 'Wigan')
        for name, candidates in [('Oxford United',['Oxford City']), ('Wigan Athletic',['Wigan Athletic U21']),
                                 ('Cambridge United',['Oxford']), ('Oxford United',['Oxford Utd U18'])]:
            self.assertIsNone(match_team(name, candidates)[0])
        self.assertEqual(match_team('Oxford United',['Oxford','Oxford '])[1], 'name_ambiguous')

    def test_download_failure_and_http_error_preserve_per_season_diagnostics(self):
        get = Mock(side_effect=TimeoutError('test timeout'))
        frame = load_history('E2', get)
        self.assertTrue(frame.empty)
        self.assertEqual(get.call_count, 4)
        self.assertTrue(all(s['status'] == 'download_error' for s in frame.attrs['history_diagnostics']['sources']))
        _, report = team_history(frame,'Oxford United',self.kickoff,'home')
        self.assertEqual(report['status'], 'download_error')
        response = SimpleNamespace(status_code=404, content=b'not found')
        frame = load_history('E2', Mock(return_value=response))
        self.assertTrue(all(s['http_status'] == 404 for s in frame.attrs['history_diagnostics']['sources']))
        self.assertEqual(frame.attrs['history_diagnostics']['status'], 'http_error')

    def test_empty_files_and_missing_columns_have_distinct_causes(self):
        for content, expected in [(b'', 'empty_file'), (HEADER.encode(),'empty_file'),
                                  (b'Date,HomeTeam\n01/10/2026,Oxford\n','missing_columns')]:
            with self.subTest(content=content):
                frame, report = parse_csv(content)
                self.assertTrue(frame.empty)
                self.assertEqual(report['status'], expected)

    def test_mixed_year_formats_are_parsed_per_row_without_swapping_day_month(self):
        frame, report = parse_csv((HEADER+'01/10/26,Oxford,Wigan,1,0\n02/10/2026,Wigan,Oxford,2,1\n2026-10-03,Oxford,Wigan,0,0\n31/02/2026,Oxford,Wigan,1,1\n').encode())
        self.assertEqual(frame.Date_dt.dt.strftime('%Y-%m-%d').tolist(), ['2026-10-01','2026-10-02','2026-10-03'])
        self.assertEqual(report['invalid_dates'], 1)
        self.assertEqual(report['valid_rows'], 3)

    def test_missing_negative_fractional_or_infinite_scores_are_not_results(self):
        frame, report = parse_csv((HEADER+'01/10/2026,Oxford,Wigan,1,\n02/10/2026,Oxford,Wigan,-1,0\n03/10/2026,Oxford,Wigan,1.5,0\n04/10/2026,Oxford,Wigan,inf,0\n05/10/2026,Oxford,Wigan,2,1\n').encode())
        self.assertEqual(len(frame), 1)
        self.assertEqual(report['invalid_scores'], 4)
        self.assertNotIn('xG_home', frame.columns)

    def test_filter_before_utc_fixture_day_excludes_same_day_and_future(self):
        hist = self.history(['20/10/2026,Oxford,Wigan,2,0','21/10/2026,Oxford,Wigan,9,0','22/10/2026,Oxford,Wigan,8,0'])
        games, report = team_history(hist,'Oxford United',self.kickoff,'home')
        self.assertEqual(len(games), 1)
        self.assertEqual(report['mapping'], 'verified_alias')
        self.assertEqual(report['matched_name'], 'Oxford')
        profile = self.f['team_form_profile'](hist,'Oxford United',self.kickoff,'home')
        self.assertEqual(profile['last5_gf'], 2)
        self.assertEqual(profile['diagnostics']['status'], 'insufficient_history')

    def test_invalid_date_name_and_no_past_games_are_distinct(self):
        hist = self.history(['22/10/2026,Oxford,Wigan,1,0'])
        self.assertEqual(team_history(hist,'Oxford United',self.kickoff,'home')[1]['status'], 'no_history_before_kickoff')
        self.assertEqual(team_history(hist,'Oxford City',self.kickoff,'home')[1]['status'], 'name_unmatched')
        self.assertEqual(team_history(hist,'Oxford United','bad date','home')[1]['status'], 'invalid_kickoff')
        invalid = pd.DataFrame([dict(Date_dt='bad date',HomeTeam='Oxford',AwayTeam='Wigan',FTHG=1,FTAG=0)])
        self.assertEqual(team_history(invalid,'Oxford United',self.kickoff,'home')[1]['status'], 'invalid_dates')

    def test_one_failed_season_does_not_discard_other_valid_seasons(self):
        good = (HEADER+'01/10/2026,Oxford,Wigan,1,0\n').encode()
        def get(url, timeout):
            return SimpleNamespace(status_code=404,content=b'404') if '/2627/' in url else SimpleNamespace(status_code=200,content=good)
        frame = load_history('E2',get)
        self.assertEqual(len(frame), 1)
        self.assertEqual(len(frame.attrs['history_diagnostics']['sources']), 4)
        self.assertEqual(frame.attrs['history_diagnostics']['status'], 'ok')

    def test_alias_recovers_both_profiles_and_a_without_enabling_b_from_scores(self):
        hist = self.history([f'{day:02}/10/2026,Oxford,Wigan,2,1' for day in range(1,11)])
        hp = self.f['team_form_profile'](hist,'Oxford United',self.kickoff,'home')
        ap = self.f['team_form_profile'](hist,'Wigan Athletic',self.kickoff,'away')
        self.assertEqual(hp['sample5'], 5)
        self.assertEqual(ap['sample5'], 5)
        form5 = self.f['estimate_form_signal'](hist,'Oxford United','Wigan Athletic',self.kickoff,'totals','over',2.5,5)
        form10 = self.f['estimate_form_signal'](hist,'Oxford United','Wigan Athletic',self.kickoff,'totals','over',2.5,10)
        a = self.f['system_a_analysis'](form5,form10,form10,hp,ap,{}, {}, {})
        self.assertIsNotNone(a['score'])
        self.assertIsNone(self.f['estimate_goals'](hist,'Oxford United','Wigan Athletic',self.kickoff))
        self.assertNotIn('soccer_england_league1',self.f['UNDERSTAT_LEAGUES'])

    def test_profile_load_diagnostic_is_visible_and_escaped_without_fake_averages(self):
        hist = load_history('E2',Mock(side_effect=TimeoutError()))
        profile = self.f['team_form_profile'](hist,'Oxford United',self.kickoff,'home')
        self.assertIsNone(profile['last5_gf'])
        tile = form_tile({'home_team':'Oxford United'},{'home_form': profile},'home')
        self.assertIn('โหลดข้อมูลไม่สำเร็จ',tile)
        profile['diagnostics']['message'] = '<img src=x onerror=alert(1)>'
        html = form_tile({}, {'home_form':profile}, 'home')
        self.assertNotIn('<img ',html)
        profile['diagnostics']['message'] = 'โหลดข้อมูลไม่สำเร็จ'
        details = details_html({'analysis_a_json':json.dumps({'home_form':profile})})
        self.assertIn('HTTP',details)
        self.assertIn('E2',details)

    def test_old_season_examples_are_identified_instead_of_hidden(self):
        hist = self.history(['01/01/2024,Oxford,Wigan,1,0','02/01/2024,Oxford,Wigan,1,0',
                             '01/10/2026,Oxford,Wigan,1,0','02/10/2026,Oxford,Wigan,1,0','03/10/2026,Oxford,Wigan,1,0'])
        profile = self.f['team_form_profile'](hist,'Oxford United',self.kickoff,'home')
        self.assertEqual(profile['sample5'], 5)
        self.assertGreater(profile['diagnostics']['sample5_span_days'], 180)
        self.assertIn('รวมเกมเก่า',form_tile({}, {'home_form': profile},'home'))

    def test_scan_serializes_alias_diagnostics_but_never_uses_csv_goals_or_hxg_for_b(self):
        kickoff = datetime.now(timezone.utc)+timedelta(hours=3)
        lines = []
        for days in range(1,11):
            date = (kickoff-timedelta(days=days)).strftime('%d/%m/%Y')
            lines.append(f'{date},Oxford,Wigan,2,1,1.7,1.1')
        hist, _ = parse_csv(('Date,HomeTeam,AwayTeam,FTHG,FTAG,HxG,AxG\n'+'\n'.join(lines)).encode())
        self.f['fetch_history'] = Mock(return_value=hist)
        self.f['fetch_understat_history'] = Mock(return_value=pd.DataFrame())
        self.f['first_observed_quotes'] = Mock(return_value={})
        self.f['record_data_health'] = Mock()
        event = dict(id='oxford-wigan',home_team='Oxford United',away_team='Wigan Athletic',commence_time=kickoff.isoformat(),
                     bookmakers=[dict(title='Book',markets=[dict(key='totals',outcomes=[dict(name='Over',point=2.5,price=1.95)])])])
        self.f['fetch_odds'] = Mock(return_value=([event],'',''))
        rows, _, _, _ = self.f['scan_league']('soccer_england_league1','ลีกวัน อังกฤษ','E2',24,1.8,2.05,'uk','offline-test')
        a, b = json.loads(rows[0]['analysis_a_json']), json.loads(rows[0]['analysis_b_json'])
        self.assertEqual(a['home_form']['diagnostics']['matched_name'],'Oxford')
        self.assertEqual(a['away_form']['diagnostics']['matched_name'],'Wigan')
        self.assertEqual(a['home_form']['sample5'],5)
        self.assertIsNotNone(a['score'])
        self.assertFalse(b['available'])
        self.assertIsNone(rows[0]['ev_pct'])
        self.f['fetch_odds'].assert_called_once()
