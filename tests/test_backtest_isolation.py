"""Byte-level evidence that backtest leaves every PR6 application module intact."""
import hashlib
import json
from pathlib import Path
import unittest


class BacktestIsolationTests(unittest.TestCase):
    def test_original_pr6_application_and_ui_sources_are_byte_identical(self):
        root=Path(__file__).resolve().parents[1]
        lock=json.loads((root/'fixtures/backtest/pr6-source-lock.json').read_text())
        self.assertEqual(lock['base_commit'],'df4b836b7bfade57f009f1ec115ae2905f76ae7d')
        self.assertTrue({'app.py','mobile_dashboard.py','market_recommendations.py',
            'ou_market.py','bill_backup.py','history_data.py'} <= set(lock['protected_files_sha256']))
        for path,digest in lock['protected_files_sha256'].items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((root/path).read_bytes()).hexdigest(),digest)
