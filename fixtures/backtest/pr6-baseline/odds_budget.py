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
    pass


def settings():
    try:
        return dict(requests=int(os.getenv('VALUE_BET_API_DAILY_REQUESTS','10')),
                    credits=int(os.getenv('VALUE_BET_API_DAILY_CREDITS','20')),
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
        key_hash=hashlib.sha256(key.encode()).hexdigest();day=now.date().isoformat();scope=json.dumps([sport,region])
        if limits['requests']<=0 or limits['credits']<2 or limits['reserve']<0:
            raise ScanStopped('งบ API ไม่อนุญาตให้ส่งคำขอ')
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            state=con.execute('SELECT remaining,blocked FROM balances WHERE key_hash=?',(key_hash,)).fetchone()
            if state and state[1]:raise ScanStopped('หยุดสแกน: สิทธิ์/โควตา API ต้องได้รับการตรวจสอบก่อน')
            count,cost=con.execute('SELECT COUNT(*),COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND day=?',(key_hash,day)).fetchone()
            if count>=limits['requests'] or cost+2>limits['credits']:
                raise ScanStopped('หยุดสแกน: ถึงงบคำขอ/เครดิตรายวัน')
            if state and state[0] is not None and state[0]-2<limits['reserve']:
                raise ScanStopped('หยุดสแกน: เครดิตคงเหลือไม่พอหลังหักเงินสำรอง')
            recent=con.execute('SELECT MAX(reserved_at) FROM calls WHERE key_hash=? AND scope=?',(key_hash,scope)).fetchone()[0]
            if recent is not None and now.timestamp()-recent<max(300,limits['cooldown']):
                raise ScanStopped('หยุดสแกนซ้ำ: กรุณารอช่วงพักการเรียก API')
            cur=con.execute('INSERT INTO calls(key_hash,day,scope,reserved_at,credits,status) VALUES(?,?,?,?,2,?)',(key_hash,day,scope,now.timestamp(),'reserved'))
            con.execute('INSERT OR IGNORE INTO balances(key_hash) VALUES(?)',(key_hash,))
            con.execute('UPDATE balances SET remaining=remaining-2 WHERE key_hash=? AND remaining IS NOT NULL',(key_hash,))
            return cur.lastrowid

    def complete(self, call_id, status, headers):
        def number(name):
            try:
                value=int(headers[name]);return value if value>=0 else None
            except (KeyError,TypeError,ValueError):return None
        actual,remaining,used=(number(n) for n in ('x-requests-last','x-requests-remaining','x-requests-used'))
        with self.connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row=con.execute('SELECT key_hash,credits FROM calls WHERE id=?',(call_id,)).fetchone()
            if row is None:raise ScanStopped('ไม่พบหลักฐานการจองงบ API')
            con.execute('UPDATE calls SET credits=?,status=? WHERE id=?',(actual if actual is not None else row[1],str(status),call_id))
            # Headers describe provider usage, but concurrent responses can arrive out of order.
            # Only advance a provider balance when its cumulative used counter is newer.
            previous=con.execute('SELECT used FROM balances WHERE key_hash=?',(row[0],)).fetchone()[0]
            if remaining is not None and used is not None and (previous is None or used>=previous):
                in_flight=con.execute("SELECT COALESCE(SUM(credits),0) FROM calls WHERE key_hash=? AND status='reserved'",(row[0],)).fetchone()[0]
                con.execute('UPDATE balances SET remaining=?,used=? WHERE key_hash=?',(remaining-in_flight,used,row[0]))
            blocked='HTTP '+str(status) if status in (401,429) else ('unexpected credit cost' if actual is not None and actual>2 else None)
            if blocked:con.execute('UPDATE balances SET blocked=? WHERE key_hash=?',(blocked,row[0]))
        if blocked:raise ScanStopped('หยุดสแกน: สิทธิ์/โควตา/ต้นทุน API ไม่ผ่านเกณฑ์')


class OddsBatch(list):
    """A cached response keeps its receipt even when it contains no fixtures."""
    def __init__(self, values, received_at):
        super().__init__(values)
        self.received_at=received_at
