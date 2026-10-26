"""The mobile presentation must preserve the PR20 operational contract."""
import ast
import hashlib
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTRACT = json.loads((ROOT / 'fixtures/ui/pr20-behavior-contract.json').read_text())


class MobilePresentationIsolationTests(unittest.TestCase):
    def test_existing_backend_models_and_database_modules_are_byte_unchanged(self):
        for name, expected in CONTRACT['files'].items():
            with self.subTest(module=name):
                self.assertEqual(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), expected)

    def test_all_existing_app_functions_and_classes_remain_unchanged(self):
        source = (ROOT / 'app.py').read_text()
        definitions = {node.name: node for node in ast.parse(source).body
                       if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))}
        self.assertEqual(set(definitions), set(CONTRACT['app_definitions']))
        for name, expected in CONTRACT['app_definitions'].items():
            with self.subTest(definition=name):
                actual = hashlib.sha256(ast.get_source_segment(source, definitions[name]).encode()).hexdigest()
                self.assertEqual(actual, expected)

    def test_presentation_does_not_import_database_network_or_analysis_implementations(self):
        for name in ('data_center_presentation.py', 'data_center_ui.py'):
            tree = ast.parse((ROOT / name).read_text())
            forbidden = {'sqlite3', 'requests', 'httpx', 'urllib', 'socket', 'app',
                         'data_center_store', 'data_center_stats', 'odds_budget',
                         'market_recommendations', 'backtest', 'numpy', 'scipy'}
            imports = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imports.update(alias.name.split('.')[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports.add(node.module.split('.')[0])
            self.assertFalse(imports & forbidden, (name, imports & forbidden))


if __name__ == '__main__':
    unittest.main()
