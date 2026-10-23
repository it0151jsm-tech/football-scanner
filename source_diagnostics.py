"""Saved, read-only source explanations; no HTTP, probabilities or substitute xG."""
from copy import deepcopy
import pandas as pd

from domestic_data import safe_match_team, season_for_kickoff


SOURCE_MESSAGES = {
    'forbidden':'HTTP 403 ถูกปฏิเสธ', 'rate_limited':'HTTP 429 จำกัดคำขอ',
    'http_error':'HTTP ไม่สำเร็จ', 'download_error':'โหลดข้อมูลไม่สำเร็จ',
    'unsupported':'ไม่มีแหล่ง xG รองรับ', 'unsupported_source':'ไม่มีแหล่ง xG รองรับ',
    'empty_payload':'ไฟล์/ข้อมูลว่าง', 'empty_history':'ไม่มีผลที่ใช้ได้',
    'invalid_json':'อ่าน JSON ไม่สำเร็จ', 'invalid_schema':'โครงสร้างข้อมูลไม่ตรง',
    'missing_columns':'ขาดคอลัมน์ xG ที่จำเป็น', 'ok':'โหลดข้อมูลแล้ว',
    'unsupported_xg':'ลีกนี้ไม่มี Understat รองรับ', 'partial':'โหลดได้บางส่วน — มีฤดูกาลหรือแถวที่ไม่ผ่าน',
    'invalid_payload':'โครงสร้างข้อมูล Understat ไม่ตรง', 'invalid_rows':'ไม่มีแถว xG ผ่านการตรวจ',
    'conflicting_fixtures':'เกม/สกอร์/xG ขัดแย้ง — แยกออกก่อนคำนวณ',
}


def csv_form_ready(frame, fixtures):
    """Source eligibility only: current membership and existing five-game form.

    This never changes form scoring or fills a gap; it only decides whether an
    explicit scan needs to try its already configured optional score provider.
    """
    from history_data import team_history, match_team
    from readiness import instant
    required = {'Date_dt', 'HomeTeam', 'AwayTeam', '_history_season'}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return False
    for fixture in fixtures:
        kickoff = instant(fixture.get('commence_time'))
        if kickoff is None:
            return False
        day = pd.Timestamp(kickoff).tz_convert('UTC').tz_localize(None).normalize()
        before = frame[pd.to_datetime(frame.Date_dt, errors='coerce') < day]
        current = before[before['_history_season'].astype(str).eq(season_for_kickoff(kickoff))]
        for side in ('home', 'away'):
            team = fixture.get(side+'_team')
            matched, _ = match_team(team, set(current.HomeTeam) | set(current.AwayTeam))
            history, _ = team_history(frame, team, kickoff, side)
            if matched is None or len(history) < 5:
                return False
            last = pd.to_datetime(history.Date_dt, errors='coerce').max()
            if pd.isna(last) or (day-pd.Timestamp(last).normalize()).days > 120:
                return False
    return bool(fixtures)


def xg_diagnostics(frame, home, away, kickoff, *, supported=True):
    """Explain provider failure separately from name, venue and freshness failures."""
    attrs=getattr(frame,'attrs',{})
    report=deepcopy(attrs.get('xg_diagnostics') or attrs.get('history_diagnostics') or {})
    status=report.get('status') or ('ok' if frame is not None and not frame.empty else 'empty_history')
    if not supported:
        status='unsupported'
    result={'provider':'Understat','status':status,'source':report,
            'eligibility':deepcopy(attrs.get('xg_eligibility',{})),
            'cutoff_date':str(pd.Timestamp(kickoff).tz_convert('UTC').date()),
            'requested_season':season_for_kickoff(kickoff)}
    day=pd.Timestamp(kickoff).tz_convert('UTC').tz_localize(None).normalize()
    columns={'Date_dt','HomeTeam','AwayTeam','xG_home','xG_away'}
    usable=frame is not None and not frame.empty and columns.issubset(frame.columns)
    if frame is not None and not frame.empty and not usable:
        result['status']='missing_columns'
    for side,name,venue in (('home',home,'HomeTeam'),('away',away,'AwayTeam')):
        info={'input_name':name,'status':'source_unavailable','matched_name':None,'sample':0}
        if usable:
            clean=frame[pd.to_datetime(frame.Date_dt,errors='coerce')<day].copy()
            candidate,method=safe_match_team(name,set(clean.HomeTeam)|set(clean.AwayTeam))
            info.update(mapping=method,matched_name=candidate)
            if candidate is None:
                info['status']='name_unmatched' if method!='name_ambiguous' else 'name_ambiguous'
            else:
                sample=clean[clean[venue].eq(candidate)].tail(10).dropna(subset=['xG_home','xG_away'])
                last=sample.Date_dt.max() if not sample.empty else None
                info.update(sample=len(sample),last_date=str(last.date()) if last is not None and pd.notna(last) else None)
                info['status']=('no_venue_history' if sample.empty else
                    'stale' if (day-pd.Timestamp(last).normalize()).days>120 else
                    'insufficient_sample' if len(sample)<5 else 'available')
        result[side]=info
    source_notes=[]
    for item in report.get('sources',[]):
        note=f"{item.get('season','?')}: {item.get('status','unknown')} HTTP {item.get('http_status','?')}"
        for key,label in (('received_at','รับ UTC'),('url','URL'),('payload_sha256','SHA256')):
            if item.get(key):
                note+=f" · {label} {item[key]}"
        source_notes.append(note)
    source_label=SOURCE_MESSAGES.get(result['status'],result['status'])
    http_codes=sorted({str(item['http_status']) for item in report.get('sources',[])
                       if item.get('http_status') not in (None,200)})
    if http_codes:
        source_label+=' (HTTP '+', '.join(http_codes)+')'
    if not supported:
        source_label=SOURCE_MESSAGES['unsupported']
    side_labels={'available':'มีตัวอย่างตามสนามครบอย่างน้อย 5 นัด',
        'source_unavailable':'ไม่มีข้อมูลจากผู้ให้บริการ','name_unmatched':'จับคู่ชื่อทีมไม่ได้',
        'name_ambiguous':'ชื่อกำกวม','no_venue_history':'ไม่มีเกมตามสนามก่อนวันแข่ง',
        'insufficient_sample':'ตัวอย่างตามสนามไม่ถึง 5 นัด','stale':'ข้อมูลเก่าเกิน 120 วัน'}
    result['summary']='Understat: '+source_label+' · '+' · '.join(
        ('เจ้าบ้าน' if side=='home' else 'ทีมเยือน')+': '+side_labels[result[side]['status']]+
        f" ({result[side]['sample']} นัด)" for side in ('home','away'))
    if result['eligibility'].get('excluded_unknown_time') or result['eligibility'].get('quarantined_unknown_time'):
        result['summary']+=' · พักข้อมูลที่ยังยืนยันเวลาก่อน cutoff ไม่ได้'
    if result['eligibility'].get('excluded_received_after_analysis'):
        result['summary']+=' · ไม่ใช้ข้อมูลที่รับหลังเวลาวิเคราะห์'
    result['details']=result['summary']+(' · '+' | '.join(source_notes) if source_notes else
        ' · ไม่มีหลักฐานเวลารับข้อมูลต้นทางที่บันทึกไว้')
    return result


def annotate_form_source(profile, frame, team, kickoff, sport_key):
    """Add evidence labels without altering the original form calculations."""
    diagnostic=profile.setdefault('diagnostics',{})
    season=season_for_kickoff(kickoff)
    source=deepcopy(getattr(frame,'attrs',{}).get('history_diagnostics',{}))
    diagnostic.update(source=source,requested_sport_key=sport_key,requested_season=season)
    api_report = deepcopy(getattr(frame, 'attrs', {}).get('api_football_diagnostics', {}))
    if api_report:
        from api_football_data import api_football_status_text, BASE_URL
        from readiness import instant
        diagnostic['api_football'] = api_report
        diagnostic['message'] = str(diagnostic.get('message', ''))+' · '+api_football_status_text(api_report)
        received = instant(api_report.get('received_at'))
        if received is not None:
            diagnostic['message'] += ' · รับ API-Football UTC: '+received.isoformat()+' · '+BASE_URL
    received=[item['received_at'] for item in source.get('sources',[]) if item.get('received_at')]
    if received:
        diagnostic['source_received_at']=received
        usable_receipts=[item['received_at'] for item in source.get('sources',[])
                         if item.get('received_at') and item.get('status')=='ok' and item.get('valid_rows',0)>0]
        if usable_receipts:
            diagnostic['message']=str(diagnostic.get('message',''))+' · รับแหล่งผลที่ใช้ได้ UTC: '+max(usable_receipts)
    if frame is None or frame.empty or '_history_season' not in frame:
        diagnostic['membership_status']='not_verified'
        return
    before=frame[pd.to_datetime(frame.Date_dt,errors='coerce')<pd.Timestamp(kickoff).tz_convert('UTC').tz_localize(None).normalize()]
    current=before[before['_history_season'].astype(str).eq(season)]
    candidate,method=safe_match_team(team,set(current.HomeTeam)|set(current.AwayTeam))
    diagnostic['membership_status']='current_source_matched' if candidate is not None else 'current_membership_unverified'
    diagnostic['membership_mapping']=method
    if candidate is None:
        diagnostic['message']=str(diagnostic.get('message',''))+f' · ยังยืนยันชื่อทีมในลีก/ฤดูกาล {season} ที่เลือกไม่ได้; ไม่อนุมานจากสมาชิกลีกเก่า'
