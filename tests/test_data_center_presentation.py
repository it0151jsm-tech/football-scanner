"""Authored display projections only; no providers, formulas or database writes."""
from copy import deepcopy
from html import escape
import json
import unittest

from data_center_presentation import analysis_summary_html, project_cards, summary_counts
from test_data_center_ui import synthetic_card


def matching_row(card=None, **changes):
    card = card or synthetic_card()
    quote = card['quotes'][0]
    quote['received_at'] = card['received_at']
    quote['freshness_status'] = 'recent_observed_snapshot'
    row = {key: card[key] for key in ('event_id', 'league', 'home_team', 'away_team', 'kickoff_utc')}
    row.update({key: quote.get(key) for key in ('market', 'side', 'point', 'odds', 'bookmaker',
                                              'bookmaker_key', 'received_at', 'source_updated_at', 'source_update_scope')})
    row.update(analysis_a_json=json.dumps({'score': 61.2, 'result': 'หลักฐานฟอร์มสนับสนุน',
                                          'status': 'คะแนนหลักฐานเท่านั้น'}),
               analysis_b_json=json.dumps({'available': True, 'selection': {'ev_pct': 4.2},
                                            'hda': {'home': 45, 'draw': 25, 'away': 30}, 'status': 'ยังไม่ผ่าน Calibration'}),
               confidence_score=61.2, score=61.2, selection='Authored Home -0.25',
               recommendation='Authored stored C result', analysis_reason='Authored stored reason',
               model_status='Authored stored model status', scanned_at='2026-10-25T12:01:00Z')
    row.update(changes)
    return row


class DataCenterPresentationTests(unittest.TestCase):
    def test_exact_current_quote_displays_only_saved_results(self):
        card=synthetic_card();row=matching_row(card)
        result=project_cards([card],[row])
        self.assertEqual(len(result[0]['display_analysis']),1)
        html=analysis_summary_html(result[0])
        for value in ('61.2/100','EV +4.20%','Authored stored C result','Authored stored reason',
                      'Authored stored model status','ไม่ใช่ความน่าจะเป็นชนะที่ผ่านการปรับเทียบ',
                      '45.00%','25.00%','30.00%','ไม่มีคะแนน C แยกต่างหาก'):
            self.assertIn(value,html)
        self.assertNotIn('61.2%',html)
        self.assertNotIn('dc-analysis" open',html)

    def test_identity_changes_never_attach_another_fixture_analysis(self):
        for key,value in (('event_id','other'),('league','other'),('home_team','other'),
                          ('away_team','other'),('kickoff_utc','2026-10-25T16:00:00Z')):
            with self.subTest(key=key):
                card=synthetic_card();row=matching_row(card,**{key:value})
                self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])

    def test_market_book_line_price_and_clock_changes_never_attach(self):
        changes=(('market','Over/Under'),('side','away'),('point',-.5),('odds',2.0),
                 ('bookmaker','another book'),('bookmaker_key','another-book'),
                 ('received_at','2026-10-25T11:50:00Z'),
                 ('source_updated_at','2026-10-25T11:58:00Z'),('source_update_scope','market'))
        for key,value in changes:
            with self.subTest(key=key):
                card=synthetic_card();row=matching_row(card,**{key:value})
                self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])

    def test_clock_equivalence_uses_confirmed_timezone_without_guessing(self):
        card=synthetic_card();row=matching_row(card)
        row.update(received_at='2026-10-25T19:00:00+07:00',
                   source_updated_at='2026-10-25T18:59:00+07:00',kickoff_utc='2026-10-25T22:00:00+07:00')
        self.assertEqual(len(project_cards([card],[row])[0]['display_analysis']),1)
        row['received_at']='2026-10-25T12:00:00'
        self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])

    def test_missing_quote_receipt_does_not_claim_card_receipt_proves_analysis(self):
        card=synthetic_card();row=matching_row(card)
        card['quotes'][0].pop('received_at')
        self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])

    def test_missing_or_invalid_source_identity_fails_closed(self):
        for field in ('bookmaker_key','bookmaker','received_at','source_updated_at','source_update_scope'):
            for value in (None,''):
                card=synthetic_card();row=matching_row(card,**{field:value})
                self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])

    def test_duplicate_identical_records_show_once_and_conflicts_show_none(self):
        card=synthetic_card();row=matching_row(card)
        self.assertEqual(len(project_cards([card],[row,deepcopy(row)])[0]['display_analysis']),1)
        conflict=dict(row,analysis_a_json=json.dumps({'score':99}))
        self.assertEqual(project_cards([card],[row,conflict])[0]['display_analysis'],[])
        broken=dict(row,analysis_a_json='broken',analysis_b_json='[]')
        self.assertEqual(project_cards([card],[row,broken])[0]['display_analysis'],[])

    def test_other_book_and_alternative_line_results_not_ranked_or_substituted(self):
        card=synthetic_card();row=matching_row(card)
        other=dict(row,bookmaker='Other Book',bookmaker_key='other',odds=8.0,
                   analysis_a_json=json.dumps({'score':99}))
        projected=project_cards([card],[other,row])[0]
        self.assertEqual(len(projected['display_analysis']),1)
        self.assertNotIn('99.0/100',analysis_summary_html(projected))
        self.assertNotIn('8.00',analysis_summary_html(projected))

    def test_form_evidence_survives_missing_ev_without_fake_b_or_c_score(self):
        card=synthetic_card();row=matching_row(card,analysis_b_json=json.dumps({'available':False,
            'selection':{'ev_pct':9},'status':'ไม่มี xG ที่เพียงพอ'}))
        html=analysis_summary_html(project_cards([card],[row])[0])
        self.assertIn('61.2/100',html)
        self.assertIn('ไม่มี xG ที่เพียงพอ',html)
        self.assertNotIn('EV +9',html)
        self.assertNotIn('คะแนน C 61',html)

    def test_nonfinite_and_non_numeric_results_not_displayed_as_scores(self):
        for value in (None,True,'99',float('nan'),float('inf'),-1,101):
            card=synthetic_card();row=matching_row(card)
            row['analysis_a_json']={'score':value}
            row['analysis_b_json']={'available':False}
            projected=project_cards([card],[row])[0]
            html=analysis_summary_html(projected)
            self.assertNotIn('/100',html)
            self.assertNotIn('nan',html)
            self.assertNotIn('inf',html)
        card=synthetic_card();row=matching_row(card)
        row['analysis_b_json']={'available':True,'selection':{'ev_pct':None}}
        html=analysis_summary_html(project_cards([card],[row])[0])
        self.assertIn('ยังไม่มีค่าจากโมเดลที่พร้อมใช้',html)
        self.assertNotIn('EV +',html)

    def test_stale_matching_analysis_is_explicitly_a_past_snapshot(self):
        card=synthetic_card();row=matching_row(card)
        card['quotes'][0]['freshness_status']='unverified_or_stale'
        html=analysis_summary_html(project_cards([card],[row])[0])
        self.assertIn('ราคาเก่าหรือเวลาไม่ยืนยัน ไม่ใช่คำแนะนำเดิมพันใหม่',html)

    def test_html_escaping_applies_to_saved_strings_and_nested_status(self):
        hostile='"><img src=x onerror="window.PWNED=1"><script>bad()</script>&'
        card=synthetic_card();row=matching_row(card,selection=hostile,recommendation=hostile,
            analysis_reason=hostile,model_status=hostile)
        row['analysis_a_json']={'score':61.2,'status':hostile,'result':hostile}
        row['analysis_b_json']={'available':False,'status':hostile}
        html=analysis_summary_html(project_cards([card],[row])[0])
        self.assertIn(escape(hostile,quote=True),html)
        self.assertNotIn('<img',html);self.assertNotIn('<script',html)

    def test_projection_does_not_mutate_cards_or_rows(self):
        card=synthetic_card();row=matching_row(card)
        before_card,before_row=deepcopy(card),deepcopy(row)
        result=project_cards([card],[row]);summary_counts(result);analysis_summary_html(result[0])
        self.assertEqual(card,before_card);self.assertEqual(row,before_row)
        result[0]['statistics']['teams']['home']['name']='Changed copy'
        self.assertEqual(card,before_card)

    def test_dataframe_rows_supported_without_dataframe_dependency(self):
        class Rows:
            def to_dict(self,orientation):
                self.orientation=orientation
                return [row]
        card=synthetic_card();row=matching_row(card);rows=Rows()
        self.assertEqual(len(project_cards([card],rows)[0]['display_analysis']),1)
        self.assertEqual(rows.orientation,'records')

    def test_malformed_inputs_and_empty_analysis_keep_raw_card(self):
        for rows in (None,{},'broken',[None,[]]):
            card=synthetic_card();result=project_cards([card],rows)
            self.assertEqual(result[0]['home_team'],'Authored Home')
            self.assertEqual(result[0]['display_analysis'],[])
            html=analysis_summary_html(result[0])
            self.assertIn('ยังไม่มีผลวิเคราะห์ที่ตรง Snapshot นี้',html)
            self.assertNotIn('Poisson',html);self.assertNotIn('EV ',html)
        self.assertEqual(project_cards(None),[])

    def test_summary_counts_fixtures_not_legs_or_duplicate_rows(self):
        card=synthetic_card();missing=deepcopy(card)
        missing.update(event_id='second',quotes=[],statistics=None)
        self.assertEqual(summary_counts([card,deepcopy(card),missing]),
                         {'fixtures':2,'priced':1,'statistics':1})

    def test_summary_does_not_count_status_only_or_zero_samples_as_statistics(self):
        for stats in (None,{}, {'status':'complete'}, {'teams':{'home':{'overall':{
            'last5':{'sample':0,'gf_avg':0,'ga_avg':0}}}}}):
            card=synthetic_card();card['statistics']=stats
            self.assertEqual(summary_counts([card])['statistics'],0)

    def test_summary_xg_only_and_verified_h2h_count_as_available_statistics(self):
        for stats in ({'teams':{'home':{'xg':{'venue':{'last5':{'sample':2,'xg_avg':1.2,'xga_avg':.8}}}}}},
                      {'h2h':{'sample':1,'matches':[{'date':'2026-10-01','home_team':'Home','away_team':'Away',
                                                   'home_goals':0,'away_goals':1}]}}):
            card=synthetic_card();card['statistics']=stats
            self.assertEqual(summary_counts([card])['statistics'],1)

    def test_summary_unknown_fixture_and_invalid_quotes_not_counted(self):
        card=synthetic_card()
        for field,value in (('odds',None),('odds',float('nan')),('odds',1),('point',1e308),
                            ('point',2.63),('side','wrong')):
            broken=deepcopy(card);broken['quotes']=[dict(card['quotes'][0],**{field:value})]
            self.assertEqual(summary_counts([broken])['priced'],0)
        card['event_id']=None
        self.assertEqual(summary_counts([card]),{'fixtures':0,'priced':0,'statistics':0})

    def test_huge_line_is_rejected_without_overflow(self):
        card=synthetic_card();row=matching_row(card,point=1e308)
        self.assertEqual(project_cards([card],[row])[0]['display_analysis'],[])


if __name__=='__main__':
    unittest.main()
