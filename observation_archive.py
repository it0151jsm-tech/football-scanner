"""Opt-in forward observations for offline validation, never a bill database.

This collector makes no requests. Receipt and normalization clocks are measured
here, not supplied or reconstructed by callers. Unknown storage rights retain
only a digest and quarantine metadata. A checksum is an integrity binding, not
independent authentication of the provider or of the host clock.
"""
from contextlib import closing
from datetime import datetime, timezone, date
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
from urllib.parse import parse_qsl, urlsplit
import uuid

KINDS = ('events', 'score_history', 'xg_history', 'quotes', 'results', 'decisions')
SOURCE = 'value_bet_local_decisions'
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024
SECRET = re.compile(r'api.?key|authorization|password|access.?token|secret', re.I)


def _now():
    return datetime.now(timezone.utc)


def _utc(value):
    try:
        result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return result.astimezone(timezone.utc) if result.utcoffset() is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _strict_json(value):
    def pairs(items):
        result={}
        for key,item in items:
            if key in result:
                raise ValueError('duplicate_source_json_key')
            result[key]=item
        return result
    def invalid(_):
        raise ValueError('nonfinite_source_json')
    return json.loads(value,object_pairs_hook=pairs,parse_constant=invalid)


def _hash(value):
    return hashlib.sha256(value).hexdigest()


def _secret_fields(value):
    if isinstance(value, dict):
        return any(SECRET.search(str(key)) or str(key).lower() == 'token' or _secret_fields(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_secret_fields(item) for item in value)
    if isinstance(value,str) and value.startswith(('https://','http://')):
        return not _safe_url(value)
    return False


def _safe_url(url):
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password:
            return False
        return not any(SECRET.search(key) or key.lower() in ('token', 'key', 'x-apisports-key')
                       for key, _ in parse_qsl(parsed.query))
    except (ValueError, TypeError):
        return False


def _number(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def _approved(policy, at):
    permissions = policy.get('permissions') if isinstance(policy,dict) else None
    return (isinstance(policy, dict) and policy.get('rights_status') == 'approved'
            and not _secret_fields(policy)
            and isinstance(policy.get('reviewed_by'), str) and bool(policy['reviewed_by'].strip())
            and _safe_url(policy.get('rights_evidence_url'))
            and _utc(policy.get('reviewed_at')) is not None and _utc(policy['reviewed_at']) <= at
            and isinstance(permissions,dict) and permissions.get('store_raw') is True
            and permissions.get('model_research') is True)


def _policy(source, policies, at):
    policy = policies.get(source, {}) if isinstance(policies, dict) else {}
    return policy if _approved(policy, at) else None


def _validate(kind, row, at):
    if row.get('period') != 'FT90':
        return 'period_not_verified_ft90'
    if not isinstance(row.get('league'), str) or not row['league'].strip():
        return 'league_not_verified'
    season = row.get('season')
    if not isinstance(season, str) or not re.fullmatch(r'\d{4}', season) or int(season[2:]) != (int(season[:2])+1) % 100:
        return 'season_not_verified'
    if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('home_team', 'away_team')) or row['home_team'] == row['away_team']:
        return 'team_identity_missing_or_self_match'
    if kind in ('events', 'quotes', 'results'):
        kickoff = _utc(row.get('kickoff_utc'))
        if not isinstance(row.get('event_id'), str) or not row['event_id'].strip() or kickoff is None:
            return 'event_identity_or_timezone_missing'
        if kind != 'results' and kickoff <= at:
            return 'received_after_kickoff_research_only'
        if kind == 'results' and kickoff >= at:
            return 'result_before_kickoff'
    if kind in ('score_history', 'xg_history', 'results'):
        if row.get('status') != 'FT' or any(type(row.get(key)) is not int or row[key] < 0 for key in ('home_goals', 'away_goals')):
            return 'final_score_missing'
    if kind in ('score_history', 'xg_history'):
        if not isinstance(row.get('match_id'), str) or not row['match_id'].strip():
            return 'historical_match_identity_missing'
        when = _utc(row.get('match_time_utc'))
        if row.get('match_time_utc') is not None:
            if when is None or when >= at:
                return 'historical_match_time_unverified'
        else:
            try:
                day = date.fromisoformat(row.get('source_date'))
                if day > at.date():
                    return 'historical_match_after_receipt'
            except (ValueError, TypeError):
                return 'historical_match_time_unverified'
    if kind == 'xg_history' and any(not _number(row.get(key)) or row[key] < 0 for key in ('xg_home', 'xg_away')):
        return 'real_xg_missing'
    if kind == 'quotes':
        market = row.get('market')
        if market not in ('Asian Handicap', 'Over/Under'):
            return 'unsupported_market'
        if row.get('side') not in (('home', 'away') if market == 'Asian Handicap' else ('over', 'under')):
            return 'invalid_market_side'
        point = row.get('point')
        if (not _number(point) or not _number(point*4) or abs(point*4-round(point*4)) > 1e-9
                or (market == 'Over/Under' and point < 0) or not _number(row.get('odds')) or row['odds'] <= 1):
            return 'invalid_market_contract'
        if any(not isinstance(row.get(key), str) or not row[key].strip() for key in ('quote_id', 'bookmaker', 'bookmaker_key')):
            return 'bookmaker_or_quote_identity_missing'
        if _utc(row.get('source_updated_at')) is None:
            return 'provider_update_time_missing'
    return None


def _connect(path, readonly=False):
    con = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True) if readonly else sqlite3.connect(path, timeout=10)
    con.row_factory = sqlite3.Row
    return con


def _verify(con):
    if con.execute('PRAGMA integrity_check').fetchall()[0][0] != 'ok':
        raise ValueError('observation_integrity_failed')
    row = con.execute("SELECT value FROM observation_meta WHERE key='schema_version'").fetchone()
    if row is None or row[0] != '1':
        raise ValueError('not_an_observation_archive')


class ObservationArchive:
    def __init__(self, path, *, bill_db_path=None, source_policies=None):
        original = Path(path)
        if original.is_symlink():
            raise ValueError('observation_path_must_not_be_symlink')
        self.path = original.resolve()
        if bill_db_path is not None:
            bill = Path(bill_db_path).resolve()
            if self.path == bill or (self.path.exists() and bill.exists() and os.path.samefile(self.path, bill)):
                raise ValueError('observation_archive_must_be_separate_from_bills')
        self.source_policies = source_policies or {}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Refuse foreign SQLite files before issuing any schema mutation.
        if self.path.exists() and self.path.stat().st_size:
            with closing(_connect(self.path, readonly=True)) as con:
                _verify(con)
        else:
            try:
                handle = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(handle)
            except FileExistsError:
                pass
        with closing(_connect(self.path)) as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS observation_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                INSERT OR IGNORE INTO observation_meta VALUES('schema_version','1');
                CREATE TABLE IF NOT EXISTS observation_raw(
                    id INTEGER PRIMARY KEY, source TEXT NOT NULL, source_url TEXT NOT NULL,
                    received_at TEXT NOT NULL, raw_sha256 TEXT NOT NULL, body BLOB,
                    policy_json TEXT, quality_status TEXT NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS observation_rows(
                    id INTEGER PRIMARY KEY, kind TEXT NOT NULL, raw_id INTEGER,
                    source TEXT NOT NULL, received_at TEXT NOT NULL, normalized_json TEXT NOT NULL,
                    normalized_sha256 TEXT NOT NULL, policy_json TEXT NOT NULL,
                    quality_status TEXT NOT NULL, reason TEXT NOT NULL);
            """)
            for table in ('observation_raw', 'observation_rows'):
                for operation in ('UPDATE','DELETE'):
                    con.execute(f'''CREATE TRIGGER IF NOT EXISTS protect_{table}_{operation.lower()}
                        BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT,'observation evidence is append only'); END''')
            con.commit()

    @classmethod
    def from_environment(cls, *, bill_db_path=None):
        path = os.environ.get('VALUE_BET_OBSERVATION_DB', '').strip()
        if not path:
            return None
        try:
            policies = json.loads(os.environ.get('VALUE_BET_OBSERVATION_RIGHTS_JSON', '{}'))
        except ValueError:
            raise ValueError('invalid_observation_source_rights_json') from None
        if not isinstance(policies, dict):
            raise ValueError('invalid_observation_source_rights_json')
        return cls(path, bill_db_path=bill_db_path, source_policies=policies)

    def record_raw(self, source, url, body):
        """Capture actual response bytes now. No caller-supplied receipt exists."""
        at = _now()
        if not isinstance(source, str) or not source.strip() or not _safe_url(url):
            raise ValueError('source_or_public_url_invalid')
        if isinstance(body, str):
            body = body.encode('utf-8')
        if not isinstance(body, bytes) or len(body) > MAX_PAYLOAD_BYTES:
            raise ValueError('source_payload_type_or_size_invalid')
        policy = _policy(source, self.source_policies, at)
        reason = '' if policy else 'source_storage_rights_not_approved'
        # Registered credentials must never be stored even in provider errors.
        from credential_redaction import redact
        text = body.decode('utf-8', errors='replace')
        if redact(text) != text or re.search(r'(?i)(api.?key|authorization|password)\s*[=:]', text):
            reason = 'source_payload_may_contain_credentials'
        try:
            decoded = json.loads(text)
            if _secret_fields(decoded):
                reason = 'source_payload_may_contain_credentials'
        except ValueError:
            pass
        status = 'observed_source' if not reason else 'quarantine'
        with closing(_connect(self.path)) as con:
            cur = con.execute('''INSERT INTO observation_raw(source,source_url,received_at,raw_sha256,body,
                policy_json,quality_status,reason) VALUES(?,?,?,?,?,?,?,?)''',
                (source,url,at.isoformat(),_hash(body),body if not reason else None,
                 _json(policy) if policy else None,status,reason))
            con.commit()
            return {'raw_id':cur.lastrowid,'received_at':at.isoformat(),'raw_sha256':_hash(body),
                    'quality_status':status,'reason':reason}

    def record_normalized(self, kind, row, raw_id):
        """Append a parser observation now, bound to its immutable raw receipt.

        The parser must supply verified identity/season/FT90 fields. There is no
        promotion of old timestamps, score-to-xG substitution, or fuzzy matching.
        """
        return self.record_batch([(kind,row)],raw_id)[0]

    def record_batch(self, observations, raw_id):
        """Store already-parsed rows from one actual source snapshot atomically.

        The batch receipt is measured only after all rows exist in memory. Its
        actual write times are recorded separately. Both sides retain the same
        snapshot clock; no caller can supply or backdate the availability time.
        """
        if not isinstance(observations,list) or not observations or len(observations)>10000:
            raise ValueError('invalid_observation_batch')
        for kind,row in observations:
            if kind not in KINDS or kind == 'decisions' or not isinstance(row,dict):
                raise ValueError('invalid_observation_kind')
            if any(key in row for key in ('received_at','source','source_payload_sha256','data_mode','recorded_at','source_received_at','raw_payload_sha256')):
                raise ValueError('observation_clocks_and_provenance_are_measured_here')
            _json(row)
            if _secret_fields(row):
                raise ValueError('normalized_observation_contains_credential_fields')
        at = _now()
        results=[]
        with closing(_connect(self.path)) as con:
            raw = con.execute('SELECT * FROM observation_raw WHERE id=?',(raw_id,)).fetchone()
            if raw is None:
                raise ValueError('raw_source_receipt_not_found')
            for kind,row in observations:
                reason = raw['reason'] or _validate(kind,row,at)
                if _utc(raw['received_at']) > at:
                    reason = 'host_clock_moved_before_source_receipt'
                normalized = dict(row, source=raw['source'], received_at=at.isoformat(),
                    source_received_at=raw['received_at'], recorded_at=_now().isoformat(),
                    raw_payload_sha256=raw['raw_sha256'], data_mode='observed')
                if kind == 'quotes':
                    if _utc(row.get('source_updated_at')) is not None and _utc(row['source_updated_at']) > _utc(raw['received_at']):
                        reason = 'provider_update_after_actual_receipt'
                    if row.get('captured_at') is not None:
                        if _utc(row['captured_at']) is None or _utc(row['captured_at']) > at:
                            reason = 'source_capture_clock_unverified'
                        normalized['source_captured_at'] = row['captured_at']
                    normalized.update(captured_at=at.isoformat(),scanned_at=at.isoformat())
                    normalized.setdefault('quote_kind','observed')
                body = _json(normalized)
                status = 'eligible_observed' if not reason else 'quarantine'
                cur = con.execute('''INSERT INTO observation_rows(kind,raw_id,source,received_at,normalized_json,
                    normalized_sha256,policy_json,quality_status,reason) VALUES(?,?,?,?,?,?,?,?,?)''',
                    (kind,raw_id,raw['source'],at.isoformat(),body,_hash(body.encode()),raw['policy_json'] or '{}',status,reason or ''))
                results.append({'observation_id':cur.lastrowid,'received_at':at.isoformat(),
                    'quality_status':status,'reason':reason,'normalized_sha256':_hash(body.encode())})
            con.commit()
            return results

    def log_decision(self, event_id, model_version, observation_ids):
        """Freeze actual input membership before outcome/closing evidence exists."""
        if (not isinstance(event_id,str) or not event_id.strip() or not isinstance(model_version,str)
                or not re.fullmatch('[0-9a-f]{64}',model_version) or not isinstance(observation_ids,list)
                or not observation_ids or any(type(value) is not int or value < 1 for value in observation_ids)
                or len(set(observation_ids)) != len(observation_ids)):
            raise ValueError('invalid_decision_identity_model_or_inputs')
        at = _now()
        with closing(_connect(self.path)) as con:
            rows = [con.execute('SELECT * FROM observation_rows WHERE id=?',(value,)).fetchone() for value in observation_ids]
            if any(row is None or row['quality_status'] != 'eligible_observed' or _utc(row['received_at']) > at
                   or _utc(json.loads(row['normalized_json']).get('recorded_at')) > at
                   or row['kind'] in ('results','decisions') for row in rows):
                raise ValueError('decision_inputs_missing_unverified_or_outcome_data')
            events=[json.loads(row['normalized_json']) for row in rows if row['kind']=='events']
            if len(events) != 1 or events[0].get('event_id') != event_id or _utc(events[0]['kickoff_utc']) <= at:
                raise ValueError('decision_event_not_received_before_kickoff')
            for row in rows:
                observation=json.loads(row['normalized_json'])
                if observation.get('event_id',event_id) != event_id or observation.get('league') != events[0]['league']:
                    raise ValueError('decision_inputs_fixture_or_league_mismatch')
            policy={'rights_status':'approved','reviewed_by':'application owner authorized forward decision logging',
                'reviewed_at':at.isoformat(),'rights_evidence_url':'https://github.com/it0151jsm-tech/football-scanner',
                'permissions':{'store_raw':True,'model_research':True},'scope':'application-generated decision metadata only'}
            observation={'decision_id':uuid.uuid4().hex,'event_id':event_id,'decision_at':at.isoformat(),
                'received_at':at.isoformat(),'recorded_at':at.isoformat(),'source':SOURCE,'data_mode':'observed',
                'model_version':model_version,'input_observation_ids':observation_ids,
                'input_normalized_sha256':[row['normalized_sha256'] for row in rows]}
            body=_json(observation)
            cur=con.execute('''INSERT INTO observation_rows(kind,raw_id,source,received_at,normalized_json,
                normalized_sha256,policy_json,quality_status,reason) VALUES('decisions',NULL,?,?,?,?,?,'eligible_observed','')''',
                (SOURCE,at.isoformat(),body,_hash(body.encode()),_json(policy)))
            con.commit()
            return {'observation_id':cur.lastrowid,'decision_at':at.isoformat(),'decision_id':observation['decision_id']}

    def summary(self):
        with closing(_connect(self.path,readonly=True)) as con:
            _verify(con)
            raw=con.execute('SELECT quality_status,reason,COUNT(*) n FROM observation_raw GROUP BY quality_status,reason').fetchall()
            rows=con.execute('SELECT kind,quality_status,reason,COUNT(*) n FROM observation_rows GROUP BY kind,quality_status,reason').fetchall()
            return {'raw':[dict(row) for row in raw],'normalized':[dict(row) for row in rows],
                    'scope':'forward evidence; not a calibration or profitability claim'}

    def export_document(self):
        return export_database(self.path)


def export_database(path):
    """Read-only export; never discovers application SQLite files."""
    with closing(_connect(Path(path),readonly=True)) as con:
        con.execute('BEGIN')
        _verify(con)
        document={'schema_version':1,'data_mode':'observed','source_catalog':{},'evidence_payloads':{},
                  **{kind:[] for kind in KINDS},'exported_at':_now().isoformat(),
                  'scope':'explicit opt-in forward observer; receipt hashes do not independently authenticate clocks'}
        rows=con.execute("SELECT * FROM observation_rows WHERE quality_status='eligible_observed' ORDER BY id").fetchall()
        source_proof_by_id={}
        decisions=[]
        def append(stored,extra=None):
            body=stored['normalized_json']
            if _hash(body.encode()) != stored['normalized_sha256']:
                raise ValueError('normalized_evidence_checksum_failed')
            row=json.loads(body)
            if stored['raw_id'] is not None:
                raw=con.execute('SELECT * FROM observation_raw WHERE id=?',(stored['raw_id'],)).fetchone()
                if (raw is None or raw['body'] is None or _hash(raw['body'])!=raw['raw_sha256']
                        or row.get('raw_payload_sha256')!=raw['raw_sha256']
                        or row.get('source_received_at')!=raw['received_at']
                        or row.get('source')!=raw['source'] or _utc(row['received_at']) < _utc(raw['received_at'])):
                    raise ValueError('original_source_evidence_checksum_or_clock_failed')
            if extra:
                row.update(extra)
            policy=json.loads(stored['policy_json'])
            # Different approved policy revisions get separate catalog identities;
            # no newer review silently overwrites the older source evidence.
            source_key=stored['source']+'@'+_hash(stored['policy_json'].encode())[:16]
            row['source']=source_key
            proof=_json(row)
            digest=_hash(proof.encode())
            document['source_catalog'][source_key]=policy
            document['evidence_payloads'][digest]={'body':proof,'source':source_key,'received_at':row['received_at']}
            document[stored['kind']].append(dict(row,source_payload_sha256=digest))
            return digest
        for stored in rows:
            if stored['kind']=='decisions':
                decisions.append(stored)
            else:
                source_proof_by_id[stored['id']]=append(stored)
        for stored in decisions:
            original=json.loads(stored['normalized_json'])
            identities=original.get('input_observation_ids')
            normalized_hashes=original.get('input_normalized_sha256')
            if (not isinstance(identities,list) or not identities
                    or any(type(value) is not int or value not in source_proof_by_id for value in identities)
                    or len(set(identities))!=len(identities) or not isinstance(normalized_hashes,list)
                    or len(normalized_hashes)!=len(identities)):
                raise ValueError('decision_input_export_mapping_missing_or_invalid')
            originals={row['id']:row['normalized_sha256'] for row in rows if row['kind']!='decisions'}
            if [originals[value] for value in identities]!=normalized_hashes:
                raise ValueError('decision_input_normalized_evidence_mismatch')
            append(stored,{'input_source_payload_sha256':[source_proof_by_id[value] for value in identities]})
        document['quarantine_counts']={row['reason']:row['n'] for row in con.execute(
            "SELECT reason,COUNT(*) n FROM observation_rows WHERE quality_status!='eligible_observed' GROUP BY reason")}
        return document


def capture_raw_if_enabled(source, url, body, *, bill_db_path=None):
    """App hook: explicit opt-in only, generic failures never reveal source/key."""
    try:
        archive=ObservationArchive.from_environment(bill_db_path=bill_db_path)
        if archive is None:
            return {'status':'disabled'}
        return {'status':'recorded',**archive.record_raw(source,url,body)}
    except (ValueError,OSError,sqlite3.Error,TypeError):
        return {'status':'capture_error','reason':'observation_capture_failed_check_separate_path_and_rights'}


def record_provider_response(provider, url, response, *, bill_db_path=None):
    """Record the original bytes once, immediately after the existing request.

    No request is made, no response JSON is reconstructed, and cache hits must
    retain this original receipt instead of calling this hook again.
    """
    try:
        if not os.environ.get('VALUE_BET_OBSERVATION_DB','').strip():
            return {'status':'disabled'}
        body=response.content
        if not isinstance(body,bytes):
            return {'status':'capture_error','reason':'original_response_bytes_unavailable'}
        return capture_raw_if_enabled(provider,url,body,bill_db_path=bill_db_path)
    except (AttributeError,ValueError,OSError,sqlite3.Error,TypeError):
        return {'status':'capture_error','reason':'observation_capture_failed_check_separate_path_and_rights'}


def capture_completed_scan(sport_key, matches, rows, snapshots, score_history, xg_history, *, bill_db_path=None):
    """Normalize only raw-bound facts with explicit reviewed competition context.

    The app can always collect raw receipts. To normalize, each event must carry
    ``_observation_raw_id`` and ``_observation_context`` with verified season,
    FT90, identity and public evidence URLs. No season is guessed from kickoff.
    Stats frames can reference previously captured row-level observations using
    ``attrs['observation_ids']``; aggregates are never expanded into fake games.
    """
    try:
        archive=ObservationArchive.from_environment(bill_db_path=bill_db_path)
        if archive is None:
            return {'status':'disabled','normalized_n':0,'decisions_n':0}
        outcomes=[]
        for match in matches:
            raw_id=match.get('_observation_raw_id')
            context=match.get('_observation_context')
            if not isinstance(context,dict):
                context={}
            required=('season_evidence_url','identity_evidence_url','period_evidence_url')
            verified=(context.get('identity_verified') is True and context.get('period')=='FT90'
                and all(_safe_url(context.get(key)) for key in required))
            if type(raw_id) is not int or not verified:
                outcomes.append({'event_id':match.get('id'),'status':'pending_verified_context',
                    'reason':'raw_receipt_or_reviewed_identity_season_ft90_context_missing'})
                continue
            with closing(_connect(archive.path,readonly=True)) as con:
                raw=con.execute('SELECT * FROM observation_raw WHERE id=?',(raw_id,)).fetchone()
            if raw is None or raw['body'] is None:
                outcomes.append({'event_id':match.get('id'),'status':'quarantine',
                    'reason':'approved_original_payload_unavailable'})
                continue
            try:
                payload=_strict_json(raw['body'])
                events=[value for value in payload if isinstance(value,dict) and value.get('id')==match.get('id')]
            except (ValueError,TypeError):
                events=[]
            if len(events)!=1:
                outcomes.append({'event_id':match.get('id'),'status':'quarantine','reason':'raw_event_identity_ambiguous'})
                continue
            event=events[0]
            if (event.get('sport_key') != sport_key or any(event.get(key)!=match.get(key)
                    for key in ('id','home_team','away_team','commence_time'))):
                outcomes.append({'event_id':match.get('id'),'status':'quarantine','reason':'raw_event_identity_mismatch'})
                continue
            identity={'event_id':event['id'],'league':sport_key,'season':context.get('season'),
                'period':'FT90','home_team':event['home_team'],'away_team':event['away_team'],
                'kickoff_utc':event['commence_time'],
                **{key:context[key] for key in required}}
            observations=[('events',identity)]
            for book_index,book in enumerate(event.get('bookmakers',[])):
                for market_index,market in enumerate(book.get('markets',[])):
                    market_name={'spreads':'Asian Handicap','totals':'Over/Under'}.get(market.get('key'))
                    if market_name is None:
                        continue
                    from quote_provenance import quote_update
                    update=quote_update(book,market)
                    for outcome_index,outcome in enumerate(market.get('outcomes',[])):
                        side=({event['home_team']:'home',event['away_team']:'away'}.get(outcome.get('name'))
                              if market_name=='Asian Handicap' else {'over':'over','under':'under'}.get(str(outcome.get('name')).casefold()))
                        quote=dict(identity,quote_id=f"{raw['raw_sha256']}:{event['id']}:{book_index}:{market_index}:{outcome_index}",
                            market=market_name,side=side,point=outcome.get('point'),odds=outcome.get('price'),
                            bookmaker=book.get('title'),bookmaker_key=book.get('key'),
                            source_updated_at=update['source_updated_at'],
                            source_update_scope=update['source_update_scope'],
                            quote_kind='observed',snapshot_group_id=f"raw:{raw_id}:{book_index}:{market_index}")
                        matching=[row for row in rows if row.get('event_id')==event['id']
                            and row.get('market')==market_name and row.get('side')==side
                            and row.get('point')==quote['point'] and row.get('odds')==quote['odds']
                            and row.get('bookmaker_key')==quote['bookmaker_key']]
                        if len(matching)==1 and type(matching[0].get('odds_in_range')) in (bool,int):
                            quote['odds_in_range']=matching[0]['odds_in_range'] in (True,1)
                        observations.append(('quotes',quote))
            results=archive.record_batch(observations,raw_id)
            eligible=[item['observation_id'] for item in results if item['quality_status']=='eligible_observed']
            feature_ids=[]
            for frame in (score_history,xg_history):
                ids=getattr(frame,'attrs',{}).get('observation_ids',[])
                if isinstance(ids,list):
                    feature_ids.extend(value for value in ids if type(value) is int and value>0)
            decision=None
            if context.get('model_version') and eligible and results[0]['quality_status']=='eligible_observed':
                decision=archive.log_decision(event['id'],context['model_version'],list(dict.fromkeys(eligible+feature_ids)))
            outcomes.append({'event_id':event['id'],'status':'normalized' if eligible else 'quarantine',
                'normalized_n':len(eligible),'decisions_n':int(decision is not None),
                'feature_capture_status':'row_bound_observations' if feature_ids else 'missing_row_bound_feature_observations',
                'reasons':sorted({item['reason'] for item in results if item['reason']})})
        return {'status':'captured' if any(item.get('normalized_n') for item in outcomes) else 'raw_captured_pending_verified_context',
            'normalized_n':sum(item.get('normalized_n',0) for item in outcomes),
            'decisions_n':sum(item.get('decisions_n',0) for item in outcomes),'events':outcomes,
            'note':'Missing verified season/FT90/feature rows remain incomplete; raw capture is not a completed backtest cohort.'}
    except (ValueError,OSError,sqlite3.Error,TypeError,KeyError,AttributeError):
        return {'status':'capture_error','normalized_n':0,'decisions_n':0,
            'reason':'observation_normalization_failed_check_rights_context_and_row_evidence'}
