"""Offline export readiness regression tests; no provider requests."""
import unittest
import pandas as pd
from source_diagnostics import summarize_export_readiness


class ExportReadinessTests(unittest.TestCase):
    def test_multiple_markets_count_as_one_fixture(self):
        data = pd.DataFrame([
            {'ลีก': 'EPL', 'คู่แข่งขัน': 'A vs B', 'เวลาแข่ง (ไทย)': '24/10 18:30',
             'คะแนนหลักฐาน A/100': 60, 'EV (%)': None},
            {'ลีก': 'EPL', 'คู่แข่งขัน': 'A vs B', 'เวลาแข่ง (ไทย)': '24/10 18:30',
             'คะแนนหลักฐาน A/100': None, 'EV (%)': 5.0},
            {'ลีก': 'EPL', 'คู่แข่งขัน': 'C vs D', 'เวลาแข่ง (ไทย)': '24/10 21:00',
             'คะแนนหลักฐาน A/100': None, 'EV (%)': None},
        ])
        report = summarize_export_readiness(data)
        self.assertEqual(report['market_rows'], 3)
        self.assertEqual(report['fixtures'], 2)
        self.assertEqual(report['a_scored_fixtures'], 1)
        self.assertEqual(report['b_ev_fixtures'], 1)

    def test_missing_and_empty_inputs(self):
        self.assertEqual(summarize_export_readiness(pd.DataFrame())['status'], 'missing_columns')
        cols = ['ลีก', 'คู่แข่งขัน', 'เวลาแข่ง (ไทย)', 'คะแนนหลักฐาน A/100', 'EV (%)']
        self.assertEqual(summarize_export_readiness(pd.DataFrame(columns=cols))['status'], 'empty')


if __name__ == '__main__':
    unittest.main()
