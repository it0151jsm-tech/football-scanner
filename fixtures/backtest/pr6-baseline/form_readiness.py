"""Explain observed score samples without changing A scoring or inventing history."""
from copy import deepcopy
import pandas as pd

from domestic_data import season_for_kickoff, safe_match_team
from history_data import team_history, validate_rows
from readiness import instant


FORM_REASONS = {
    'name_unmatched': 'จับคู่ชื่อทีมไม่ได้ — ยังไม่มี alias ที่ยืนยัน',
    'name_ambiguous': 'ชื่อทีมกำกวม — ไม่เลือกทีมแทน',
    'lossy_normalization': 'ชื่อไม่ตรงหลังเก็บอักขระครบ — ไม่เดาทีม',
    'no_current_season_results': 'ไม่พบผลฤดูกาลปัจจุบันก่อนวันแข่ง',
    'unknown_season': 'แหล่งข้อมูลไม่ยืนยันฤดูกาล',
    'insufficient_venue_samples': 'ฟอร์มตามสนามที่มีจริงน้อยกว่า 5 นัด',
    'after_cutoff': 'มีผลวันเดียวกับหรือหลังวันแข่งที่ต้องตัดออก',
    'provider_failure': 'แหล่งผลย้อนหลังโหลดไม่สำเร็จ',
    'empty_history': 'ไม่มีผลย้อนหลังจากแหล่งนี้',
    'historical_source_invalidity': 'พบข้อมูลต้นทางไม่ผ่านการตรวจ',
    'stale_history': 'ผลล่าสุดเก่าเกิน 120 วัน',
    'invalid_kickoff': 'ไม่มีวันแข่งพร้อม timezone ที่ยืนยันได้',
}


def _season_validity(frame):
    labels = frame['_history_season'].astype(str)
    valid = pd.Series(False, index=frame.index)
    for label in labels.unique():
        if len(label) != 4 or not label.isdigit() or int(label[2:]) != (int(label[:2])+1) % 100:
            continue
        first = pd.Timestamp(2000+int(label[:2]), 7, 1)
        last = pd.Timestamp(2001+int(label[:2]), 7, 1)
        valid |= labels.eq(label) & frame.Date_dt.ge(first) & frame.Date_dt.lt(last)
    return labels, valid


def form_data_readiness(frame, team, kickoff, venue):
    """Count actual current/prior-season games separately, with a date-only cutoff.

    Five is the existing availability label / C eligibility threshold, not a new
    A scoring gate. Existing A may return a score from fewer real observations;
    every score must still come from its unchanged historical-line formula.
    """
    report = {
        'input_name': str(team), 'venue': venue, 'requested_season': season_for_kickoff(kickoff),
        'current_season_venue_count': 0, 'prior_season_venue_count': 0,
        'unknown_season_venue_count': 0, 'full_history_venue_count': 0,
        'current_season_team_count': 0, 'after_cutoff_count': 0,
        'status': 'unavailable', 'mapping': None, 'matched_name': None,
        'reason_codes': [], 'availability_threshold': 5,
        'a_scoring_gate': False,
        'policy': 'สูตร A เดิมใช้ผลย้อนหลังที่มีจริงก่อนวันแข่ง; 5/10 นัดอาจข้ามฤดูกาล ไม่ถือเป็น 5 นัดฤดูกาลปัจจุบัน',
    }
    source = deepcopy(getattr(frame, 'attrs', {}).get('history_diagnostics', {}))
    report['source'] = source
    report['provider'] = source.get('provider', 'Football-Data' if source.get('sources') else 'unknown')
    reasons = report['reason_codes']
    stamp = instant(kickoff)
    if stamp is None:
        reasons.append('invalid_kickoff')
        return _finish(report)
    day = pd.Timestamp(stamp).tz_localize(None).normalize()
    report['cutoff_date'] = str(day.date())
    if frame is None or frame.empty:
        status = source.get('status', 'empty_history')
        reasons.append('provider_failure' if status in ('http_error', 'download_error', 'parse_error') else
                       'historical_source_invalidity' if status in ('missing_columns', 'invalid_dates', 'invalid_scores', 'conflicting_scores', 'invalid_season') else 'empty_history')
        return _finish(report)
    clean, validation = validate_rows(frame)
    report['validation'] = validation
    source_invalid = any(validation.get(key, 0) for key in ('invalid_dates', 'invalid_scores', 'missing_teams'))
    for item in source.get('sources', []):
        if item.get('status') in ('download_error', 'http_error', 'parse_error'):
            reasons.append('provider_failure')
        if any(item.get(key, 0) for key in ('invalid_dates', 'invalid_scores', 'conflicting_result_rows', 'invalid_season_rows')):
            source_invalid = True
    if source_invalid or clean.empty:
        reasons.append('historical_source_invalidity')
    if clean.empty:
        return _finish(report)
    candidate, method = safe_match_team(team, set(clean.HomeTeam) | set(clean.AwayTeam))
    report.update(mapping=method, matched_name=candidate)
    if candidate is None:
        reasons.insert(0, method if method in FORM_REASONS else 'name_unmatched')
        return _finish(report)
    team_rows = clean[(clean.HomeTeam == candidate) | (clean.AwayTeam == candidate)]
    report['after_cutoff_count'] = int((team_rows.Date_dt >= day).sum())
    if report['after_cutoff_count']:
        reasons.append('after_cutoff')
    before = team_rows[team_rows.Date_dt < day]
    games, _ = team_history(frame, team, kickoff, venue)
    report['full_history_venue_count'] = len(games)
    season = report['requested_season']
    if '_history_season' in games:
        labels, known = _season_validity(games)
        report['current_season_venue_count'] = int((known & labels.eq(season)).sum())
        # Only explicit valid earlier labels become prior-season context.
        prior = known & labels.lt(season)
        report['prior_season_venue_count'] = int(prior.sum())
        report['unknown_season_venue_count'] = len(games)-report['current_season_venue_count']-int(prior.sum())
        before_labels, before_valid = _season_validity(before)
        report['current_season_team_count'] = int((before_valid & before_labels.eq(season)).sum())
        if not known.all():
            reasons.append('historical_source_invalidity')
    else:
        report['unknown_season_venue_count'] = len(games)
    if report['unknown_season_venue_count']:
        reasons.append('unknown_season')
    if not report['current_season_team_count']:
        reasons.append('no_current_season_results')
    if len(games) < 5:
        reasons.append('insufficient_venue_samples')
    if not games.empty:
        last = pd.Timestamp(games.Date_dt.max())
        report['last_date'] = str(last.date())
        if (day-last.normalize()).days > 120:
            reasons.append('stale_history')
        report['sample5_dates'] = [str(pd.Timestamp(value).date()) for value in games.tail(5).Date_dt]
        report['sample10_dates'] = [str(pd.Timestamp(value).date()) for value in games.tail(10).Date_dt]
    report['status'] = ('available' if len(games) >= 5 else 'limited' if len(games) else 'unavailable')
    return _finish(report)


def _finish(report):
    report['reason_codes'] = list(dict.fromkeys(report['reason_codes']))
    report['reasons'] = [FORM_REASONS.get(code, code) for code in report['reason_codes']]
    report['summary'] = (f"ฟอร์มตามสนามทั้งหมด {report['full_history_venue_count']} นัด; "
        f"ฤดูกาล {report['requested_season'] or 'ไม่ยืนยัน'} {report['current_season_venue_count']} นัด; "
        f"ฤดูกาลก่อน {report['prior_season_venue_count']} นัด; ไม่ยืนยันฤดูกาล {report['unknown_season_venue_count']} นัด")
    if report['reasons']:
        report['summary'] += ' · '+' · '.join(report['reasons'])
    report['summary'] += ' · '+report['policy']
    return report
