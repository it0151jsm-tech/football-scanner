"""Identity/source health from observed labels only, without network access."""
from datetime import datetime, timezone

import pandas as pd

from history_data import match_team


def check_source(frame,name,supported,as_of):
    if not supported:
        return dict(status='unsupported',message='ไม่มีแหล่งข้อมูลรองรับ',matched_name=None)
    if frame is None or frame.empty:
        return dict(status='load_error',message='ไม่มีข้อมูลที่โหลดสำเร็จสำหรับตรวจชื่อ',matched_name=None)
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
        rows.append({'ลีก':label,'ทีม API ที่ตรวจจริง':audit.get('teams_checked',0),
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
