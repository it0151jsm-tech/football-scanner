"""Export an explicit forward observation DB as a new offline backtest JSON."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def main(argv=None):
    parser=argparse.ArgumentParser(description='Read-only observer export; never opens bill database automatically')
    parser.add_argument('--archive-db',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        source,output=Path(args.archive_db),Path(args.output)
        if not source.is_file() or output.suffix.lower()!='.json' or output.exists() or output.is_symlink():
            raise ValueError('explicit_observer_and_new_json_required')
        from observation_archive import export_database
        document=export_database(source)
        rendered=json.dumps(document,ensure_ascii=False,indent=2,allow_nan=False)+'\n'
        with output.open('x',encoding='utf-8') as handle:
            handle.write(rendered)
    except (ValueError,OSError,TypeError,sqlite3.Error):
        print('Observer export rejected: check observer schema and new JSON path.',file=sys.stderr)
        return 2
    print('Forward observations exported; review source rights, quarantine and timing before backtesting.')
    return 0


if __name__=='__main__':
    raise SystemExit(main())
