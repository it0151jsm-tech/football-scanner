"""Immutable PR6 reference plus independent production-formula parity checks.

The recovery task authorizes operational backend changes. They must not rewrite
the source bundle used by the historical comparator or its protected formulas.
"""
import ast
import hashlib
import json
from pathlib import Path
import unittest


class BacktestIsolationTests(unittest.TestCase):
    def test_original_pr6_application_and_ui_reference_sources_are_byte_identical(self):
        root=Path(__file__).resolve().parents[1]
        lock=json.loads((root/'fixtures/backtest/pr6-source-lock.json').read_text())
        self.assertEqual(lock['base_commit'],'df4b836b7bfade57f009f1ec115ae2905f76ae7d')
        self.assertTrue({'app.py','mobile_dashboard.py','market_recommendations.py',
            'ou_market.py','bill_backup.py','history_data.py'} <= set(lock['protected_files_sha256']))
        for path,digest in lock['protected_files_sha256'].items():
            with self.subTest(path=path):
                self.assertEqual(hashlib.sha256((root/'fixtures/backtest/pr6-baseline'/path).read_bytes()).hexdigest(),digest)

    def test_live_protected_eleven_formulas_still_match_the_original_reference(self):
        root=Path(__file__).resolve().parents[1]
        contract_path=Path('fixtures/readiness/pr2-contract.json')
        original_contract=json.loads((root/'fixtures/backtest/pr6-baseline'/contract_path).read_text())
        live_contract=json.loads((root/contract_path).read_text())
        expected=original_contract['formula_source_sha256']
        self.assertEqual(len(expected),11)
        self.assertEqual(live_contract['formula_source_sha256'],expected)
        source=(root/'app.py').read_text()
        functions={node.name:node for node in ast.parse(source).body if isinstance(node,ast.FunctionDef)}
        self.assertTrue(set(expected)<=set(functions))
        for name,digest in expected.items():
            with self.subTest(function=name):
                self.assertEqual(hashlib.sha256(ast.get_source_segment(source,functions[name]).encode()).hexdigest(),digest)

    def test_original_reference_contract_cannot_rewrite_mobile_css_or_template_guards(self):
        root=Path(__file__).resolve().parents[1]
        reference=json.loads((root/'fixtures/backtest/pr6-baseline/fixtures/readiness/pr2-contract.json').read_text())
        original_mobile=(root/'fixtures/backtest/pr6-baseline/mobile_dashboard.py').read_text()
        tree=ast.parse(original_mobile)
        css=next(node for node in tree.body if isinstance(node,ast.Assign)
                 and any(isinstance(target,ast.Name) and target.id=='DASHBOARD_CSS' for target in node.targets))
        self.assertEqual(hashlib.sha256(ast.literal_eval(css.value).encode()).hexdigest(),reference['mobile_css_sha256'])
        functions={node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
        for name,digest in reference['mobile_protected_source_sha256'].items():
            self.assertEqual(hashlib.sha256(ast.get_source_segment(original_mobile,functions[name]).encode()).hexdigest(),digest)
