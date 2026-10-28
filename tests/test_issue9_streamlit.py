"""Issue #9 saved-source diagnostics on the original mobile UI, entirely offline."""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import test_issue9_scan as synthetic


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Streamlit unavailable locally')
class Issue9StreamlitTests(unittest.TestCase):
    def fixture(self, *, english=False, blocked=False):
        case=synthetic.Issue9ScanTests('test_csv_aliases_recover_stuttgart_gladbach_5_and_10_form')
        case.setUp();self.addCleanup(case.doCleanups)
        case.prepare(english=english,blocked=blocked)
        case.seed_legacy_bills()
        rows,snapshots,_,_=case.scan()
        case.f['save_picks'](rows,snapshots)
        return case

    def snapshot(self, case):
        with case.f['db']() as con:
            return {table:[tuple(row) for row in con.execute('SELECT * FROM '+table+' ORDER BY id')]
                    for table in ('picks','slips','odds_snapshots')}

    def app(self, case):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=30).run()
        app.multiselect[0].set_value([case.sport]).run()
        self.assertEqual(len(app.exception),0)
        self.assertEqual([tab.label for tab in app.tabs],
                         ['📊 ผลวิเคราะห์ราคา','🎟️ ตัวเลือกสเต็ป','📚 ราคาไหลและตรวจผล','ℹ️ วิธีใช้'])
        return app

    def test_corrected_german_profiles_show_five_ten_games_and_real_b_saved_offline(self):
        case=self.fixture();before=self.snapshot(case)
        env=dict(VALUE_BET_DB=str(case.f['DB_FILE']),VALUE_BET_PERSONAL_STATS_PILOT='0')
        with patch.dict(os.environ,env),patch('requests.get',side_effect=AssertionError('saved view must never fetch')) as get:
            app=self.app(case)
            cards=[item.value for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards),1)
            self.assertIn('VfB Stuttgart',cards[0]);self.assertIn('Borussia Monchengladbach',cards[0])
            self.assertIn('มีข้อมูล 5 นัด',cards[0]);self.assertIn('data-model="available"',cards[0])
            details=next(item for item in app.tabs[0].expander if item.label=='ดูรายละเอียดเพิ่มเติม')
            html='\n'.join(item.value for item in details.markdown)
            self.assertIn('10 นัด',html)
            app.radio[0].set_value('ตารางเดิม').run();self.assertEqual(len(app.exception),0)
            app.radio[0].set_value('การ์ดมือถือ').run();self.assertEqual(len(app.exception),0)
            get.assert_not_called()
        self.assertEqual(self.snapshot(case),before)

    def test_blocked_provider_reason_survives_saved_mobile_and_table_toggle_without_bill_changes(self):
        case=self.fixture(blocked=True);before=self.snapshot(case)
        env=dict(VALUE_BET_DB=str(case.f['DB_FILE']),VALUE_BET_PERSONAL_STATS_PILOT='0')
        with patch.dict(os.environ,env),patch('requests.get',side_effect=AssertionError('diagnostics must render offline')) as get:
            app=self.app(case)
            cards=[item.value for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards),1);self.assertIn('ข้อมูลโมเดลไม่เพียงพอ',cards[0])
            self.assertIn('มีข้อมูล 5 นัด',cards[0]);self.assertNotIn('ตัวเลือกเด่น',cards[0])
            details=next(item for item in app.tabs[0].expander if item.label=='ดูรายละเอียดเพิ่มเติม')
            html=cards[0]+'\n'+'\n'.join(item.value for item in details.markdown)
            self.assertIn('403',html);self.assertIn('Understat',html)
            self.assertIn(synthetic.SOURCE_URL,html)
            app.radio[0].set_value('ตารางเดิม').run();self.assertEqual(len(app.exception),0)
            app.radio[0].set_value('การ์ดมือถือ').run();self.assertEqual(len(app.exception),0)
            get.assert_not_called()
        self.assertEqual(self.snapshot(case),before)

    def test_ipswich_nottingham_recovered_form_survives_saved_view(self):
        case=self.fixture(english=True);before=self.snapshot(case)
        env=dict(VALUE_BET_DB=str(case.f['DB_FILE']),VALUE_BET_PERSONAL_STATS_PILOT='0')
        with patch.dict(os.environ,env),patch('requests.get',side_effect=AssertionError('no automatic quota usage')) as get:
            app=self.app(case)
            cards=[item.value for item in app.tabs[0].markdown if '<article class="vbp-dashboard"' in item.value]
            self.assertEqual(len(cards),1)
            self.assertIn('Ipswich Town',cards[0]);self.assertIn('Nottingham Forest',cards[0])
            self.assertEqual(cards[0].count('มีข้อมูล 5 นัด'),2)
            self.assertIn('data-model="available"',cards[0])
            get.assert_not_called()
        self.assertEqual(self.snapshot(case),before)
