"""Integration checks use disposable DBs and mocked HTTP only."""
import importlib.util
import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import test_streamlit as original


@unittest.skipUnless(importlib.util.find_spec('streamlit'),'Streamlit unavailable locally')
class ReadinessStreamlitTests(unittest.TestCase):
    setUp=original.StreamlitSmokeTests.setUp
    app=original.StreamlitSmokeTests.app

    def test_401_and_429_stop_the_whole_multileague_scan(self):
        for status in (401,429):
            with self.subTest(status=status):
                env=dict(THE_ODDS_API_KEY='integration-key-'+str(status),VALUE_BET_API_LEDGER=str(Path(self.tmp.name)/('ledger-'+str(status)+'.db')))
                with patch.dict(os.environ,env):
                    app=self.app();app.multiselect[0].set_value(['soccer_epl','soccer_spain_la_liga']).run()
                    response=SimpleNamespace(status_code=status,headers={},text='apiKey=must-not-render')
                    with patch('requests.get',return_value=response) as get:
                        next(b for b in app.button if b.label=='🚀 สแกนราคาและวิเคราะห์').click().run()
                    self.assertEqual(len(app.exception),0);get.assert_called_once()
                    self.assertTrue(any('หยุดสแกน' in item.value for item in app.warning))
                    self.assertFalse(any('must-not-render' in item.value for item in app.warning))
                    self.assertEqual(len(app.tabs),4)
