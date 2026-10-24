"""Manual quote validation; existing model functions are injected unchanged."""
import json
import math
from datetime import datetime, timezone

from mobile_dashboard import model_ready


def validate_quote(bookmaker,line,over,under):
    if not isinstance(bookmaker,str) or not bookmaker.strip() or len(bookmaker)>120:
        raise ValueError('ระบุชื่อเว็บ/เจ้ามือไม่เกิน 120 ตัวอักษร')
    if any(type(v) not in (int,float) or not math.isfinite(v) for v in (line,over,under)):
        raise ValueError('เส้นและราคาต้องเป็นตัวเลขที่ถูกต้อง')
    if not 0 <= line <= 20 or abs(line*4-round(line*4))>1e-9 or over<=1 or under<=1:
        raise ValueError('เส้นต้องทีละ 0.25 ระหว่าง 0–20 และราคา decimal มากกว่า 1')
    return dict(bookmaker=bookmaker.strip(),point=float(line),odds_over=float(over),odds_under=float(under),source='MANUAL')


def evaluate_manual(row,line,over,under,now,score_matrix,evaluate_market):
    unavailable = {'available':False,'reason':'ไม่มีโมเดล B ที่ยืนยันแหล่ง xG และความสดได้ — ไม่สร้าง EV ทดแทน'}
    if not row:
        return unavailable
    try:
        a,b = json.loads(row.get('analysis_a_json') or '{}'),json.loads(row.get('analysis_b_json') or '{}')
        if not isinstance(a,dict) or not isinstance(b,dict):
            return unavailable
        scan = datetime.fromisoformat(row['scanned_at'])
        kickoff = datetime.fromisoformat(row['kickoff_utc'])
        if scan.tzinfo is None or kickoff.tzinfo is None or not 0 <= (now-scan).total_seconds() <= 86400 or kickoff<=now:
            return unavailable
        if row.get('model_status')!='Poisson จาก xG/xGA จริง (Understat)' or not model_ready(b):
            return unavailable
        goals = [row.get('lambda_home'),row.get('lambda_away')]
        if goals != b['expected_goals'] or any(type(v) not in (int,float) or not math.isfinite(v) or not .1<=v<=4.5 for v in goals):
            return unavailable
        for side in ('home','away'):
            xg = a['xg'][side]
            date = datetime.fromisoformat(xg['last_date']).date()
            if xg['sample']<5 or not 0 < (kickoff.astimezone(timezone.utc).date()-date).days <=120:
                return unavailable
        matrix = score_matrix(*goals)
        results = {side:evaluate_market(matrix,'totals',side,line,price) for side,price in [('over',over),('under',under)]}
        if any(result is None for result in results.values()):
            return unavailable
        return dict(available=True,reason='EV จากโมเดล B เดิมสำหรับเส้น/ราคา Manual นี้; ไม่ใช้ A ของเส้นอื่นและยังไม่ผ่าน Calibration',
                    model_pick_id=int(row['id']),model_scanned_at=row['scanned_at'],results=results,calibrated=False)
    except (ValueError,TypeError,KeyError,OverflowError):
        return unavailable
