"""Offline, rights-reviewed Openfootball adapter; never fetches data or guesses UTC."""
from collections import Counter
from datetime import date
import hashlib
import json
from pathlib import Path
import re

from .ingest import import_dataset, season_valid
from .store import connect, digest, timestamp, utc_now

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'fixtures/openfootball'
PROVIDER = 'openfootball/football.json'
LEAGUES = {
    'en.1': ('soccer_epl', 'English Premier League'),
    'es.1': ('soccer_spain_la_liga', 'Spain Primera División'),
    'it.1': ('soccer_italy_serie_a', 'Italian Serie A'),
    'de.1': ('soccer_germany_bundesliga', 'Deutsche Bundesliga'),
    'fr.1': ('soccer_france_ligue_one', 'French Ligue 1'),
}


class AdapterError(ValueError):
    def __init__(self, code, detail):
        self.code = code
        super().__init__(code + ': ' + detail)


def read_json(path):
    raw = Path(path).read_bytes()
    if len(raw) > 10 * 1024 * 1024:
        raise ValueError('reviewed local input exceeds 10 MiB')
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def local_team_id(league, name):
    """Exact-name league-scoped identity, explicitly NOT a provider/club-global ID."""
    return 'of-local-' + hashlib.sha256((league + '\0' + name).encode()).hexdigest()[:24]


def checked_name(value):
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > 200:
        raise AdapterError('invalid_team_name', 'nonempty exact source name required')
    return value


def ft_score(row):
    score = row.get('score')
    if isinstance(score, dict):
        if set(score) - {'ft', 'ht'}:
            raise AdapterError('unsupported_result_period', 'extra-time/penalty/unknown score fields require review')
        goals = score.get('ft')
    else:
        goals = score
    if goals is None:
        raise AdapterError('missing_final_result', 'no final score supplied')
    if not isinstance(goals, list) or len(goals) != 2 or any(type(g) is not int or g < 0 for g in goals):
        raise AdapterError('invalid_final_result', 'expected two nonnegative integer FT goals')
    if isinstance(score, dict) and 'ht' in score:
        half = score['ht']
        if (not isinstance(half, list) or len(half) != 2
                or any(type(g) is not int or g < 0 or g > goals[i] for i, g in enumerate(half))):
            raise AdapterError('invalid_final_result', 'half-time score conflicts with FT')
    return dict(home_goals=goals[0], away_goals=goals[1], period='FT90', status='final')


def match_date(row, season):
    value = row.get('date')
    try:
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
            raise ValueError()
        day = date.fromisoformat(value)
    except ValueError as exc:
        raise AdapterError('invalid_match_date', 'valid YYYY-MM-DD required') from exc
    # Calendar-only QC; this is never converted into a synthetic kickoff timestamp.
    if not season_valid(season, day):
        raise AdapterError('season_mismatch', 'date outside declared July-June season')
    return day


def confirmed_kickoff(row):
    value = row.get('time')
    if value in (None, ''):
        raise AdapterError('kickoff_time_missing', 'date-only record; no UTC time inferred')
    if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:\d{2})?', value):
        raise AdapterError('invalid_match_time', 'unsupported or invalid clock time')
    if not re.search(r'(Z|[+-]\d{2}:\d{2})$', value):
        # Check the clock independently so malformed HH:MM is not called a timezone issue.
        try:
            from datetime import time
            time.fromisoformat(value)
        except ValueError as exc:
            raise AdapterError('invalid_match_time', 'invalid clock time') from exc
        raise AdapterError('timezone_unverified', 'clock time supplied without an evidenced UTC offset')
    try:
        kickoff = timestamp(row['date'] + 'T' + value)
    except ValueError as exc:
        raise AdapterError('invalid_match_time', 'invalid timezone-aware clock time') from exc
    if kickoff >= timestamp(utc_now()):
        raise AdapterError('future_final_result', 'future fixture cannot have a final result')
    return kickoff.isoformat()


def convert(sample, registry, manifest):
    """Pure conversion of reviewed local rows; raw rejections remain outside matches."""
    if not all(isinstance(value, dict) for value in (sample, registry, manifest)):
        raise ValueError('sample, registry and manifest must be objects')
    if manifest.get('dataset_kind') != 'research':
        raise ValueError('Openfootball historical imports must be research')
    commit = sample.get('source_commit')
    if (sample.get('schema_version') != 1 or sample.get('provider') != PROVIDER
            or not isinstance(commit, str) or not re.fullmatch(r'[0-9a-f]{40}', commit)
            or registry.get('provider') != PROVIDER or registry.get('source_commit') != commit):
        raise ValueError('provider, schema or pinned source commit mismatch')
    if timestamp(sample['recorded_at']) > timestamp(utc_now()):
        raise ValueError('future local recording time')
    if not isinstance(registry.get('teams'), list) or not isinstance(sample.get('documents'), list):
        raise ValueError('team registry and documents must be lists')
    by_name = {}; ids = set()
    for team in registry['teams']:
        if not isinstance(team, dict):
            raise ValueError('team registry entry must be an object')
        name = checked_name(team.get('name'))
        if team.get('category') != 'men_first' or not isinstance(team.get('memberships'), list):
            raise ValueError('reviewed senior competition membership required')
        evidence = team.get('evidence', {})
        if evidence.get('source_commit') != commit:
            raise ValueError('team evidence commit mismatch')
        for member in team['memberships']:
            if not isinstance(member, dict):
                raise ValueError('membership must be an object')
            league, season = member.get('league'), member.get('season')
            code = next((code for code, spec in LEAGUES.items() if spec[0] == league), None)
            if (code is None or evidence.get('source_path') != f'{season}/{code}.json'
                    or team.get('source_team_id') != local_team_id(league, name)):
                raise ValueError('unreviewed team ID, league or evidence path')
            key = (league, season, name)
            if key in by_name:
                raise ValueError('duplicate or ambiguous team registry entry')
            by_name[key] = team
        if team['source_team_id'] in ids:
            raise ValueError('team ID collision or duplicate registry entry')
        ids.add(team['source_team_id'])

    matches = []; rejected = []; provenance = []; coverage = {}; used_teams = set()
    candidates = []
    for document in sample['documents']:
        if not isinstance(document, dict):
            raise ValueError('source document must be an object')
        path = document.get('path', '')
        parsed = re.fullmatch(r'(20\d{2}-\d{2})/(en\.1|es\.1|it\.1|de\.1|fr\.1)\.json', path)
        if not parsed:
            raise ValueError('unsupported source league/season path')
        season, code = parsed.groups(); league, title = LEAGUES[code]
        content = document.get('document', {})
        if not isinstance(content, dict):
            raise ValueError('source document content must be an object')
        expected_name = title + ' ' + season.replace('-', '/')
        if content.get('name') != expected_name:
            raise ValueError('source document league/season name mismatch')
        if (document.get('source_url') != f'https://github.com/{PROVIDER}/blob/{commit}/{path}'
                or not re.fullmatch(r'[0-9a-f]{40}', document.get('upstream_blob_sha', ''))):
            raise ValueError('source URL or blob provenance mismatch')
        rows = content.get('matches'); indexes = document.get('source_row_indexes')
        if (not isinstance(rows, list) or not isinstance(indexes, list) or len(rows) != len(indexes)
                or any(type(i) is not int or i < 0 for i in indexes) or len(set(indexes)) != len(indexes)):
            raise ValueError('invalid source row indexes')
        item = coverage.setdefault(league, dict(league=league, season=season, input_matches=0,
                                               converted_matches=0, quarantined_matches=0, reasons={}))
        if item['season'] != season:
            raise ValueError('pilot report accepts one season per league')
        for index, row in zip(indexes, rows):
            item['input_matches'] += 1
            ref = f'{path}#matches[{index}]'
            candidates.append((league, season, row, ref, document))
    # Conflicting duplicate fixtures are ALL held, rather than choosing a revision.
    groups = {}
    for league, season, row, ref, document in candidates:
        if isinstance(row, dict):
            key = (league, season, str(row.get('team1')), str(row.get('team2')))
            groups.setdefault(key, []).append(digest(row))
    seen = set(); seen_refs = set()
    for league, season, row, ref, document in candidates:
        meta = dict(source_commit=commit, source_url=document['source_url'],
                    upstream_blob_sha=document['upstream_blob_sha'], source_row=ref,
                    raw_row_sha256=digest(row), local_recorded_at=sample['recorded_at'])
        try:
            if ref in seen_refs:
                raise AdapterError('duplicate_source_row', 'source row repeated in input documents')
            seen_refs.add(ref)
            if not isinstance(row, dict) or set(row) - {'round', 'date', 'time', 'team1', 'team2', 'score'}:
                raise AdapterError('unsupported_match_fields', 'unknown match fields require adapter review')
            home, away = checked_name(row.get('team1')), checked_name(row.get('team2'))
            if home == away:
                raise AdapterError('same_team', 'home and away identities must differ')
            teams = []
            for name in (home, away):
                team = by_name.get((league, season, name))
                if team is None:
                    raise AdapterError('team_unmatched', 'exact reviewed name/membership missing; no fuzzy match')
                teams.append(team)
                used_teams.add(team['source_team_id'])
            match_date(row, season)
            result = ft_score(row)
            key = (league, season, home, away)
            if len(set(groups[key])) > 1:
                raise AdapterError('conflicting_duplicate_fixture', 'different records for the same home/away fixture')
            if key in seen:
                raise AdapterError('duplicate_fixture', 'identical fixture repeated')
            seen.add(key)
            kickoff = confirmed_kickoff(row)
            sid = 'of-match-' + digest([league, season, teams[0]['source_team_id'], teams[1]['source_team_id']])[:32]
            normalized = dict(source_match_id=sid, league=league, season=season, kickoff_utc=kickoff,
                              home_id=teams[0]['source_team_id'], away_id=teams[1]['source_team_id'],
                              home_name=home, away_name=away, result=result)
            matches.append(normalized)
            coverage[league]['converted_matches'] += 1
            provenance.append(dict(source_match_id=sid, **meta))
        except AdapterError as exc:
            rejected.append(dict(entity='match', source_row_id=ref, reason=str(exc),
                                 payload=dict(raw=row, provenance=meta, reason_code=exc.code,
                                              league=league, season=season)))
            item = coverage[league]; item['quarantined_matches'] += 1
            item['reasons'][exc.code] = item['reasons'].get(exc.code, 0) + 1
    foundation_teams = [{key: value for key, value in team.items() if key != 'evidence'}
                        for team in registry['teams'] if team['source_team_id'] in used_teams]
    batch = dict(manifest=manifest, teams=foundation_teams, matches=matches, odds=[], quarantine=rejected)
    report = dict(source_commit=commit, mode='research', input_matches=len(candidates),
                  leagues=list(coverage.values()), accepted_match_provenance=provenance,
                  missing=['xG/xGA', 'shots', 'AH/OU odds snapshots'],
                  team_id_policy=registry.get('id_policy'), accuracy_claim_permitted=False,
                  calibrated=False)
    if any(row['payload']['reason_code'] in ('kickoff_time_missing', 'timezone_unverified') for row in rejected):
        report['missing'].insert(0, 'verified kickoff timezone')
    return batch, report


def import_openfootball(sample_path, db_path, registry_path=FIXTURES / 'teams.json',
                        review_path=FIXTURES / 'rights-review.json'):
    """Import only an explicitly checksum-reviewed local sample, never a live URL."""
    sample, sample_hash = read_json(sample_path)
    registry, registry_hash = read_json(registry_path)
    review, _ = read_json(review_path)
    if not all(isinstance(value, dict) for value in (sample, registry, review)):
        raise ValueError('sample, registry and rights review must be objects')
    if (sample_hash != review.get('sample_sha256') or registry_hash != review.get('registry_sha256')
            or sample.get('source_commit') != review.get('source_commit')
            or review.get('provider') != PROVIDER or review.get('license') != 'CC0-1.0'):
        raise ValueError('sample/registry not covered by this pinned rights review')
    for filename, item in review['files'].items():
        if filename not in ('README.md', 'LICENSE.md'):
            raise ValueError('unexpected rights evidence file')
        raw = (Path(review_path).parent / 'evidence' / filename).read_bytes()
        blob = hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest()
        if (hashlib.sha256(raw).hexdigest() != item['sha256'] or blob != item['git_blob_sha1']
                or item['url'] != f'https://github.com/{PROVIDER}/blob/{review["source_commit"]}/{filename}'):
            raise ValueError('upstream rights evidence mismatch')
    if set(review['files']) != {'README.md', 'LICENSE.md'}:
        raise ValueError('both public-domain declaration and CC0 text required')
    manifest = {key: review[key] for key in ('license', 'evidence', 'rights_status', 'allowed_uses',
                                           'reviewed_by', 'reviewed_at', 'evidence_sha256')}
    manifest.update(source_id='openfootball-' + sample['source_commit'][:12] + '-' + sample_hash[:12],
                    name='Openfootball pinned 2025/26 five-league research pilot', dataset_kind='research')
    batch, report = convert(sample, registry, manifest)
    imported = import_dataset(batch, db_path)
    with connect(db_path, readonly=True) as con:
        run = con.execute('SELECT * FROM ingestion_runs WHERE id=?', (imported['run_id'],)).fetchone()
        source = manifest['source_id']
        report['accepted_matches'] = con.execute('SELECT COUNT(*) FROM matches WHERE source_id=?', (source,)).fetchone()[0]
        report['accepted_teams'] = con.execute('SELECT COUNT(*) FROM teams WHERE source_id=?', (source,)).fetchone()[0]
        held = con.execute("SELECT reason FROM quarantine WHERE run_id=? AND entity='match'", (run['id'],)).fetchall()
        report['quarantined_matches'] = len(held)
        report['quarantine_reasons'] = dict(Counter(row['reason'].split(':', 1)[0] for row in held))
        report['actual_ingested_at'] = run['ingested_at']
        for league in report['leagues']:
            league['accepted_matches'] = con.execute('SELECT COUNT(*) FROM matches WHERE source_id=? AND league=?',
                                                     (source, league['league'])).fetchone()[0]
    report.update(source_id=source, foundation_import=imported, source_recorded_at=sample['recorded_at'],
                  sample_sha256=sample_hash, rights_evidence_sha256=manifest['evidence_sha256'])
    return report
