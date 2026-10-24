"""Fail-closed quote eligibility; no models, network or UI dependencies."""
import json
import math
import os
from datetime import datetime, timedelta, timezone

UTC = timezone.utc


def instant(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(UTC) if dt.tzinfo is not None else None
    except (ValueError, TypeError, OverflowError):
        return None


def quote_freshness(row, now=None, max_age=None):
    now = now or datetime.now(UTC)
    if not all(isinstance(row.get(k),str) and row[k].strip() for k in ('event_id','bookmaker','bookmaker_key')):
        return False, 'ไม่มีรหัสคู่/เจ้ามือที่ตรวจสอบได้ — ยังไม่แนะนำ'
    point, odds = row.get('point'),row.get('odds')
    if any(not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) for v in (point,odds)) or odds<=1 or abs(point*4-round(point*4))>1e-9:
        return False, 'เส้น/ราคาไม่ผ่านการตรวจสอบ — ยังไม่แนะนำ'
    try:
        max_age = int(os.getenv('VALUE_BET_QUOTE_MAX_AGE_SECONDS', '900')) if max_age is None else max_age
    except (ValueError, TypeError):
        return False, 'การตั้งค่าอายุราคาไม่ถูกต้อง — ยังไม่แนะนำ'
    received, updated = instant(row.get('received_at')), instant(row.get('source_updated_at'))
    if received is None or updated is None:
        return False, 'ไม่มีหลักฐานเวลารับราคา/เวลาอัปเดตตลาดจากเจ้ามือ — ยังไม่แนะนำ'
    if max_age <= 0 or max_age > 900:
        return False, 'การตั้งค่าอายุราคาไม่ผ่านเกณฑ์ความสด — ยังไม่แนะนำ'
    if updated > received + timedelta(seconds=30) or any(t > now + timedelta(seconds=30) for t in (received, updated)):
        return False, 'ลำดับเวลาราคาไม่ถูกต้อง — ยังไม่แนะนำ'
    if any((now-t).total_seconds() > max_age for t in (received, updated)):
        return False, 'ราคาเก่าเกินเกณฑ์ความสด — ยังไม่แนะนำ'
    return True, ''


def analysis_scope(df, league_names, now=None, latest_receipts=None, hours_limit=24):
    """Keep historical storage untouched; apply scope before any presentation/ranking."""
    import pandas as pd
    now = now or datetime.now(UTC)
    if df.empty:
        return df.copy()
    if (isinstance(hours_limit, bool) or hours_limit not in (24, 48)
            or not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None):
        return df.iloc[0:0].copy()
    now = now.astimezone(UTC)
    kicks = pd.to_datetime(df['kickoff_utc'].map(instant), errors='coerce', utc=True)
    mask = ((kicks > now) & (kicks <= now + timedelta(hours=hours_limit))
            & df['league'].isin(list(league_names)))
    result = df[mask].copy()
    if latest_receipts and not result.empty and 'received_at' in result:
        stamps = pd.to_datetime(result['received_at'].map(instant), errors='coerce', utc=True)
        receipts = pd.to_datetime(result['league'].map(latest_receipts).map(instant), errors='coerce', utc=True)
        obsolete = stamps.notna() & receipts.notna() & (stamps < receipts)
        result = result[~obsolete].copy()
    # A bookmaker removed in the latest observed response must not reappear from history.
    if not result.empty and 'received_at' in result:
        stamps = pd.to_datetime(result['received_at'].map(instant), errors='coerce', utc=True)
        newest = stamps.groupby(result['event_id']).transform('max')
        obsolete = stamps.notna() & newest.notna() & (stamps < newest)
        result = result[~obsolete].copy()
    return result


def result_time_ready(row, now=None):
    kickoff=instant(row.get('kickoff_utc'))
    return kickoff is not None and kickoff < (now or datetime.now(UTC))


def unique_result(history, home, away, kickoff):
    """Date-only CSV evidence: exact date and one unambiguous, valid result only."""
    import pandas as pd
    dt = instant(kickoff)
    if dt is None or dt >= datetime.now(UTC) or not home or not away:
        return None
    required = {'HomeTeam','AwayTeam','Date_dt','FTHG','FTAG'}
    if not required.issubset(history.columns):
        return None
    candidates = history[(history.HomeTeam == home) & (history.AwayTeam == away)].copy()
    dates = pd.to_datetime(candidates.Date_dt, errors='coerce', utc=True)
    candidates = candidates[(dates.dt.normalize()-pd.Timestamp(dt).normalize()).abs() <= pd.Timedelta(days=1)]
    # Even a second candidate on an adjacent day needs manual review.
    if len(candidates) != 1:
        return None
    row = candidates.iloc[0]
    day = pd.to_datetime(row.Date_dt, errors='coerce', utc=True)
    if pd.isna(day) or day.normalize() != pd.Timestamp(dt).normalize():
        return None
    for key in ('FTHG','FTAG'):
        if pd.api.types.is_bool_dtype(type(row[key])):
            return None
        try:
            n = float(row[key])
            if not math.isfinite(n) or n < 0 or not n.is_integer(): return None
        except (TypeError,ValueError): return None
    return row
