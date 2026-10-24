"""Identity/source health from observed labels only, without network access."""
from datetime import datetime, timezone

import pandas as pd

from history_data import match_team


def decode_health_audits(records):
    """Read damaged legacy diagnostics as unknown, preserving stored evidence."""
    import json
    audits = {}
    for record in records:
        key = record['sport_key']
        try:
            audit = json.loads(record['payload_json'])
            if not isinstance(audit, dict):
                raise ValueError('Invalid saved diagnostic')
        except (ValueError, TypeError):
            audit = {'diagnostic_status': 'invalid_saved_health',
                     'scope': 'ข้อมูลวินิจฉัยเดิมอ่านไม่ได้ — ยังไม่ยืนยันความพร้อม'}
        audit.setdefault('league', key)
        teams = audit.get('teams')
        audit['teams'] = []
        for team in teams if isinstance(teams, list) else []:
            if not isinstance(team, dict):
                continue
            team = dict(team)
            for provider in ('csv', 'understat'):
                if not isinstance(team.get(provider), dict) or not team[provider].get('status'):
                    team[provider] = {'status': 'unknown', 'message': 'ไม่มีหลักฐานวินิจฉัยเดิมที่อ่านได้'}
            audit['teams'].append(team)
        audits[key] = audit
    return audits


def check_source(frame,name,supported,as_of):
    if not supported:
        return dict(status='unsupported',message='ไม่มีแหล่งข้อมูลรองรับ',matched_name=None)
    if frame is None or frame.empty:
        report=getattr(frame,'attrs',{}).get('xg_diagnostics') or getattr(frame,'attrs',{}).get('history_diagnostics',{})
        codes=sorted({str(source['http_status']) for source in report.get('sources',[])
                      if source.get('http_status') not in (None,200)})
        reason=report.get('status')
        return dict(status='load_error',message='ไม่มีข้อมูลที่โหลดสำเร็จสำหรับตรวจชื่อ'+
                    (' · '+str(reason) if reason else '')+(' · HTTP '+', '.join(codes) if codes else ''),
                    reason_code=reason,source=report,matched_name=None)
    names = sorted(set(frame['HomeTeam'].dropna())|set(frame['AwayTeam'].dropna()))
    mapped,method = match_team(name,names)
    if not mapped:
        return dict(status='unmatched',message='จับคู่ไม่ได้',matched_name=None,method=method)
    dates = pd.to_datetime(frame.loc[(frame.HomeTeam==mapped)|(frame.AwayTeam==mapped),'Date_dt'],utc=True,errors='coerce')
    stamp = pd.Timestamp(as_of)
    dates = dates[dates<stamp]
    last = dates.max()
    stale = pd.isna(last) or (stamp-last).days>120
    return dict(status='stale' if stale else 'matched',message='ข้อมูลเก่า/ยังไม่มีเกมก่อนวันตรวจ' if stale else 'จับคู่สำเร็จ',
                matched_name=mapped,method=method,last_date=None if pd.isna(last) else last.isoformat())


def observe_league(key,label,csv_code,understat_supported,score_frame,xg_frame,matches,as_of):
    names = sorted({m.get(side) for m in matches for side in ('home_team','away_team') if m.get(side)})
    teams = [dict(api_name=name,csv=check_source(score_frame,name,bool(csv_code),as_of),
                  understat=check_source(xg_frame,name,understat_supported,as_of)) for name in names]
    problems = [team for team in teams if any(team[source]['status'] in ('unmatched','stale','load_error') for source in ('csv','understat'))]
    return dict(key=key,league=label,checked_at=as_of.isoformat(),teams_checked=len(teams),teams_with_problems=len(problems),teams=teams,
                scope='เฉพาะชื่อทีมที่ได้รับจากการสแกน API รอบนี้ ไม่ใช่ครบทุกทีมในลีก',
                csv_supported=bool(csv_code),understat_supported=understat_supported)


def table_rows(leagues,understat,audits,source_report):
    source_by_key = {r['key']:r for r in source_report.get('leagues',[])}
    rows = []
    for key,(label,csv) in leagues.items():
        audit = audits.get(key,{})
        source = source_by_key.get(key,{})
        readiness = scan_readiness_details(audit)
        rows.append({'ลีก':label,'ทีม API ที่ตรวจจริง':audit.get('teams_checked',0),
                     'ความพร้อมจากสแกนล่าสุด': readiness['summary'],
                     'ทีม API ที่ยังมีปัญหา':audit.get('teams_with_problems',0),
                     'CSV':('ไม่มีแหล่งข้อมูลรองรับ' if not csv else source.get('csv_current',{}).get('status','ยังไม่ได้ตรวจแหล่งสด')),
                     'Understat':('ไม่มีแหล่งข้อมูลรองรับ' if key not in understat else source.get('understat',{}).get('status','ยังไม่ได้ตรวจแหล่งสด')),
                     'ทีมใน CSV ปัจจุบัน':source.get('current_source_teams',0),
                     'ตรวจ CSV ↔ Understat':source.get('cross_source_checked',0),
                     'จับคู่ CSV ↔ Understat':source.get('cross_source_matched',0),
                     'ยังจับคู่ข้ามแหล่งไม่ได้':source.get('cross_source_checked',0)-source.get('cross_source_matched',0),
                     'เวลาตรวจ API':audit.get('checked_at','ยังไม่เคยสแกน'),
                     'ขอบเขต':audit.get('scope','ยังไม่มีชื่อ API ที่ตรวจจริง; ผลแหล่งภายนอกไม่ยืนยัน mapping กับ API')})
    return rows


def scan_readiness_details(audit, now=None):
    """Read saved scan evidence only; never relabel an old result as a live scan."""
    from readiness import instant
    from scan_diagnostics import scan_report_text, readiness_table_rows
    report = audit.get('scan_window')
    required = {'A_ready', 'api_returned', 'passed_filter', 'excluded_count', 'hours_limit', 'start_th', 'end_th'}
    fixtures = report.get('per_fixture_readiness') if isinstance(report, dict) else None
    valid_fixtures = isinstance(fixtures, list) and all(isinstance(item, dict) and
        isinstance(item.get('teams'), dict) and all(isinstance(item['teams'].get(side), dict)
        for side in ('home', 'away')) for item in fixtures)
    if not isinstance(report, dict) or not required.issubset(report) or not valid_fixtures:
        return {'summary': 'ยังไม่มีรายงานรายคู่แบบใหม่ — ไม่สรุปความพร้อมจากบิลย้อนหลัง', 'fixtures': []}
    now = now or datetime.now(timezone.utc)
    checked = instant(audit.get('checked_at'))
    historical = checked is None or (now-checked).total_seconds() > 900 or checked > now
    label = 'หลักฐานสแกนย้อนหลัง' if historical else 'หลักฐาน ณ เวลาสแกนล่าสุด (ไม่ใช่การเรียก API สด)'
    summary = label+' '+str(audit.get('checked_at', 'ไม่ทราบเวลา'))+' · '+scan_report_text(report)
    rows = readiness_table_rows(report)
    import json
    for row in rows:
        for side in ('เจ้าบ้าน', 'ทีมเยือน'):
            details = json.loads(row[side])
            row[side] = {key: details.get(key) for key in ('api_name', 'mapping', 'provider',
                'current_season_venue_count', 'prior_season_venue_count', 'full_history_venue_count', 'reason_codes')}
    return {'summary': summary, 'historical': historical, 'fixtures': rows,
            'source': audit.get('api_football', {}), 'window_start_th': report.get('start_th'),
            'window_end_th': report.get('end_th')}
