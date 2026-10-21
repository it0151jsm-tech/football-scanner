"""Synthetic source-quality regressions: no provider calls, DBs or real results."""
from copy import deepcopy
import json
import unittest

import pandas as pd

from domestic_data import build_domestic_history


SPORT='soccer_germany_bundesliga'


def row(home='Home Club',away='League Opponent',gf=2,ga=1,day='2026-09-01'):
    return dict(Date_dt=pd.Timestamp(day),HomeTeam=home,AwayTeam=away,FTHG=gf,FTAG=ga,
                _history_season='2627',_history_source_url='https://example.invalid/synthetic.csv',
                _history_received_at='2026-10-21T12:00:00+00:00',_history_payload_sha256='a'*64)


def resolution(input_name='Home Club',csv_name='Home Club',sport=SPORT):
    return dict(status='matched',sport_key=sport,input_name=input_name,csv_name=csv_name,
                csv_code='D1' if sport==SPORT else 'E0',source_season='2627')


def build(records,away=None):
    frame=pd.DataFrame(records)
    frame.attrs['history_diagnostics']={'status':'ok','quality_audit':{'conflicting_rows':0,'duplicate_rows':0}}
    return build_domestic_history(resolution(),away or dict(status='unmatched',input_name='Away Club'),{SPORT:frame})


class DomesticPostMappingQualityTests(unittest.TestCase):
    def quality(self,frame):
        return frame.attrs['domestic_source_reports'][SPORT]['post_mapping_quality']

    def test_conflicting_mapped_fixture_is_quarantined_in_both_source_orders(self):
        first=row(gf=2);second=row(home='Home  Club',gf=9)
        for records in ([first,second],[second,first]):
            with self.subTest(order=[r['FTHG'] for r in records]):
                output=build(records);quality=self.quality(output)
                self.assertTrue(output.empty)
                self.assertEqual(quality['conflicting_rows'],2)
                self.assertEqual(quality['accepted_rows'],0)
                self.assertEqual(quality['duplicate_rows'],0)
                self.assertEqual(output.attrs['domestic_source_reports'][SPORT]['status'],'conflicting_scores')
                self.assertEqual({d['home_goals'] for d in quality['details']},{2.0,9.0})
                self.assertTrue(all(d['reason']=='conflicting_score_after_verified_mapping' for d in quality['details']))

    def test_conflicting_conceded_goals_are_also_quarantined(self):
        output=build([row(ga=1),row(home='HOME CLUB',ga=7)])
        self.assertTrue(output.empty)
        self.assertEqual(self.quality(output)['conflicting_rows'],2)

    def test_unrelated_valid_games_remain_without_arbitrary_score_selection(self):
        output=build([row(gf=2),row(home='Home  Club',gf=9),row(day='2026-09-10',gf=3)])
        self.assertEqual(output.FTHG.tolist(),[3])
        quality=self.quality(output)
        self.assertEqual((quality['raw_rows'],quality['conflicting_rows'],quality['accepted_rows']),(3,2,1))

    def test_identical_mapped_fixture_is_counted_once_and_preserves_evidence(self):
        output=build([row(),row(home='home club')])
        self.assertEqual(len(output),1)
        quality=self.quality(output)
        self.assertEqual((quality['conflicting_rows'],quality['duplicate_rows'],quality['accepted_rows']),(0,1,1))
        detail=quality['details'][0]
        self.assertEqual(detail['reason'],'exact_duplicate_after_verified_mapping')
        self.assertEqual(detail['original_home'],'home club')
        self.assertEqual(detail['mapped_home'],'Home Club')
        self.assertEqual(detail['source_url'],'https://example.invalid/synthetic.csv')
        self.assertEqual(detail['received_at'],'2026-10-21T12:00:00+00:00')
        self.assertEqual(detail['payload_sha256'],'a'*64)
        self.assertEqual(output.attrs['post_mapping_quality'][SPORT],quality)
        self.assertEqual(output.attrs['domestic_source_reports'][SPORT]['source']['quality_audit']['conflicting_rows'],0)

    def test_verified_mapping_of_both_selected_clubs_is_checked_after_rename(self):
        output=build([row(away='Away Club'),row(home='HOME CLUB',away='Away  Club',gf=8)],resolution('Away Club','Away Club'))
        self.assertTrue(output.empty)
        quality=self.quality(output)
        self.assertEqual(quality['conflicting_rows'],2)
        self.assertTrue(all(d['mapped_away']=='Away Club' for d in quality['details']))

    def test_labels_that_only_look_similar_are_not_remapped(self):
        output=build([row(),row(home='Hôme Club',gf=9)])
        self.assertEqual(len(output),2)
        self.assertEqual(self.quality(output)['conflicting_rows'],0)
        self.assertEqual(output.loc[output.HomeTeam=='Home Club','FTHG'].tolist(),[2])
        self.assertTrue(any(name.startswith('__domestic__[') for name in output.HomeTeam))

    def test_different_leagues_do_not_quarantine_distinct_source_fixtures(self):
        home=resolution('Home Club','United',SPORT)
        away=resolution('Away Club','United','soccer_epl')
        output=build_domestic_history(home,away,{
            SPORT:pd.DataFrame([row(home='United',gf=2)]),
            'soccer_epl':pd.DataFrame([row(home='United',gf=9)])})
        self.assertEqual(len(output),2)
        self.assertEqual(sorted(output.FTHG.tolist()),[2,9])
        self.assertEqual(output.attrs['post_mapping_quality'][SPORT]['conflicting_rows'],0)
        self.assertEqual(output.attrs['post_mapping_quality']['soccer_epl']['conflicting_rows'],0)

    def test_evidence_details_are_bounded_without_hiding_total_counts(self):
        rows=[]
        for i in range(30):
            date=(pd.Timestamp('2026-08-01')+pd.Timedelta(days=i)).date().isoformat()
            rows.extend([row(day=date),row(home='home club',gf=9,day=date)])
        output=build(rows);quality=self.quality(output)
        self.assertTrue(output.empty)
        self.assertEqual(quality['conflicting_rows'],60)
        self.assertEqual(len(quality['details']),50)
        self.assertTrue(quality['details_truncated'])

    def test_input_source_and_attributes_are_unchanged(self):
        source=pd.DataFrame([row(),row(home='home club',gf=9)])
        source.attrs['history_diagnostics']={'status':'ok','quality_audit':{'conflicting_rows':0}}
        before=source.copy(deep=True);attrs=deepcopy(source.attrs)
        build_domestic_history(resolution(),dict(status='unmatched',input_name='Away Club'),{SPORT:source})
        pd.testing.assert_frame_equal(source,before)
        self.assertEqual(source.attrs,attrs)

    def test_missing_provenance_is_null_in_quarantine_not_invented_or_nan(self):
        first=row();second=row(home='home club',gf=9)
        for record in (first,second):
            record['_history_source_url']=float('nan')
            record['_history_received_at']=pd.NA
            record['_history_payload_sha256']=None
        quality=self.quality(build([first,second]))
        json.dumps(quality,allow_nan=False)
        for detail in quality['details']:
            self.assertIsNone(detail['source_url'])
            self.assertIsNone(detail['received_at'])
            self.assertIsNone(detail['payload_sha256'])
