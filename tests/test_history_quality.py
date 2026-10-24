"""Source-quality regression examples are synthetic; no provider requests."""
import hashlib
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
from history_data import load_history, validate_rows

HEADER='Date,HomeTeam,AwayTeam,FTHG,FTAG\n'


class HistoryQualityTests(unittest.TestCase):
    def load(self, lines, code='D1', seasons=('2627',)):
        payload=(HEADER+'\n'.join(lines)+'\n').encode()
        response=SimpleNamespace(status_code=200,content=payload,url='https://example.invalid/synthetic.csv')
        frame=load_history(code,Mock(return_value=response),seasons=seasons)
        return frame,payload,frame.attrs['history_diagnostics']['quality_audit']

    def test_contradictory_score_never_silently_chooses_first_or_last(self):
        frame,_,audit=self.load(['01/10/2026,Bayern Munich,Opponent,9,0',
                                 '01/10/2026,Bayern Munich,Opponent,1,0',
                                 '02/10/2026,Bayern Munich,Other,2,1'])
        self.assertEqual(frame.FTHG.tolist(),[2]);self.assertEqual(audit['conflicting_rows'],2)
        self.assertEqual({r['reason'] for r in audit['details']},{'conflicting_score'})
        self.assertEqual({r['home_goals'] for r in audit['details']},{9,1})

    def test_exact_duplicate_not_a_second_sample(self):
        frame,_,audit=self.load(['01/10/2026,Bayern Munich,Opponent,6,0']*2+['02/10/2026,Bayern Munich,Other,2,1'])
        self.assertEqual(len(frame),2);self.assertEqual(frame.FTHG.mean(),4)
        self.assertEqual(audit['duplicate_rows'],1);self.assertEqual(audit['conflicting_rows'],0)

    def test_misfiled_season_cannot_contribute_to_form(self):
        frame,_,audit=self.load(['01/10/2025,Bayern Munich,Opponent,9,0','01/10/2026,Bayern Munich,Other,2,1'])
        self.assertEqual(len(frame),1);self.assertEqual(audit['invalid_season_rows'],1)
        self.assertEqual(audit['details'][0]['reason'],'date_season_mismatch')

    def test_conflicts_and_misfiled_season_have_distinct_empty_diagnostics(self):
        frame,_,_=self.load(['01/10/2026,A,B,9,0','01/10/2026,A,B,1,0'])
        self.assertTrue(frame.empty);self.assertEqual(frame.attrs['history_diagnostics']['status'],'conflicting_scores')
        frame,_,_=self.load(['01/10/2025,A,B,1,0'])
        self.assertEqual(frame.attrs['history_diagnostics']['status'],'invalid_season')

    def test_provenance_records_actual_receipt_and_payload_digest(self):
        before=datetime.now(timezone.utc)
        frame,payload,_=self.load(['01/10/2026,A,B,1,0'])
        after=datetime.now(timezone.utc)
        row=frame.iloc[0]
        self.assertEqual(row['_history_payload_sha256'],hashlib.sha256(payload).hexdigest())
        self.assertEqual(row['_history_source_url'],'https://example.invalid/synthetic.csv')
        received=datetime.fromisoformat(row['_history_received_at'])
        self.assertLessEqual(before,received);self.assertLessEqual(received,after)
        self.assertNotEqual(received.date(),row.Date_dt.date())

    def test_other_leagues_without_verified_season_bounds_keep_legacy_scope(self):
        frame,_,audit=self.load(['01/10/2026,A,B,1,0'],code='E2',seasons=('2526',))
        self.assertEqual(len(frame),1);self.assertEqual(audit['invalid_season_rows'],0)

    def test_boolean_scores_are_not_numeric_football_results(self):
        frame=pd.DataFrame([dict(Date_dt='2026-10-01',HomeTeam='A',AwayTeam='B',FTHG=True,FTAG=0),
                            dict(Date_dt='2026-10-02',HomeTeam='A',AwayTeam='B',FTHG=1,FTAG=0)])
        clean,info=validate_rows(frame)
        self.assertEqual(len(clean),1);self.assertEqual(info['invalid_scores'],1)

