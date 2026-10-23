"""Injected-transport Understat history with fail-closed quality and time evidence.

No requests run on import. Unknown source timezones remain source-calendar dates;
only eligible_xg_history may admit them after their conservative uncertainty bound.
Historical downloads are research data, never evidence of past observation times.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

import pandas as pd

SUPPORTED_SPORTS = {'soccer_epl': 'EPL', 'soccer_spain_la_liga': 'La_liga',
                    'soccer_germany_bundesliga': 'Bundesliga', 'soccer_italy_serie_a': 'Serie_A',
                    'soccer_france_ligue_one': 'Ligue_1', 'soccer_russia_premier_league': 'RFPL'}
SUPPORTED_LEAGUES = frozenset(SUPPORTED_SPORTS.values())
MAX_PAYLOAD_BYTES = 5_000_000
MAX_MATCHES = 2_000
DETAIL_LIMIT = 50
COLUMNS = ['Date_dt', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'xG_home', 'xG_away',
           'source_league', '_history_season', '_history_source_url', '_history_received_at',
           '_history_payload_sha256', '_understat_id', '_understat_home_id', '_understat_away_id',
           '_understat_row_index', '_xg_time_quality', '_xg_match_utc', '_xg_source_datetime',
           '_xg_timezone_status', '_xg_data_mode']
HEADERS = {'User-Agent': 'Mozilla/5.0', 'Accept-Language': 'en-US,en;q=0.9',
           'X-Requested-With': 'XMLHttpRequest'}
MESSAGES = {
    'ok': 'โหลด xG จริงจาก Understat และตรวจแถวข้อมูลแล้ว',
    'partial': 'มี xG บางส่วน แต่บางฤดูกาล/บางแถวไม่ผ่าน — ดูรายละเอียดแหล่งข้อมูล',
    'unsupported_xg': 'ลีกนี้ไม่มีแหล่ง Understat ที่ตั้งค่าไว้',
    'invalid_configuration': 'การตั้งค่าลีก/ฤดูกาล Understat ไม่ถูกต้อง',
    'download_error': 'เชื่อมต่อ Understat ไม่สำเร็จ',
    'http_error': 'Understat ตอบ HTTP ไม่สำเร็จ',
    'empty_payload': 'Understat ส่งเนื้อหาว่าง',
    'invalid_payload': 'โครงสร้างข้อมูล Understat ไม่ตรงรูปแบบที่ตรวจสอบได้',
    'invalid_rows': 'ไม่พบแถว xG ที่ผ่านการตรวจผล วัน ฤดูกาล และตัวเลข',
    'empty_history': 'ไม่พบผลแข่งขันพร้อม xG ที่ยืนยันแล้ว',
    'conflicting_fixtures': 'พบเกมหรือค่า xG ขัดแย้ง — กักกันก่อนคำนวณ',
    'source_unavailable': 'ยังไม่มีผล xG ที่ตรวจสอบได้จากแหล่งที่ร้องขอ',
    'invalid_cutoff': 'เวลาแข่งขัน/เวลาวิเคราะห์ไม่มีเขตเวลาที่ตรวจสอบได้',
    'no_eligible_history': 'ข้อมูล xG ยังไม่ผ่านเวลาที่ปลอดภัยก่อนวิเคราะห์',
}


def _aware(value):
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return None
        return stamp.tz_convert('UTC')
    except (ValueError, TypeError, OverflowError):
        return None


def _empty():
    frame = pd.DataFrame(columns=COLUMNS)
    frame['Date_dt'] = pd.to_datetime(frame['Date_dt'])
    return frame


def _attach(frame, report):
    report.setdefault('message', source_status_message(report))
    frame.attrs['xg_diagnostics'] = deepcopy(report)
    frame.attrs['history_diagnostics'] = deepcopy(report)
    return frame


def source_status_message(report):
    """Bounded presentation text; source bodies and exception strings stay private."""
    if not isinstance(report, dict):
        return MESSAGES['source_unavailable']
    message = MESSAGES.get(report.get('status'), MESSAGES['source_unavailable'])
    sources = report.get('sources') if isinstance(report.get('sources'), list) else []
    failed = [source for source in sources if isinstance(source, dict)
              and isinstance(source.get('http_status'), int) and source['http_status'] != 200]
    if failed:
        parts = [f"{source.get('season')} HTTP {source['http_status']}" for source in failed[:5]]
        message += ' · ' + ', '.join(parts)
    return message



class UnderstatUnavailable(Exception):
    """An uncached failure carries validated diagnostics, never provider bodies."""
    def __init__(self, frame):
        self.frame = frame.copy()
        self.frame.attrs = deepcopy(frame.attrs)
        self.report = deepcopy(frame.attrs.get('xg_diagnostics', frame.attrs.get('history_diagnostics', {})))
        super().__init__(source_status_message(self.report))


def _identifier(value, *, optional=False):
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise ValueError('invalid_identifier')
    text = str(value)
    if not re.fullmatch(r'[0-9]{1,20}', text) or int(text) <= 0:
        raise ValueError('invalid_identifier')
    return str(int(text))


def _team(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 200 or
            any(ord(character) < 32 for character in value)):
        raise ValueError('invalid_teams')
    return value.strip()


def _number(value, *, score=False):
    # Understat serializes real numeric values as decimal strings. Permit only a
    # decimal grammar, not arbitrary float coercion, bool, NaN, or infinity.
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise ValueError('invalid_scores' if score else 'invalid_xg')
    if isinstance(value, str) and not re.fullmatch(r'[0-9]+(?:\.[0-9]+)?', value):
        raise ValueError('invalid_scores' if score else 'invalid_xg')
    try:
        number = float(value)
        if not math.isfinite(number) or not 0 <= number <= 100 or (score and not number.is_integer()):
            raise ValueError('invalid_scores' if score else 'invalid_xg')
    except (ValueError, OverflowError):
        raise ValueError('invalid_scores' if score else 'invalid_xg') from None
    return int(number) if score else number


def _date(value):
    if not isinstance(value, str) or len(value) > 50 or not re.fullmatch(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2}(?:[T ][0-9]{2}:[0-9]{2}(?::[0-9]{2}(?:\.[0-9]{1,6})?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?)?', value):
        raise ValueError('invalid_dates')
    try:
        stamp = pd.Timestamp(value)
        if pd.isna(stamp):
            raise ValueError('invalid_dates')
        if stamp.tzinfo is not None:
            utc = stamp.tz_convert('UTC')
            return utc.tz_localize(None), 'aware_timestamp', utc.isoformat(), 'explicit_source_offset'
        return stamp.normalize(), 'date_only_timezone_unknown', None, 'unspecified_source_timezone'
    except (ValueError, TypeError, OverflowError):
        raise ValueError('invalid_dates') from None


def _pairs(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError('duplicate_json_field')
        out[key] = value
    return out


def _constant(value):
    raise ValueError('invalid_json_number')


def _detail(item, index, reasons, season, url):
    def bounded(value, limit=200):
        return value[:limit] if isinstance(value, str) else None
    h = item.get('h') if isinstance(item.get('h'), dict) else {}
    a = item.get('a') if isinstance(item.get('a'), dict) else {}
    return dict(row_index=index, reasons=list(reasons), season=season, source_url=url,
                match_id=bounded(str(item.get('id')), 20), date=bounded(item.get('datetime'), 50),
                home=bounded(h.get('title')), away=bounded(a.get('title')))


def _parse_rows(payload, season, league, report, now):
    if not isinstance(payload, dict) or not isinstance(payload.get('dates'), list) or len(payload['dates']) > MAX_MATCHES:
        raise ValueError('invalid_payload')
    rows, rejected, poisoned_ids, poisoned_fixtures = [], [], set(), set()
    report.update(raw_rows=len(payload['dates']), parsed_valid_rows=0, rejected_rows=0,
                  scheduled_rows=0, rejection_counts={})
    start = pd.Timestamp(year=season, month=7, day=1)
    end = pd.Timestamp(year=season + 1, month=7, day=1)
    for index, item in enumerate(payload['dates']):
        if not isinstance(item, dict):
            item, reasons = {}, ['invalid_match_schema']
        else:
            reasons = []
        identity = home = away = dt = None
        try:
            identity = _identifier(item.get('id'))
        except ValueError as exc:
            reasons.append(str(exc))
        try:
            h, a = item.get('h'), item.get('a')
            if not isinstance(h, dict) or not isinstance(a, dict):
                raise ValueError('invalid_teams')
            home, away = _team(h.get('title')), _team(a.get('title'))
            home_id, away_id = _identifier(h.get('id'), optional=True), _identifier(a.get('id'), optional=True)
            if home.casefold() == away.casefold() or (home_id is not None and home_id == away_id):
                raise ValueError('invalid_teams')
        except ValueError as exc:
            reasons.append(str(exc))
        try:
            dt, time_quality, match_utc, timezone_status = _date(item.get('datetime'))
            if not start <= dt < end:
                reasons.append('date_season_mismatch')
            if item.get('isResult') is True and ((time_quality == 'aware_timestamp' and pd.Timestamp(match_utc) > now) or (
                    time_quality != 'aware_timestamp' and dt.date() > now.date())):
                reasons.append('future_dates')
            if 'season' in item and str(item['season']) != str(season):
                reasons.append('date_season_mismatch')
        except ValueError as exc:
            reasons.append(str(exc))
        confirmed = item.get('isResult') is True
        if not confirmed:
            if item.get('isResult') is False:
                report['scheduled_rows'] += 1
            else:
                reasons.append('missing_or_invalid_result_flag')
        values = None
        if confirmed:
            try:
                goals, xg = item.get('goals'), item.get('xG')
                if not isinstance(goals, dict):
                    raise ValueError('invalid_scores')
                if not isinstance(xg, dict):
                    raise ValueError('invalid_xg')
                values = (_number(goals.get('h'), score=True), _number(goals.get('a'), score=True),
                          _number(xg.get('h')), _number(xg.get('a')))
            except ValueError as exc:
                reasons.append(str(exc))
        if not confirmed or reasons:
            if identity is not None:
                poisoned_ids.add(identity)
            if dt is not None and home and away:
                poisoned_fixtures.add((str(dt.date()), home, away))
            if reasons:
                rejected.append(_detail(item, index, sorted(set(reasons)), season, report['url']))
                report['rejected_rows'] += 1
                for reason in set(reasons):
                    report['rejection_counts'][reason] = report['rejection_counts'].get(reason, 0) + 1
            continue
        rows.append(dict(Date_dt=dt, HomeTeam=home, AwayTeam=away, FTHG=values[0], FTAG=values[1],
                         xG_home=values[2], xG_away=values[3], source_league=league,
                         _history_season=f'{season % 100:02d}{(season + 1) % 100:02d}',
                         _history_source_url=report['url'], _history_received_at=report['received_at'],
                         _history_payload_sha256=report['payload_sha256'], _understat_id=identity,
                         _understat_home_id=home_id, _understat_away_id=away_id, _understat_row_index=index,
                         _xg_time_quality=time_quality, _xg_match_utc=match_utc,
                         _xg_source_datetime=item['datetime'], _xg_timezone_status=timezone_status,
                         _xg_data_mode='research_not_observed'))
    report['parsed_valid_rows'] = len(rows)
    report['status'] = 'ok' if rows else 'invalid_rows' if rejected else 'empty_history'
    if rows and rejected:
        report['status'] = 'partial'
    return rows, rejected, poisoned_ids, poisoned_fixtures


def load_understat_history(sport_key, league_code, seasons, get, timeout=(3, 8), *, now=None):
    """Load only explicitly supported, injected sources; keep every source outcome.

    Season bounds are conservative July-to-June validation for configured sources;
    an exceptional extension needs separate evidence, not an invented date rule.
    """
    clock = _aware(now) if now is not None else pd.Timestamp(datetime.now(timezone.utc))
    diagnostic = dict(provider='Understat', sport_key=sport_key, league_code=league_code,
                      status='unsupported_xg', sources=[], valid_rows=0,
                      data_mode='research_not_observed', timezone_policy='unknown timezone retained as source-calendar date; eligibility uses date+2 days UTC',
                      quality_audit=dict(raw_rows=0, rejected_rows=0, conflicting_rows=0, duplicate_rows=0,
                                         accepted_rows=0, details=[], details_truncated=False))
    if not league_code:
        return _attach(_empty(), diagnostic)
    if (clock is None or not isinstance(league_code, str) or league_code not in SUPPORTED_LEAGUES or not isinstance(sport_key, str) or not sport_key.strip()
            or SUPPORTED_SPORTS.get(sport_key) != league_code
            or not isinstance(seasons, (list, tuple)) or not 1 <= len(seasons) <= 20
            or any(isinstance(season, bool) or not isinstance(season, int) or not 2014 <= season <= clock.year for season in seasons)
            or not callable(get)):
        diagnostic['status'] = 'invalid_configuration'
        return _attach(_empty(), diagnostic)
    unique_seasons = list(dict.fromkeys(seasons))
    diagnostic.update(requested_seasons=list(seasons), duplicate_season_requests_avoided=len(seasons) - len(unique_seasons))
    rows, rejected, poison_ids, poison_fixtures = [], [], set(), set()
    stop_reason = None
    for season in unique_seasons:
        url = f'https://understat.com/getLeagueData/{league_code}/{season}'
        report = dict(provider='Understat', sport_key=sport_key, league_code=league_code, season=season,
                      url=url, reference_url=f'https://understat.com/league/{league_code}/{season}',
                      requested_at=datetime.now(timezone.utc).isoformat(), http_status=None, received_at=None,
                      payload_sha256=None, bytes=0, accepted_rows=0, raw_rows=0, parsed_valid_rows=0,
                      rejected_rows=0, scheduled_rows=0, rejection_counts={}, status='download_error')
        diagnostic['sources'].append(report)
        if stop_reason is not None:
            report.update(status='not_requested', reason_code=stop_reason, requested_at=None)
            continue
        try:
            response = get(url, headers={**HEADERS, 'Referer': report['reference_url']}, timeout=timeout)
            report['received_at'] = datetime.now(timezone.utc).isoformat()
            status = response.status_code
            if not isinstance(status, int) or isinstance(status, bool):
                raise ValueError('invalid_http_status')
            report['http_status'] = status
            content = response.content
            if isinstance(content, bytes):
                report.update(bytes=len(content), payload_sha256=hashlib.sha256(content).hexdigest())
            if status != 200:
                reason = 'http_forbidden' if status in (401, 403) else 'rate_limited' if status == 429 else 'http_error'
                report.update(status='http_error', reason_code=reason)
                if status in (401, 403, 429):
                    stop_reason = 'not_requested_after_' + reason
                continue
            if not isinstance(content, bytes):
                report.update(status='invalid_payload', reason_code='non_bytes_payload')
                continue
            final_url = getattr(response, 'url', None)
            if isinstance(final_url, str) and final_url:
                parsed = urlsplit(final_url)
                if (parsed.scheme != 'https' or parsed.hostname not in ('understat.com', 'www.understat.com')
                        or parsed.path != urlsplit(url).path or parsed.query or parsed.fragment or parsed.username or parsed.password):
                    report.update(status='invalid_payload', reason_code='unverified_redirect')
                    continue
            if not content.strip():
                report['status'] = 'empty_payload'
                continue
            if len(content) > MAX_PAYLOAD_BYTES:
                report.update(status='invalid_payload', reason_code='oversized_payload')
                continue
            try:
                payload = json.loads(content.decode('utf-8-sig'), object_pairs_hook=_pairs, parse_constant=_constant)
                parsed_rows, rejected_rows, invalid_ids, invalid_fixtures = _parse_rows(payload, season, league_code, report, clock)
            except (ValueError, UnicodeError, TypeError, OverflowError):
                report.update(status='invalid_payload', reason_code='invalid_payload_schema')
                continue
            rows.extend(parsed_rows)
            rejected.extend(rejected_rows)
            poison_ids.update(invalid_ids)
            poison_fixtures.update(invalid_fixtures)
        except Exception as exc:
            report.update(status='download_error', error=type(exc).__name__[:80])
    quality = diagnostic['quality_audit']
    quality['raw_rows'] = sum(report.get('raw_rows', 0) for report in diagnostic['sources'])
    quality['rejected_rows'] = len(rejected)
    quality['details'] = rejected[:DETAIL_LIMIT]
    quality['details_truncated'] = len(rejected) > DETAIL_LIMIT
    frame = pd.DataFrame(rows, columns=COLUMNS) if rows else _empty()
    if not frame.empty:
        fixture_keys = pd.Series([(str(row.Date_dt.date()), row.HomeTeam, row.AwayTeam)
                                  for row in frame.itertuples()], index=frame.index)
        signature = ['Date_dt', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG', 'xG_home', 'xG_away',
                     '_xg_time_quality', '_xg_match_utc']
        id_conflict = frame.groupby('_understat_id', dropna=False)[signature].transform('nunique', dropna=False).gt(1).any(axis=1)
        fixture_conflict = frame.assign(_fixture_key=fixture_keys).groupby('_fixture_key', dropna=False)[
            ['FTHG', 'FTAG', 'xG_home', 'xG_away', '_understat_id', '_xg_time_quality', '_xg_match_utc']
        ].transform('nunique', dropna=False).gt(1).any(axis=1)
        conflicts = id_conflict | fixture_conflict | frame['_understat_id'].isin(poison_ids) | fixture_keys.isin(poison_fixtures)
        quality['conflicting_rows'] = int(conflicts.sum())
        for report in diagnostic['sources']:
            report['conflicting_rows'] = int((frame.loc[conflicts, '_history_source_url'] == report['url']).sum())
        for _, row in frame[conflicts].iterrows():
            if len(quality['details']) >= DETAIL_LIMIT:
                quality['details_truncated'] = True
                break
            quality['details'].append(dict(reasons=['conflicting_fixture'], match_id=row['_understat_id'],
                date=str(row['Date_dt']), home=row['HomeTeam'], away=row['AwayTeam'], home_goals=int(row['FTHG']),
                away_goals=int(row['FTAG']), xg_home=float(row['xG_home']), xg_away=float(row['xG_away']),
                season=row['_history_season'], source_url=row['_history_source_url']))
        frame = frame[~conflicts].copy()
        duplicate = frame.duplicated(['Date_dt', 'HomeTeam', 'AwayTeam', '_understat_id'], keep='first')
        quality['duplicate_rows'] = int(duplicate.sum())
        for report in diagnostic['sources']:
            report['duplicate_rows'] = int((frame.loc[duplicate, '_history_source_url'] == report['url']).sum())
        frame = frame[~duplicate].sort_values('Date_dt').reset_index(drop=True)
    for report in diagnostic['sources']:
        if not frame.empty:
            report['accepted_rows'] = int((frame['_history_source_url'] == report['url']).sum())
        if report.get('conflicting_rows'):
            report['status'] = 'partial' if report['accepted_rows'] else 'conflicting_fixtures'
    quality['accepted_rows'] = diagnostic['valid_rows'] = len(frame)
    if not frame.empty:
        issues = any(report['status'] not in ('ok', 'empty_history') for report in diagnostic['sources']) or quality['conflicting_rows']
        diagnostic['status'] = 'partial' if issues else 'ok'
        diagnostic['reason_code'] = 'partial_source_failure' if issues else 'validated_rows'
        diagnostic['latest_source_calendar_date'] = str(frame.Date_dt.max().date())
    elif quality['conflicting_rows']:
        diagnostic['status'] = 'conflicting_fixtures'
    else:
        statuses = {report['status'] for report in diagnostic['sources'] if report['status'] != 'not_requested'}
        diagnostic['status'] = next(iter(statuses)) if len(statuses) == 1 else 'source_unavailable'
    diagnostic['message'] = source_status_message(diagnostic)
    return _attach(frame, diagnostic)


def eligible_xg_history(frame, kickoff, analysis_at):
    """Filter source evidence before existing formula functions; never change math.

    No metadata means a legacy caller/test frame: preserve its existing behavior.
    Unknown timezone dates get a conservative exclusive upper bound of calendar
    date+2 days UTC; that bound must strictly precede both kickoff and analysis.
    Actual retrieval after analysis is excluded, without claiming historical
    observation or substituting past receive times.
    """
    if frame is None:
        return _empty()
    result = frame.copy()
    result.attrs = deepcopy(frame.attrs)
    report = dict(status='legacy_unannotated', input_rows=len(frame), accepted_rows=len(frame),
                  excluded_unknown_time=0, excluded_future_or_at_cutoff=0,
                  excluded_received_after_analysis=0, excluded_invalid_metadata=0)
    if '_xg_time_quality' not in frame:
        result.attrs['xg_eligibility'] = report
        return result
    kickoff_utc, analysis_utc = _aware(kickoff), _aware(analysis_at)
    if kickoff_utc is None or analysis_utc is None:
        report.update(status='invalid_cutoff', accepted_rows=0, excluded_invalid_metadata=len(frame))
        result = result.iloc[0:0].copy()
    else:
        cutoff = min(kickoff_utc, analysis_utc)
        keep = []
        for _, row in frame.iterrows():
            quality, usable = row.get('_xg_time_quality'), True
            received = _aware(row.get('_history_received_at'))
            if received is None:
                usable = False
                report['excluded_invalid_metadata'] += 1
            elif received > analysis_utc:
                usable = False
                report['excluded_received_after_analysis'] += 1
            if quality == 'date_only_timezone_unknown':
                try:
                    day = pd.Timestamp(row.get('Date_dt'))
                    if pd.isna(day) or day.tzinfo is not None or day != day.normalize():
                        raise ValueError('invalid_source_calendar_date')
                    upper_bound = day.tz_localize('UTC') + timedelta(days=2)
                    if upper_bound >= cutoff:
                        usable = False
                        report['excluded_unknown_time'] += 1
                except (ValueError, TypeError, OverflowError):
                    usable = False
                    report['excluded_invalid_metadata'] += 1
            elif quality == 'aware_timestamp':
                match_utc = _aware(row.get('_xg_match_utc'))
                if match_utc is None or pd.isna(row.get('Date_dt')) or pd.Timestamp(row['Date_dt']) != match_utc.tz_localize(None):
                    usable = False
                    report['excluded_invalid_metadata'] += 1
                elif match_utc >= cutoff:
                    usable = False
                    report['excluded_future_or_at_cutoff'] += 1
            else:
                usable = False
                report['excluded_invalid_metadata'] += 1
            keep.append(usable)
        result = result.loc[keep].copy()
        report.update(status='ok' if not result.empty else 'no_eligible_history', accepted_rows=len(result),
                      cutoff_utc=cutoff.isoformat())
    result.attrs['xg_eligibility'] = report
    for key in ('xg_diagnostics', 'history_diagnostics'):
        diagnostic = deepcopy(frame.attrs.get(key, {}))
        diagnostic['eligibility'] = deepcopy(report)
        result.attrs[key] = diagnostic
    return result
