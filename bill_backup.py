"""Consistent read-only SQLite backups. Restore is deliberately an offline operation."""
from contextlib import closing
import sqlite3
import tempfile
from pathlib import Path


def integrity(con):
    if con.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise ValueError('SQLite integrity check failed')


def backup_bytes(source):
    source = Path(source).resolve(strict=True)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp)/'backup.db'
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(target)) as dst:
            src.backup(dst)
            integrity(dst)
        return target.read_bytes()


def restore_new(payload, target):
    """Never overwrite an existing database; verify before publishing the new copy."""
    target = Path(target)
    with tempfile.TemporaryDirectory() as tmp:
        candidate = Path(tmp)/'candidate.db';candidate.write_bytes(payload)
        with closing(sqlite3.connect(candidate.as_uri()+'?mode=ro',uri=True)) as con:
            integrity(con)
        with target.open('xb') as output:
            output.write(payload)
    return target
