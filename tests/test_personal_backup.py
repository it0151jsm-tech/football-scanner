"""Backup/restore personal revisions and fake legacy bills; disposable files only."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bill_backup import backup_bytes, restore_new
from personal_stats import initialize, save_verified, read_history, eligible_history

UTC = timezone.utc


def bill_hash(connection):
    rows = {table: connection.execute('SELECT * FROM ' + table + ' ORDER BY id').fetchall()
            for table in ('picks', 'slips')}
    return hashlib.sha256(json.dumps(rows, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


class PersonalBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'personal-test.db'
        self.db = sqlite3.connect(self.path); self.addCleanup(self.db.close)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE picks(id INTEGER PRIMARY KEY,point REAL,odds REAL,status TEXT,final_score TEXT)')
        self.db.execute('CREATE TABLE slips(id INTEGER PRIMARY KEY,selections_json TEXT,status TEXT,profit_units REAL)')
        self.db.execute('INSERT INTO picks VALUES(1,-0.25,1.95,?,?)', ('ตรวจแล้ว', '2-1'))
        for row in ((1,'[{"pick_id":1}]','ชนะเต็มทุกคู่',.95),
                    (2,'{damaged legacy json','แพ้บิล',-1), (3,'','รอตรวจผล',None)):
            self.db.execute('INSERT INTO slips VALUES(?,?,?,?)',row)
        self.db.commit()
        self.bill_before = bill_hash(self.db)
        args = dict(match_key='real-shape-synthetic-fixture', kickoff_utc='2026-10-20T19:00:00Z',
                    home_team='Home', away_team='Away', home_goals=2, away_goals=1,
                    source='Owner reviewed synthetic evidence', league='soccer_epl', season='2627',
                    reviewed=True, completed=True, evidence_mode='observed',
                    metadata={'image_sha256': ['a'*64]},stats={'xG':{'home':1.8,'away':.7}})
        with patch('personal_stats._utc_now',return_value=datetime(2026,10,21,8,tzinfo=UTC)):
            save_verified(self.db, **args)
        with patch('personal_stats._utc_now',return_value=datetime(2026,10,21,12,tzinfo=UTC)):
            save_verified(self.db, **dict(args, home_goals=3))
        self.db.commit()

    def test_backup_restore_keeps_all_versions_provenance_and_bill_hash(self):
        original_revisions = self.db.execute('SELECT * FROM personal_match_stats_revisions ORDER BY revision_id').fetchall()
        payload = backup_bytes(self.path)
        restored_path = restore_new(payload,Path(self.tmp.name)/'restored.db')
        with sqlite3.connect(restored_path) as restored:
            self.assertEqual(restored.execute('PRAGMA integrity_check').fetchone(),('ok',))
            self.assertEqual(restored.execute('SELECT * FROM personal_match_stats_revisions ORDER BY revision_id').fetchall(),original_revisions)
            self.assertEqual(bill_hash(restored),self.bill_before)
            self.assertEqual(read_history(restored)[0]['home_goals'],3)
            rows = eligible_history(restored,team='Home',before='2026-10-23T19:00:00Z',as_of='2026-10-21T10:00:00Z',league='soccer_epl',season='2627')
            self.assertEqual(rows[0]['home_goals'],2)
            self.assertEqual(json.loads(rows[0]['metadata_json'])['image_sha256'],['a'*64])
            with self.assertRaises(sqlite3.IntegrityError):
                restored.execute('UPDATE personal_match_stats_revisions SET home_goals=9')
        self.assertEqual(bill_hash(self.db),self.bill_before)
        self.assertEqual(self.db.execute('SELECT * FROM personal_match_stats_revisions ORDER BY revision_id').fetchall(),original_revisions)

    def test_existing_restore_destination_including_source_is_never_overwritten(self):
        payload = backup_bytes(self.path)
        before = self.path.read_bytes()
        with self.assertRaises(FileExistsError):
            restore_new(payload,self.path)
        self.assertEqual(self.path.read_bytes(),before)
        target = Path(self.tmp.name)/'existing.db';target.write_bytes(b'owner file')
        with self.assertRaises(FileExistsError):
            restore_new(payload,target)
        self.assertEqual(target.read_bytes(),b'owner file')
        self.assertEqual(bill_hash(self.db),self.bill_before)

    def test_malformed_backup_never_creates_restore_target_or_changes_bills(self):
        for index,payload in enumerate((b'',b'not SQLite',b'\x00'*200)):
            target = Path(self.tmp.name)/f'bad-{index}.db'
            with self.assertRaises((sqlite3.DatabaseError,ValueError)):
                restore_new(payload,target)
            self.assertFalse(target.exists())
        self.assertEqual(bill_hash(self.db),self.bill_before)

    def test_legacy_personal_table_and_legacy_bill_payloads_survive_backup(self):
        self.db.execute('INSERT INTO personal_match_stats VALUES(?,?,?,?,?,?,?,?,?,?)',
                        ('legacy','2025-10-20T19:00:00+00:00','Home','Old Away',0,0,'legacy source','{}','2025-10-21T00:00:00+00:00',1))
        self.db.commit()
        original = self.db.execute('SELECT * FROM personal_match_stats').fetchall()
        path = restore_new(backup_bytes(self.path),Path(self.tmp.name)/'legacy-restored.db')
        with sqlite3.connect(path) as restored:
            self.assertEqual(restored.execute('SELECT * FROM personal_match_stats').fetchall(),original)
            self.assertEqual(bill_hash(restored),self.bill_before)
            rows = eligible_history(restored,team='Home',before='2026-10-23T19:00:00Z',as_of='2026-10-22T00:00:00Z',league='soccer_epl',season='2627')
            self.assertEqual([r['match_key'] for r in rows],['real-shape-synthetic-fixture'])


if __name__ == '__main__':
    unittest.main()
