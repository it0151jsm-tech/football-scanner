"""Explicit scan windows and fixture exclusion evidence; no network or models."""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import math

from readiness import instant, quote_freshness

UTC = timezone.utc
TH_TZ = timezone(timedelta(hours=7))


def scan_window(now, hours_limit=24):
    """Use elapsed 24/48 hours from an aware instant, with Thai display bounds."""
    if isinstance(hours_limit, bool) or hours_limit not in (24, 48):
        raise ValueError('ช่วงเวลาสแกนต้องเป็น 24 หรือ 48 ชั่วโมง')
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError('เวลาวิเคราะห์ต้องมี timezone ที่ยืนยันได้')
    start = now.astimezone(UTC)
    return start, start + timedelta(hours=hours_limit)


REASONS = {
    'invalid_fixture': 'ข้อมูลคู่แข่งขันไม่ใช่รายการที่อ่านได้',
    'missing_identity': 'ไม่มีรหัสคู่หรือชื่อทีมที่ตรวจสอบได้',
    'league_mismatch': 'รหัสลีกไม่ตรงลีกที่เลือกสแกน',
    'invalid_kickoff': 'ไม่มีเวลาแข่งพร้อม timezone ที่ยืนยันได้',
    'already_started': 'เริ่มแข่งขันแล้วหรือถึงเวลาแข่งขัน',
    'outside_window': 'อยู่นอกช่วงเวลาล่วงหน้าที่เลือก',
    'duplicate_event': 'API ส่งคู่เดิมซ้ำ',
    'conflicting_event': 'API ส่งรหัสคู่เดียวกันแต่ทีม/เวลาแข่งขัดแย้ง',
}


def filter_scan_fixtures(matches, sport_key, now, hours_limit=24):
    """Classify every provider row once; return accepted rows without mutation.

    An absent embedded sport_key is allowed because the caller already requested
    the selected sport endpoint. A contradictory embedded key is rejected. IDs
    that identify multiple teams/kickoffs in one response are quarantined rather
    than choosing whichever record appeared first.
    """
    start, end = scan_window(now, hours_limit)
    records = list(matches)
    identities = {}
    for row in records:
        if (not isinstance(row, dict) or any(not isinstance(row.get(key), str) or not row[key].strip()
                for key in ('id', 'home_team', 'away_team'))):
            continue
        kickoff = instant(row.get('commence_time'))
        if kickoff is not None:
            identity = (row['home_team'], row['away_team'], kickoff, str(row.get('sport_key') or sport_key))
            identities.setdefault(row['id'], set()).add(identity)
    classified, candidates = [], []
    for index, row in enumerate(records):
        reason = None
        kickoff = None
        if not isinstance(row, dict):
            reason = 'invalid_fixture'
        elif any(not isinstance(row.get(key), str) or not row[key].strip()
                 for key in ('id', 'home_team', 'away_team')):
            reason = 'missing_identity'
        elif row.get('sport_key') not in (None, '', sport_key):
            reason = 'league_mismatch'
        else:
            kickoff = instant(row.get('commence_time'))
            if kickoff is None:
                reason = 'invalid_kickoff'
            elif kickoff <= start:
                reason = 'already_started'
            elif kickoff > end:
                reason = 'outside_window'
        entry = {'index': index, 'event_id': row.get('id') if isinstance(row, dict) else None,
                 'kickoff_utc': kickoff.isoformat() if kickoff is not None else None,
                 'reason': reason}
        classified.append(entry)
        if reason is None:
            candidates.append((row, entry, (row['home_team'], row['away_team'], kickoff)))
    selected, seen = [], set()
    for row, entry, _ in candidates:
        event_id = row['id']
        if len(identities[event_id]) > 1:
            entry['reason'] = 'conflicting_event'
        elif event_id in seen:
            entry['reason'] = 'duplicate_event'
        else:
            seen.add(event_id)
            selected.append(row)
    counts = Counter(entry['reason'] for entry in classified if entry['reason'])
    report = {
        'sport_key': sport_key, 'hours_limit': hours_limit,
        'start_utc': start.isoformat(), 'end_utc': end.isoformat(),
        'start_th': start.astimezone(TH_TZ).isoformat(),
        'end_th': end.astimezone(TH_TZ).isoformat(),
        'api_returned': len(records), 'passed_filter': len(selected),
        'excluded_count': len(records) - len(selected),
        'excluded_by_reason': dict(counts),
        'fixtures': classified,
    }
    return selected, report


def scan_report_text(report):
    """A compact audit summary for an existing details/caption area."""
    reasons = ' | '.join(f'{REASONS.get(key, key)}: {value}'
                         for key, value in report.get('excluded_by_reason', {}).items())
    summary = (f"API ส่งมา {report['api_returned']} คู่ | ผ่านตัวกรอง "
               f"{report['passed_filter']} คู่ | ตัดออก {report['excluded_count']} คู่ | "
               f"ช่วง {report['hours_limit']} ชั่วโมง (ไทย): "
               f"{report['start_th']} ถึง {report['end_th']}")
    if 'analyzed_fixtures' in report:
        summary += (f" | วิเคราะห์ {report['analyzed_fixtures']} คู่: มีราคา "
                    f"{report['priced_fixtures']} / ไม่มีราคา {report['no_price_fixtures']} | "
                    f"A มีคะแนน {report['a_available_fixtures']} คู่ | B พร้อม "
                    f"{report['b_available_fixtures']} คู่ | ตรวจ {report['teams_checked']} ทีม; "
                    f"ฟอร์มตามสนาม ≥5 นัด {report['team_venue_form5']}/{report['team_venue_checked']} ทีม+สนาม")
    if report.get('api_football_status'):
        summary += ' | '+report['api_football_status']
    return summary + (' | เหตุผล: ' + reasons if reasons else '')


def _analysis(value):
    try:
        parsed = json.loads(value) if isinstance(value, str) else value
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _count(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _finite_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def finalize_scan_report(report, rows, now=None):
    """Count evidence once per fixture/team context, never per bookmaker quote.

    Team identities use this scan's sport endpoint and exact API label; no team
    matching occurs here. A team counts as form-ready only when every venue in
    which this scan evaluates it has at least five prior results. Counts report
    availability, not accuracy, calibration or betting eligibility.
    """
    result = deepcopy(report)
    rows = list(rows)
    events, priced, a_ready, b_ready = set(), set(), set(), set()
    contexts, quotes = {}, {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id = row.get('event_id')
        if not isinstance(event_id, str) or not event_id:
            continue
        events.add(event_id)
        analysis_a, analysis_b = _analysis(row.get('analysis_a_json')), _analysis(row.get('analysis_b_json'))
        if _finite_number(analysis_a.get('score')):
            a_ready.add(event_id)
        if analysis_b.get('available') is True:
            b_ready.add(event_id)
        market = row.get('market')
        if (market in ('Asian Handicap', 'Over/Under') and _finite_number(row.get('odds'))
                and row['odds'] > 1 and _finite_number(row.get('point'))):
            priced.add(event_id)
            key = tuple(row.get(field) for field in ('event_id', 'market', 'side', 'point', 'odds',
                        'bookmaker', 'bookmaker_key', 'received_at', 'source_updated_at'))
            quotes[key] = row
        for venue in ('home', 'away'):
            name = row.get(venue + '_team')
            if not isinstance(name, str) or not name.strip():
                continue
            key = (report.get('sport_key'), name, venue)
            profile = analysis_a.get(venue + '_form')
            profile = profile if isinstance(profile, dict) else {}
            counts = (_count(profile.get('sample5')), _count(profile.get('sample10')))
            # Conflicting saved contexts never inflate coverage: retain the
            # lowest available sample instead of whichever quote came last.
            if key in contexts:
                counts = tuple(min(a, b) for a, b in zip(contexts[key], counts))
            contexts[key] = counts
    team_contexts = {}
    for (sport, name, venue), counts in contexts.items():
        team_contexts.setdefault((sport, name), []).append(counts)
    ready = sum(all(counts[0] >= 5 for counts in entries) for entries in team_contexts.values())
    quote_results = [quote_freshness(row, now=now) for row in quotes.values()]
    stale = Counter(reason for passed, reason in quote_results if not passed)
    result.update(
        analyzed_fixtures=len(events), priced_fixtures=len(priced),
        no_price_fixtures=len(events - priced), a_available_fixtures=len(a_ready),
        b_available_fixtures=len(b_ready), teams_checked=len(team_contexts),
        teams_with_form5=ready, team_venue_checked=len(contexts),
        team_venue_form5=sum(counts[0] >= 5 for counts in contexts.values()),
        team_venue_samples=[dict(sport_key=sport, api_name=name, venue=venue, sample5=counts[0], sample10=counts[1])
                            for (sport, name, venue), counts in sorted(contexts.items(), key=lambda pair: str(pair[0]))],
        unique_quotes=len(quotes), fresh_quotes=sum(passed for passed, _ in quote_results),
        rejected_quotes=sum(not passed for passed, _ in quote_results),
        rejected_quotes_by_reason=dict(stale),
        coverage_scope='เฉพาะคู่และชื่อทีมที่ผ่านตัวกรองในสแกนนี้; ฟอร์มตามสนามก่อน kickoff ≥5 นัด ไม่ใช่ความแม่นยำหรือการรับรอง EV',
    )
    return result
