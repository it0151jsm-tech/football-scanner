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
        self.assertEqual(len(report['problem_fixtures']), 1)

    def test_problem_fixtures_deduplicated_and_reason_retained(self):
        data = pd.DataFrame([
            {'ลีก': 'E1', 'คู่แข่งขัน': 'A vs B', 'เวลาแข่ง (ไทย)': '24/10 18:30',
             'คะแนนหลักฐาน A/100': None, 'EV (%)': None,
             'สถานะข้อมูล': 'ทีมเจ้าบ้าน: จับคู่ชื่อทีมไม่ได้'},
            {'ลีก': 'E1', 'คู่แข่งขัน': 'A vs B', 'เวลาแข่ง (ไทย)': '24/10 18:30',
             'คะแนนหลักฐาน A/100': None, 'EV (%)': None,
             'สถานะข้อมูล': 'ทีมเจ้าบ้าน: จับคู่ชื่อทีมไม่ได้'},
        ])
        report = summarize_export_readiness(data)
        self.assertEqual(report['fixtures'], 1)
        self.assertEqual(len(report['problem_fixtures']), 1)
        self.assertIn('จับคู่ชื่อทีมไม่ได้', report['problem_fixtures'][0]['observed_reasons']['สถานะข้อมูล'][0])

    def test_real_export_column_names_are_supported(self):
        rows = [
            {'ลีก': 'พรีเมียร์ลีก อังกฤษ', 'คู่แข่งขัน': 'Arsenal vs Everton',
             'เวลาแข่ง (ไทย)': '24/10/2026 18:30', 'คะแนนหลักฐาน A/100': 60,
             'EV (%)': 2.5, 'ระบบ A': 'หลักฐานฟอร์มสนับสนุน'},
            {'ลีก': 'แชมเปียนชิพ อังกฤษ', 'คู่แข่งขัน': 'Stoke City vs Bristol City',
             'เวลาแข่ง (ไทย)': '24/10/2026 21:00', 'คะแนนหลักฐาน A/100': None,
             'EV (%)': None, 'ระบบ A': 'ข้อมูลหลักฐานไม่พอ'},
        ]
        report = summarize_export_readiness(pd.DataFrame(rows))
        self.assertEqual(report['a_scored_fixtures'], 1)
        self.assertEqual(report['b_ev_fixtures'], 1)
        self.assertEqual(report['problem_fixtures'][0]['observed_reasons']['ระบบ A'],
                         ['ข้อมูลหลักฐานไม่พอ'])

    def test_missing_and_empty_inputs(self):
        self.assertEqual(summarize_export_readiness(pd.DataFrame())['status'], 'missing_columns')
        cols = ['ลีก', 'คู่แข่งขัน', 'เวลาแข่ง (ไทย)', 'คะแนนหลักฐาน A/100', 'EV (%)']
        self.assertEqual(summarize_export_readiness(pd.DataFrame(columns=cols))['status'], 'empty')


if __name__ == '__main__':
    unittest.main()
