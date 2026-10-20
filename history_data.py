"""Validated Football-Data scores and explicit diagnostics; never creates xG."""
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from io import BytesIO

import pandas as pd


SEASONS = ('2627', '2526', '2425', '2324')
REQUIRED = {'Date', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}
# Direct E2 CSV audit, 2026-10-20, Actions run 39432169259:
# 2627/2324 contain "Oxford" and all four seasons contain "Wigan".
# The full names are the supplied Odds API fixture labels, not guessed spellings.
TEAM_ALIASES = (frozenset({'oxfordunited', 'oxford'}), frozenset({'wiganathletic', 'wigan'}))
ALIAS_SOURCE = 'https://www.football-data.co.uk/mmz4281/2627/E2.csv'
MESSAGES = {
    'download_error': 'โหลดข้อมูลไม่สำเร็จ', 'http_error': 'แหล่ง CSV ตอบ HTTP ไม่สำเร็จ',
    'empty_file': 'ไฟล์ CSV ว่าง', 'empty_history': 'ไม่มีแถวผลย้อนหลังที่ใช้ได้',
    'missing_columns': 'ไฟล์ขาดคอลัมน์ที่จำเป็น', 'parse_error': 'อ่านไฟล์ CSV ไม่สำเร็จ',
    'invalid_dates': 'วันที่ผลย้อนหลังผิดรูปแบบ', 'invalid_scores': 'ผลประตูไม่ครบหรือไม่ถูกต้อง',
    'name_unmatched': 'จับคู่ชื่อทีมไม่สำเร็จ', 'name_ambiguous': 'ชื่อทีมกำกวม — ไม่เดาจากชื่อคล้าย',
    'no_history_before_kickoff': 'ไม่มีผลก่อนวันแข่งขัน', 'insufficient_history': 'มีผลย้อนหลังตามสนามไม่ถึง 5 นัด',
    'invalid_kickoff': 'วันแข่งขันไม่ถูกต้องหรือไม่มีเขตเวลา', 'unsupported_source': 'ลีกนี้ไม่มีรหัสแหล่งผลย้อนหลัง',
    'ok': 'ข้อมูลฟอร์มพร้อมใช้',
}


def normalize(name):
    return re.sub(r'[^a-z0-9]', '', str(name or '').lower().replace('&', 'and'))


def match_team(name, candidates):
    normalized = normalize(name)
    exact = [c for c in set(candidates) if normalized and normalize(c) == normalized]
    if len(exact) == 1:
        return exact[0], 'exact'
    if len(exact) > 1:
        return None, 'name_ambiguous'
    for group in TEAM_ALIASES:
        if normalized in group:
            matches = [c for c in set(candidates) if normalize(c) in group]
            if len(matches) == 1:
                return matches[0], 'verified_alias'
            if len(matches) > 1:
                return None, 'name_ambiguous'
    return None, 'name_unmatched'


def parse_dates(values):
    """Explicit per-row formats avoid inference dropping mixed 2/4 digit years."""
    strings = values.astype('string').str.strip()
    result = pd.Series(pd.NaT, index=values.index, dtype='datetime64[ns]')
    for fmt in ('%d/%m/%Y', '%d/%m/%y', '%Y-%m-%d'):
        parsed = pd.to_datetime(strings, format=fmt, errors='coerce')
        result = result.fillna(parsed)
    return result


def validate_rows(frame):
    required = {'Date_dt', 'HomeTeam', 'AwayTeam', 'FTHG', 'FTAG'}
    if not required.issubset(frame.columns):
        return pd.DataFrame(), {'status': 'missing_columns', 'missing_columns': sorted(required-set(frame.columns))}
    out = frame.copy()
    out['Date_dt'] = pd.to_datetime(out['Date_dt'], utc=True, errors='coerce', format='mixed').dt.tz_convert(None)
    hg, ag = pd.to_numeric(out['FTHG'], errors='coerce'), pd.to_numeric(out['FTAG'], errors='coerce')
    scores = hg.notna() & ag.notna() & (hg >= 0) & (ag >= 0) & (hg < float('inf')) & (ag < float('inf')) & (hg % 1 == 0) & (ag % 1 == 0)
    teams = out['HomeTeam'].notna() & out['AwayTeam'].notna() & out['HomeTeam'].astype(str).str.strip().ne('') & out['AwayTeam'].astype(str).str.strip().ne('')
    valid = out['Date_dt'].notna() & scores & teams
    info = {'raw_rows': len(out), 'invalid_dates': int(out['Date_dt'].isna().sum()), 'invalid_scores': int((~scores).sum()),
            'missing_teams': int((~teams).sum()), 'valid_rows': int(valid.sum()), 'status': 'ok' if valid.any() else 'empty_history'}
    out['FTHG'], out['FTAG'] = hg, ag
    return out[valid].sort_values('Date_dt').reset_index(drop=True), info


def parse_csv(content):
    if not content or not content.strip():
        return pd.DataFrame(), {'status': 'empty_file', 'valid_rows': 0}
    try:
        frame = pd.read_csv(BytesIO(content))
    except (ValueError, UnicodeError):
        return pd.DataFrame(), {'status': 'parse_error', 'valid_rows': 0}
    missing = sorted(REQUIRED-set(frame.columns))
    if missing:
        return pd.DataFrame(), {'status': 'missing_columns', 'missing_columns': missing, 'columns': frame.columns.tolist(), 'valid_rows': 0}
    frame['Date_dt'] = parse_dates(frame['Date'])
    clean, info = validate_rows(frame)
    info['columns'] = frame.columns.tolist()
    if frame.empty:
        info['status'] = 'empty_file'
    elif not len(clean) and info['invalid_dates']:
        info['status'] = 'invalid_dates'
    elif not len(clean) and info['invalid_scores']:
        info['status'] = 'invalid_scores'
    return clean, info


def load_history(code, get, timeout=(3, 8), seasons=SEASONS):
    if not code:
        frame = pd.DataFrame()
        frame.attrs['history_diagnostics'] = {'status': 'unsupported_source', 'sources': []}
        return frame
    def download(season):
        url = f'https://www.football-data.co.uk/mmz4281/{season}/{code}.csv'
        report = {'season': season, 'csv_code': code, 'url': url, 'checked_at': datetime.now(timezone.utc).isoformat()}
        try:
            response = get(url, timeout=timeout)
            report.update(http_status=response.status_code, bytes=len(response.content), final_url=getattr(response, 'url', url))
            if response.status_code != 200:
                report.update(status='http_error', valid_rows=0)
                return pd.DataFrame(), report
            frame, parsed = parse_csv(response.content)
            frame['_history_season'] = season
            report.update(parsed)
            return frame, report
        except Exception as exc:
            report.update(status='download_error', error=type(exc).__name__, valid_rows=0)
            return pd.DataFrame(), report
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(download, seasons))
    frames = [frame for frame, _ in results if not frame.empty]
    out = pd.concat(frames, ignore_index=True).sort_values('Date_dt').drop_duplicates(['Date_dt', 'HomeTeam', 'AwayTeam']).reset_index(drop=True) if frames else pd.DataFrame()
    reports = [report for _, report in results]
    out.attrs['history_diagnostics'] = {'status': 'ok' if frames else (reports[0]['status'] if len({r['status'] for r in reports}) == 1 else 'empty_history'),
                                       'sources': reports, 'valid_rows': len(out)}
    return out


def team_history(df, name, kickoff, venue=None):
    source = df.attrs.get('history_diagnostics', {}) if df is not None else {}
    report = {'input_name': str(name), 'matched_name': None, 'mapping': None, 'venue': venue,
              'source': source, 'status': 'empty_history'}
    def done(frame, status):
        report['status'], report['message'] = status, MESSAGES.get(status, status)
        return frame, report
    if df is None or df.empty:
        return done(pd.DataFrame(), source.get('status', 'empty_history'))
    hist, validation = validate_rows(df)
    report['validation'] = validation
    if hist.empty:
        status = 'invalid_dates' if validation.get('invalid_dates') else 'invalid_scores' if validation.get('invalid_scores') else validation['status']
        return done(hist, status)
    try:
        stamp = pd.Timestamp(kickoff)
        if pd.isna(stamp) or stamp.tzinfo is None:
            return done(pd.DataFrame(), 'invalid_kickoff')
        cutoff = stamp.tz_convert('UTC').tz_localize(None).normalize()
    except (ValueError, TypeError):
        return done(pd.DataFrame(), 'invalid_kickoff')
    names = sorted(set(hist['HomeTeam']) | set(hist['AwayTeam']))
    team, method = match_team(name, names)
    report.update(matched_name=team, mapping=method, cutoff_date=str(cutoff.date()))
    if method == 'verified_alias':
        report['alias_source'] = ALIAS_SOURCE
    if team is None:
        return done(pd.DataFrame(), method)
    hist = hist[hist['Date_dt'] < cutoff]
    games = hist[(hist['HomeTeam'] == team) | (hist['AwayTeam'] == team)]
    report['all_before_count'] = len(games)
    if venue == 'home':
        games = games[games['HomeTeam'] == team]
    elif venue == 'away':
        games = games[games['AwayTeam'] == team]
    report['venue_before_count'] = len(games)
    if not games.empty:
        report.update(first_date=str(games['Date_dt'].min().date()), last_date=str(games['Date_dt'].max().date()))
        report['sample5_dates'] = [str(day.date()) for day in games.tail(5)['Date_dt']]
        report['sample5_span_days'] = int((games.tail(5)['Date_dt'].max()-games.tail(5)['Date_dt'].min()).days)
        if '_history_season' in games:
            report['season_counts'] = {str(k): int(v) for k, v in games['_history_season'].value_counts().items()}
    return done(games, 'ok' if len(games) >= 5 else 'insufficient_history' if len(games) else 'no_history_before_kickoff')
