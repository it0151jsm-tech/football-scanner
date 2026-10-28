"""Presentation regression tests; fixture probabilities use the real Poisson code."""
import json
import unittest
from html import escape
from html.parser import HTMLParser
from pathlib import Path

import test_regressions as regression
from mobile_dashboard import DASHBOARD_CSS, dashboard_html, details_html


def complete_row():
    f = regression.load_functions(Path(__file__).resolve().parents[1] / 'app.py')
    profile = dict(last5_gf=1.8, last5_ga=.8, sample5=5, last10_gf=1.6, last10_ga=1.0,
                   sample10=10, season_gf=1.5, season_ga=1.1, season_n=30, summary='ฟอร์มย้อนหลังทั้งหมด')
    a = f['system_a_analysis'](dict(form_rate=70, form_sample=5), dict(form_rate=60, form_sample=10),
                               dict(form_rate=60, form_sample=30), profile, profile,
                               {'home': {'mean_ppg': 1.5, 'sample': 8, 'status': 'คุณภาพคู่แข่งจริง'}},
                               {'home': {'xg': 1.7, 'xga': 1.1, 'sample': 10}},
                               f['price_evidence'](dict(point=2.25, odds=1.85, captured_at='FIRST-TIMESTAMP'),
                                                   2.5, 1.95, 'CURRENT-TIMESTAMP'))
    matrix = f['score_matrix'](1.7, 1.1)
    evaluation = f['evaluate_market_poisson'](matrix, 'totals', 'over', 2.5, 1.95)
    b = f['system_b_analysis'](dict(home_lambda=1.7, away_lambda=1.1), matrix, evaluation, f['split_line'](2.5), 2.5)
    return dict(match='เจ้าบ้าน เอฟซี vs ทีมเยือน ยูไนเต็ด', home_team='เจ้าบ้าน เอฟซี', away_team='ทีมเยือน ยูไนเต็ด',
                league='พรีเมียร์ลีก อังกฤษ', kickoff_th='20/10/2026 23:30', market='Over/Under',
                selection='สูง (Over 2.5)', point=2.5, odds=1.95, bookmaker='Test bookmaker',
                analysis_a_json=json.dumps(a), analysis_b_json=json.dumps(b), model_status='Poisson จาก xG จริง')


class Elements(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class DashboardTests(unittest.TestCase):
    def test_complete_model_has_goal_hero_probability_bar_and_market_ou_tiles(self):
        row = complete_row()
        html = dashboard_html(row, 'พิจารณาอย่างระวัง')
        parser = Elements()
        parser.feed(html)
        self.assertEqual(sum('vbp-goal' == attrs.get('class') for _, attrs in parser.tags), 2)
        self.assertEqual(sum('vbp-tile vbp-ou' == attrs.get('class') for _, attrs in parser.tags), 2)
        self.assertTrue(any(attrs.get('role') == 'img' and 'เจ้าบ้านชนะ' in attrs.get('aria-label', '') for _, attrs in parser.tags))
        self.assertIn('เส้นตลาดจริง', html)
        b = json.loads(row['analysis_b_json'])
        self.assertIn(f"{b['hda']['home']:.2f}%", html)
        self.assertIn(f"{b['ou']['over_full']:.2f}%", html)
        self.assertIn('ยิงเฉลี่ย', html)
        self.assertIn('1.80', html)

    def test_absent_or_partial_model_is_a_status_box_without_fake_goals(self):
        for b in [{}, {'available': False}, {'available': True},
                  {'available': True, 'expected_goals': [float('nan'), 1.2], 'hda': {}}]:
            with self.subTest(b=b):
                row = complete_row()
                row['analysis_b_json'] = json.dumps(b)
                html = dashboard_html(row, 'ยังประเมินความคุ้มค่าราคาไม่ได้')
                self.assertIn('ข้อมูลโมเดลไม่เพียงพอ', html)
                self.assertNotIn('class="vbp-goal"', html)
                self.assertNotIn('class="vbp-bar"', html)
                self.assertNotIn('class="vbp-tile vbp-ou"', html)
                self.assertIn('คะแนนหลักฐาน', html)

    def test_all_external_text_is_escaped_in_main_and_detail_html(self):
        payload = '\"><img src=x onerror="window.PWNED=1"><script>window.PWNED=1</script>&\''
        row = complete_row()
        for key in ['match', 'home_team', 'away_team', 'league', 'kickoff_th', 'market', 'selection', 'bookmaker', 'model_status', 'scanned_at', 'kickoff_utc', 'h2h_summary']:
            row[key] = payload
        a = json.loads(row['analysis_a_json'])
        for key in ['result', 'method', 'status']:
            a[key] = payload
        for side in ['home_form', 'away_form']:
            a[side]['summary'] = payload
        a['price']['first_at'] = payload
        a['price']['current_at'] = payload
        a['opponents']['home']['status'] = payload
        row['analysis_a_json'] = json.dumps(a)
        b = json.loads(row['analysis_b_json'])
        b['status'] = payload
        b['ou_line'] = payload
        row['analysis_b_json'] = json.dumps(b)
        for html in [dashboard_html(row, payload), details_html(row)]:
            self.assertIn(escape(payload, quote=True), html)
            self.assertNotIn('<script>', html)
            self.assertNotIn('<img ', html)
            parser = Elements()
            parser.feed(html)
            self.assertFalse(any(key.startswith('on') for _, attrs in parser.tags for key in attrs))

    def test_numeric_string_payload_never_enters_bar_css(self):
        row = complete_row()
        b = json.loads(row['analysis_b_json'])
        b['hda']['home'] = '50%;background:url(https://example.com)'
        row['analysis_b_json'] = json.dumps(b)
        html = dashboard_html(row, 'ข้อมูลไม่พอ')
        self.assertNotIn('url(', html)
        self.assertNotIn('style="width:', html)
        self.assertIn('ข้อมูลโมเดลไม่เพียงพอ', html)

    def test_details_and_timestamps_are_outside_primary_dashboard(self):
        row = complete_row()
        main, detail = dashboard_html(row, 'พิจารณาอย่างระวัง'), details_html(row)
        for value in ['FIRST-TIMESTAMP', 'CURRENT-TIMESTAMP', 'PPG', 'ฟอร์มย้อนหลังทั้งหมด', '10 นัด:']:
            self.assertNotIn(value, main)
            self.assertIn(value, detail)
        self.assertIn('เส้นเปลี่ยน 2.25 → 2.50', main)
        self.assertIn('ไม่เทียบค่าน้ำตรง ๆ', main)
        self.assertIn('First Observed Price', main)

    def test_reference_ou_is_not_mislabeled_as_market_line(self):
        row = complete_row()
        row.update(market='Asian Handicap', point=-.25)
        html = dashboard_html(row, 'พิจารณาอย่างระวัง')
        self.assertIn('เส้นอ้างอิงโมเดล', html)
        self.assertNotIn('เส้นตลาดจริง', html)

    def test_legacy_card_and_malformed_json_remain_renderable(self):
        for raw in [None, '', '{broken', 'null', '[]']:
            row = dict(match='คู่เดิม', analysis_a_json=raw, analysis_b_json=raw)
            self.assertIn('ข้อมูลโมเดลไม่เพียงพอ', dashboard_html(row, 'ข้อมูลเดิม'))
            self.assertIn('ไม่มีข้อมูล', details_html(row))

    def test_scoped_css_uses_theme_and_flexible_mobile_grids(self):
        self.assertIn('var(--text-color', DASHBOARD_CSS)
        self.assertIn('prefers-color-scheme:dark', DASHBOARD_CSS)
        self.assertIn('repeat(2,minmax(0,1fr))', DASHBOARD_CSS)
        self.assertIn('overflow-wrap:anywhere', DASHBOARD_CSS)
