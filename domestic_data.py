"""Pure, fail-closed routing of cup clubs to evidenced domestic score histories.

Only existing, supplied Football-Data rows are inspected. This module never fetches
anything, creates football statistics, or estimates a cross-league probability.
"""
from copy import deepcopy
import re
import unicodedata

import pandas as pd

from history_data import match_team, validate_rows


# Verified July-to-June Football-Data competition codes. Calendar-year leagues
# are excluded until their season validation is implemented separately.
SUPPORTED_CODES = frozenset({'E0', 'E1', 'E2', 'E3', 'SP1', 'I1', 'I2',
                             'D1', 'D2', 'F1', 'F2', 'N1', 'P1', 'SC0', 'B1'})
MAX_ROSTER_AGE_DAYS = 120


def season_for_kickoff(kickoff):
    """Return the source season only for an explicit aware kickoff, never guess UTC."""
    try:
        stamp = pd.Timestamp(kickoff)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        stamp = stamp.tz_convert('UTC')
        year = stamp.year if stamp.month >= 7 else stamp.year - 1
        return f'{year % 100:02d}{(year + 1) % 100:02d}'
    except (ValueError, TypeError, OverflowError):
        return None


def _identity(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().replace('&', 'and')
    return ''.join(c for c in value if c.isalnum())


def safe_match_team(name, candidates):
    """Reuse reviewed aliases; reject matches created by lossy ASCII stripping."""
    matched, method = match_team(name, candidates)
    if matched is not None and method == 'exact' and _identity(name) != _identity(matched):
        return None, 'lossy_normalization'
    return matched, method


def _season_dates(frame):
    """Require an explicit valid season label consistent with each result date."""
    if '_history_season' not in frame:
        return frame.iloc[0:0].copy(), len(frame)
    labels = frame['_history_season'].astype(str)
    keep = pd.Series(False, index=frame.index)
    for label in labels.unique():
        if not re.fullmatch(r'\d{4}', label):
            continue
        start, end = int(label[:2]), int(label[2:])
        if end != (start + 1) % 100:
            continue
        first = pd.Timestamp(year=2000 + start, month=7, day=1)
        last = first + pd.DateOffset(years=1)
        keep |= labels.eq(label) & frame.Date_dt.ge(first) & frame.Date_dt.lt(last)
    return frame[keep].copy(), int((~keep).sum())


def _validated(frame):
    if frame is None:
        return pd.DataFrame(), {'status': 'source_unavailable', 'source': {}}
    source = deepcopy(frame.attrs.get('history_diagnostics', {}))
    if frame.empty:
        return frame.copy(), {'status': source.get('status', 'empty_history'), 'source': source}
    clean, validation = validate_rows(frame)
    clean, invalid_seasons = _season_dates(clean)
    # Exact duplicates are harmless, conflicting scores for the same fixture are not.
    conflicts = 0
    if not clean.empty:
        keys = ['Date_dt', 'HomeTeam', 'AwayTeam']
        ambiguous = clean.groupby(keys, dropna=False)[['FTHG', 'FTAG']].transform('nunique').gt(1).any(axis=1)
        conflicts = int(ambiguous.sum())
        clean = clean[~ambiguous].drop_duplicates(keys).sort_values('Date_dt').reset_index(drop=True)
    clean.attrs['history_diagnostics'] = source
    return clean, {'status': 'ok' if not clean.empty else 'unknown_season' if invalid_seasons else validation['status'],
                   'source': source, 'validation': validation, 'invalid_season_rows': invalid_seasons,
                   'conflicting_result_rows': conflicts}


def resolve_domestic_sources(names, frames_by_sport, kickoff, league_definitions):
    """Map each supplied API label only against usable current-season source rosters.

    `league_definitions` maps sport key to {csv_code, label, understat}. Every
    resolution preserves source failures, even if a different source proves a match.
    Membership in an older season cannot establish today's domestic competition.
    """
    names = list(dict.fromkeys(names))
    season = season_for_kickoff(kickoff)
    output = {}
    if season is None:
        return {name: {'input_name': name, 'status': 'invalid_kickoff', 'sport_key': None,
                       'csv_code': None, 'csv_name': None, 'source_season': None,
                       'reason': 'เวลาแข่งขันไม่ถูกต้องหรือไม่มีเขตเวลา — ไม่อนุมานลีก/ฤดูกาล',
                       'source_diagnostics': {}} for name in names}
    cutoff = pd.Timestamp(kickoff).tz_convert('UTC').tz_localize(None).normalize()
    prepared = {}
    for sport, definition in league_definitions.items():
        if definition.get('csv_code') not in SUPPORTED_CODES:
            prepared[sport] = (pd.DataFrame(), {'status': 'unsupported_source', 'source': {}})
        else:
            prepared[sport] = _validated(frames_by_sport.get(sport))
    for name in names:
        diagnostics, candidates, ambiguities = {}, [], []
        old_membership = False
        current_sources = 0
        for sport, definition in league_definitions.items():
            history, report = prepared[sport]
            report = deepcopy(report)
            report.update(sport_key=sport, csv_code=definition.get('csv_code'), label=definition.get('label'),
                          requested_season=season)
            diagnostics[sport] = report
            if history.empty:
                continue
            before = history[history.Date_dt < cutoff]
            current = before[before['_history_season'].astype(str).eq(season)]
            if current.empty:
                report['status'] = 'no_current_season'
                past_name, _ = safe_match_team(name, set(before.HomeTeam) | set(before.AwayTeam))
                old_membership |= past_name is not None
                continue
            current_sources += 1
            matched, method = safe_match_team(name, set(current.HomeTeam) | set(current.AwayTeam))
            report.update(mapping=method, matched_name=matched, current_rows=len(current))
            if method == 'name_ambiguous':
                report['status'] = 'ambiguous'; ambiguities.append(sport)
                continue
            if matched is None:
                report['status'] = method
                past_name, _ = safe_match_team(name, set(before.HomeTeam) | set(before.AwayTeam))
                old_membership |= past_name is not None
                continue
            games = current[(current.HomeTeam == matched) | (current.AwayTeam == matched)]
            latest = games.Date_dt.max()
            stale = (cutoff - latest.normalize()).days > MAX_ROSTER_AGE_DAYS
            report.update(status='stale' if stale else 'matched', last_date=str(latest.date()), team_rows=len(games))
            candidates.append((sport, matched, stale))
        result = dict(input_name=name, status='unmatched', sport_key=None, csv_code=None, csv_name=None,
                      source_season=season, reason='จับคู่ชื่อทีมไม่ได้ในแหล่งลีกภายในประเทศที่ตรวจได้ — ไม่เดาจากชื่อคล้าย',
                      source_diagnostics=diagnostics,
                      coverage_complete=all(r['status'] in {'matched','stale','name_unmatched','lossy_normalization','ambiguous'} for r in diagnostics.values()),
                      scope='ตรวจเฉพาะลีกที่รองรับจากผลฤดูกาลปัจจุบันที่โหลดได้; ไม่ยืนยันทีมครบทุกลีก')
        if len(candidates) > 1 or ambiguities:
            result.update(status='ambiguous', reason='ชื่อทีมตรงกับหลายลีกหรือหลายชื่อ — ยังไม่ยืนยันแหล่งลีกภายในประเทศ')
        elif len(candidates) == 1:
            sport, matched, stale = candidates[0]
            definition = league_definitions[sport]
            result.update(status='stale' if stale else 'matched', sport_key=sport,
                          csv_code=definition['csv_code'], csv_name=matched,
                          reason=('ข้อมูลลีกภายในประเทศเก่ากว่า 120 วัน — ยังไม่ใช้วิเคราะห์' if stale else
                                  f"ยืนยันจากผลฤดูกาล {season} ก่อนวันแข่ง: {definition.get('label',sport)} ({definition['csv_code']})"))
        elif old_membership:
            result.update(status='stale', reason='พบชื่อเฉพาะข้อมูลเก่า/ฤดูกาลเก่า — ห้ามถือว่าเป็นลีกปัจจุบัน')
        elif not current_sources:
            statuses = {r['status'] for r in diagnostics.values()}
            if statuses and statuses.issubset({'unsupported_source'}):
                result.update(status='unsupported_source', reason='ไม่มีแหล่งลีกภายในประเทศที่รองรับในขอบเขตนี้')
            elif 'unknown_season' in statuses:
                result.update(status='unknown_season', reason='ข้อมูลไม่มีฤดูกาลที่ตรวจสอบได้หรือวันที่ไม่ตรงฤดูกาล')
            elif 'no_current_season' in statuses:
                result.update(status='stale', reason='ไม่มีผลฤดูกาลปัจจุบันก่อนวันแข่งขัน — ยังยืนยันลีกไม่ได้')
            else:
                result.update(status='source_error', reason='โหลดแหล่งข้อมูลไม่ได้/ข้อมูลว่างหรือใช้ไม่ได้ — ยังยืนยันลีกไม่ได้')
        output[name] = result
    return output


def build_domestic_history(home_resolution, away_resolution, frames):
    """Build safe score evidence, preserving full resolved domestic histories.

    All non-selected club labels are namespaced by source sport. Only each proven
    club is remapped to its exact API input name for unchanged form functions.
    The return value is a DataFrame; attrs retain per-side and original diagnostics.
    It is context for cup form, never a pooled cross-league Poisson baseline.
    """
    resolutions = {'home': deepcopy(home_resolution), 'away': deepcopy(away_resolution)}
    usable = {side: r for side, r in resolutions.items() if r.get('status') == 'matched'}
    conflict = len(usable) == 2 and (
        _identity(usable['home']['input_name']) == _identity(usable['away']['input_name']) or
        (usable['home']['sport_key'] == usable['away']['sport_key'] and usable['home']['csv_name'] == usable['away']['csv_name']))
    if conflict:
        for report in resolutions.values():
            report.update(status='ambiguous', reason='ทีมเจ้าบ้านและทีมเยือนมีตัวตนเดียวกัน/กำกวม — ไม่รวมสถิติ')
        usable = {}
    combined, source_reports = [], {}
    for sport in dict.fromkeys(r['sport_key'] for r in usable.values()):
        history, report = _validated(frames.get(sport))
        source_reports[sport] = report
        if history.empty:
            continue
        history = history.copy()
        history['_domestic_sport_key'] = sport
        targets = [r for r in usable.values() if r['sport_key'] == sport]
        original_names = history[['HomeTeam', 'AwayTeam']].copy()
        def rename(label):
            for target in targets:
                mapped, _ = safe_match_team(target['csv_name'], [label])
                if mapped is not None:
                    return target['input_name']
            return f'__domestic__[{sport}]::{label}'
        history['HomeTeam'] = history.HomeTeam.map(rename)
        history['AwayTeam'] = history.AwayTeam.map(rename)
        # Verified identity mapping can reveal duplicates hidden by spelling or
        # whitespace variations. Recheck score contradictions before reducing
        # those newly identical fixtures; source-row order must never choose a score.
        quality = dict(raw_rows=len(history),conflicting_rows=0,duplicate_rows=0,
                       accepted_rows=0,details=[],details_truncated=False,
                       scope='post_verified_team_mapping; source-wide counts')
        keys = ['Date_dt','HomeTeam','AwayTeam']
        def record(kind, rows):
            for index,row in rows.iterrows():
                if len(quality['details']) >= 50:
                    quality['details_truncated'] = True
                    break
                original = original_names.loc[index]
                metadata = {field: row.get('_history_'+field) if isinstance(row.get('_history_'+field),str) else None
                            for field in ('source_url','received_at','payload_sha256')}
                quality['details'].append(dict(
                    reason=kind,sport_key=sport,date=str(row['Date_dt'].date()),
                    original_home=str(original['HomeTeam']),original_away=str(original['AwayTeam']),
                    mapped_home=str(row['HomeTeam']),mapped_away=str(row['AwayTeam']),
                    home_goals=float(row['FTHG']),away_goals=float(row['FTAG']),
                    season=str(row.get('_history_season')),
                    **metadata))
        conflicting = history.groupby(keys,dropna=False)[['FTHG','FTAG']].transform('nunique').gt(1).any(axis=1)
        quality['conflicting_rows'] = int(conflicting.sum())
        record('conflicting_score_after_verified_mapping',history[conflicting])
        history = history[~conflicting].copy()
        duplicate = history.duplicated(keys,keep='first')
        quality['duplicate_rows'] = int(duplicate.sum())
        record('exact_duplicate_after_verified_mapping',history[duplicate])
        history = history[~duplicate].copy()
        quality['accepted_rows'] = len(history)
        report['post_mapping_quality'] = quality
        if history.empty and quality['conflicting_rows']:
            report['status'] = 'conflicting_scores'
        history.attrs = {}
        combined.append(history)
    out = pd.concat(combined, ignore_index=True).drop_duplicates(
        ['_domestic_sport_key','Date_dt','HomeTeam','AwayTeam']).sort_values('Date_dt').reset_index(drop=True) if combined else pd.DataFrame()
    reports = []
    for sport, report in source_reports.items():
        for source in report.get('source', {}).get('sources', []):
            item = deepcopy(source); item['domestic_sport_key'] = sport; reports.append(item)
    out.attrs['domestic_sources'] = resolutions
    out.attrs['domestic_source_reports'] = source_reports
    out.attrs['post_mapping_quality'] = {sport: deepcopy(report['post_mapping_quality'])
                                         for sport,report in source_reports.items()
                                         if 'post_mapping_quality' in report}
    out.attrs['history_diagnostics'] = {'status': 'ok' if not out.empty else 'empty_history', 'sources': reports,
                                       'scope': 'domestic cup context only; not UCL results or cross-league probabilities'}
    return out
