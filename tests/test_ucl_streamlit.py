"""Saved UCL context is rendered offline in the unchanged four-tab UI."""
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch
import test_ucl_scan as synthetic


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Streamlit unavailable locally')
class UCLStreamlitTests(unittest.TestCase):
    def test_saved_cup_context_renders_and_switches_without_any_provider_call(self):
        from streamlit.testing.v1 import AppTest
        case=synthetic.UCLScanTests();case.setUp();self.addCleanup(case.doCleanups)
        case.prepare();rows,snaps,_,_=case.scan();case.f['save_picks'](rows,snaps)
        with case.f['db']() as con:
            before={t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in ('picks','slips','odds_snapshots')}
        with patch.dict(os.environ,VALUE_BET_DB=str(case.f['DB_FILE'])),patch('requests.get',side_effect=AssertionError('saved views must be offline')) as get:
            app=AppTest.from_file(str(Path(__file__).resolve().parents[1]/'app.py'),default_timeout=25).run()
            app.multiselect[0].set_value(['soccer_uefa_champs_league']).run()
            self.assertEqual(len(app.exception),0);self.assertEqual(len(app.tabs),4)
            cards=[m.value for m in app.tabs[0].markdown if '<article class="vbp-dashboard"' in m.value]
            self.assertEqual(len(cards),1);self.assertIn('ฟอร์มลีกในประเทศ',cards[0]);self.assertIn('มี xG ลีกในประเทศจริง',cards[0])
            app.radio[0].set_value('ตารางเดิม').run();self.assertEqual(len(app.exception),0)
            app.radio[0].set_value('การ์ดมือถือ').run();self.assertEqual(len(app.exception),0)
            get.assert_not_called()
        with case.f['db']() as con:
            self.assertEqual(before,{t:[tuple(r) for r in con.execute('SELECT * FROM '+t)] for t in before})
