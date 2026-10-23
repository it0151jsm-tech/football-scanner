"""Replay the evidence behind saved 5/10-match form, without estimating a model.

Inputs are supplied histories only. Nothing is downloaded, changed, capped or
written. Review flags are explanatory, never probabilities or betting filters.
"""
from copy import deepcopy
import math
import re

import pandas as pd

from domestic_data import safe_match_team, season_for_kickoff
from history_data import validate_rows


_HIGH_MEAN_GF = 5.0
_HIGH_MATCH_GF = 8
_NAMESPACE = re.compile(r'^__(?:domestic|personal|foreign)__\[[^\]]+\]::(.*)$', re.S)


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _plain_name(name):
    match = _NAMESPACE.match(str(name))
    return match.group(1) if match else str(name)


def _season_ok(day, season):
    if not isinstance(season, str) or not re.fullmatch(r'\d{4}', season):
        return False
    return season_for_kickoff(pd.Timestamp(day).tz_localize('UTC')) == season


def _source_info(attrs, side, sport, season):
    resolution = attrs.get('domestic_sources', {}).get(side, {})
    report = attrs.get('domestic_source_reports', {}).get(sport, {})
    source = report.get('source', {})
    sources = source.get('sources', []) or attrs.get('history_diagnostics', {}).get('sources', [])
    sources = [s for s in sources if (not s.get('domestic_sport_key') or s.get('domestic_sport_key') == sport)
               and (not s.get('season') or str(s['season']) == str(season))]
    source_urls = list(dict.fromkeys(s['url'] for s in sources if isinstance(s.get('url'), str)))
    definition = resolution.get('source_diagnostics', {}).get(sport, {})
    code = resolution.get('csv_code') or definition.get('csv_code')
    provider = 'Football-Data CSV' if code or any('football-data.co.uk/' in url for url in source_urls) else 'ไม่ระบุผู้ให้บริการ'
    return dict(sport_key=sport or None, league=definition.get('label') or sport or 'ไม่ระบุลีก',
                csv_code=code, source=provider, source_urls=source_urls,
                provenance_status='source_url_recorded' if source_urls else 'source_url_not_recorded')


def _original_name(value, attrs, sport):
    for resolution in attrs.get('domestic_sources', {}).values():
        if resolution.get('sport_key') == sport and resolution.get('input_name') == value:
            return resolution.get('csv_name') or str(value)
    return _plain_name(value)


def _row_record(row, team, venue, attrs, side):
    sport = row.get('_domestic_sport_key')
    if pd.isna(sport):
        sport = None
    season = row.get('_history_season')
    season = str(season) if pd.notna(season) else None
    home = _original_name(row['HomeTeam'], attrs, sport)
    away = _original_name(row['AwayTeam'], attrs, sport)
    opponent = away if venue == 'home' else home
    gf = int(row['FTHG'] if venue == 'home' else row['FTAG'])
    ga = int(row['FTAG'] if venue == 'home' else row['FTHG'])
    value = dict(date=str(pd.Timestamp(row['Date_dt']).date()), home_team=home, away_team=away,
                 opponent=opponent, venue=venue, score=f"{int(row['FTHG'])}-{int(row['FTAG'])}",
                 gf=gf, ga=ga, season=season, review_flags=[])
    value.update(_source_info(attrs, side, sport, season))
    personal_source=row.get('_personal_source')
    if isinstance(personal_source,str) and personal_source.strip():
        value.update(source='Personal reviewed: '+personal_source,source_urls=[],
                     provenance_status='personal_reviewed_receipt',csv_code=None)
        revision=row.get('_personal_revision_id')
        value['personal_revision_id']=int(revision) if pd.notna(revision) else None
    for field in ('source_url','received_at','payload_sha256'):
        raw=row.get('_history_'+field)
        value[field]=str(raw) if raw is not None and pd.notna(raw) else None
    if value['source_url']:
        value['source_urls']=[value['source_url']]
        value['provenance_status']='source_url_recorded'
    value['input_position']=int(row['_audit_position']) if '_audit_position' in row else None
    if gf >= _HIGH_MATCH_GF:
        value['review_flags'].append('high_single_match_gf_review_only')
    return value


def _empty_window(size):
    return dict(requested=size, count=0, rows=[], sum_gf=0, sum_ga=0, mean_gf=None, mean_ga=None,
                expected_mean_gf=None, expected_mean_ga=None, expected_count=None,
                matches_profile=None, warnings=[])


def _window(games, team, venue, attrs, side, size, expected):
    result = _empty_window(size)
    rows = [_row_record(row, team, venue, attrs, side) for _, row in games.tail(size).iterrows()]
    result.update(rows=rows, count=len(rows), sum_gf=sum(r['gf'] for r in rows), sum_ga=sum(r['ga'] for r in rows))
    if rows:
        result['mean_gf'] = round(result['sum_gf']/len(rows), 3)
        result['mean_ga'] = round(result['sum_ga']/len(rows), 3)
        if result['mean_gf'] >= _HIGH_MEAN_GF:
            result['warnings'].append('high_mean_gf_review_only')
        if len(rows) < size:
            result['warnings'].append('insufficient_sample')
        if len({r['season'] for r in rows}) > 1:
            result['warnings'].append('multiple_seasons_in_sample')
        if (pd.Timestamp(rows[-1]['date'])-pd.Timestamp(rows[0]['date'])).days > 180:
            result['warnings'].append('sample_span_over_180_days')
    else:
        result['warnings'].append('no_eligible_matches')
    for label, key in [('gf', f'last{size}_gf'), ('ga', f'last{size}_ga')]:
        v = expected.get(key)
        result[f'expected_mean_{label}'] = v if _number(v) else None
    count = expected.get(f'sample{size}')
    result['expected_count'] = int(count) if _number(count) and count >= 0 and int(count) == count else None
    comparisons = []
    if result['expected_count'] is not None:
        comparisons.append(result['expected_count'] == result['count'])
    for key in ('gf', 'ga'):
        saved, actual = result[f'expected_mean_{key}'], result[f'mean_{key}']
        if saved is not None:
            comparisons.append(actual is not None and abs(saved-actual) <= .001)
    if comparisons:
        result['matches_profile'] = all(comparisons)
        if not result['matches_profile']:
            result['warnings'].append('saved_profile_mismatch')
    return result


def audit_fixture_forms(frame, home_name, away_name, kickoff, expected_profiles=None):
    """Replay venue-specific samples with source/season/date/duplicate evidence.

    `expected_profiles` optionally maps home/away to the already-computed profile.
    A mean of 5.20 is retained if the five GF values sum to 26. Its review flag
    does not prove bad data. Source-wide exclusions are explicitly labelled and
    cannot reconstruct evidence previously removed by an upstream collector.
    """
    attrs = deepcopy(frame.attrs) if isinstance(frame, pd.DataFrame) else {}
    expected_profiles = expected_profiles or {}
    output = dict(scope='domestic_form_evidence_only', audit_version=1,
                  cutoff_date=None, status='available', review_only=True,
                  review_thresholds={'mean_gf': _HIGH_MEAN_GF, 'single_match_gf': _HIGH_MATCH_GF},
                  source_reports=deepcopy(attrs.get('domestic_source_reports', {})))
    for side, name, venue in [('home', home_name, 'home'), ('away', away_name, 'away')]:
        output[side] = dict(team=name, venue=venue, status='no_history', mapping=None, matched_name=None,
                            source_scope='per-team venue; source_reports are whole-source diagnostics',
                            warnings=[], excluded={'invalid_rows': 0, 'invalid_season_rows': 0,
                                                   'duplicate_rows': 0, 'conflicting_rows': 0,
                                                   'on_or_after_kickoff_day': 0, 'other_venue_rows': 0},
                            windows={'5': _empty_window(5), '10': _empty_window(10)})
    try:
        stamp = pd.Timestamp(kickoff)
        if pd.isna(stamp) or stamp.tzinfo is None:
            raise ValueError('kickoff requires timezone')
        cutoff = stamp.tz_convert('UTC').tz_localize(None).normalize()
        output['cutoff_date'] = str(cutoff.date())
    except (TypeError, ValueError, OverflowError):
        output['status'] = 'invalid_kickoff'
        for side in ('home', 'away'):
            output[side]['status'] = 'invalid_kickoff'
        return output
    if frame is None or frame.empty:
        output['status'] = 'no_saved_evidence'
        return output
    raw=frame.copy()
    raw['_audit_position']=range(len(raw))
    score_columns={'FTHG','FTAG'}.issubset(raw.columns)
    boolean_scores=(raw.FTHG.map(lambda v: pd.api.types.is_bool_dtype(type(v))) | raw.FTAG.map(lambda v: pd.api.types.is_bool_dtype(type(v)))) if score_columns else pd.Series(False,index=raw.index)
    clean, validation = validate_rows(raw[~boolean_scores])
    validation['raw_rows']=len(raw)
    validation['invalid_scores']=int(validation.get('invalid_scores',0)+boolean_scores.sum())
    if clean.empty:
        output['status'] = 'invalid_or_empty_history'
        for side in ('home', 'away'):
            output[side]['validation'] = validation
        return output
    if '_domestic_sport_key' not in clean:
        clean['_domestic_sport_key'] = None
    if '_history_season' not in clean:
        clean['_history_season'] = None
    names = set(clean.HomeTeam) | set(clean.AwayTeam)
    for side, name in [('home', home_name), ('away', away_name)]:
        entry = output[side]
        team, method = safe_match_team(name, names)
        entry.update(mapping=method, matched_name=team, validation=validation)
        if team is None:
            entry['status'] = method
            continue
        rows = clean[(clean.HomeTeam == team) | (clean.AwayTeam == team)].copy()
        raw_team=raw[(raw.HomeTeam==team)|(raw.AwayTeam==team)]
        resolution = attrs.get('domestic_sources', {}).get(side, {})
        sport = resolution.get('sport_key')
        if sport:
            rows = rows[rows['_domestic_sport_key'] == sport]
            if '_domestic_sport_key' in raw_team:
                raw_team=raw_team[raw_team['_domestic_sport_key']==sport]
        elif rows['_domestic_sport_key'].nunique(dropna=True) > 1:
            entry['status'] = 'ambiguous_sources'
            entry['warnings'].append('multiple_sources_for_same_team')
            continue
        valid_positions=set(clean['_audit_position'])
        entry['excluded']['invalid_rows']=int((~raw_team['_audit_position'].isin(valid_positions)).sum())
        entry['upstream_quality']=deepcopy(attrs.get('domestic_source_reports',{}).get(sport,{}).get('source',{}).get('quality_audit',{}))
        entry['post_mapping_quality']=deepcopy(attrs.get('domestic_source_reports',{}).get(sport,{}).get('post_mapping_quality',{}))
        if not entry['upstream_quality']:
            entry['upstream_quality']=deepcopy(attrs.get('history_diagnostics',{}).get('quality_audit',{}))
        entry['source_validation'] = validation
        after = rows.Date_dt.ge(cutoff)
        entry['excluded']['on_or_after_kickoff_day'] = int(after.sum())
        rows = rows[~after].copy()
        seasons = pd.Series([_season_ok(r.Date_dt,str(r['_history_season']) if pd.notna(r['_history_season']) else None) for _,r in rows.iterrows()],index=rows.index,dtype=bool)
        entry['excluded']['invalid_season_rows'] = int((~seasons).sum())
        rows = rows[seasons].copy()
        if not rows.empty:
            keys = ['_domestic_sport_key','Date_dt','HomeTeam','AwayTeam']
            conflicting = rows.groupby(keys, dropna=False)[['FTHG','FTAG']].transform('nunique').gt(1).any(axis=1)
            entry['excluded']['conflicting_rows'] = int(conflicting.sum())
            rows = rows[~conflicting]
            duplicates = rows.duplicated(keys)
            entry['excluded']['duplicate_rows'] = int(duplicates.sum())
            rows = rows[~duplicates]
        venue = entry['venue']
        selected = rows.HomeTeam.eq(team) if venue == 'home' else rows.AwayTeam.eq(team)
        entry['excluded']['other_venue_rows'] = int((~selected).sum())
        games = rows[selected].sort_values('Date_dt')
        entry['status'] = 'ok' if not games.empty else 'no_eligible_matches'
        for key, count in entry['excluded'].items():
            if count:
                entry['warnings'].append(key)
        entry['windows'] = {str(size): _window(games, team, venue, attrs, side, size,
                                               expected_profiles.get(side, {})) for size in (5,10)}
    return output
