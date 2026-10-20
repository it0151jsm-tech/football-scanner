"""Streamlit integration smoke tests; CI installs the app requirements."""
import importlib.util
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from test_regressions import load_functions


@unittest.skipUnless(importlib.util.find_spec('streamlit'), 'Streamlit is not installed locally')
class StreamlitSmokeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(__file__).resolve().parents[1] / 'app.py'
        self.db_path = Path(self.tmp.name) / 'smoke.db'
        self.env = patch.dict(os.environ, VALUE_BET_DB=str(self.db_path))
        self.env.start()
        self.addCleanup(self.env.stop)

    def app(self):
        from streamlit.testing.v1 import AppTest
        return AppTest.from_file(str(self.path), default_timeout=20).run()

    def test_existing_four_tabs_start_with_empty_database(self):
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.tabs), 4)

    def test_legacy_unsupported_bill_can_be_manually_settled(self):
        import json
        f = load_functions(self.path)
        f['DB_FILE'] = self.db_path
        f['init_db']()
        f['save_picks']([dict(event_id='legacy', market='Asian Handicap', side='home',
                             league=f['LEAGUES']['soccer_usa_mls'][0], bookmaker='Book',
                             kickoff_utc=(datetime.now(timezone.utc)-timedelta(days=1)).isoformat(),
                             home_team='A', away_team='B', match='A vs B',
                             point=-.25, odds=2, selection='ต่อ A (-0.25)')])
        with f['db']() as con:
            con.execute('INSERT INTO slips(selections_json,status) VALUES (?,?)',
                        (json.dumps([dict(pick_id=1, market='Asian Handicap', selection='ต่อ A (-0.25)', odds=2)]), 'รอตรวจผล'))
        app = self.app()
        self.assertEqual(len(app.exception), 0)
        app.checkbox[0].check().run()
        next(button for button in app.button if button.label == 'บันทึกผลที่ยืนยันแล้ว').click().run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(f['load_slips']().iloc[0].profit_units, -.5)
        self.assertEqual(f['load_picks']().iloc[0].status, 'ตรวจแล้ว')
