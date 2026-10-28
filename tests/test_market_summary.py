"""Synthetic read-only summaries preserve source rows and original market policy."""
from copy import deepcopy
import json
import unittest

from market_recommendations import group_fixture_recommendations, ou_table_rows, TREND
from market_summary import summary_market_rows, EVALUATING, NO_PRICE, NO_BET
from test_market_recommendations import quote


def choose_c(score,ev,in_range):
    return 'ตัวเลือกเด่น' if score>=55 and ev>0 and in_range else NO_BET


def pair(*,line=2.5,book='OU Book',event='event',pick=1):
    return [quote(point=line,side='over',book=book,event=event,pick=pick,odds=1.95),
            quote(point=line,side='under',book=book,event=event,pick=pick+1,odds=1.95)]


class MarketSummaryTests(unittest.TestCase):
    def test_two_markets_many_sides_books_and_lines_have_one_summary_each(self):
        rows=[quote('Asian Handicap',-.25,'home',pick=1),
              quote('Asian Handicap',.25,'away',pick=2)]
        rows+=pair(pick=3)+pair(line=2.75,book='Another',pick=5)
        result=summary_market_rows(rows,choose_c)
        self.assertEqual([(row['event_id'],row['market']) for row in result],
                         [('event','Asian Handicap'),('event','Over/Under')])
        self.assertEqual(result[1]['summary_alternative_count'],4)
        self.assertEqual(len(rows),6)

    def test_original_ou_both_side_rows_remain_available_for_details(self):
        rows=pair()
        self.assertEqual(len(ou_table_rows(rows,choose_c)),2)
        self.assertEqual(len(summary_market_rows(rows,choose_c,include_missing=False)),1)
        groups=group_fixture_recommendations(rows,choose_c)
        self.assertEqual(len(groups[0]['alternatives']['Over/Under']),2)

    def test_ou_selected_side_is_original_policy_choice_and_exact_quote(self):
        rows=pair()
        fixture=group_fixture_recommendations(rows,choose_c)[0]
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertEqual(result['side'],fixture['ou_direction'])
        self.assertTrue(result['summary_recommended'])
        selected=next(row for row in rows if row['side']==fixture['ou_direction'])
        for key in ('id','point','odds','bookmaker','received_at','source_updated_at'):
            self.assertEqual(result[key],selected[key])

    def test_unknown_ev_with_form_never_becomes_default_over_recommendation(self):
        rows=pair()
        for row in rows:
            row.update(analysis_b_json=json.dumps({'available':False}),ev_pct=None)
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertEqual(result['recommendation'],TREND)
        self.assertIsNone(result['ev_pct'])
        self.assertFalse(result['ou_selected'])
        self.assertFalse(result['summary_recommended'])
        self.assertNotIn('ตัวเลือกเด่น',result['recommendation'])
        self.assertIn('EV สองฝั่งยังไม่ครบ',result['summary_reason'])

    def test_unpaired_market_is_an_evaluation_even_when_anchor_ev_is_positive(self):
        rows=[pair()[0]]
        self.assertGreater(rows[0]['ev_pct'],0)
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertEqual(result['recommendation'],EVALUATING)
        self.assertFalse(result['summary_recommended'])
        self.assertFalse(result['ou_selected'])
        self.assertIn('ไม่สามารถยืนยันเส้นหลัก',result['summary_reason'])

    def test_bad_numeric_main_pair_never_creates_recommendation(self):
        rows=[quote(side='over',odds=9,pick=1),quote(side='under',odds=9,pick=2)]
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertFalse(result['summary_recommended'])
        self.assertEqual(result['recommendation'],EVALUATING)
        self.assertNotEqual(result['ou_main_status'],'estimated')

    def test_higher_price_or_a_alone_cannot_replace_original_good_ou_policy(self):
        rows=pair()
        bad=quote(point=2.5,book='Expensive unpaired',pick=30,odds=9)
        a=json.loads(bad['analysis_a_json'])
        a['score']=99
        bad.update(analysis_a_json=json.dumps(a),analysis_b_json=json.dumps({'available':False}),ev_pct=None)
        rows.append(bad)
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertEqual(result['bookmaker'],'OU Book')
        self.assertNotEqual(result['odds'],9)

    def test_missing_ou_placeholder_has_no_line_price_book_or_probability(self):
        rows=[quote('Asian Handicap',-.25,'home')]
        result=summary_market_rows(rows,choose_c)
        missing=next(row for row in result if row['market']=='Over/Under')
        self.assertEqual(missing['selection'],NO_PRICE)
        self.assertEqual(missing['summary_status'],'no_market_price')
        self.assertEqual(missing['recommendation'],NO_BET)
        for key in ('id','side','point','odds','bookmaker','bookmaker_key','ev_pct',
                    'p_win','p_partial','p_loss','score','confidence_score'):
            self.assertIsNone(missing[key],key)
        self.assertNotIn('analysis_a_json',missing)

    def test_missing_ah_is_also_explicit_without_synthetic_contract(self):
        result=summary_market_rows(pair(),choose_c)
        missing=next(row for row in result if row['market']=='Asian Handicap')
        self.assertEqual(missing['selection'],NO_PRICE)
        self.assertIsNone(missing['point'])
        self.assertFalse(missing['summary_recommended'])

    def test_program_without_any_price_has_two_no_market_rows(self):
        row=quote()
        row.update(market='ยังไม่มีราคา',side=None,point=None,odds=None,bookmaker=None)
        result=summary_market_rows([row],choose_c)
        self.assertEqual(len(result),2)
        self.assertTrue(all(row['selection']==NO_PRICE for row in result))
        self.assertTrue(all(row['odds'] is None for row in result))

    def test_optional_actual_markets_only_has_no_placeholders(self):
        result=summary_market_rows(pair(),choose_c,include_missing=False)
        self.assertEqual(len(result),1)
        self.assertEqual(result[0]['market'],'Over/Under')

    def test_distinct_event_ids_never_merge_even_when_match_text_same(self):
        rows=pair(event='one')+pair(event='two',pick=3)
        result=summary_market_rows(rows,choose_c)
        self.assertEqual(len(result),4)
        self.assertEqual({row['event_id'] for row in result},{'one','two'})

    def test_missing_event_id_rows_are_isolated_and_cannot_recommend(self):
        rows=pair()
        for row in rows:
            row['event_id']=None
        result=summary_market_rows(rows,choose_c,include_missing=False)
        self.assertEqual(len(result),2)
        self.assertTrue(all(row['summary_status']=='identity_unverified' for row in result))
        self.assertTrue(all(not row['summary_recommended'] for row in result))

    def test_reused_event_id_with_conflicting_teams_waits_for_review(self):
        rows=pair()
        rows.append(quote('Asian Handicap',-.25,'home',pick=7))
        rows[-1]['home_team']='Different Home'
        result=summary_market_rows(rows,choose_c)
        self.assertEqual(len(result),2)
        actual=[row for row in result if row['summary_status']!='no_market_price']
        self.assertTrue(all(row['summary_status']=='identity_unverified' for row in actual))
        self.assertTrue(all(row['recommendation']==NO_BET for row in actual))

    def test_summary_never_mutates_raw_quotes_or_saved_json(self):
        rows=pair()+[quote('Asian Handicap',-.25,'home',pick=4)]
        before=deepcopy(rows)
        summary_market_rows(rows,choose_c)
        self.assertEqual(rows,before)

    def test_outside_recorded_price_range_remains_no_bet(self):
        rows=pair()
        for row in rows:
            row['odds_in_range']=0
        result=summary_market_rows(rows,choose_c,include_missing=False)[0]
        self.assertFalse(result['summary_recommended'])
        self.assertFalse(result['ou_selected'])
        self.assertEqual(result['recommendation'],NO_BET)
        self.assertIn('กรอบ',result['summary_reason'])

    def test_empty_input_has_no_invented_fixtures(self):
        self.assertEqual(summary_market_rows([],choose_c),[])


if __name__=='__main__':
    unittest.main()
