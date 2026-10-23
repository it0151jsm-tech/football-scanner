"""Append-only, human-reviewed football evidence with actual UTC receipt times.

The original personal_match_stats table is retained, never rewritten. Legacy rows
remain reviewable, but cannot silently acquire observed-before-kickoff provenance.
"""
import json
import re
from datetime import datetime, timezone
from urllib.parse import urlsplit

UTC = timezone.utc
DOMESTIC_LEAGUES = frozenset({
    'soccer_epl', 'soccer_spain_la_liga', 'soccer_italy_serie_a',
    'soccer_germany_bundesliga', 'soccer_france_ligue_one',
})


def _utc_now():
    return datetime.now(UTC)


def _instant(value):
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if dt.tzinfo is None or dt.utcoffset() is None:
            raise ValueError('timestamp must include an explicit UTC offset')
        return dt.astimezone(UTC)
    except (TypeError, OverflowError, ValueError):
        raise ValueError('invalid timezone-aware timestamp') from None


def _label(value, name, limit=250, optional=False):
    if optional and value is None:
        return None
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise ValueError('invalid ' + name)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError('invalid ' + name)
    return value.strip()


def _season(value, league, kickoff):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('invalid season')
    text = str(value).strip()
    if not re.fullmatch(r'\d{4}', text):
        raise ValueError('season must be a four-digit source season')
    if league in DOMESTIC_LEAGUES:
        year = kickoff.year if kickoff.month >= 7 else kickoff.year - 1
        expected = f'{year % 100:02d}{(year + 1) % 100:02d}'
        if text != expected:
            raise ValueError('season does not match the domestic competition kickoff')
    return text


def _url(value):
    if value is None:
        return None
    text = _label(value, 'source URL', limit=2000)
    parsed = urlsplit(text)
    if parsed.scheme not in ('http', 'https') or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('invalid source URL')
    return text


def _metadata(value):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError('metadata must be an object')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    except (TypeError, ValueError):
        raise ValueError('metadata must contain finite JSON values') from None
    if len(encoded.encode('utf-8')) > 65536:
        raise ValueError('metadata too large')
    # Claims inside evidence metadata never replace authoritative receipt/review time.
    return encoded


def _exists(connection, table):
    return connection.execute('SELECT 1 FROM sqlite_master WHERE type=\'table\' AND name=?', (table,)).fetchone() is not None


def _rows(cursor):
    names = [item[0] for item in cursor.description]
    return [dict(zip(names, row)) for row in cursor.fetchall()]


def initialize(connection):
    """Add isolated evidence tables; do not migrate or reinterpret legacy rows."""
    connection.execute("""CREATE TABLE IF NOT EXISTS personal_match_stats (
        match_key TEXT PRIMARY KEY, kickoff_utc TEXT NOT NULL,
        home_team TEXT NOT NULL, away_team TEXT NOT NULL,
        home_goals INTEGER, away_goals INTEGER,
        source TEXT NOT NULL, stats_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1
    )""")
    connection.execute("""CREATE TABLE IF NOT EXISTS personal_match_stats_revisions (
        revision_id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_key TEXT NOT NULL, kickoff_utc TEXT NOT NULL,
        home_team TEXT NOT NULL, away_team TEXT NOT NULL,
        home_team_id TEXT, away_team_id TEXT,
        league TEXT, season TEXT, home_goals INTEGER, away_goals INTEGER,
        source TEXT NOT NULL, source_url TEXT, source_match_id TEXT,
        stats_json TEXT NOT NULL, metadata_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL, received_at TEXT NOT NULL, reviewed_at TEXT,
        verified INTEGER NOT NULL, completed INTEGER NOT NULL,
        evidence_mode TEXT NOT NULL, quality_status TEXT NOT NULL,
        supersedes_id INTEGER
    )""")
    connection.execute('CREATE INDEX IF NOT EXISTS idx_personal_revision_match ON personal_match_stats_revisions(match_key, revision_id)')
    connection.execute('CREATE INDEX IF NOT EXISTS idx_personal_revision_scope ON personal_match_stats_revisions(league, season, home_team, away_team)')
    connection.execute("""CREATE TRIGGER IF NOT EXISTS immutable_personal_revision_update
        BEFORE UPDATE ON personal_match_stats_revisions
        BEGIN SELECT RAISE(ABORT,'personal evidence revisions are append-only'); END""")
    connection.execute("""CREATE TRIGGER IF NOT EXISTS immutable_personal_revision_delete
        BEFORE DELETE ON personal_match_stats_revisions
        BEGIN SELECT RAISE(ABORT,'personal evidence revisions are append-only'); END""")


def clean_stats(raw):
    """Compatibility entry point using the shared, explicit canonical metric policy."""
    from stats_quality import validate_stats
    return validate_stats(raw)


def _identity_conflicts(prior, current):
    for key in ('home_team', 'away_team', 'league', 'season', 'home_team_id', 'away_team_id'):
        if prior.get(key) is not None and current.get(key) is not None and prior[key] != current[key]:
            return True
    try:
        return _instant(prior['kickoff_utc']) != _instant(current['kickoff_utc'])
    except (KeyError, ValueError):
        return True


def save_verified(connection, *, match_key, kickoff_utc, home_team, away_team,
                  home_goals, away_goals, source, stats, league=None, season=None,
                  home_team_id=None, away_team_id=None, reviewed=False, completed=False,
                  source_url=None, source_match_id=None, evidence_mode='research', metadata=None):
    """Append one revision; no caller can supply received/reviewed/recorded times.

    Explicit reviewed=True AND completed=True are required for verified evidence.
    Legacy callers remain reviewable research entries, never model-eligible records.
    Importing an old fixture now cannot make it observed before its old kickoff.
    """
    now = _utc_now()
    kickoff = _instant(kickoff_utc)
    if kickoff >= now:
        raise ValueError('kickoff must be in the past; explicit final-result review is also required')
    if type(reviewed) is not bool or type(completed) is not bool:
        raise ValueError('reviewed and completed must be explicit booleans')
    if evidence_mode not in ('research', 'observed'):
        raise ValueError('invalid evidence mode')
    if evidence_mode == 'observed' and not (reviewed and completed):
        raise ValueError('observed evidence requires explicit human review of a completed result')
    match_key = _label(match_key, 'match key')
    home_team, away_team = _label(home_team, 'home team'), _label(away_team, 'away team')
    if home_team.casefold() == away_team.casefold():
        raise ValueError('home and away team must be distinct')
    source = _label(source, 'source')
    league = _label(league, 'league', optional=True)
    season = _season(season, league, kickoff)
    home_team_id = _label(home_team_id, 'home team ID', optional=True)
    away_team_id = _label(away_team_id, 'away team ID', optional=True)
    if (home_team_id is None) != (away_team_id is None) or (home_team_id and home_team_id == away_team_id):
        raise ValueError('team IDs must be distinct and supplied for both teams')
    if (league is None) != (season is None):
        raise ValueError('league and season must be supplied together')
    if evidence_mode == 'observed' and (not league or not season):
        raise ValueError('observed evidence requires explicit league and season')
    for goal in (home_goals, away_goals):
        if goal is not None and (type(goal) is not int or not 0 <= goal <= 9223372036854775807):
            raise ValueError('invalid score')
    if (home_goals is None) != (away_goals is None) or (completed and home_goals is None):
        raise ValueError('completed score must have both integer sides')
    checked = clean_stats(stats)
    checked_metadata = _metadata(metadata)
    source_url = _url(source_url)
    source_match_id = _label(source_match_id, 'source match ID', optional=True)
    stamp = now.isoformat()
    current = dict(match_key=match_key, kickoff_utc=kickoff.isoformat(), home_team=home_team,
                   away_team=away_team, league=league, season=season,
                   home_team_id=home_team_id, away_team_id=away_team_id)
    connection.execute('SAVEPOINT personal_evidence_save')
    try:
        initialize(connection)
        versions = _rows(connection.execute('SELECT * FROM personal_match_stats_revisions WHERE match_key=? ORDER BY revision_id', (match_key,)))
        legacy = _rows(connection.execute('SELECT * FROM personal_match_stats WHERE match_key=?', (match_key,)))
        if any(_identity_conflicts(row, current) for row in versions + legacy):
            raise ValueError('match key conflicts with recorded team/league/season/kickoff identity')
        verified = reviewed and completed
        status = 'reviewed_observed' if verified and evidence_mode == 'observed' else 'research_only'
        values = dict(current, home_goals=home_goals, away_goals=away_goals, source=source,
                      source_url=source_url, source_match_id=source_match_id,
                      stats_json=json.dumps(checked, ensure_ascii=False, allow_nan=False, sort_keys=True),
                      metadata_json=checked_metadata, recorded_at=stamp, received_at=stamp,
                      reviewed_at=stamp if reviewed else None, verified=int(verified),
                      completed=int(completed), evidence_mode=evidence_mode, quality_status=status,
                      supersedes_id=versions[-1]['revision_id'] if versions else None)
        names = tuple(values)
        cursor = connection.execute('INSERT INTO personal_match_stats_revisions (' + ','.join(names) + ') VALUES (' + ','.join('?' for _ in names) + ')', tuple(values[key] for key in names))
        revision_id = cursor.lastrowid
        connection.execute('RELEASE SAVEPOINT personal_evidence_save')
        return revision_id
    except Exception:
        connection.execute('ROLLBACK TO SAVEPOINT personal_evidence_save')
        connection.execute('RELEASE SAVEPOINT personal_evidence_save')
        raise


def read_history(connection, *, team=None, before=None):
    """Human-review view only; legacy records are not granted model provenance."""
    cutoff = _instant(before) if before is not None else None
    latest = {}
    if _exists(connection, 'personal_match_stats'):
        for row in _rows(connection.execute('SELECT * FROM personal_match_stats')):
            if row.get('verified') == 1:
                latest[row['match_key']] = dict(row, quality_status='legacy_unverified_provenance', evidence_mode='research')
    if _exists(connection, 'personal_match_stats_revisions'):
        for row in _rows(connection.execute('SELECT * FROM personal_match_stats_revisions ORDER BY revision_id')):
            latest[row['match_key']] = row
    output = []
    for row in latest.values():
        if team is not None and team not in (row.get('home_team'), row.get('away_team')):
            continue
        try:
            kickoff = _instant(row['kickoff_utc'])
        except ValueError:
            if cutoff is not None:
                continue
            kickoff = datetime.min.replace(tzinfo=UTC)
        if cutoff is not None and kickoff >= cutoff:
            continue
        output.append((kickoff, row))
    return [row for _, row in sorted(output, key=lambda item: (item[0], item[1].get('revision_id', 0)), reverse=True)]


def eligible_history(connection, *, team, before, as_of=None, league=None, season=None,
                     team_id=None, exclude_match_key=None, source=None):
    """Point-in-time observed evidence, exact scope, latest known revision, deduped.

    Legacy/research records are never backdated into observed evidence. The latest
    received revision as of the cutoff supersedes prior versions even if it is an
    unreviewed correction, so known uncertainty cannot revive an older confident row.
    """
    target = _instant(before)
    cutoff = _instant(as_of) if as_of is not None else min(_utc_now(), target)
    if cutoff > target:
        raise ValueError('as_of cannot be after the prediction kickoff')
    team = _label(team, 'team')
    if league is None or season is None:
        return []
    league = _label(league, 'league')
    season = _season(season, league, target)
    if team_id is not None:
        team_id = _label(team_id, 'team ID')
    if not _exists(connection, 'personal_match_stats_revisions'):
        return []
    rows = _rows(connection.execute('SELECT * FROM personal_match_stats_revisions ORDER BY revision_id'))
    latest = {}
    for row in rows:
        try:
            received, recorded = _instant(row['received_at']), _instant(row['recorded_at'])
        except (KeyError, ValueError):
            continue
        if received >= cutoff or recorded >= cutoff:
            continue
        latest[row['match_key']] = row
    candidates = []
    for row in latest.values():
        if row.get('league') != league or row.get('season') != season or row.get('verified') != 1 or row.get('completed') != 1 or row.get('evidence_mode') != 'observed':
            continue
        if source is not None and row.get('source') != source:
            continue
        if exclude_match_key is not None and exclude_match_key in (row.get('match_key'), row.get('source_match_id')):
            continue
        if team not in (row.get('home_team'), row.get('away_team')):
            continue
        if team_id is not None:
            side_id = row.get('home_team_id') if row['home_team'] == team else row.get('away_team_id')
            if side_id != team_id:
                continue
        try:
            kickoff, received, reviewed = (_instant(row[key]) for key in ('kickoff_utc', 'received_at', 'reviewed_at'))
            if not kickoff < received <= reviewed < cutoff or kickoff >= target:
                continue
            if _season(row['season'], row['league'], kickoff) != season:
                continue
            if type(row['home_goals']) is not int or type(row['away_goals']) is not int or min(row['home_goals'], row['away_goals']) < 0:
                continue
            checked = clean_stats(json.loads(row['stats_json']))
            _metadata(json.loads(row['metadata_json']))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            continue
        candidates.append(dict(row, id=row['revision_id'], reviewed=True, completed=True, verified=True, stats=checked, metadata=json.loads(row['metadata_json'])))
    # Exact same league/day/teams is one fixture, never multiple training samples.
    # Conflicting kickoff, score, IDs or canonical statistics quarantine the group.
    fixtures = {}
    for row in candidates:
        key = (row['league'], row['season'], _instant(row['kickoff_utc']).date(), row['home_team'], row['away_team'])
        fixtures.setdefault(key, []).append(row)
    output = []
    for group in fixtures.values():
        signatures = {(_instant(row['kickoff_utc']), row['home_goals'], row['away_goals'],
                       row.get('home_team_id'), row.get('away_team_id'), row['stats_json']) for row in group}
        if len(signatures) != 1:
            continue
        output.append(max(group, key=lambda row: (_instant(row['received_at']), row['revision_id'])))
    return sorted(output, key=lambda row: (_instant(row['kickoff_utc']), row['revision_id']), reverse=True)
