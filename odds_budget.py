"""Persistent, atomic request reservations for instances sharing ONE ledger file.
No global guarantee across separate Cloud apps/filesystems. No raw keys are stored.
"""
import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class ScanStopped(RuntimeError):
    """A safety stop that applies to the entire remaining scan."""
    halt_all = True

    def __init__(self, message, *, reason='safety_stop', status=None):
        super().__init__(message)
        self.reason, self.status = reason, status


class LeagueScanError(ScanStopped):
    """A safe, per-league failure; do not abort unrelated selected leagues.

    Inherits ScanStopped for legacy callers that conservatively catch all API
    failures. New orchestration must distinguish this class before ScanStopped.
    Messages must be static/sanitized, never provider bodies or request URLs.
    """
    halt_all = False


def settings():
    try:
        return dict(requests=int(os.getenv('VALUE_BET_API_DAILY_REQUESTS','40')),
                    credits=int(os.getenv('VALUE_BET_API_DAILY_CREDITS','80')),
                    monthly_credits=int(os.getenv('VALUE_BET_API_MONTHLY_CREDITS','500')),
                    reserve=int(os.getenv('VALUE_BET_API_CREDIT_RESERVE','10')),
                    cooldown=max(300,int(os.getenv('VALUE_BET_API_COOLDOWN_SECONDS','300'))))
    except (ValueError,TypeError):
        raise ScanStopped('หยุดสแกน: การตั้งค่างบ API ไม่ถูกต้อง') from None


class Budget:
    def __init__(self, path=None, bill_path=None):
        self.path=Path(path or os.getenv('VALUE_BET_API_LEDGER','data/odds_api_budget.db'))
        if bill_path is not None and self.path.resolve()==Path(bill_path).resolve():
            raise ScanStopped('ไฟล์งบ API ต้องแยกจากฐานบิล')
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS calls (id INTEGER PRIMARY KEY, key_hash TEXT, day TEXT, scope TEXT, reserved_at REAL, credits INTEGER, status TEXT)')
            con.execute('CREATE TABLE IF NOT EXISTS balances (key_hash TEXT PRIMARY KEY, remaining INTEGER, used INTEGER, blocked TEXT)')

    def connect(self):
        return sqlite3.connect(self.path,timeout=30)

    def reserve(self, key, sport, region, now=None, limits=None):
        now=now or datetime.now(timezone.utc);limits=limits or settings()
        if not isinstance(now,datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ScanStopped('หยุดสแกน: เวลาบันทึกงบไม่มี timezone',reason='budget_clock_invalid')
        now=now.astimezone(timezone.utc)
        if region not in ('uk','eu','us'):
            raise LeagueScanError('ภูมิภาคราคาไม่รองรับ: เลือก uk, eu หรือ us หนึ่งภูมิภาค',reason='invalid_region')
        if not isinstance(key,str) or not key.strip() or not isinstance(sport,str) or not sport.strip():
            raise ScanStopped('หยุดสแกน: ไม่มีคีย์หรือรหัสลีกที่ตรวจสอบได้',reason='invalid_request_identity')
        monthly=limits.get('monthly_credits',500)
        if (any(type(limits.get(name)) is not int for name in ('requests','credits','reserve','cooldown'))
                or type(monthly) is not int or not 1<=monthly<=500):
            raise ScanStopped('หยุดสแกน: การตั้งค่างบ API ไม่ถูกต้อง',reason='budget_settings_invalid')
        key_hash=hashlib.sha256(key.encode()).hexdigest();day=now.date().isoformat();scope=json.dumps([sport,region])
        if limits['requests']<=0 or limits['credits']<2 or limits['reserve']<0:
            raise ScanStopped('งบ API ไม่อนุญาตให้ส่งคำขอ',reason='budget_disabled')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            state=con.execute('SELECT remaining,blocked FROM balances WHERE key_hash=?',(key_hash,)).fetchone()
            if state and state[1]:raise ScanStopped('หยุดสแกน: สิทธิ์/โควตา API ต้องได้รับการตรวจสอบก่อน',reason='provider_blocked')
            count,cost=con.execute('SELECT COUNT(*),COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND day=?',(key_hash,day)).fetchone()
            if count>=limits['requests'] or cost+2>limits['credits']:
                raise ScanStopped('หยุดสแกน: ถึงงบคำขอ/เครดิตรายวัน',reason='daily_budget_reached')
            month_cost=con.execute('SELECT COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND day LIKE ?',
                (key_hash,day[:7]+'-%')).fetchone()[0]
            if month_cost+2>max(0,monthly-limits['reserve']):
                raise ScanStopped('หยุดสแกน: ถึงงบเครดิตรายเดือนหลังหักสำรอง',reason='monthly_budget_reached')
            if state and state[0] is not None and state[0]-2<limits['reserve']:
                raise ScanStopped('หยุดสแกน: เครดิตคงเหลือไม่พอหลังหักเงินสำรอง',reason='provider_balance_reserve')
            recent=con.execute('SELECT MAX(reserved_at) FROM calls WHERE key_hash=? AND scope=?',(key_hash,scope)).fetchone()[0]
            if recent is not None and now.timestamp()-recent<max(300,limits['cooldown']):
                raise LeagueScanError('ข้ามลีกที่เพิ่งเรียก API: อยู่ในช่วงพักการสแกนซ้ำ',reason='cooldown')
            cur=con.execute('INSERT INTO calls(key_hash,day,scope,reserved_at,credits,status) VALUES(?,?,?,?,2,?)',(key_hash,day,scope,now.timestamp(),'reserved'))
            con.execute('INSERT OR IGNORE INTO balances(key_hash) VALUES(?)',(key_hash,))
            con.execute('UPDATE balances SET remaining=remaining-2 WHERE key_hash=? AND remaining IS NOT NULL',(key_hash,))
            return cur.lastrowid

    def complete(self, call_id, status, headers):
        normalized={str(key).lower():value for key,value in dict(headers or {}).items()}
        def number(name):
            try:
                raw=normalized[name]
                if isinstance(raw,bool) or (isinstance(raw,float) and not raw.is_integer()):return None
                value=int(raw);return value if value>=0 else None
            except (KeyError,TypeError,ValueError):return None
        actual,remaining,used=(number(n) for n in ('x-requests-last','x-requests-remaining','x-requests-used'))
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT key_hash,credits,status FROM calls WHERE id=?',(call_id,)).fetchone()
            if row is None:raise ScanStopped('ไม่พบหลักฐานการจองงบ API')
            if row[2]!='reserved':
                if str(status)==row[2] and (actual is None or actual==row[1]):
                    # A repeated callback cannot refresh an older balance or
                    # charge/refund the same reservation a second time.
                    return
                raise ScanStopped('หยุดสแกน: ผลการใช้งบรายการนี้ถูกบันทึกแล้ว',reason='completion_conflict')
            con.execute('UPDATE calls SET credits=?,status=? WHERE id=?',(actual if actual is not None else row[1],str(status),call_id))
            # Headers describe provider usage, but concurrent responses can arrive out of order.
            # Only advance a provider balance when its cumulative used counter is newer.
            previous=con.execute('SELECT used FROM balances WHERE key_hash=?',(row[0],)).fetchone()[0]
            if remaining is not None and used is not None and (previous is None or used>=previous):
                in_flight=con.execute("SELECT COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND status='reserved'",(row[0],)).fetchone()[0]
                con.execute('UPDATE balances SET remaining=?,used=? WHERE key_hash=?',(remaining-in_flight,used,row[0]))
            blocked='HTTP '+str(status) if status in (401,402,403,429) else ('unexpected credit cost' if actual is not None and actual>2 else None)
            if blocked:con.execute('UPDATE balances SET blocked=? WHERE key_hash=?',(blocked,row[0]))
        if blocked:raise ScanStopped('หยุดสแกน: สิทธิ์/โควตา/ต้นทุน API ไม่ผ่านเกณฑ์',reason='provider_safety_stop',status=status)

    def snapshot(self,key,now=None,limits=None):
        """Safe accounting metadata only; never returns the raw API key.

        Calendar month/day are UTC local-ledger limits, not a promise that the
        provider's billing month resets on the same day. Separate files/apps do
        not share reservations; provider headers are still required to see
        account-wide consumption from other clients.
        """
        now=now or datetime.now(timezone.utc)
        if now.tzinfo is None or now.utcoffset() is None:
            raise ScanStopped('หยุดสแกน: เวลาบันทึกงบไม่มี timezone',reason='budget_clock_invalid')
        now=now.astimezone(timezone.utc);limits=limits or settings()
        digest=hashlib.sha256(key.encode()).hexdigest();day=now.date().isoformat()
        with self.connect() as con:
            daily=con.execute('SELECT COUNT(*),COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND day=?',(digest,day)).fetchone()
            monthly=con.execute('SELECT COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND day LIKE ?',(digest,day[:7]+'-%')).fetchone()[0]
            balance=con.execute('SELECT remaining,used,blocked FROM balances WHERE key_hash=?',(digest,)).fetchone()
        return dict(requests_today=daily[0],credits_today=daily[1],credits_month=monthly,
            daily_request_limit=limits['requests'],daily_credit_limit=limits['credits'],
            monthly_credit_limit=limits.get('monthly_credits',500),credit_reserve=limits['reserve'],
            provider_remaining=balance[0] if balance else None,provider_used=balance[1] if balance else None,
            provider_blocked=bool(balance and balance[2]),accounting_timezone='UTC',
            scope='instances sharing this ledger only; not global across Cloud apps')


class OddsBatch(list):
    """A cached response keeps its receipt even when it contains no fixtures."""
    def __init__(self, values, received_at):
        super().__init__(values)
        self.received_at=received_at
