"""Evaluate an explicitly supplied, redacted JSON archive entirely offline.

Never discovers/opens SQLite files, imports the Streamlit app, or fetches data.
Existing outputs are refused so an earlier evidence report cannot be replaced.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
MAX_JSON_BYTES = 64 * 1024 * 1024


class _RedactedParser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(2, 'Invalid arguments; use --help. No API credentials are accepted.\n')


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate_json_key')
        result[key] = value
    return result


def _read_json(path):
    path = Path(path)
    if path.suffix.lower() != '.json' or not path.is_file():
        raise ValueError('explicit_json_file_required')
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ValueError('archive_size_limit_exceeded')
    with path.open('r', encoding='utf-8') as handle:
        value = json.load(handle, object_pairs_hook=_object_pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))
    if not isinstance(value, dict):
        raise ValueError('json_object_required')
    return value


def main(argv=None):
    parser = _RedactedParser(description='Offline PR6 baseline backtest; no API or SQLite access.')
    parser.add_argument('--archive', required=True, help='Explicit redacted .json archive')
    parser.add_argument('--output', required=True, help='New .json report; existing files are never overwritten')
    parser.add_argument('--protocol', help='Optional predeclared .json protocol; omit for descriptive evaluation only')
    args = parser.parse_args(argv)
    output = Path(args.output)
    try:
        if output.suffix.lower() != '.json' or output.exists() or output.is_symlink():
            raise ValueError('new_json_output_required')
        if not output.parent.is_dir():
            raise ValueError('output_parent_missing')
        document = _read_json(args.archive)
        if document.get('data_mode') == 'synthetic_test':
            raise ValueError('synthetic_archives_are_test_only')
        protocol = _read_json(args.protocol) if args.protocol else None
        # Import only the isolated offline evaluator, never app.py.
        from backtest.engine import run_backtest
        report = run_backtest(document, protocol, report_at=datetime.now(timezone.utc).isoformat())
        rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + '\n'
        # Exclusive creation also protects against a concurrent report writer.
        with output.open('x', encoding='utf-8') as handle:
            handle.write(rendered)
    except (OSError, UnicodeError, ValueError, TypeError, OverflowError):
        # Do not echo source records, URLs, credentials, paths or exception bodies.
        print('Backtest rejected: check redacted JSON, evidence, protocol and new output path.', file=sys.stderr)
        return 2
    print('Offline backtest report written. Read evidence limitations before interpreting results.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
