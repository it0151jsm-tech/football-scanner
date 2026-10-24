"""Saved readiness diagnostics and original UI, with disposable SQLite and mocks."""
import copy
import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest
from unittest.mock import Mock, patch

from data_health import decode_health_audits, scan_readiness_details
import test_issue9_scan as synthetic

ROOT = Path(__file__).resolve().parents[1]


def case_for_test(test):
    case = synthetic.Issue9ScanTests('test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form')
    case.setUp()
    test.addCleanup(case.doCleanups)
    case.prepare(english=True, blocked=True)
    return case


class Issue15ScanIntegrationTests(unittest.TestCase):
    def test_two_fixture_quotes_store_own_manifest_and_full_audit_once(self):
        case = case_for_test(self)
        second = copy.deepcopy(case.event)
        second.update(id='second-synthetic', commence_time=(case.kickoff+timedelta(hours=2)).isoformat())
        case.f['fetch_odds'] = Mock(return_value=([case.event, second], '100', '2'))
        before = case.seed_legacy_bills()
        rows, snapshots, _, _ = case.scan()
        case.f['save_picks'](rows, snapshots)
        with case.f['db']() as con:
            report = json.loads(con.execute('SELECT payload_json FROM league_health ORDER BY id DESC LIMIT 1').fetchone()[0])
        self.assertEqual(len(report['scan_window']['per_fixture_readiness']), 2)
        self.assertEqual(report['scan_window']['A_ready'], 2)
        self.assertEqual(report['scan_window']['B_ready'], 0)
        for row in rows:
            manifest = json.loads(row['analysis_a_json'])['scan_diagnostics']
            self.assertEqual(manifest['api_returned'], 2)
            self.assertEqual([item['event_id'] for item in manifest['per_fixture_readiness']], [row['event_id']])
            self.assertIn('matching_fixture_only', manifest['detail_scope'])
        self.assertEqual(case.legacy_snapshot(), before)

    def test_dashboard_labels_old_evidence_and_does_not_rescan(self):
        case = case_for_test(self)
        rows, _, _, _ = case.scan()
        with case.f['db']() as con:
            audit = json.loads(con.execute('SELECT payload_json FROM league_health ORDER BY id DESC LIMIT 1').fetchone()[0])
        with patch('requests.get', side_effect=AssertionError('read-only diagnostics must not request')) as get:
            fresh = scan_readiness_details(audit, case.now+timedelta(minutes=1))
            old = scan_readiness_details(audit, case.now+timedelta(minutes=16))
            self.assertFalse(fresh['historical'])
            self.assertTrue(old['historical'])
            self.assertIn('ย้อนหลัง', old['summary'])
            self.assertEqual(len(old['fixtures']), 1)
            self.assertIsInstance(old['fixtures'][0]['เจ้าบ้าน'], dict)
            self.assertEqual(json.loads(rows[0]['analysis_a_json'])['scan_diagnostics']['C_supported'], 0)
            get.assert_not_called()

    def test_damaged_saved_health_is_unknown_without_rewriting_evidence(self):
        records = [dict(sport_key=str(i), payload_json=value) for i, value in enumerate(
            ('{broken', 'null', '[]', '{"teams":[null,{}, {"csv":null}], "scan_window":{"A_ready":0}}'))]
        before = copy.deepcopy(records)
        audits = decode_health_audits(records)
        for audit in audits.values():
            view = scan_readiness_details(audit)
            self.assertEqual(view['fixtures'], [])
            self.assertIn('ยังไม่มีรายงาน', view['summary'])
        self.assertEqual(records, before)

    @unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit unavailable locally')
    def test_saved_readiness_and_damaged_legacy_health_keep_four_tabs_and_no_http(self):
        from streamlit.testing.v1 import AppTest
        case = case_for_test(self)
        before = case.seed_legacy_bills()
        rows, snapshots, _, _ = case.scan()
        case.f['save_picks'](rows, snapshots)
        with case.f['db']() as con:
            con.execute('INSERT INTO league_health(sport_key,checked_at,payload_json) VALUES(?,?,?)',
                        ('old-damaged-health', datetime.now(timezone.utc).isoformat(), '{broken'))
        with patch.dict(os.environ, VALUE_BET_DB=str(case.f['DB_FILE'])), \
                patch('requests.get', side_effect=AssertionError('view must not request API')) as get:
            app = AppTest.from_file(str(ROOT/'app.py'), default_timeout=30).run()
            app.multiselect[0].set_value([case.sport]).run()
            self.assertEqual(len(app.exception), 0)
            self.assertEqual(len(app.tabs), 4)
            health = next(item for item in app.expander if item.label.startswith('Data Health'))
            self.assertTrue(health.dataframe)
            app.radio[0].set_value('ตารางเดิม').run()
            app.radio[0].set_value('การ์ดมือถือ').run()
            self.assertEqual(len(app.exception), 0)
            get.assert_not_called()
        self.assertEqual(case.legacy_snapshot(), before)


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'Playwright unavailable locally')
class Issue15MobileBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import test_dashboard_browser as browser
        browser.DashboardBrowserTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.runtime.stop()

    def test_actual_saved_source_diagnostics_fit_iphone_light_dark_and_escape_html(self):
        import test_dashboard_browser as browser
        from test_dashboard import complete_row
        from market_recommendations import group_fixture_recommendations, RANKING
        from mobile_dashboard import fixture_dashboard_html, fixture_details_html
        case = case_for_test(self)
        rows, snapshots, _, _ = case.scan()
        for row in rows:
            a = json.loads(row['analysis_a_json'])
            a['home_form']['diagnostics']['message'] += '<script>window.PWNED=1</script>'
            row['analysis_a_json'] = json.dumps(a)
        case.f['save_picks'](rows, snapshots)
        fixture = group_fixture_recommendations(case.f['load_picks']().to_dict('records'), case.f['choose_recommendation'])[0]
        for width in (320, 375, 390, 430):
            for dark in (False, True):
                page = self.browser.new_page(viewport={'width': width, 'height': 844}, color_scheme='dark' if dark else 'light')
                try:
                    page.set_content(browser.DashboardBrowserTests.page_content(self, complete_row(), dark))
                    page.locator('body').evaluate('(body,markup)=>body.innerHTML=markup',
                        fixture_dashboard_html(fixture)+'<details><summary>ดูรายละเอียดเพิ่มเติม</summary>'+
                        fixture_details_html(fixture, RANKING)+'</details>')
                    page.locator('summary').click()
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    self.assertEqual(page.locator('script,img').count(), 0)
                    self.assertIsNone(page.evaluate('window.PWNED'))
                    self.assertIn('ฤดูกาลก่อน', page.inner_text('body'))
                    if width == 375 and os.environ.get('DASHBOARD_SCREENSHOTS'):
                        folder = Path(os.environ['DASHBOARD_SCREENSHOTS'])
                        folder.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=str(folder/f'issue15-diagnostics-{dark}.png'), full_page=True)
                finally:
                    page.close()
