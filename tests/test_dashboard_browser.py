"""Real Chromium layout checks at iPhone widths; no app or network required."""
import importlib.util
import json
import os
import shutil
import unittest
from pathlib import Path

from mobile_dashboard import DASHBOARD_CSS, dashboard_html, details_html, fixture_dashboard_html, fixture_details_html
from test_dashboard import complete_row


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Install playwright for viewport tests')
class DashboardBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from playwright.sync_api import sync_playwright
        cls.runtime = sync_playwright().start()
        options = {'headless': True, 'args': ['--no-sandbox']}
        executable = shutil.which('chromium')
        if executable:
            options['executable_path'] = executable
        try:
            cls.browser = cls.runtime.chromium.launch(**options)
        except Exception as exc:
            cls.runtime.stop()
            if os.environ.get('CI'):
                raise
            raise unittest.SkipTest('Local sandbox cannot launch Chromium; CI runs viewport tests') from exc

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def page_content(self, row, dark=False):
        colors = '--background-color:#0e1117;--text-color:#fafafa' if dark else '--background-color:#ffffff;--text-color:#182332'
        return ('<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1">'
                + DASHBOARD_CSS + '<style>body{margin:0;padding:16px;font-family:Arial,sans-serif;' + colors +
                ';background:var(--background-color)}</style></head><body>' + dashboard_html(row, 'ยังประเมินความคุ้มค่าราคาไม่ได้') +
                '<details><summary>ดูรายละเอียดเพิ่มเติม</summary>' + details_html(row) + '</details></body></html>')

    def test_complete_dashboard_fits_iphone_widths_in_light_and_dark(self):
        for width in [320, 375, 390, 430]:
            for dark in [False, True]:
                with self.subTest(width=width, dark=dark):
                    page = self.browser.new_page(viewport={'width': width, 'height': 844}, color_scheme='dark' if dark else 'light')
                    try:
                        page.set_content(self.page_content(complete_row(), dark))
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        tiles = page.locator('.vbp-score-row + .vbp-grid2 .vbp-tile')
                        self.assertEqual(tiles.count(), 2)
                        self.assertEqual(tiles.nth(0).bounding_box()['y'], tiles.nth(1).bounding_box()['y'])
                        self.assertFalse(page.locator('details').evaluate('(e)=>e.open'))
                        if width == 375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                            folder = Path(os.environ['DASHBOARD_SCREENSHOTS'])
                            folder.mkdir(parents=True, exist_ok=True)
                            page.screenshot(path=str(folder / ('iphone-dark.png' if dark else 'iphone-light.png')), full_page=True)
                        page.locator('summary').click()
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    finally:
                        page.close()

    def test_missing_model_and_long_escaped_api_text_do_not_overflow_or_execute(self):
        row = complete_row()
        row['match'] = '<img src=x onerror="window.PWNED=1">' + 'LONGTEAMNAME' * 35
        row['selection'] = '\"><script>window.PWNED=1</script>'
        row['analysis_b_json'] = json.dumps({'available': False})
        page = self.browser.new_page(viewport={'width': 320, 'height': 844})
        try:
            page.set_content(self.page_content(row))
            self.assertEqual(page.locator('[data-model="unavailable"]').count(), 1)
            self.assertEqual(page.locator('img,script').count(), 0)
            self.assertIsNone(page.evaluate('window.PWNED'))
            self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
        finally:
            page.close()

    def test_grouped_markets_fit_mobile_without_horizontal_scroll(self):
        import test_regressions as regression
        from market_recommendations import group_fixture_recommendations, RANKING
        from test_market_recommendations import quote
        f = regression.load_functions(Path(__file__).resolve().parents[1] / 'app.py')
        fixture = group_fixture_recommendations([quote('Asian Handicap', -.25, 'home', 'AH Book'), quote(point=2.75, pick=2),quote(point=2.75,side='under',odds=1.85,pick=3)], f['choose_recommendation'])[0]
        for width, dark in [(w,d) for w in (320,375,390,430) for d in (False,True)]:
            page = self.browser.new_page(viewport={'width': width, 'height': 844}, color_scheme='dark' if dark else 'light')
            try:
                # Replace only the fixture markup; retain the tested theme and viewport wrapper.
                page.set_content(self.page_content(complete_row(), dark))
                page.locator('body').evaluate('(body, markup)=>body.innerHTML=markup', fixture_dashboard_html(fixture) + '<details><summary>ดูรายละเอียดเพิ่มเติม</summary>' + fixture_details_html(fixture, RANKING) + '</details>')
                self.assertEqual(page.locator('article.vbp-dashboard').count(), 1)
                self.assertEqual(page.locator('[data-market]').count(), 2)
                self.assertEqual(page.locator('.vbp-tile.vbp-ou').count(), 2)
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                if os.environ.get('DASHBOARD_SCREENSHOTS'):
                    folder = Path(os.environ['DASHBOARD_SCREENSHOTS'])
                    folder.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(folder / ('grouped-ah-ou-dark.png' if dark else 'grouped-ah-ou-light.png')), full_page=True)
                page.locator('summary').click()
                self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
            finally:
                page.close()

    def test_ou_and_ucl_form_diagnostics_fit_open_details_at_iphone_widths(self):
        from market_recommendations import group_fixture_recommendations, RANKING
        from test_ucl_scan import UCLScanTests
        case=UCLScanTests();case.setUp();self.addCleanup(case.doCleanups)
        case.prepare();rows,_,_,_=case.scan()
        # Long source strings and genuine synthetic row evidence must wrap safely.
        for row in rows:
            a=json.loads(row['analysis_a_json'])
            audit=a['form_audit']
            for side in ('home','away'):
                for window in audit[side]['windows'].values():
                    for match in window['rows']:
                        match.update(payload_sha256='a'*64,source_url='https://example.invalid/'+('source'*50)+'.csv')
            row['analysis_a_json']=json.dumps(a)
        fixture=group_fixture_recommendations(rows,case.f['choose_recommendation'])[0]
        for width in (320,375,390,430):
            for dark in (False,True):
                with self.subTest(width=width,dark=dark):
                    page=self.browser.new_page(viewport={'width':width,'height':844},color_scheme='dark' if dark else 'light')
                    try:
                        page.set_content(self.page_content(complete_row(),dark))
                        page.locator('body').evaluate('(body,markup)=>body.innerHTML=markup',fixture_dashboard_html(fixture)+'<details open><summary>ดูรายละเอียดเพิ่มเติม</summary>'+fixture_details_html(fixture,RANKING)+'</details>')
                        self.assertEqual(page.locator('[data-ou-diagnostic="pair"]').count(),1)
                        self.assertGreater(page.locator('[data-form-audit="saved"]').count(),0)
                        self.assertEqual(page.locator('img,script').count(),0)
                        self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                        if width==375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                            folder=Path(os.environ['DASHBOARD_SCREENSHOTS']);folder.mkdir(parents=True,exist_ok=True)
                            page.screenshot(path=str(folder/('ou-form-diagnostics-dark.png' if dark else 'ou-form-diagnostics-light.png')),full_page=True)
                    finally:
                        page.close()
