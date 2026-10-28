"""Exercise the real app loader boundary without Streamlit or provider HTTP."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import unittest

import pandas as pd
import test_regressions as regression


class Issue9FetchWrapperTests(unittest.TestCase):
    setUp=regression.Regressions.setUp

    def configure(self, response):
        self.get=Mock(return_value=response)
        self.f.update(requests=SimpleNamespace(get=self.get),TIMEOUT=1,UNDERSTAT_SEASONS=(2026,))

    def test_failed_cache_boundary_returns_safe_http_diagnostics(self):
        self.configure(SimpleNamespace(status_code=403,content=b'forbidden'))
        frame=self.f['fetch_understat_with_diagnostics']('soccer_epl')
        self.assertTrue(frame.empty)
        self.assertEqual(frame.attrs['xg_diagnostics']['sources'][0]['http_status'],403)
        self.assertIn('received_at',frame.attrs['xg_diagnostics']['sources'][0])
        self.get.assert_called_once()

    def test_unsupported_source_never_requests_another_league(self):
        self.configure(SimpleNamespace(status_code=200,content=b'not used'))
        frame=self.f['fetch_understat_with_diagnostics']('soccer_efl_champ')
        self.assertTrue(frame.empty);self.get.assert_not_called()

    def test_cached_frame_receipt_is_not_redated_by_wrapper(self):
        frame=pd.DataFrame();frame.attrs['xg_diagnostics']={'status':'http_error','sources':[{'received_at':'2026-10-20T12:00:00Z'}]}
        self.f['fetch_understat_history']=Mock(return_value=frame)
        returned=self.f['fetch_understat_with_diagnostics']('soccer_epl')
        self.assertIs(returned,frame)
        self.assertEqual(returned.attrs['xg_diagnostics']['sources'][0]['received_at'],'2026-10-20T12:00:00Z')

    def test_nonempty_partial_failure_uses_short_failure_path_without_losing_rows(self):
        self.configure(SimpleNamespace(status_code=200,content=b'not used'))
        frame=pd.DataFrame([{'Date_dt':pd.Timestamp('2025-10-01'),'xG_home':1.2,'xG_away':.8}])
        frame.attrs['xg_diagnostics']={'status':'partial','sources':[{'season':2026,'http_status':404}]}
        from understat_history import UnderstatUnavailable
        with patch('understat_history.load_understat_history',return_value=frame):
            with self.assertRaises(UnderstatUnavailable) as failure:
                self.f['fetch_understat_history']('soccer_epl')
            self.assertEqual(len(failure.exception.frame),1)
            returned=self.f['fetch_understat_with_diagnostics']('soccer_epl')
            self.assertEqual(len(returned),1)
            self.assertEqual(returned.attrs['xg_diagnostics']['status'],'partial')
