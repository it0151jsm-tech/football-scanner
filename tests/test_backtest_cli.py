"""CLI safety tests use explicit JSON and a fake evaluator, never football APIs."""
from contextlib import redirect_stderr, redirect_stdout
import builtins
import importlib.util
import io
import json
import socket
import sqlite3
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('offline_backtest_cli', ROOT / 'scripts/run_backtest.py')
CLI = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CLI)


class BacktestCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.archive = self.folder / 'archive.json'
        self.archive.write_text((ROOT / 'fixtures/backtest/empty-observed.json').read_text(encoding='utf-8'), encoding='utf-8')
        self.output = self.folder / 'report.json'
        self.calls = []
        self.engine = types.ModuleType('backtest.engine')
        def evaluate(document, protocol, **kwargs):
            self.calls.append((document, protocol, kwargs))
            return {'sample_count': 0, 'roi_pct': None, 'status': 'insufficient_data'}
        self.engine.run_backtest = evaluate

    def invoke(self, *extra):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.dict(sys.modules, {'backtest.engine': self.engine}), redirect_stdout(stdout), redirect_stderr(stderr):
            code = CLI.main(['--archive', str(self.archive), '--output', str(self.output), *extra])
        return code, stdout.getvalue(), stderr.getvalue()

    def test_empty_fixture_has_no_invented_records(self):
        value = json.loads(self.archive.read_text())
        for key in ('events', 'decisions', 'score_history', 'xg_history', 'quotes', 'results'):
            self.assertEqual(value[key], [])
        self.assertEqual(value['data_mode'], 'observed')

    def test_explicit_json_writes_new_null_performance_report_with_actual_report_clock(self):
        self.assertEqual(self.invoke()[0], 0)
        self.assertEqual(json.loads(self.output.read_text())['roi_pct'], None)
        document, protocol, kwargs = self.calls[0]
        self.assertIsNone(protocol)
        self.assertIn('+00:00', kwargs['report_at'])
        self.assertNotIn('allow_test_fixtures', kwargs)
        self.assertEqual(document['events'], [])

    def test_existing_report_is_preserved_byte_for_byte(self):
        self.output.write_text('original evidence')
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.output.read_text(), 'original evidence')
        self.assertEqual(self.calls, [])

    def test_input_cannot_be_overwritten(self):
        original = self.archive.read_bytes()
        self.output = self.archive
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.archive.read_bytes(), original)

    def test_sqlite_input_and_output_refused_without_opening_bill_database(self):
        bill = self.folder / 'bills.db'
        bill.write_bytes(b'SQLite format 3\x00PRIVATE BILL DATA')
        original = bill.read_bytes()
        self.archive = bill
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(bill.read_bytes(), original)
        self.archive = self.folder / 'archive.json'
        self.output = bill
        self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(bill.read_bytes(), original)
        self.assertEqual(self.calls, [])

    def test_synthetic_archive_cannot_be_presented_as_real_cli_results(self):
        self.archive.write_text('{"schema_version":1,"data_mode":"synthetic_test"}')
        self.assertEqual(self.invoke()[0], 2)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.calls, [])

    def test_invalid_json_error_does_not_echo_sensitive_payload(self):
        marker = 'DO-NOT-LOG-API-KEY'
        self.archive.write_text('{"api_key":"'+marker+'" broken')
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertNotIn(marker, stdout+stderr)
        self.assertFalse(self.output.exists())

    def test_evaluator_error_is_redacted(self):
        self.engine.run_backtest = lambda *a, **k: (_ for _ in ()).throw(ValueError('api_key=DO-NOT-LOG'))
        code, stdout, stderr = self.invoke()
        self.assertEqual(code, 2)
        self.assertNotIn('DO-NOT-LOG', stdout+stderr)
        self.assertFalse(self.output.exists())

    def test_nonfinite_json_and_non_object_inputs_refused(self):
        for value in ('{"value":NaN}', '[]', '{"data_mode":"research","data_mode":"observed"}'):
            with self.subTest(value=value):
                self.archive.write_text(value)
                self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_protocol_is_explicit_and_immutable_input(self):
        protocol = self.folder / 'protocol.json'
        protocol.write_text('{"protocol_id":"predeclared"}')
        self.assertEqual(self.invoke('--protocol', str(protocol))[0], 0)
        self.assertEqual(self.calls[0][1], {'protocol_id': 'predeclared'})
        self.assertEqual(protocol.read_text(), '{"protocol_id":"predeclared"}')

    def test_symlink_output_is_not_followed(self):
        target = self.folder / 'absent.json'
        self.output.symlink_to(target)
        self.assertEqual(self.invoke()[0], 2)
        self.assertFalse(target.exists())

    def test_invalid_output_suffix_or_missing_parent_refused_before_evaluation(self):
        for path in (self.folder/'report.db', self.folder/'missing'/'report.json'):
            with self.subTest(path=path):
                self.output = path
                self.assertEqual(self.invoke()[0], 2)
        self.assertEqual(self.calls, [])

    def test_unknown_credential_argument_is_not_echoed(self):
        marker = 'DO-NOT-ECHO-SECRET'
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr), self.assertRaises(SystemExit) as exit_context:
            CLI.main(['--archive', str(self.archive), '--output', str(self.output), '--api-key', marker])
        self.assertEqual(exit_context.exception.code, 2)
        self.assertNotIn(marker, stdout.getvalue()+stderr.getvalue())
        self.assertFalse(self.output.exists())

    def test_real_empty_cli_engine_preserves_n0_without_app_network_or_database(self):
        original_import = builtins.__import__
        forbidden_imports = []
        def offline_import(name, *args, **kwargs):
            if name == 'app' or name == 'streamlit' or name.startswith('streamlit.'):
                forbidden_imports.append(name)
                raise AssertionError('Backtest must not import the running application')
            return original_import(name, *args, **kwargs)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(builtins, '__import__', offline_import), \
                patch.object(socket.socket, 'connect', side_effect=AssertionError('network forbidden')) as connect, \
                patch.object(socket.socket, 'connect_ex', side_effect=AssertionError('network forbidden')) as connect_ex, \
                patch.object(sqlite3, 'connect', side_effect=AssertionError('SQLite forbidden')) as db_connect, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = CLI.main(['--archive', str(self.archive), '--output', str(self.output)])
        self.assertEqual(code, 0, stderr.getvalue())
        self.assertEqual(forbidden_imports, [])
        connect.assert_not_called()
        connect_ex.assert_not_called()
        db_connect.assert_not_called()
        report = json.loads(self.output.read_text(encoding='utf-8'))
        self.assertEqual(report['data_mode'], 'observed')
        self.assertEqual(report['status'], 'insufficient_real_data')
        self.assertEqual(report['qualified_real_fixture_n'], 0)
        self.assertEqual(report['evaluated_fixture_n'], 0)
        self.assertEqual(report['settled_records'], [])
        self.assertEqual(report['predictions'], [])
        self.assertEqual(report['price_reports'], [])
        self.assertEqual(report['training_provenance_status'], 'unknown')
        for system in ('A', 'B', 'C'):
            cell = report['systems'][system]
            self.assertEqual(cell['selected_n'], 0)
            self.assertEqual(cell['pending_n'], 0)
            for metrics in [cell['overall'], *cell['by_market'].values()]:
                settlement = metrics['settlement']
                calibration = metrics['calibration']
                self.assertEqual(settlement['n'], 0)
                for key in ('roi_pct', 'yield_pct', 'profit_units', 'stake_units', 'return_units'):
                    self.assertIsNone(settlement[key], (system, key))
                self.assertEqual(calibration['n'], 0)
                for key in ('brier_multiclass', 'log_loss', 'ece_macro'):
                    self.assertIsNone(calibration[key], (system, key))
                self.assertFalse(calibration['calibrated'])
        self.assertEqual(report['b_calibration_contract_n'], 0)
        self.assertEqual(report['b_calibration_all_contracts']['overall']['calibration']['n'], 0)
        self.assertIsNone(report['b_calibration_all_contracts']['overall']['calibration']['brier_multiclass'])

    def test_nonfinite_report_is_not_written(self):
        self.engine.run_backtest = lambda *a, **k: {'roi': float('inf')}
        self.assertEqual(self.invoke()[0], 2)
        self.assertFalse(self.output.exists())


if __name__ == '__main__':
    unittest.main()
