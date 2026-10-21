"""Explicit offline real-data pilot; no network calls, app imports or bill access."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foundation.openfootball import FIXTURES, import_openfootball
from foundation.store import backup, initialize, inventory
from foundation.validate import quality_report


def run(db, output):
    output = Path(output)
    # A caller chooses a fresh output folder; never overwrite evidence/backups.
    if output.exists():
        raise ValueError('choose a new output directory; pilot artifacts are never overwritten')
    output.mkdir(parents=True)
    initialize(db)
    report = import_openfootball(FIXTURES / 'sample.json', db)
    saved = output / 'openfootball.foundation-backup.db'
    saved_report = backup(db, saved)
    restored = output / 'restored' / 'football_foundation.db'
    restored_report = backup(saved, restored, restore=True)
    if inventory(db) != inventory(restored):
        raise ValueError('restore differs from imported database')
    report['backup'] = saved_report
    report['restore'] = restored_report
    report['coverage_scope'] = '15 original Openfootball rows, 2025/26, first 3 per league; not full-season coverage'
    report['results_status'] = 'source-reported results; not independently verified against an official league feed'
    (output / 'import-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'quality-report.json').write_text(json.dumps(quality_report(db), ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'foundation-data/openfootball-pilot/football_foundation.db')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.output), ensure_ascii=False, indent=2))
