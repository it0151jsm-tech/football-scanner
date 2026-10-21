"""Append-only, offline resolution using explicit offsets in reviewed source records."""
from collections import Counter
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re

from .ingest import import_dataset
from .openfootball import FIXTURES, LEAGUES, ft_score, local_team_id, read_json
from .store import connect, digest, timestamp, utc_now

EVIDENCE = FIXTURES / 'timezone'
TITLES = {
    'en.1': 'England | Premier League 2025/26',
    'es.1': 'Spain | Primera División 2025/26',
    'it.1': 'Italy | Serie A 2025/26',
    'de.1': 'Germany | Bundesliga 2025/26',
    'fr.1': 'France | Ligue 1 2025/26',
}
SOURCES = {
    'en.1': ('openfootball/football.json', '2025-26/en.1-full.json'),
    'de.1': ('openfootball/football.json', '2025-26/de.1-full.json'),
    'es.1': ('openfootball/espana', '2025-26/1-liga-full.txt'),
    'it.1': ('openfootball/italy', '2025-26/1-seriea-full.txt'),
    'fr.1': ('openfootball/europe', 'france/2025-26_fr1-full.txt'),
}
MONTHS = {m: i + 1 for i, m in enumerate('Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec'.split())}


def explicit_utc(day, clock):
    """Interpret only a literal per-record UTC offset; no zone/country lookup."""
    match = re.fullmatch(r'([01]\d|2[0-3]):([0-5]\d) UTC([+-])(\d{1,2})(?::([0-5]\d))?', clock)
    if not match:
        raise ValueError('missing_or_invalid_explicit_offset')
    hour, minute, sign, off_hour, off_minute = match.groups()
    delta = timedelta(hours=int(off_hour), minutes=int(off_minute or 0))
    if delta > timedelta(hours=14):
        raise ValueError('offset_outside_reviewed_range')
    offset = timezone(delta if sign == '+' else -delta)
    local = datetime.combine(date.fromisoformat(day), datetime.min.time()).replace(
        hour=int(hour), minute=int(minute), tzinfo=offset)
    return local.astimezone(timezone.utc).isoformat()


def parse_proof(document, row):
    """Extract timestamp and identity from literal, small source excerpts."""
    code = document['code']
    if code not in SOURCES or (document['repository'], document['path']) != SOURCES[code]:
        raise ValueError('unreviewed_source_document')
    if document['source_title'].removeprefix('= ') != TITLES[code]:
        raise ValueError('league_or_season_evidence_mismatch')
    raw = row['raw_excerpt']
    if (type(row['source_line']) is not int or row['source_line'] < 1
            or row['source_url'] != f'https://github.com/{document["repository"]}/blob/{document["commit"]}/{document["path"]}#L{row["source_line"]}'):
        raise ValueError('invalid_proof_source_reference')
    if document['format'] == 'json_prefix':
        # Exact first six fields from the original JSON object; closing brace is
        # parser framing only. The unchanged raw excerpt is retained as evidence.
        fields = json.loads(raw.strip().removesuffix(',') + '}')
        if set(fields) != {'round', 'date', 'time', 'team1', 'team2', 'score'}:
            raise ValueError('unexpected_json_evidence_fields')
        day, clock = fields['date'], fields['time']
        home, away = fields['team1'], fields['team2']
        result = ft_score(fields)
    elif document['format'] == 'football_txt':
        lines = raw.splitlines()
        if len(lines) != 2:
            raise ValueError('invalid_txt_evidence')
        timing = re.fullmatch(r'(Mon|Tue|Wed|Thu|Fri|Sat|Sun) ([A-Z][a-z]{2}) (\d{1,2}) (.+?) @ .+', lines[0])
        score = re.fullmatch(r'  (.+?) v (.+?)  (\d+)-(\d+)(?: \((\d+)-(\d+)\))?', lines[1])
        if not timing or not score:
            raise ValueError('invalid_txt_match_fields')
        weekday, month, day_number, clock = timing.groups()
        # Year comes from the explicit season header, never today's calendar.
        month_number = MONTHS[month]
        year = 2025 if month_number >= 7 else 2026
        dt = date(year, month_number, int(day_number))
        if dt.weekday() != ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].index(weekday):
            raise ValueError('weekday_date_conflict')
        day = dt.isoformat()
        home, away, hg, ag, hh, ah = score.groups()
        parsed_score = {'ft': [int(hg), int(ag)]}
        if hh is not None:
            parsed_score['ht'] = [int(hh), int(ah)]
        result = ft_score({'score': parsed_score})
    else:
        raise ValueError('unsupported_evidence_format')
    utc = explicit_utc(day, clock)
    if timestamp(utc) >= timestamp(utc_now()):
        raise ValueError('evidence_describes_future_final_result')
    if row.get('confirmed_utc') != utc or row.get('raw_excerpt_sha256') != hashlib.sha256(raw.encode()).hexdigest():
        raise ValueError('evidence_utc_or_checksum_conflict')
    return dict(date=day, clock=clock, home=home, away=away, result=result, kickoff_utc=utc)


def load_evidence(folder=EVIDENCE):
    folder = Path(folder)
    review, _ = read_json(folder / 'review.json')
    values = {}
    if (review.get('mode') != 'research' or review.get('rights_status') != 'verified'
            or review.get('verification_basis') != 'provider_explicit_per_match_offset'):
        raise ValueError('unreviewed_timezone_basis')
    if timestamp(review['reviewed_at']) > timestamp(utc_now()):
        raise ValueError('future_evidence_review')
    if set(review['files']) != {'collection.json', 'identity-map.json', 'upstream-rights.json'}:
        raise ValueError('incomplete_timezone_review')
    for name, checksum in review['files'].items():
        value, actual = read_json(folder / name)
        if actual != checksum:
            raise ValueError('timezone_evidence_checksum_mismatch')
        values[name] = value
    collection = values['collection.json']; rights = values['upstream-rights.json']
    received = timestamp(collection['received_at'])
    if received > timestamp(review['reviewed_at']) or received > timestamp(utc_now()):
        raise ValueError('invalid_actual_evidence_receipt')
    if rights['received_at'] != collection['received_at']:
        raise ValueError('rights_receipt_mismatch')
    snapshots = {}
    for item in rights['snapshots']:
        raw = item['text'].encode('utf-8')
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if blob != item['git_blob_sha1'] or item['url'] != f'https://github.com/{item["repository"]}/blob/{item["commit"]}/{item["path"]}':
            raise ValueError('rights_source_or_blob_mismatch')
        key = (item['repository'], item['commit'], item['path'])
        if key in snapshots:
            raise ValueError('duplicate_rights_snapshot')
        snapshots[key] = item['text']
    original, _ = read_json(FIXTURES / 'rights-review.json')
    for doc in collection['documents']:
        repo, commit = doc['repository'], doc['commit']
        if not re.fullmatch(r'[0-9a-f]{40}', commit) or not re.fullmatch(r'[0-9a-f]{40}', doc['blob_sha']):
            raise ValueError('unpinned_source_evidence')
        if repo == 'openfootball/football.json':
            if commit != original['source_commit']:
                raise ValueError('original_provider_commit_mismatch')
            continue  # Original public-domain review remains immutable and checked on import.
        license_text = snapshots.get((repo, commit, 'LICENSE.md'), '')
        declaration = snapshots.get((repo, commit, 'README.md'), '').lower()
        if 'CC0 1.0 Universal' not in license_text or 'public domain' not in declaration or 'data' not in declaration:
            raise ValueError('source_rights_not_verified')
    return collection, values['identity-map.json'], rights, review


def prepare_revision(sample, registry, collection, mapping, manifest, original_rows):
    """Join reviewed exact identities; incomplete/conflicting proofs remain held."""
    if manifest['dataset_kind'] != 'research':
        raise ValueError('historical_timezone_revision_must_be_research')
    originals = {}
    for doc in sample['documents']:
        code = Path(doc['path']).stem
        for index, raw in zip(doc['source_row_indexes'], doc['document']['matches']):
            originals[f'{doc["path"]}#matches[{index}]'] = (code, raw)
    if len(originals) != 15:
        raise ValueError('only_the_original_15_game_pilot_is_approved')
    if len({m['target_ref'] for m in mapping['matches']}) != len(mapping['matches']):
        raise ValueError('duplicate_identity_review_target')
    by_target = {m['target_ref']: m for m in mapping['matches']}
    if set(by_target) - set(originals):
        raise ValueError('identity_review_expands_original_scope')
    proofs = {}
    for doc in collection['documents']:
        for row in doc['rows']:
            key = (doc['code'], row['source_line'])
            if key in proofs:
                raise ValueError('duplicate_evidence_row')
            proofs[key] = (doc, row)
    registry_by_name = {(m['league'], m['season'], t['name']): t for t in registry['teams'] for m in t['memberships']}
    matches = []; decisions = []; used = set()
    for ref, (code, original) in originals.items():
        decision = dict(target_ref=ref, status='still_quarantined')
        try:
            old = original_rows.get(ref)
            if old is None:
                raise ValueError('original_quarantine_record_missing')
            payload = json.loads(old['payload_json'])['adapter_rejection']
            origin = payload['provenance']
            if (digest(payload['raw']) != digest(original) or old['reason'].split(':', 1)[0] != 'timezone_unverified'
                    or origin['source_commit'] != sample['source_commit'] or origin['source_row'] != ref
                    or origin['raw_row_sha256'] != digest(original)):
                raise ValueError('original_quarantine_payload_or_reason_mismatch')
            identity = by_target.get(ref)
            if identity is None:
                raise ValueError('no_reviewed_identity_evidence')
            key = (code, identity['source_line'])
            if key in used:
                raise ValueError('evidence_reused_for_another_fixture')
            if key not in proofs:
                raise ValueError('timezone_proof_missing')
            doc, proof = proofs[key]
            if proof['received_at'] != collection['received_at']:
                raise ValueError('proof_receipt_conflict')
            facts = parse_proof(doc, proof)
            league = LEAGUES[code][0]
            if (identity['original_home'], identity['original_away']) != (original['team1'], original['team2']):
                raise ValueError('original_team_identity_mismatch')
            if (identity['evidence_home'], identity['evidence_away']) != (facts['home'], facts['away']):
                raise ValueError('reviewed_evidence_team_mismatch')
            if facts['date'] != original['date'] or facts['result'] != ft_score(original):
                raise ValueError('date_or_final_score_conflict')
            teams = [registry_by_name[(league, '2025-26', original[k])] for k in ('team1', 'team2')]
            for team in teams:
                if team['source_team_id'] != local_team_id(league, team['name']):
                    raise ValueError('team_registry_id_mismatch')
            sid = 'of-match-' + digest([league, '2025-26', teams[0]['source_team_id'], teams[1]['source_team_id']])[:32]
            row = dict(source_match_id=sid, league=league, season='2025-26', kickoff_utc=facts['kickoff_utc'],
                       home_id=teams[0]['source_team_id'], away_id=teams[1]['source_team_id'],
                       home_name=original['team1'], away_name=original['team2'], result=ft_score(original))
            matches.append(row); used.add(key)
            url = f'https://github.com/{doc["repository"]}/blob/{doc["commit"]}/{doc["path"]}#L{proof["source_line"]}'
            decision.update(status='verified_pending_import', source_match_id=sid, league=league,
                            original_quarantine_id=old['id'], original_quarantine_sha256=digest(dict(old)),
                            provider='Openfootball', independent_provider=False,
                            source_url=url, source_commit=doc['commit'], source_blob_sha=doc['blob_sha'],
                            source_clock=facts['clock'], confirmed_utc=facts['kickoff_utc'],
                            raw_excerpt_sha256=hashlib.sha256(proof['raw_excerpt'].encode()).hexdigest(),
                            evidence_received_at=collection['received_at'])
        except (ValueError, KeyError, TypeError) as exc:
            decision['reason'] = str(exc)
        decisions.append(decision)
    batch = dict(manifest=manifest, teams=[], matches=matches, odds=[], quarantine=[],
                 provenance=dict(adapter='openfootball-timezone-revision-v1', mode='research',
                                 basis='provider_explicit_per_match_offset',
                                 collection=collection, identity_review=mapping, decisions=decisions))
    return batch, decisions


def import_timezone_revision(db_path, evidence_folder=EVIDENCE):
    collection, mapping, rights, review = load_evidence(evidence_folder)
    sample, sample_hash = read_json(FIXTURES / 'sample.json')
    registry, registry_hash = read_json(FIXTURES / 'teams.json')
    old_review, _ = read_json(FIXTURES / 'rights-review.json')
    if sample_hash != old_review['sample_sha256'] or registry_hash != old_review['registry_sha256']:
        raise ValueError('original_sample_or_registry_changed')
    source = 'openfootball-' + sample['source_commit'][:12] + '-' + sample_hash[:12]
    with connect(db_path, readonly=True) as con:
        found = con.execute('SELECT * FROM sources WHERE id=?', (source,)).fetchone()
        if not found:
            raise ValueError('import_original_quarantine_batch_first')
        manifest = json.loads(found['manifest_json'])
        rows = [dict(r) for r in con.execute('''SELECT q.* FROM quarantine q JOIN ingestion_runs i ON i.id=q.run_id
                    WHERE i.source_id=? AND q.entity='match' ORDER BY q.id''', (source,))]
        if len({row['source_row_id'] for row in rows}) != len(rows):
            raise ValueError('ambiguous_original_quarantine_history')
    batch, decisions = prepare_revision(sample, registry, collection, mapping, manifest,
                                         {r['source_row_id']: r for r in rows})
    batch['provenance'].update(rights_snapshots=rights, checksum_review=review)
    result = import_dataset(batch, db_path)
    expected_rows = {row['source_match_id']: row for row in batch['matches']}
    with connect(db_path, readonly=True) as con:
        current = [dict(r) for r in con.execute('SELECT * FROM quarantine WHERE id IN (' + ','.join('?' for _ in rows) + ') ORDER BY id',
                                                [r['id'] for r in rows])]
        if current != rows:
            raise ValueError('original_quarantine_history_changed')
        ingested_at = con.execute('SELECT ingested_at FROM ingestion_runs WHERE id=?', (result['run_id'],)).fetchone()[0]
        for decision in decisions:
            if decision['status'] != 'verified_pending_import':
                continue
            expected = expected_rows[decision['source_match_id']]
            accepted = con.execute('''SELECT m.id,m.kickoff_utc FROM matches m
                JOIN observations o ON o.match_id=m.id JOIN match_results r ON r.observation_id=o.id
                WHERE m.source_id=? AND m.source_match_id=? AND o.payload_sha256=?
                AND r.home_goals=? AND r.away_goals=? AND r.period='FT90' AND r.status='final' ''',
                (source, decision['source_match_id'], digest(expected), expected['result']['home_goals'],
                 expected['result']['away_goals'])).fetchone()
            if accepted and accepted['kickoff_utc'] == decision['confirmed_utc']:
                decision.update(status='accepted_revision', match_id=accepted['id'])
            else:
                decision.update(status='still_quarantined', reason='foundation_rejected_revision_or_identity_conflict')
    passed = sum(d['status'] == 'accepted_revision' for d in decisions)
    return dict(mode='research', original_scope=15, passed=passed, unresolved=15-passed,
                original_quarantine_preserved=True, historical_quarantine_rows=len(rows),
                unresolved_reasons=dict(Counter(d['reason'] for d in decisions if d['status'] == 'still_quarantined')),
                verification_basis='provider_explicit_per_match_offset', independent_provider_verification=False,
                evidence_received_at=collection['received_at'], revision_ingested_at=ingested_at,
                source_id=source, import_result=result, decisions=decisions,
                accuracy_claim_permitted=False, calibrated=False)
