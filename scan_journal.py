"""Append-only scan outcomes in the application's SQLite transaction domain.

No bill/price row is deleted or rewritten here. A failed latest league cannot
silently revive its older quotes on the primary screen. Old storage stays intact.
"""
import json
from datetime import datetime, timezone
from uuid import uuid4

from readiness import instant

SUCCESS = frozenset(('success','empty_api','filtered_out','fixtures_without_rows','success_no_rows'))


def initialize(con):
    con.execute('''CREATE TABLE IF NOT EXISTS scan_runs (
        id TEXT PRIMARY KEY, started_at TEXT NOT NULL, regions TEXT NOT NULL,
        hours_limit INTEGER NOT NULL, selected_json TEXT NOT NULL)''')
    con.execute('''CREATE TABLE IF NOT EXISTS scan_outcomes (
        id INTEGER PRIMARY KEY, run_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
        payload_json TEXT NOT NULL, FOREIGN KEY(run_id) REFERENCES scan_runs(id))''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_scan_outcomes_run ON scan_outcomes(run_id,id)')
    con.execute('''CREATE TRIGGER IF NOT EXISTS protect_scan_run_update
        BEFORE UPDATE ON scan_runs BEGIN SELECT RAISE(ABORT,'scan run is immutable'); END''')
    con.execute('''CREATE TRIGGER IF NOT EXISTS protect_scan_run_delete
        BEFORE DELETE ON scan_runs BEGIN SELECT RAISE(ABORT,'scan run is immutable'); END''')
    con.execute('''CREATE TRIGGER IF NOT EXISTS protect_scan_outcome_update
        BEFORE UPDATE ON scan_outcomes BEGIN SELECT RAISE(ABORT,'scan outcome is append only'); END''')
    con.execute('''CREATE TRIGGER IF NOT EXISTS protect_scan_outcome_delete
        BEFORE DELETE ON scan_outcomes BEGIN SELECT RAISE(ABORT,'scan outcome is append only'); END''')


def begin(con, selected, regions, hours_limit):
    if regions not in ('uk','eu','us') or type(hours_limit) is not int or hours_limit not in (24,48):
        raise ValueError('invalid_scan_scope')
    if not isinstance(selected,list) or not selected or any(not isinstance(k,str) for k in selected):
        raise ValueError('invalid_selected_leagues')
    run_id=uuid4().hex
    con.execute('INSERT INTO scan_runs VALUES(?,?,?,?,?)',(run_id,datetime.now(timezone.utc).isoformat(),
        regions,hours_limit,json.dumps(selected)))
    return run_id


def append(con, run_id, entry):
    # Entry is the controller's allowlisted safe diagnostics, never raw HTTP.
    con.execute('INSERT INTO scan_outcomes(run_id,recorded_at,payload_json) VALUES(?,?,?)',
        (run_id,datetime.now(timezone.utc).isoformat(),json.dumps(entry,ensure_ascii=False,allow_nan=False)))


def latest(con, league_config):
    run=con.execute('SELECT * FROM scan_runs ORDER BY rowid DESC LIMIT 1').fetchone()
    if run is None:
        return None
    try:
        selected=json.loads(run['selected_json'])
        entries={}
        for row in con.execute('SELECT payload_json FROM scan_outcomes WHERE run_id=? ORDER BY id',(run['id'],)):
            entry=json.loads(row[0])
            if not isinstance(entry,dict) or entry.get('sport_key') not in selected:
                raise ValueError('invalid_outcome')
            entries[entry['sport_key']]=entry
        records=[entries.get(key,{'sport_key':key,'league':league_config.get(key,(key,None))[0],
            'status':'incomplete','reason':'scan_interrupted_or_not_completed','api_returned':None,
            'passed_filter':None,'saved_fixture_count':0,'api_received_at':None}) for key in selected]
        successful=[e for e in records if e['status'] in SUCCESS]
        counts={'selected_leagues':len(selected),'successful_leagues':len(successful),
            'api_returned':sum(e.get('api_returned') or 0 for e in records),
            'passed_filter':sum(e.get('passed_filter') or 0 for e in records),
            'saved_fixture_count':sum(e.get('saved_fixture_count') or 0 for e in records),
            'league_errors':sum(e['status']=='league_error' for e in records),
            'cooldown_leagues':sum(e['status']=='cooldown' for e in records),
            'safety_stops':sum(e['status']=='safety_stop' for e in records),
            'skipped_safety_stop':sum(e['status']=='skipped_safety_stop' for e in records),
            'response_counts_complete':all(e.get('api_returned') is not None and e.get('passed_filter') is not None for e in records)}
        return {'run_id':run['id'],'started_at':run['started_at'],'regions':run['regions'],
            'hours_limit':run['hours_limit'],'leagues':records,'counts':counts,
            'stop_reason':next((entry.get('progress_stop_reason') or entry.get('reason') for entry in records
                if entry.get('progress_stop_reason') or entry.get('status') in ('safety_stop','skipped_safety_stop')),None)}
    except (ValueError,TypeError,KeyError):
        return {'run_id':run['id'],'regions':run['regions'],'hours_limit':run['hours_limit'],
            'leagues':[],'invalid':True,'reason':'stored_scan_report_unreadable'}


def current_rows(frame, report, selected, regions, hours_limit, league_config):
    """Read-only primary display gate; failed/omitted leagues stay in archive."""
    if frame.empty or report is None:
        return frame.copy()  # Pre-upgrade data still uses original freshness gates.
    if report.get('invalid') or report.get('regions')!=regions or report.get('hours_limit')!=hours_limit:
        return frame.iloc[0:0].copy()
    entries={e.get('sport_key'):e for e in report.get('leagues',[]) if e.get('status') in SUCCESS}
    allowed={league_config[key][0]:instant(entries[key].get('api_received_at'))
             for key in selected if key in entries and key in league_config}
    allowed={name:stamp for name,stamp in allowed.items() if stamp is not None}
    if not allowed or 'received_at' not in frame:
        return frame.iloc[0:0].copy()
    mask=frame.apply(lambda row:row.get('league') in allowed and
        instant(row.get('received_at'))==allowed[row['league']],axis=1)
    return frame[mask].copy()
