"""Bounded optional-provider requests, isolated from the Value Bet bill database.

This ledger stores only allowlisted status metadata and a SHA-256 scope derived
from league/season/credential fingerprint/config revision. It never stores keys,
provider error bodies, requests, or response data. A disabled switch does no I/O.
The file coordinates processes sharing this path, not independent Cloud apps.
"""
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
from urllib.parse import quote
import uuid

from api_football_data import LEAGUE_IDS

UTC = timezone.utc
DEFAULT_LEDGER = Path('data/api_football_backoff.db')
_ALLOWED_TABLES = {'api_football_backoff', 'api_football_backoff_meta'}
_EXPECTED_COLUMNS = {'scope', 'sport_key', 'season', 'provider_state', 'http_status',
    'source_received_at', 'source_requested_at', 'blocked_until', 'lease_token', 'leased_until', 'updated_at'}
_STATES = {'success', 'plan_unsupported', 'http_error', 'provider_error', 'empty',
           'invalid_response', 'download_error', 'partial', 'unknown'}


@dataclass(frozen=True)
class BackoffPolicy:
    plan_seconds: int = 86400
    auth_seconds: int = 86400
    throttle_seconds: int = 3600
    failure_seconds: int = 900
    lease_seconds: int = 60


@dataclass(frozen=True)
class Reservation:
    scope: str
    token: str
    ledger_path: Path
    policy: BackoffPolicy


@dataclass(frozen=True)
class RequestDecision:
    allowed: bool
    diagnostic: dict
    reservation: Reservation | None = None


def api_football_enabled(value=None):
    """Preserve configured-provider compatibility; explicit false stops requests."""
    if value is None:
        value = os.environ.get('VALUE_BET_API_FOOTBALL_ENABLED', '1')
    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {'1', 'true', 'yes', 'on'}


def policy_from_environment():
    def seconds(name, default, minimum, maximum):
        try:
            value = int(os.environ.get(name, str(default)))
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, value))
    return BackoffPolicy(
        plan_seconds=seconds('VALUE_BET_API_FOOTBALL_PLAN_BACKOFF_SECONDS', 86400, 3600, 2592000),
        auth_seconds=seconds('VALUE_BET_API_FOOTBALL_AUTH_BACKOFF_SECONDS', 86400, 900, 2592000),
        throttle_seconds=seconds('VALUE_BET_API_FOOTBALL_QUOTA_BACKOFF_SECONDS', 3600, 60, 604800),
        failure_seconds=seconds('VALUE_BET_API_FOOTBALL_FAILURE_BACKOFF_SECONDS', 900, 60, 86400),
        lease_seconds=seconds('VALUE_BET_API_FOOTBALL_LEASE_SECONDS', 60, 30, 600))


def _instant(value=None):
    if value is None:
        return datetime.now(UTC)
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        if parsed.tzinfo is None:
            return None
        parsed = parsed.astimezone(UTC)
        return parsed if math.isfinite(parsed.timestamp()) else None
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _iso(timestamp):
    return datetime.fromtimestamp(timestamp, UTC).isoformat()


def _scope(sport_key, season, api_key, revision):
    fingerprint = hashlib.sha256(api_key.encode('utf-8')).hexdigest()
    revision_hash = hashlib.sha256(str(revision).encode('utf-8')).hexdigest()
    body = json.dumps(['api-football', sport_key, season, fingerprint, revision_hash], separators=(',', ':'))
    return hashlib.sha256(body.encode('utf-8')).hexdigest()


def _check_existing(path):
    """A foreign SQLite database is read-only inspected and never migrated."""
    if path.is_symlink():
        raise ValueError('Dedicated ledger required')
    if not path.exists() or path.stat().st_size == 0:
        return
    uri = 'file:' + quote(str(path.resolve()), safe='/') + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=3)) as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables != _ALLOWED_TABLES:
            raise ValueError('Dedicated ledger required')
        marker = connection.execute('SELECT version FROM api_football_backoff_meta').fetchall()
        columns = {row[1] for row in connection.execute('PRAGMA table_info(api_football_backoff)')}
        if marker != [(1,)] or columns != _EXPECTED_COLUMNS:
            raise ValueError('Unsupported ledger')


def _connect(path):
    _check_existing(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=3, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute('BEGIN IMMEDIATE')
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables and tables != _ALLOWED_TABLES:
            raise ValueError('Dedicated ledger required')
        if not tables:
            connection.execute('CREATE TABLE api_football_backoff_meta(version INTEGER NOT NULL)')
            connection.execute('INSERT INTO api_football_backoff_meta VALUES (1)')
            connection.execute('''CREATE TABLE api_football_backoff(
                scope TEXT PRIMARY KEY, sport_key TEXT NOT NULL, season INTEGER NOT NULL,
                provider_state TEXT NOT NULL, http_status INTEGER, source_received_at TEXT, source_requested_at TEXT,
                blocked_until REAL NOT NULL DEFAULT 0, lease_token TEXT,
                leased_until REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)''')
        return connection
    except Exception:
        connection.rollback()
        connection.close()
        raise


def _blocked(reason, *, sport_key=None, season=None, **extra):
    return RequestDecision(False, {
        'provider': 'api-football', 'state': 'not_called', 'status': 'not_called',
        'reason': reason, 'reason_code': reason, 'request_attempted': False,
        'used_for_form': False, 'sport_key': sport_key, 'season': season, **extra})


def reserve_request(sport_key, season, api_key, *, enabled=None, ledger_path=None,
                    revision=None, now=None, policy=None):
    """Atomically authorize one actual fetch; call outside view rendering only.

    A successful cached fetch needs no reservation. Put this guard inside the
    uncached provider-call boundary. A crash leaves at most a short pending lease.
    """
    if not api_football_enabled(enabled):
        return _blocked('disabled_by_owner', sport_key=sport_key, season=season)
    if sport_key not in LEAGUE_IDS:
        return _blocked('unsupported_league', sport_key=sport_key, season=season)
    if not isinstance(api_key, str) or not api_key.strip():
        return _blocked('not_configured', sport_key=sport_key, season=season)
    if not isinstance(season, int) or isinstance(season, bool) or not 2000 <= season <= 2100:
        return _blocked('invalid_season', sport_key=sport_key, season=season)
    current = _instant(now)
    if current is None:
        return _blocked('invalid_clock', sport_key=sport_key, season=season)
    policy = policy or policy_from_environment()
    if not isinstance(policy, BackoffPolicy) or any(
            not isinstance(value, int) or isinstance(value, bool) or not 0 < value <= 2592000
            for value in (policy.plan_seconds, policy.auth_seconds, policy.throttle_seconds,
                          policy.failure_seconds, policy.lease_seconds)):
        return _blocked('invalid_backoff_policy', sport_key=sport_key, season=season)
    path = Path(ledger_path or os.environ.get('VALUE_BET_API_FOOTBALL_LEDGER', DEFAULT_LEDGER))
    revision = os.environ.get('VALUE_BET_API_FOOTBALL_BACKOFF_REVISION', '') if revision is None else revision
    scope = _scope(sport_key, season, api_key, revision)
    timestamp = current.timestamp()
    connection = None
    try:
        connection = _connect(path)
        row = connection.execute('SELECT * FROM api_football_backoff WHERE scope=?', (scope,)).fetchone()
        if row is not None and row['blocked_until'] > timestamp:
            state = row['provider_state'] if row['provider_state'] in _STATES else 'unknown'
            reason = ('plan_backoff' if state == 'plan_unsupported' else
                      'auth_backoff' if row['http_status'] in (401, 403) else
                      'quota_backoff' if row['http_status'] == 429 else 'failure_backoff')
            connection.commit()
            return _blocked(reason, sport_key=sport_key, season=season,
                provider_state=state, http_status=row['http_status'],
                received_at=row['source_received_at'], requested_at=row['source_requested_at'],
                next_retry_at=_iso(row['blocked_until']),
                backoff_active=True)
        if row is not None and row['leased_until'] > timestamp:
            connection.commit()
            return _blocked('request_in_progress', sport_key=sport_key, season=season,
                next_retry_at=_iso(row['leased_until']), backoff_active=True)
        token = uuid.uuid4().hex
        connection.execute('''INSERT INTO api_football_backoff(
            scope,sport_key,season,provider_state,blocked_until,lease_token,leased_until,updated_at)
            VALUES(?,?,?,'unknown',0,?,?,?) ON CONFLICT(scope) DO UPDATE SET
            lease_token=excluded.lease_token, leased_until=excluded.leased_until,updated_at=excluded.updated_at''',
            (scope, sport_key, season, token, timestamp + policy.lease_seconds, current.isoformat()))
        connection.commit()
        return RequestDecision(True, {'provider': 'api-football', 'state': 'ready',
            'request_attempted': False, 'reason': 'request_reserved'}, Reservation(scope, token, path, policy))
    except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
        if connection is not None:
            connection.rollback()
        return _blocked('backoff_store_unavailable', sport_key=sport_key, season=season)
    finally:
        if connection is not None:
            connection.close()


def complete_request(reservation, diagnostic, *, now=None):
    """Persist a bounded result for the matching lease; never retain raw errors.

    Cooldowns start at the actual source receipt/request time, never at the time a
    cached diagnostic is read. Old completion tokens cannot clear a newer lease.
    Returns false on unavailable storage so callers can report the limitation.
    """
    if not isinstance(reservation, Reservation):
        return False
    current = _instant(now)
    if current is None:
        return False
    report = diagnostic if isinstance(diagnostic, dict) else {}
    state = report.get('provider_state') if report.get('state') == 'cache' else report.get('state')
    if state in ('ok', None) and report.get('status') == 'ok':
        state = 'success'
    if not isinstance(state, str) or state not in _STATES:
        state = 'unknown'
    http_status = report.get('http_status')
    if not isinstance(http_status, int) or isinstance(http_status, bool) or not 100 <= http_status <= 599:
        http_status = None
    if state == 'success' and (report.get('rejected_rows') or report.get('conflicting_rows')):
        state = 'partial'
    policy = reservation.policy
    cooldown = (0 if state == 'success' else
                policy.plan_seconds if state == 'plan_unsupported' else
                policy.auth_seconds if http_status in (401, 403) else
                policy.throttle_seconds if http_status == 429 else policy.failure_seconds)
    receipt = _instant(report.get('received_at')) if report.get('received_at') is not None else None
    requested = _instant(report.get('requested_at')) if report.get('requested_at') is not None else None
    source_time = receipt or requested
    # Retain the real old receipt; do not restart a cooldown when cache is read.
    basis = min(source_time.timestamp(), current.timestamp()) if source_time is not None else current.timestamp()
    blocked_until = basis + cooldown if cooldown else 0
    connection = None
    try:
        connection = _connect(reservation.ledger_path)
        result = connection.execute('''UPDATE api_football_backoff SET provider_state=?,http_status=?,
            source_received_at=?,source_requested_at=?,blocked_until=?,lease_token=NULL,leased_until=0,updated_at=?
            WHERE scope=? AND lease_token=?''', (state, http_status,
            receipt.isoformat() if receipt is not None else None, requested.isoformat() if requested is not None else None, blocked_until,
            current.isoformat(), reservation.scope, reservation.token))
        connection.commit()
        return result.rowcount == 1
    except (OSError, ValueError, TypeError, sqlite3.Error, OverflowError):
        if connection is not None:
            connection.rollback()
        return False
    finally:
        if connection is not None:
            connection.close()
