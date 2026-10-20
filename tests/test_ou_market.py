"""Offline market/provenance checks; no fabricated production recommendations."""
import json
import unittest
from pathlib import Path

from market_recommendations import assess, group_fixture_recommendations
from ou_market import select_main_line, compare_sides, selection_counts
from test_market_recommendations import quote
from test_regressions import load_functions
from mobile_dashboard import fixture_dashboard_html, fixture_details_html


class MainOUTests(unittest.TestCase):
    def setUp(self):
        self.f = load_functions(Path(__file__).resolve().parents[1] / 'app.py')

    def pair(self, line=3, book='Book', over=1.95, under=1.95):
        return [quote(point=line,book=book,side=s,odds=p,pick=i) for i,(s,p) in enumerate([('over',over),('under',under)],1)]

    def main(self, rows):
        return select_main_line([assess(r,self.f['choose_recommendation']) for r in rows])

    def test_balanced_three_beats_expensive_under_two_half(self):
        main = self.main(self.pair(2.5,under=2.8,over=1.45)+self.pair(3))
        self.assertEqual(main['line'],3)
        self.assertEqual(main['pair']['under']['row']['odds'],1.95)

    def test_all_requested_lines_are_evaluated_without_substitution(self):
        for line in (2.5,2.75,3,3.25,3.5):
            with self.subTest(line=line):
                fixture=group_fixture_recommendations(self.pair(line),self.f['choose_recommendation'])[0]
                self.assertEqual(fixture['ou_main']['line'],line)
                for side in ('over','under'):
                    entry=fixture['ou_main']['pair'][side]
                    self.assertEqual(entry['b']['ou_line'],line)
                    expected=self.f['evaluate_market_poisson'](self.f['score_matrix'](1.7,1.1),'totals',side,line,1.95)
                    self.assertAlmostEqual(entry['ev'],expected['ev_pct'])

    def test_no_cross_book_scan_or_line_pairing(self):
        for key,value in [('bookmaker','Other'),('point',2.75),('scanned_at','2026-10-20T13:00:00+07:00')]:
            rows=self.pair();rows[1][key]=value
            self.assertEqual(self.main(rows)['status'],'unconfirmed')

    def test_near_two_alone_and_incomplete_prices_do_not_confirm_main(self):
        self.assertEqual(self.main(self.pair(over=2,under=5))['status'],'unconfirmed')
        self.assertEqual(self.main(self.pair()[:1])['status'],'unconfirmed')
        rows=self.pair();rows[1]['odds']=None
        self.assertEqual(self.main(rows)['status'],'unconfirmed')

    def test_multi_book_support_precedes_balance_and_ties_are_unconfirmed(self):
        rows=self.pair(3,book='A',over=1.9,under=2)+self.pair(3,book='B',over=1.9,under=2)+self.pair(2.5,book='A')
        self.assertEqual(self.main(rows)['line'],3)
        self.assertEqual(self.main(self.pair(2.5)+self.pair(3))['status'],'unconfirmed')

    def test_five_lines_from_same_scan_are_preserved(self):
        rows=sum((self.pair(line) for line in (2.5,2.75,3,3.25,3.5)),[])
        fixture=group_fixture_recommendations(rows,self.f['choose_recommendation'])[0]
        self.assertEqual(len(fixture['alternatives']['Over/Under']),10)
        self.assertIsNone(fixture['ou_direction'])

    def test_real_ev_can_select_either_side_symmetrically(self):
        # Real Poisson means 2.8 total: Over 2.5 and Under 3.5 have positive EV.
        for line,side in [(2.5,'over'),(3.5,'under')]:
            fixture=group_fixture_recommendations(self.pair(line),self.f['choose_recommendation'])[0]
            self.assertEqual(fixture['ou_direction'],side)
            self.assertIn('EV',fixture['ou_reason'])
            self.assertGreater(fixture['markets']['Over/Under']['ev'],0)

    def test_equal_ev_never_resolves_by_order_id_odds_or_a(self):
        main=self.main(self.pair())
        for side in ('over','under'):
            main['pair'][side].update(tier=4,ev=5)
        for reverse in (False,True):
            if reverse:
                main['pair']['over'],main['pair']['under']=main['pair']['under'],main['pair']['over']
            self.assertIsNone(compare_sides(main)[0])

    def test_missing_ev_is_neutral_even_with_high_form_and_price(self):
        rows=self.pair(over=2.1,under=1.85)
        for r in rows:
            a=json.loads(r['analysis_a_json']);a['score']=99
            r.update(analysis_a_json=json.dumps(a),analysis_b_json=None,ev_pct=None)
        fixture=group_fixture_recommendations(rows,self.f['choose_recommendation'])[0]
        self.assertIsNone(fixture['ou_direction'])
        html=fixture_dashboard_html(fixture)
        self.assertIn('ตัวเลือกตลาดที่กำลังประเมิน',html)
        self.assertNotIn('ตัวเลือกเด่น',html)
        self.assertNotIn('class="vbp-goal"',html)

    def test_counts_exclude_neutral_and_are_not_win_probabilities(self):
        fixtures=[{'ou_main':{'status':'estimated'},'ou_direction':s} for s in ['over','under','under',None]]+[{}]
        c=selection_counts(fixtures)
        self.assertAlmostEqual(c['over_pct'],100/3)
        self.assertEqual((c['neutral'],c['missing_main']),(1,1))
        self.assertIsNone(selection_counts([{}])['over_pct'])

    def test_pair_prices_time_and_alternatives_are_escaped(self):
        rows=self.pair(3,book='<script>bad</script>')+self.pair(2.5,book='Alternative',over=1.45,under=2.8)
        fixture=group_fixture_recommendations(rows,self.f['choose_recommendation'])[0]
        html=fixture_dashboard_html(fixture)
        self.assertIn('1.95',html);self.assertIn('2026-10-20',html)
        self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)
        self.assertNotIn('Alternative',html)
        self.assertIn('Alternative',fixture_details_html(fixture,'rule'))
