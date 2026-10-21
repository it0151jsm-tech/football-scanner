"""Fail-closed database access and online SQLite backup. No app DB imports."""
from contextlib import contextmanager
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

APP_ID = 1447186502
DEFAULT_DB = Path('foundation-data/football_foundation.db')
LEAGUES = ('soccer_epl','soccer_spain_la_liga','soccer_italy_serie_a',
           'soccer_germany_bundesliga','soccer_france_ligue_one')


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec='microseconds')


def timestamp(value):
    if not isinstance(value,str):
        raise ValueError('timestamp must be an ISO8601 string with timezone')
    dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    if dt.tzinfo is None:
        raise ValueError('timezone required')
    return dt.astimezone(timezone.utc)


def encoded(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(',',':'))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def safe_path(path):
    path=Path(path).absolute()
    configured=os.getenv('VALUE_BET_DB')
    if 'value_bet' in path.name.lower() or (configured and path.resolve()==Path(configured).resolve()):
        raise ValueError('legacy bill path is forbidden')
    if path.is_symlink():
        raise ValueError('database symlinks are forbidden')
    return path


def verify(con):
    if con.execute('PRAGMA application_id').fetchone()[0]!=APP_ID:
        raise ValueError('not an experimental foundation database')
    names={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if {'picks','slips','manual_ou_quotes'} & names:
        raise ValueError('legacy bill tables are forbidden')
    required={'foundation_meta','sources','ingestion_runs','teams','matches','observations','team_stats',
              'xg_stats','odds_snapshots','model_predictions','match_results','quarantine','validation_runs','source_permissions','team_season_memberships'}
    if not required<=names or con.execute('SELECT kind,schema_version FROM foundation_meta').fetchall()!=[('value-bet-foundation',1)]:
        raise ValueError('unsupported foundation schema')


@contextmanager
def connect(path=DEFAULT_DB,readonly=False):
    path=safe_path(path)
    if not path.is_file():
        raise ValueError('initialize the separate foundation database first')
    # Validate read-only before any connection capable of writing is opened.
    probe=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)
    try:
        verify(probe)
    finally:
        probe.close()
    con=sqlite3.connect(path.as_uri()+('?mode=ro' if readonly else '?mode=rw'),uri=True,timeout=30)
    con.row_factory=sqlite3.Row
    con.execute('PRAGMA foreign_keys=ON')
    try:
        with con:
            yield con
    finally:
        con.close()


def initialize(path=DEFAULT_DB):
    path=safe_path(path)
    if path.name!='football_foundation.db':
        raise ValueError('experimental database must be named football_foundation.db')
    if path.exists():
        with connect(path,readonly=True):
            pass
        return path
    path.parent.mkdir(parents=True,exist_ok=True)
    fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
    os.close(fd)
    try:
        with sqlite3.connect(path) as con:
            con.executescript(Path(__file__).with_name('schema.sql').read_text())
            verify(con)
    except Exception:
        path.unlink()
        raise
    return path


def inventory(path):
    with connect(path,readonly=True) as con:
        names=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name:[tuple(row) for row in con.execute('SELECT * FROM '+name+' ORDER BY rowid')] for name in names}


def backup(source,destination,restore=False):
    source=safe_path(source); destination=safe_path(destination)
    if restore and destination.name!='football_foundation.db':
        raise ValueError('restore destination must be a new football_foundation.db')
    if not restore and not destination.name.endswith('.foundation-backup.db'):
        raise ValueError('backup destination must end with .foundation-backup.db')
    if destination.exists():
        raise ValueError('refusing to overwrite any existing file, including the experimental database')
    with connect(source,readonly=True) as src:
        if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
            raise ValueError('source integrity check failed')
        destination.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(destination,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
        try:
            with sqlite3.connect(destination) as target:
                src.backup(target)
                verify(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok' or target.execute('PRAGMA foreign_key_check').fetchall():
                    raise ValueError('backup integrity check failed')
            if inventory(source)!=inventory(destination):
                raise ValueError('restored content differs')
        except Exception:
            destination.unlink()
            raise
    return {'source':str(source),'destination':str(destination),'integrity':'ok','all_tables_equal':True}
