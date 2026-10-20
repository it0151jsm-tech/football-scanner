import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock

from manual_quotes import evaluate_manual, validate_quote
from mobile_dashboard import manual_quotes_html
from test_market_recommendations import quote
from test_regressions import load_functions


class ManualQuoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.f=load_functions(Path(__file__).resolve().parents[1]/'app.py')
        self.f['DB_FILE']=Path(self.tmp.name)/'test.db';self.f['init_db']()
        self.now=datetime.now(timezone.utc)
        self.row=quote()
        self.row.update(scanned_at=self.now.isoformat(),kickoff_utc=(self.now+timedelta(hours=10)).isoformat(),
                        model_status='Poisson จาก xG/xGA จริง (Understat)',lambda_home=1.7,lambda_away=1.1)
        a=json.loads(self.row['analysis_a_json'])
        a['xg']={s:dict(xg=1.7,xga=1.1,sample=6,last_date=(self.now-timedelta(days=3)).date().isoformat()) for s in ('home','away')}
        self.row['analysis_a_json']=json.dumps(a)

    def evaluate(self,row,line=3):
        return evaluate_manual(row,line,1.98,1.91,self.now,self.f['score_matrix'],self.f['evaluate_market_poisson'])

    def snapshot(self):
        with self.f['db']() as con:
            return {t:[tuple(r) for r in con.execute('SELECT * FROM '+t+' ORDER BY id')] for t in ('picks','slips','odds_snapshots')}

    def test_fresh_real_model_uses_existing_ev_for_each_actual_manual_line(self):
        for line in (2.5,2.75,3,3.25,3.5):
            result=self.evaluate(self.row,line)
            self.assertTrue(result['available'])
            for side,odds in [('over',1.98),('under',1.91)]:
                expected=self.f['evaluate_market_poisson'](self.f['score_matrix'](1.7,1.1),'totals',side,line,odds)
                self.assertEqual(result['results'][side],expected)
            self.assertFalse(result['calibrated'])

    def test_stale_missing_damaged_and_legacy_models_cannot_produce_ev(self):
        cases=[{},dict(analysis_b_json='[]'),dict(analysis_a_json='{bad'),dict(model_status='legacy'),
               dict(scanned_at=(self.now-timedelta(days=2)).isoformat()),dict(lambda_home=2),
               dict(kickoff_utc=(self.now-timedelta(hours=1)).isoformat())]
        a=json.loads(self.row['analysis_a_json']);a['xg']['home']['last_date']='2020-01-01'
        cases.append(dict(analysis_a_json=json.dumps(a)))
        a['xg']['home'].pop('last_date');cases.append(dict(analysis_a_json=json.dumps(a)))
        for changes in cases:
            row=dict(self.row,**changes) if changes else None
            result=self.evaluate(row)
            self.assertFalse(result['available'])
            self.assertNotIn('results',result)

    def test_invalid_quotes_rejected_without_writes(self):
        before=self.snapshot()
        for args in [('',3,2,2),('Book',2.6,2,2),('Book',None,2,2),('Book',3,1,2),
                     ('Book',float('nan'),2,2),('Book',3,float('inf'),2),('Book',True,2,2)]:
            with self.assertRaises(ValueError):self.f['save_manual_quote']('event',*args)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(validate_quote(' Book ',2.75,1.9,2)['source'],'MANUAL')

    def test_append_manual_keeps_all_api_snapshots_and_old_bills_byte_for_byte(self):
        self.f['save_picks']([self.row],[dict(event_id='event',market='Over/Under',side='over',point=2.5,odds=2.1,bookmaker='OU Book')])
        with self.f['db']() as con:
            for payload in ('{bad','',None,'[{"pick_id":1}]'):
                con.execute('INSERT INTO slips(selections_json,status,profit_units,final_summary) VALUES (?,?,?,?)',
                            (payload,'แพ้บิล',-1,'keep final'))
        before=self.snapshot()
        self.f['fetch_odds']=Mock(side_effect=AssertionError('must remain offline'))
        first=self.f['save_manual_quote']('event','<script>book</script>',3,1.98,1.91)
        second=self.f['save_manual_quote']('event','Other',3.25,2,1.9)
        self.assertGreater(second,first)
        self.assertEqual(before,self.snapshot())
        self.f['fetch_odds'].assert_not_called()
        rows=self.f['load_manual_quotes']('event')
        self.assertEqual(len(rows),2)
        self.assertTrue(json.loads(rows[0]['model_result_json'])['available'])
        self.assertEqual(self.f['load_manual_quotes']('other'),[])
        html=manual_quotes_html(rows)
        self.assertIn('&lt;script&gt;book',html);self.assertNotIn('<script>',html)
        self.assertIn('MANUAL',html)

    def test_quote_without_trusted_model_is_saved_without_replacement_ev(self):
        self.row['analysis_a_json']='{}'
        self.f['save_picks']([self.row])
        self.f['save_manual_quote']('event','Manual',3,2,1.9)
        row=self.f['load_manual_quotes']('event')[0]
        self.assertFalse(json.loads(row['model_result_json'])['available'])
        self.assertEqual(row['source'],'MANUAL')

    def test_rescan_preserves_multiple_lines_and_frozen_bill(self):
        self.f['save_picks']([self.row])
        original=self.f['load_picks'](limit=None)
        self.f['save_slip_record'](original,2.1,1)
        before=self.snapshot()
        rows=[dict(self.row,point=line) for line in (2.5,2.75,3,3.25,3.5)]
        self.f['save_picks'](rows)
        after=self.snapshot()
        self.assertEqual(before['slips'],after['slips'])
        self.assertEqual(before['picks'][0],after['picks'][0])
        self.assertEqual({r['point'] for r in self.f['load_picks'](limit=None).to_dict('records')},{2.5,2.75,3,3.25,3.5})
