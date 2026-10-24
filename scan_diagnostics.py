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
    supplied_receipt = instant(getattr(matches, 'received_at', None))
    records = list(matches)
    receipts = {instant(row.get('_received_at')) for row in records if isinstance(row, dict)} - {None}
    api_receipt = supplied_receipt or (next(iter(receipts)) if len(receipts) == 1 else None)
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
                 'home_team': row.get('home_team') if isinstance(row, dict) else None,
                 'away_team': row.get('away_team') if isinstance(row, dict) else None,
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
        'api_received_at': api_receipt.isoformat() if api_receipt is not None else None,
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
    if 'A_ready' in report:
        c_count = str(report['C_supported']) if report.get('C_policy_evaluated') else 'ยังไม่ได้ตรวจเกณฑ์'
        summary += (f" | ความพร้อมจากสแกนปัจจุบัน A {report['A_ready']} / B {report['B_ready']} / "
                    f"C ผ่านเกณฑ์ {c_count} คู่; B ยังไม่ผ่าน Calibration")
        if any(report.get(key) for key in ('A_unknown', 'B_unknown', 'C_unknown')):
            summary += (f" | ยังยืนยันไม่ได้ A {report['A_unknown']} / B {report['B_unknown']} / "
                        f"C {report['C_unknown']} คู่")
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


def finalize_scan_report(report, rows, now=None, *, choose_c=None, current_scan_rows=False):
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
    result.update(fixture_readiness_report(report, rows, now=now, choose_c=choose_c,
                                           current_scan_rows=current_scan_rows))
    return result


FORM_REASON_LABELS = {
    'name_unmatched': 'จับคู่ชื่อทีมไม่ได้', 'name_ambiguous': 'ชื่อทีมกำกวม',
    'lossy_normalization': 'ชื่อมีอักขระที่ยังยืนยันไม่ได้',
    'no_current_season': 'ยังไม่ยืนยันผลในลีก/ฤดูกาลปัจจุบัน',
    'insufficient_venue_history': 'ผลตามสนามก่อนแข่งน้อยกว่า 5 นัด (ไม่ใช่ข้อบังคับการให้คะแนน A)',
    'after_cutoff': 'ผลอยู่หลังเวลาตัดข้อมูล', 'provider_failure': 'แหล่งข้อมูลโหลดไม่สำเร็จ',
    'invalid_history': 'แถวผลย้อนหลังไม่ผ่านการตรวจสอบ',
    'no_decisive_results': 'ผลเทียบเส้นเป็นคืนทุนทั้งหมด จึงไม่มีคะแนนจากผลชี้ขาด',
    'no_valid_quote': 'ไม่มีราคาตลาดที่ใช้ประเมินเส้นนี้ได้',
    'a_score_unavailable': 'สูตรเดิมยังไม่มีคะแนน A',
    'unknown': 'หลักฐานวินิจฉัยที่บันทึกไว้ยังไม่ครบ',
    'unknown_season': 'แหล่งข้อมูลไม่ยืนยันฤดูกาล',
    'stale_history': 'ผลย้อนหลังเก่าเกิน 120 วัน',
    'empty_history': 'แหล่งนี้ไม่มีผลย้อนหลังที่ใช้ได้',
}


def _form_readiness(profile):
    """Summarize saved evidence; five-game labels do not change A scoring."""
    profile = profile if isinstance(profile, dict) else {}
    diagnostic = profile.get('diagnostics')
    diagnostic = diagnostic if isinstance(diagnostic, dict) else {}
    declared = diagnostic.get('readiness')
    declared = declared if isinstance(declared, dict) else {}
    source_codes = declared.get('reason_codes')
    source_codes = [code for code in source_codes if isinstance(code, str)] if isinstance(source_codes, list) else []
    aliases = {'no_current_season_results': 'no_current_season',
               'insufficient_venue_samples': 'insufficient_venue_history',
               'historical_source_invalidity': 'invalid_history'}
    codes = [aliases.get(code, code) for code in source_codes]
    status, mapping = diagnostic.get('status'), declared.get('mapping', diagnostic.get('mapping'))
    if status in ('name_unmatched', 'name_ambiguous', 'lossy_normalization'):
        codes.append(status)
    elif status in ('http_error', 'download_error', 'forbidden', 'rate_limited', 'parse_error'):
        codes.append('provider_failure')
    elif status in ('invalid_dates', 'invalid_scores', 'invalid_season', 'missing_columns', 'conflicting_scores'):
        codes.append('invalid_history')
    elif status == 'no_history_before_kickoff':
        codes.append('after_cutoff')
    membership = declared.get('current_membership', diagnostic.get('membership_status'))
    if membership in ('current_membership_unverified', 'not_verified'):
        codes.append('no_current_season')
    if _count(profile.get('sample5')) < 5 and (profile or diagnostic):
        codes.append('insufficient_venue_history')
    season_counts = diagnostic.get('season_counts')
    season_counts = season_counts if isinstance(season_counts, dict) else {}
    requested = str(diagnostic.get('requested_season') or '')
    current = declared.get('current_season_venue_count')
    current = current if type(current) is int and current >= 0 else (
        _count(season_counts[requested]) if requested in season_counts else None)
    prior = declared.get('prior_season_venue_count')
    prior = prior if type(prior) is int and prior >= 0 else (
        sum(_count(value) for season, value in season_counts.items() if season != requested)
        if requested and season_counts else None)
    full = declared.get('full_history_venue_count', diagnostic.get('venue_before_count'))
    full = full if type(full) is int and full >= 0 else None
    return dict(
        status=declared.get('status') or status or 'unknown', mapping=mapping or 'unknown',
        reason_codes=sorted(set(codes)) or ([] if status == 'ok' else ['unknown']),
        source_reason_codes=source_codes,
        sample5=_count(profile.get('sample5')), sample10=_count(profile.get('sample10')),
        current_season_venue_count=current, prior_season_venue_count=prior,
        full_history_venue_count=full, current_membership=membership or 'unknown',
        matched_name=declared.get('matched_name', diagnostic.get('matched_name')),
        current_season_team_count=declared.get('current_season_team_count'),
        unknown_season_venue_count=declared.get('unknown_season_venue_count'),
        provider=declared.get('provider') or diagnostic.get('source', {}).get('provider')
        if isinstance(diagnostic.get('source', {}), dict) else declared.get('provider'),
        source=deepcopy(declared.get('source') or diagnostic.get('source') or {}),
        date_exclusions=deepcopy(declared.get('date_exclusions') or
                                {'after_cutoff_count': declared.get('after_cutoff_count')}),
        form5_gate='availability_label_and_C_requirement; not_A_scoring_requirement',
    )


def _readiness_scope(row, entry, report, now, current_scan_rows):
    if entry is None:
        return 'outside_current_scan'
    kickoff, start, end = (instant(value) for value in
                            (row.get('kickoff_utc'), report.get('start_utc'), report.get('end_utc')))
    if kickoff is None or start is None or end is None:
        return 'unverified_kickoff'
    if kickoff <= now or not start < kickoff <= end:
        return 'outside_current_window'
    expected = instant(entry.get('kickoff_utc'))
    if expected is not None and kickoff != expected:
        return 'fixture_identity_conflict'
    if any(entry.get(field) and entry[field] != row.get(field) for field in ('home_team', 'away_team')):
        return 'fixture_identity_conflict'
    if row.get('status') not in (None, '', 'รอตรวจผล'):
        return 'settled_record'
    received = instant(row.get('received_at'))
    if received is None:
        return 'unverified_receipt'
    if received > now + timedelta(seconds=30):
        return 'invalid_receipt'
    if (now - received).total_seconds() > 900:
        return 'historical_receipt'
    api_received = instant(report.get('api_received_at'))
    if api_received is not None and received != api_received:
        return 'different_snapshot'
    if api_received is None and not current_scan_rows and received < start - timedelta(seconds=30):
        return 'unverified_current_scan'
    return 'current_scan'


def fixture_readiness_report(report, rows, now=None, *, choose_c=None, current_scan_rows=False):
    """Readiness is saved evidence plus the existing market policy, never a model.

    Archived/started/settled/unverified-receipt rows cannot count as current.
    Temporary in-memory IDs and the actual assessment timestamp allow the exact
    existing C policy to inspect new scan rows before SQLite assigns IDs. All
    original quote/source timestamps remain unchanged.
    """
    from mobile_dashboard import model_ready
    wall_clock = datetime.now(UTC)
    now = now or wall_clock
    if now.tzinfo is None:
        raise ValueError('เวลาตรวจความพร้อมต้องมี timezone')
    now = now.astimezone(UTC)
    accepted = {entry.get('event_id'): entry for entry in report.get('fixtures', [])
                if isinstance(entry, dict) and entry.get('reason') is None and entry.get('event_id')}
    grouped, excluded = {}, Counter()
    for row in rows:
        if not isinstance(row, dict):
            continue
        event_id = row.get('event_id')
        if not isinstance(event_id, str):
            excluded['missing_identity'] += 1
            continue
        scope = _readiness_scope(row, accepted.get(event_id), report, now, current_scan_rows)
        if scope != 'current_scan':
            excluded[scope] += 1
            continue
        grouped.setdefault(event_id, []).append(row)
    policy_rows = []
    for event_rows in grouped.values():
        for row in event_rows:
            if row.get('market') not in ('Asian Handicap', 'Over/Under'):
                continue
            copied = dict(row, id=len(policy_rows) + 1)
            if not copied.get('scanned_at') and current_scan_rows:
                copied['scanned_at'] = now.isoformat()
            policy_rows.append(copied)
    supported, policy_error = set(), None
    # The existing recommendation policy checks quote freshness against the
    # real clock. It has no validated historical/as-of execution mode, so a
    # replay must remain unknown rather than mix two clocks or imply backtest.
    if callable(choose_c) and abs((now - wall_clock).total_seconds()) > 30:
        policy_error = 'historical_clock_not_supported'
    if callable(choose_c) and policy_error is None:
        try:
            from market_recommendations import recommended_pick_ids
            supported_ids = recommended_pick_ids(policy_rows, choose_c)
            supported = {(row['event_id'], row['market']) for row in policy_rows if row['id'] in supported_ids}
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            policy_error = 'invalid_saved_market_evidence'
    fixtures, a_reasons, missing, unknown_markets = [], Counter(), Counter(), Counter()
    for event_id, entry in accepted.items():
        event_rows = grouped.get(event_id, [])
        analyses = [(_analysis(row.get('analysis_a_json')), _analysis(row.get('analysis_b_json')))
                    for row in event_rows]
        a_values = [a for a, _ in analyses if a]
        b_values = [b for _, b in analyses if b]
        a_ready = any(_finite_number(a.get('score')) for a in a_values) if a_values else None
        b_ready = any(model_ready(b) for b in b_values) if b_values else None
        teams = {}
        for side in ('home', 'away'):
            evidence = [_form_readiness(a.get(side + '_form')) for a in a_values]
            profile = deepcopy(evidence[0]) if evidence else _form_readiness(None)
            if evidence:
                profile['reason_codes'] = sorted({code for item in evidence for code in item['reason_codes']})
            profile['api_name'] = entry.get(side + '_team') or (event_rows[0].get(side + '_team') if event_rows else None)
            teams[side] = profile
        codes = {code for profile in teams.values() for code in profile['reason_codes']}
        forms = [form for a in a_values for key in ('form5', 'form10', 'form_all')
                 for form in [a.get(key)] if isinstance(form, dict)]
        if not a_ready:
            if forms and any(form.get('form_sample', 0) for form in forms) and all(form.get('form_rate') is None for form in forms):
                if any('คืนทุนทั้งหมด' in str(form.get('form_summary', '')) or form.get('decisive_sample') == 0 for form in forms):
                    codes.add('no_decisive_results')
            codes.add('a_score_unavailable' if a_values else 'unknown')
        markets = {}
        for market in ('Asian Handicap', 'Over/Under'):
            quotes = [row for row in event_rows if row.get('market') == market and
                      _finite_number(row.get('point')) and _finite_number(row.get('odds')) and row['odds'] > 1]
            fresh = sum(quote_freshness(row, now)[0] for row in quotes)
            market_supported = (event_id, market) in supported
            market_codes = ([] if quotes else ['no_valid_quote']) + ([] if fresh or not quotes else ['stale_or_unverified_quote'])
            markets[market] = dict(present=bool(quotes), fresh_quotes=fresh,
                status='unknown' if not event_rows else 'available' if quotes else 'missing_market',
                C_supported=market_supported if event_rows and callable(choose_c) and policy_error is None else None,
                reason_codes=market_codes if event_rows else ['no_current_analysis_evidence'])
            if not event_rows:
                unknown_markets[market] += 1
            elif not quotes:
                missing[market] += 1
        if not any(market['present'] for market in markets.values()):
            codes.add('no_valid_quote')
        c_supported = any(key[0] == event_id for key in supported) if callable(choose_c) and policy_error is None else None
        if not event_rows:
            scope, a_ready, b_ready, c_supported = 'no_current_analysis_evidence', None, None, None
        else:
            scope = 'current_scan'
        a_reasons.update(codes if a_ready is not True else ())
        b_codes, b_data = set(), []
        for b in b_values:
            evidence = b.get('data_readiness')
            if isinstance(evidence, dict):
                b_data.append(deepcopy(evidence))
                if b_ready is not True:
                    reason_codes = evidence.get('reason_codes')
                    if isinstance(reason_codes, list):
                        b_codes.update(code for code in reason_codes if isinstance(code, str))
        if b_ready is not True and not b_codes:
            for a in a_values:
                xg = a.get('xg')
                provider = xg.get('diagnostics') if isinstance(xg, dict) else None
                if isinstance(provider, dict) and provider.get('status'):
                    b_codes.add(str(provider['status']))
            if not b_codes:
                b_codes.add('unknown' if b_ready is None else 'b_unavailable_without_saved_reason')
        c_codes = []
        if c_supported is not True:
            if c_supported is None:
                c_codes.append(policy_error or ('unknown' if event_rows else 'no_current_analysis_evidence'))
            else:
                if not any(market['present'] for market in markets.values()):
                    c_codes.append('no_valid_quote')
                if b_ready is not True:
                    c_codes.append('b_unavailable')
                evs = [row.get('ev_pct') for row in event_rows if _finite_number(row.get('ev_pct'))]
                if not evs:
                    c_codes.append('ev_unavailable')
                elif not any(ev > 0 for ev in evs):
                    c_codes.append('ev_not_positive')
                if any(market['present'] for market in markets.values()) and not any(market['fresh_quotes'] for market in markets.values()):
                    c_codes.append('stale_or_unverified_quote')
                c_codes.append('market_policy_not_supported')
        fixtures.append(dict(event_id=event_id, home_team=teams['home']['api_name'], away_team=teams['away']['api_name'],
            kickoff_utc=entry.get('kickoff_utc'), scope=scope, A_ready=a_ready, B_ready=b_ready, C_supported=c_supported,
            A_status='available' if a_ready else 'unavailable' if a_ready is False else 'unknown',
            B_status=('available_uncalibrated' if not all(b.get('calibrated') is True for b in b_values if model_ready(b)) else 'available')
                if b_ready else 'unavailable' if b_ready is False else 'unknown',
            C_status='supported_uncalibrated' if c_supported else 'not_supported' if c_supported is False else 'unknown',
            A_reason_codes=sorted(codes), B_reason_codes=sorted(b_codes), B_data_readiness=b_data,
            C_reason_codes=c_codes, teams=teams, markets=markets,
            source_received_at=sorted({row['received_at'] for row in event_rows if row.get('received_at')})))
    return dict(
        A_ready=sum(item['A_ready'] is True for item in fixtures),
        B_ready=sum(item['B_ready'] is True for item in fixtures),
        C_supported=sum(item['C_supported'] is True for item in fixtures),
        A_unknown=sum(item['A_ready'] is None for item in fixtures),
        B_unknown=sum(item['B_ready'] is None for item in fixtures),
        C_unknown=sum(item['C_supported'] is None for item in fixtures),
        C_policy_evaluated=callable(choose_c) and policy_error is None,
        C_policy_error=policy_error,
        A_missing_reason_counts=dict(a_reasons), missing_market_counts=dict(missing),
        unknown_market_counts=dict(unknown_markets),
        readiness_excluded_rows=dict(excluded), per_fixture_readiness=fixtures,
        readiness_checked_at=now.isoformat(),
        readiness_scope='เฉพาะหลักฐานสแกนปัจจุบันที่ยืนยันคู่/เวลา/เวลารับ; ไม่รวมบิลหรือราคาเก่า; ≥5 นัดเป็นป้ายความพร้อม/C ไม่เปลี่ยนเกณฑ์คะแนน A; ไม่รับรองความแม่นยำหรือกำไร',
    )


def readiness_table_rows(report):
    """Plain values for the existing Data Health/details table; no UI changes."""
    return [dict(
        คู่=f"{item.get('home_team') or '?'} vs {item.get('away_team') or '?'}",
        A=item.get('A_status', 'unknown'), B=item.get('B_status', 'unknown'), C=item.get('C_status', 'unknown'),
        เหตุผล_A=' · '.join(FORM_REASON_LABELS.get(code, code) for code in item.get('A_reason_codes', [])),
        เหตุผล_B=('คำนวณได้ แต่ยังไม่ผ่าน Calibration' if item.get('B_status') == 'available_uncalibrated' else
            ' · '.join(dict.fromkeys(value.get('message', '') for value in item.get('B_data_readiness', [])
                if isinstance(value, dict) and value.get('message'))) or ' · '.join(item.get('B_reason_codes', []))),
        เหตุผล_C=' · '.join({
            'no_valid_quote': 'ไม่มีราคาที่ใช้ได้', 'b_unavailable': 'B ยังไม่พร้อม',
            'ev_unavailable': 'ยังไม่มี EV ที่ยืนยันได้', 'ev_not_positive': 'EV ไม่เป็นบวก',
            'stale_or_unverified_quote': 'ราคาเก่าหรือยังยืนยันความสดไม่ได้',
            'market_policy_not_supported': 'ยังไม่ผ่านเกณฑ์ A/ตัวอย่าง/เส้นหลัก/EV ของตลาดเดิม',
            'historical_clock_not_supported': 'ไม่ประเมินคำแนะนำย้อนหลังด้วยเวลาปัจจุบัน',
            'no_current_analysis_evidence': 'ไม่มีหลักฐานสแกนปัจจุบัน',
        }.get(code, code) for code in item.get('C_reason_codes', [])) or
            ('ผ่านเกณฑ์เดิม แต่ยังไม่ผ่าน Calibration' if item.get('C_supported') is True else 'ยังไม่ยืนยัน'),
        เจ้าบ้าน=json.dumps(item.get('teams', {}).get('home', {}), ensure_ascii=False),
        ทีมเยือน=json.dumps(item.get('teams', {}).get('away', {}), ensure_ascii=False),
        AH=_market_label(item.get('markets', {}).get('Asian Handicap', {})),
        OU=_market_label(item.get('markets', {}).get('Over/Under', {})),
        ขอบเขต=item.get('scope', 'unknown'),
    ) for item in report.get('per_fixture_readiness', [])]


def _market_label(market):
    if market.get('status') == 'unknown':
        return 'ยังยืนยันราคาปัจจุบันไม่ได้'
    return 'มีราคา' if market.get('present') else 'ไม่มีราคาตลาด'
