"""Synthetic display checks: no football provider, model or production database."""
from contextlib import nullcontext
from copy import deepcopy
from html import escape
from html.parser import HTMLParser
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from data_center_ui import (DATA_CENTER_CSS, analysis_panel, data_center_card_html,
                            mode_selector, render_center, run_price_scan, show_scan_issue)


def synthetic_card():
    window={'sample':5,'wins':3,'draws':1,'losses':1,'form':['W','D','L','W','W'],
        'gf_avg':1.8,'ga_avg':.8,'matches':[{'date':'2026-10-01','opponent':'Authored Opponent',
            'goals_for':2,'goals_against':1,'season':'2627','source_url':'https://example.invalid/results',
            'source_received_at':'2026-10-02T12:00:00Z'}]}
    team={'name':'Authored Home','matched_name':'Authored Home','mapping':'exact','status':'success',
        'overall':{'last5':window,'last10':dict(window,sample=10)},
        'venue':{'venue':'home','last5':window,'last10':dict(window,sample=10)},
        'xg':{'status':'available','venue':{'last5':{'sample':5,'xg_avg':1.7,'xga_avg':1.1},
            'last10':{'sample':10,'xg_avg':1.6,'xga_avg':1.2}}}}
    return {'run_id':'authored-run','event_id':'authored-event','sport_key':'soccer_epl',
        'home_team':'Authored Home','away_team':'Authored Away','league':'Authored League',
        'kickoff_utc':'2026-10-25T15:00:00Z','kickoff_th':'25/10/2026 22:00',
        'received_at':'2026-10-25T12:00:00Z','data_status':'available','provider':'The Odds API',
        'quotes':[{'market':'Asian Handicap','side':'home','point':-.25,'odds':1.95,
            'bookmaker':'Authored Book','bookmaker_key':'authored-book',
            'source_updated_at':'2026-10-25T11:59:00Z','source_update_scope':'bookmaker',
            'first_observed_status':'actual','first_observed':{'point':-.5,'odds':1.85,
                'received_at':'2026-10-25T10:00:00Z','line_changed':True}},
            {'market':'Over/Under','side':'over','point':2.75,'odds':2.01,
             'bookmaker':'Authored Book','bookmaker_key':'authored-book',
             'source_updated_at':'2026-10-25T11:58:00Z','source_update_scope':'market'}],
        'statistics':{'status':'partial','as_of':'2026-10-25T12:01:00Z',
            'teams':{'home':team,'away':dict(team,name='Authored Away',matched_name='Authored Away')},
            'h2h':{'status':'success','sample':1,'matches':[{'date':'2025-10-01',
                'home_team':'Authored Home','away_team':'Authored Away','home_goals':2,'away_goals':0}]},
            'standings':{'status':'unsupported','home':None,'away':None},
            'sources':{'football_data':{'provider':'Football-Data','status':'success',
                'reason':'author_fixture','received_at':['2026-10-25T11:00:00Z'],
                'cache_status':'cache_hit','sources':[{'url':'https://example.invalid/results',
                    'season':'2627','received_at':'2026-10-25T11:00:00Z'}]}},
            'limitations':['Authored fixture, not real coverage']}}


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags=[]
    def handle_starttag(self,tag,attrs):
        self.tags.append((tag,dict(attrs)))


class FakeStreamlit:
    def __init__(self,clicked=None):
        self.clicked=clicked
        self.events=[]
        self.session_state={}
        self.rerun=Mock()
    def __getattr__(self,name):
        def record(*args,**kwargs):
            self.events.append((name,args,kwargs))
            if name=='expander':return nullcontext()
            if name=='empty':return SimpleNamespace(container=lambda:nullcontext(),empty=Mock())
            if name=='progress':return SimpleNamespace(progress=Mock(),empty=Mock())
            if name=='toggle':return kwargs['value']
            if name=='button':return args[0]==self.clicked
        return record


class DataCenterDisplayTests(unittest.TestCase):
    def test_complete_data_uses_actual_quotes_and_descriptive_stats_without_model_outputs(self):
        html=data_center_card_html(synthetic_card())
        for value in ('Authored Home','Authored Away','25/10/2026 22:00','AH','OU','-0.25','2.75',
                      '1.95','2.01','1.80 / 0.80','1.70','1.10','H2H','Football-Data','The Odds API'):
            self.assertIn(value,html)
        self.assertNotIn('Poisson',html)
        self.assertNotIn('EV ',html)

    def test_no_statistics_does_not_hide_program_or_price(self):
        card=synthetic_card();card['statistics']=None
        html=data_center_card_html(card)
        self.assertIn('data-dc-statistics="not_loaded"',html)
        self.assertIn('Authored Home',html)
        self.assertIn('1.95',html)
        self.assertIn('ฟอร์ม 5/10',html)
        self.assertNotIn('1.70',html)

    def test_no_markets_preserves_fixture_with_explicit_two_market_states(self):
        card=synthetic_card();card.update(quotes=[],data_status='no_prices',statistics=None)
        html=data_center_card_html(card)
        self.assertEqual(html.count('ไม่มีราคาตลาด'),3)  # Header plus AH and OU states.
        self.assertIn('Authored Away',html)
        self.assertNotIn('ไม่มีโปรแกรม',html)

    def test_only_one_market_does_not_fabricate_other_market_line(self):
        for market in ('Asian Handicap','Over/Under'):
            card=synthetic_card();card['quotes']=[q for q in card['quotes'] if q['market']==market]
            html=data_center_card_html(card)
            self.assertIn('ไม่มีราคาตลาด',html)
            self.assertEqual(html.count('class="dc-book"'),1)

    def test_multiple_bookmakers_and_actual_quarter_lines_stay_separate(self):
        card=synthetic_card()
        card['quotes'].append(dict(card['quotes'][1],bookmaker='Other Authored Book',bookmaker_key='other',
            side='under',point=3.25,odds=1.79))
        html=data_center_card_html(card)
        self.assertEqual(html.count('class="dc-book"'),3)
        self.assertIn('Other Authored Book',html)
        self.assertIn('3.25',html)
        self.assertIn('1.79',html)

    def test_first_observed_is_scoped_and_never_called_opening(self):
        html=data_center_card_html(synthetic_card())
        self.assertIn('First Observed Price (Data Center)',html)
        self.assertIn('1.85',html)
        self.assertIn('เส้นเปลี่ยน',html)
        self.assertIn('ราคาเปิดจริง: ไม่มีข้อมูลที่ยืนยันแล้ว',html)
        self.assertNotIn('Opening Odds',html)

    def test_unverified_opening_flag_cannot_add_an_opening_price(self):
        card=synthetic_card()
        card['quotes'][0].update(opening_verified=True,opening_odds=9.99)
        self.assertNotIn('9.99',data_center_card_html(card))

    def test_ambiguous_first_observation_is_not_displayed_as_confirmed(self):
        card=synthetic_card();card['quotes'][0]['first_observed_status']='ambiguous'
        html=data_center_card_html(card)
        self.assertIn('First Observed Price (Data Center): ไม่มีข้อมูลที่ยืนยันได้',html)
        self.assertNotIn('1.85',html)

    def test_receipt_and_provider_clock_scope_remain_distinct(self):
        html=data_center_card_html(synthetic_card())
        self.assertIn('2026-10-25T12:00:00Z',html)
        self.assertIn('2026-10-25T11:59:00Z',html)
        self.assertIn('เวลาอัปเดตข้อมูลระดับเจ้ามือ',html)
        self.assertIn('เวลาอัปเดตตลาด',html)

    def test_api_football_plan_backoff_is_not_hidden_as_plain_missing_data(self):
        card=synthetic_card()
        card['statistics']['sources']['api_football']={'provider':'API-Football','status':'not_called',
            'provider_state':'plan_unsupported','reason':'plan_backoff','http_status':403,
            'next_retry_at':'2026-10-25T13:00:00Z','cache_status':'backoff'}
        html=data_center_card_html(card)
        for value in ('API-Football','ไม่ได้เรียก','แพ็กเกจไม่รองรับ','พักการเรียกเพราะแพ็กเกจไม่รองรับ','HTTP 403','2026-10-25T13:00:00Z'):
            self.assertIn(value,html)

    def test_zero_samples_keep_specific_thai_name_source_reasons_and_fact_scope(self):
        card=synthetic_card();card['league']='Authored Cup'
        home=card['statistics']['teams']['home']
        empty={'sample':0,'form':[],'gf_avg':None,'ga_avg':None,'matches':[]}
        home.update(status='name_unmatched',matched_name=None,mapping='name_unmatched',
            diagnostics={'overall_status':'name_unmatched','venue_status':'empty_history'},
            overall={'last5':empty,'last10':empty},venue={'last5':empty,'last10':empty})
        card['statistics']['teams']['away']['scope']='domestic_context'
        sources=card['statistics']['sources']
        sources['football_data'].update(status='error',reason='results_source_error')
        sources['understat']={'provider':'Understat','status':'unsupported','reason':'xg_source_not_supported'}
        sources['api_football']={'provider':'API-Football','status':'not_called','reason':'not_configured'}
        html=data_center_card_html(card)
        for value in ('จับคู่ชื่อทีมไม่ได้','ไม่มีผลย้อนหลังที่ใช้ได้','โหลดแหล่งผลย้อนหลังไม่สำเร็จ',
                      'ลีกนี้ไม่มีแหล่ง xG ที่รองรับ','ยังไม่ได้ตั้งค่าผู้ให้บริการนี้',
                      'ฟอร์มรวมเฉพาะลีก/ข้อมูลต้นทาง','ไม่ใช่ฟอร์มรวมทุกถ้วย',
                      'วันเกมล่าสุดในชุดฟอร์ม: 2026-10-01'):
            self.assertIn(value,html)
        for value in ('results_source_error','xg_source_not_supported','name_unmatched'):
            self.assertNotIn(value,html)
        self.assertIn('Authored Home',html)
        self.assertIn('1.95',html)

    def test_http_success_with_all_rows_filtered_shows_quality_counts_and_no_ready_team_claim(self):
        card=synthetic_card()
        for team in card['statistics']['teams'].values():
            empty={'sample':0,'form':[],'gf_avg':None,'ga_avg':None,'matches':[]}
            team.update(status='ok',diagnostics={'overall_status':'ok','venue_status':'ok'},
                overall={'last5':empty,'last10':empty},venue={'last5':empty,'last10':empty})
        card['statistics']['sources']['understat']={'provider':'Understat','status':'success','http_status':200,
            'quality':{'input_rows':10,'accepted_rows':0,'status':'no_data',
                'late_rows':4,'conflicting_rows':1,'invalid_xg_rows':2,'late_or_invalid_receipt_rows':1,
                'duplicate_rows':0,'validation':{'invalid_dates':2}}}
        html=data_center_card_html(card)
        for value in ('HTTP 200','ผ่านการตรวจ 0 / 10 แถว','ต้นทางมีข้อมูล แต่ไม่มีแถวผ่านการกรอง',
                      'ไม่อยู่ก่อนเวลาคัดข้อมูล 4 แถว','ข้อมูลขัดแย้ง 1 แถว','ค่า xG ไม่ถูกต้อง 2 แถว',
                      'รับข้อมูลภายหลังหรือเวลารับไม่ถูกต้อง 1 แถว','วันที่ไม่ถูกต้อง 2 แถว',
                      'ไม่มีผลย้อนหลังที่ผ่านการกรองสำหรับรอบนี้'):
            self.assertIn(value,html)
        self.assertNotIn('แถวซ้ำ 0 แถว',html)
        self.assertNotIn('สถานะฟอร์ม: มีผลย้อนหลังตามเกณฑ์',html)
        self.assertIn('1.95',html)

    def test_missing_unknown_and_nonfinite_data_never_becomes_zero_stats(self):
        card=synthetic_card();card['statistics']={'status':'no_data','teams':{},'h2h':{},'sources':{}}
        html=data_center_card_html(card)
        self.assertIn('ไม่มีข้อมูล',html)
        self.assertNotIn('0.00',html)
        self.assertNotIn('1.70',html)
        card['quotes'][0].update(point=float('nan'),odds=float('inf'))
        html=data_center_card_html(card)
        self.assertNotIn('nan',html)
        self.assertNotIn('inf',html)

    def test_stale_quote_stays_visible_but_is_explicitly_not_current(self):
        card=synthetic_card();card['quotes'][0].update(freshness_status='unverified_or_stale',freshness_reason='author_fixture_stale')
        html=data_center_card_html(card)
        self.assertIn('1.95',html)
        self.assertIn('ราคาเก่าหรือเวลาไม่ยืนยัน',html)
        self.assertIn('author_fixture_stale',html)

    def test_standings_need_actual_supported_source(self):
        card=synthetic_card();card['statistics']['standings']={'status':'success','home':1,'away':4}
        self.assertIn('ไม่มีข้อมูลอันดับตาราง',data_center_card_html(card))
        card['statistics']['standings']['source']='https://example.invalid/table'
        self.assertIn('เจ้าบ้าน 1 · ทีมเยือน 4',data_center_card_html(card))

    def test_malformed_optional_statistics_render_as_missing(self):
        for value in (None,[],{},'broken'):
            card=synthetic_card();card['statistics']=value
            self.assertIn('Authored Home',data_center_card_html(card))
        card=synthetic_card();card['statistics'].update(status=[],teams=['broken'],sources=['broken'],h2h=None)
        self.assertIn('ไม่มีข้อมูล',data_center_card_html(card))

    def test_every_api_string_is_html_escaped_even_in_nested_sources(self):
        payload='\"><img src=x onerror="window.PWNED=1"><script>window.PWNED=1</script>&\''
        card=synthetic_card()
        for key in ('event_id','home_team','away_team','league','kickoff_th','received_at','provider'):
            card[key]=payload
        for quote in card['quotes']:
            quote.update(bookmaker=payload,received_at=payload,source_updated_at=payload)
        stats=card['statistics'];stats['limitations']=[payload]
        stats['sources']['football_data'].update(provider=payload,reason=payload,received_at=[payload])
        stats['sources']['football_data']['sources'][0]['url']=payload
        stats['teams']['home']['matched_name']=payload
        stats['teams']['home']['overall']['last10']['matches'][0].update(opponent=payload,source_url=payload)
        html=data_center_card_html(card);parser=Tags();parser.feed(html)
        self.assertIn(escape(payload,quote=True),html)
        self.assertNotIn('<script>',html)
        self.assertNotIn('<img ',html)
        self.assertFalse(any(key.startswith('on') for _,attrs in parser.tags for key in attrs))
        self.assertFalse(any(tag=='a' and attrs.get('href','').startswith('javascript:') for tag,attrs in parser.tags))

    def test_markup_has_accessible_tables_and_stable_fixture_marker(self):
        parser=Tags();parser.feed(data_center_card_html(synthetic_card()))
        self.assertEqual(sum(tag=='article' and attrs.get('data-center-fixture')=='authored-event' for tag,attrs in parser.tags),1)
        self.assertTrue(all(attrs.get('scope')=='col' for tag,attrs in parser.tags if tag=='th'))
        self.assertTrue(all(attrs.get('aria-label') for tag,attrs in parser.tags if tag=='table'))

    def test_scoped_css_supports_theme_small_screens_and_long_provider_text(self):
        for value in ('var(--text-color','prefers-color-scheme:dark','overflow-wrap:anywhere',
                      'table-layout:fixed','max-width:500px','minmax(0,1fr)'):
            self.assertIn(value,DATA_CENTER_CSS)
        self.assertNotIn('body{',DATA_CENTER_CSS)

    def test_rendering_does_not_mutate_evidence(self):
        card=synthetic_card();before=deepcopy(card)
        data_center_card_html(card)
        self.assertEqual(card,before)


class DataCenterStreamlitRendererTests(unittest.TestCase):
    def run_renderer(self,st,cards=None,**callbacks):
        with patch.dict(sys.modules,{'streamlit':st}),patch('requests.get',side_effect=AssertionError('view cannot fetch')) as get:
            render_center(cards if cards is not None else [synthetic_card()],None,None,**callbacks)
        get.assert_not_called()

    def test_plain_view_never_invokes_callbacks_or_reruns(self):
        st=FakeStreamlit();stats=Mock();analysis=Mock()
        self.run_renderer(st,on_load_statistics=stats,on_analyze=analysis)
        stats.assert_not_called();analysis.assert_not_called();st.rerun.assert_not_called()

    def test_stats_click_occurs_after_fixture_markup_and_reruns_once(self):
        st=FakeStreamlit('โหลดสถิติที่มีสำหรับคู่ที่แสดง (ไม่เรียก Odds API)')
        def load(cards):
            self.assertTrue(any(name=='markdown' and 'data-center-fixture' in args[0] for name,args,_ in st.events))
            self.assertEqual(len(cards),1)
            return {'status':'partial'}
        self.run_renderer(st,on_load_statistics=load)
        st.rerun.assert_called_once()
        self.assertEqual(st.session_state['dc_last_action_summary'],{'action':'statistics','status':'partial'})

    def test_analysis_is_explicit_and_opens_retained_legacy_panel(self):
        st=FakeStreamlit('วิเคราะห์ A/B/C จากข้อมูลที่บันทึกไว้');callback=Mock(return_value={'status':'completed'})
        self.run_renderer(st,on_analyze=callback)
        callback.assert_called_once();st.rerun.assert_called_once()
        self.assertTrue(st.session_state['dc_analysis_requested'])

    def test_callback_error_keeps_fixture_and_never_exposes_exception_body(self):
        st=FakeStreamlit('โหลดสถิติที่มีสำหรับคู่ที่แสดง (ไม่เรียก Odds API)')
        self.run_renderer(st,on_load_statistics=Mock(side_effect=RuntimeError('apiKey=DO_NOT_ECHO')))
        self.assertTrue(any(name=='markdown' and 'Authored Home' in args[0] for name,args,_ in st.events))
        self.assertTrue(any(name=='warning' for name,_,_ in st.events))
        self.assertNotIn('DO_NOT_ECHO',str(st.events))
        st.rerun.assert_not_called()

    def test_empty_scoped_data_has_status_not_claim_of_no_programme(self):
        st=FakeStreamlit();callback=Mock()
        self.run_renderer(st,[],on_load_statistics=callback,on_analyze=callback)
        callback.assert_not_called()
        info=' '.join(str(args) for name,args,_ in st.events if name=='info')
        self.assertIn('API error',info)
        self.assertNotIn('ไม่มีโปรแกรมแข่งขัน',info)

    def test_toggle_and_analysis_context_preserve_legacy_widget_indices(self):
        st=FakeStreamlit()
        with patch.dict(sys.modules,{'streamlit':st}):
            self.assertTrue(mode_selector())
            with analysis_panel(True):pass
        self.assertFalse(any(name in ('radio','selectbox','multiselect') for name,_,_ in st.events))
        expander=next(kwargs for name,_,kwargs in st.events if name=='expander')
        self.assertFalse(expander['expanded'])

    def test_price_scan_renders_each_committed_batch_before_next_league(self):
        st=FakeStreamlit();cards=[]
        def scan(progress):
            cards.append(synthetic_card())
            progress(1,2,{'status':'success'})
            self.assertTrue(any(name=='markdown' and 'data-center-fixture' in args[0] for name,args,_ in st.events))
            cards.append(dict(synthetic_card(),event_id='second-authored'))
            progress(2,2,{'status':'success'})
            return {'status':'completed'}
        with patch.dict(sys.modules,{'streamlit':st}),patch('requests.get',side_effect=AssertionError('renderer cannot request')) as get:
            result=run_price_scan(scan,lambda:cards,lambda:None,lambda:None)
        get.assert_not_called()
        self.assertEqual(result,{'status':'completed'})
        self.assertEqual(sum(name=='markdown' and 'data-center-fixture' in args[0] for name,args,_ in st.events),3)
        self.assertFalse(any(name=='button' and args[0].startswith('โหลดสถิติ') for name,args,_ in st.events))

    def test_price_scan_exception_has_static_message_and_keeps_existing_storage(self):
        st=FakeStreamlit();load=Mock(return_value=[synthetic_card()])
        with patch.dict(sys.modules,{'streamlit':st}):
            result=run_price_scan(Mock(side_effect=RuntimeError('apiKey=DO_NOT_ECHO')),load,lambda:None,lambda:None)
        self.assertEqual(result['status'],'error')
        self.assertNotIn('DO_NOT_ECHO',str(st.events))
        self.assertTrue(any(name=='warning' for name,_,_ in st.events))

    def test_scan_validation_messages_are_static_and_do_not_invoke_any_callback(self):
        st=FakeStreamlit()
        with patch.dict(sys.modules,{'streamlit':st}):
            show_scan_issue('missing_key');show_scan_issue('no_leagues');show_scan_issue('apiKey=DO_NOT_ECHO')
        self.assertNotIn('DO_NOT_ECHO',str(st.events))
        self.assertEqual(sum(name=='error' for name,_,_ in st.events),1)
        self.assertEqual(sum(name=='warning' for name,_,_ in st.events),2)


if __name__=='__main__':
    unittest.main()
