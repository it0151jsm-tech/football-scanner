"""Reproducible offline artifact generation; every database is experimental."""
import json
import shutil
import sys
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from foundation.ingest import import_file
from foundation.store import backup, initialize
from foundation.validate import quality_report,validate


def run(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as temp:
        db=Path(temp)/'source'/'football_foundation.db';initialize(db)
        imported=import_file(ROOT/'fixtures/foundation/demo.json',db)
        quality=quality_report(db)
        sample=validate(db,'sample');research=validate(db,'research');observed=validate(db,'observed')
        saved=Path(temp)/'sample.foundation-backup.db';backup(db,saved)
        restored=Path(temp)/'restored'/'football_foundation.db'
        restored_result=backup(saved,restored,restore=True)
        assert quality['real_matches']==0
        assert all(r['n']==0 for r in observed['leagues'])
        assert sum(r['n'] for r in sample['leagues'])==60
        assert sample['sample_only'] and not sample['accuracy_claim_permitted']
        assert all(r['n']==0 for r in research['leagues'])
        shutil.copyfile(saved,folder/'football_foundation.db')
        report=dict(import_result=imported,backup_restore=restored_result,
                    real_match_count=0,status='offline_sample_smoke_only_no_accuracy_claim')
        for name,data in [('quality',quality),('research',research),('sample',sample),('observed',observed),('smoke',report)]:
            (folder/('foundation-'+name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
        return report


if __name__=='__main__':
    result=run(ROOT/'test-artifacts')
    print(json.dumps(result,ensure_ascii=False))
