"""Replay the original 15-row quarantine, append evidenced UTC revisions, back up."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from foundation.openfootball import FIXTURES, import_openfootball
from foundation.store import backup, initialize, inventory
from foundation.timezone_revision import import_timezone_revision
from foundation.validate import quality_report


def run(db, output):
    output = Path(output)
    if output.exists():
        raise ValueError('choose a fresh output folder; evidence and backups are never overwritten')
    initialize(db)
    # Idempotent in the existing pilot. A fresh CI DB records its real receipt now.
    original = import_openfootball(FIXTURES / 'sample.json', db)
    report = import_timezone_revision(db)
    before = inventory(db)
    repeated = import_timezone_revision(db)
    if not repeated['import_result']['duplicate_batch'] or inventory(db) != before:
        raise ValueError('timezone revision replay is not idempotent')
    output.mkdir(parents=True)
    saved = output / 'timezone.foundation-backup.db'
    saved_report = backup(db, saved)
    restored = output / 'restored/football_foundation.db'
    restored_report = backup(saved, restored, restore=True)
    if inventory(db) != inventory(restored):
        raise ValueError('restored timezone revision differs')
    report.update(original_import=original['foundation_import'], replay_idempotent=True,
                  backup=saved_report, restore=restored_report,
                  missing=['xG/xGA', 'shots', 'AH/OU odds', 'sufficient team-form history'])
    (output / 'revision-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (output / 'quality-report.json').write_text(json.dumps(quality_report(db), ensure_ascii=False, indent=2) + '\n')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path, default=ROOT / 'foundation-data/openfootball-pilot/football_foundation.db')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(run(args.db, args.output), ensure_ascii=False, indent=2))
