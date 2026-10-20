"""Read-only selection of saved quotes, never a new probability/EV model."""
import json
import math
from datetime import datetime

from mobile_dashboard import model_ready


MARKETS = ('Asian Handicap', 'Over/Under')
TREND = 'แนวโน้มจากสถิติ — ยังยืนยันความคุ้มค่าราคาไม่ได้'
RANKING = ('อันดับเฉพาะการ์ด: A ≥55 จากอย่างน้อย 5 ตัวอย่าง และ B/EV ตรงข้อมูลพร้อม EV บวกก่อน; '
           'ถัดมาฟอร์มที่ยังไม่มี EV, ข้อมูล EV ไม่บวก, และหลักฐานไม่พอ ตามลำดับ; '
           'ภายในกลุ่มดูจำนวนตัวอย่างฟอร์ม (ไม่เกิน 10), EV, เวลา snapshot, คะแนน A และรหัสแถว '
           'ไม่จัดอันดับด้วยราคาสูงสุด; ไม่เปลี่ยนสูตร A/B/C หรือการจัดบิล')


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def parse(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else {}
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def obj(value):
    return value if isinstance(value, dict) else {}


def timestamp(row):
    try:
        value = datetime.fromisoformat(str(row.get('scanned_at', '')).replace('Z', '+00:00'))
        # No invented timezone for malformed legacy timestamps.
        return value.timestamp() if value.tzinfo is not None else 0
    except (ValueError, TypeError, OverflowError):
        return 0


def row_id(row):
    value = row.get('id')
    return int(value) if finite(value) else 0


def assess(row, choose_c):
    """A/B, price, bookmaker and snapshot stay attached to this exact row."""
    a, b = parse(row.get('analysis_a_json')), parse(row.get('analysis_b_json'))
    score = a.get('score')
    has_form = finite(score) or any(finite(obj(a.get(k)).get('form_rate')) for k in ('form5', 'form10'))
    sample = max([obj(a.get(k)).get('form_sample', 0) if finite(obj(a.get(k)).get('form_sample', 0)) else 0 for k in ('form5', 'form10')])
    support = finite(score) and score >= 55 and sample >= 5
    valid = model_ready(b)
    reason = '' if valid else 'ข้อมูลโมเดลไม่เพียงพอ หรือยังไม่มีผล B ที่บันทึกไว้'
    # A model reference of 2.5 from an AH row is never a market OU quote.
    if row['market'] == 'Over/Under' and (not finite(b.get('ou_line')) or abs(b['ou_line'] - row['point']) > 1e-9):
        valid, reason = False, 'ผล OU ไม่ตรงเส้นตลาดที่บันทึกไว้ — ไม่ใช้เปอร์เซ็นต์จากเส้นอื่น'
    if row['market'] == 'Over/Under' and valid:
        ou = obj(b.get('ou'))
        if not all(finite(ou.get(k)) and 0 <= ou[k] <= 100 for k in ('over_full', 'under_full', 'partial_or_push')):
            valid, reason = False, 'ผล OU สำหรับเส้นนี้ไม่ครบ'
        elif abs(sum(ou[k] for k in ('over_full', 'under_full', 'partial_or_push')) - 100) > .1:
            valid, reason = False, 'ผล OU สำหรับเส้นนี้ไม่ครบ 100%'
    price = obj(a.get('price'))
    if price and (not finite(price.get('current_point')) or not finite(price.get('current_odds')) or
                  abs(price['current_point'] - row['point']) > 1e-9 or abs(price['current_odds'] - row['odds']) > 1e-9):
        valid, reason = False, 'ราคา/เส้นในหลักฐาน A ไม่ตรงแถวที่บันทึกไว้'
    selection = obj(b.get('selection'))
    probs_ok = all(finite(selection.get(k)) and 0 <= selection[k] <= 100 for k in ('p_win', 'p_partial', 'p_loss'))
    if valid and not probs_ok:
        valid, reason = False, 'ผลความน่าจะเป็นของตัวเลือกนี้ไม่ครบ'
    if valid and (abs(sum(selection[k] for k in ('p_win', 'p_partial', 'p_loss')) - 100) > .1 or
                  abs(sum(b['hda'][k] for k in ('home', 'draw', 'away')) - 100) > .1):
        valid, reason = False, 'ความน่าจะเป็นที่บันทึกไว้ไม่ครบ 100%'
    ev = selection.get('ev_pct') if valid else None
    if not finite(ev) or not finite(row.get('ev_pct')) or abs(ev - row['ev_pct']) > 1e-9:
        ev = None
        if valid:
            reason = 'EV ไม่มีค่า หรือ EV ในแถวไม่ตรงกับผล B ที่บันทึกไว้'
    if support and ev is not None and ev > 0:
        tier = 4
        conclusion = choose_c(score, ev, bool(row.get('odds_in_range', 1)))
        reason = 'A สนับสนุนและ B มี EV บวกสำหรับเส้น/ราคาของแถวนี้; ยังไม่ผ่าน Calibration'
    elif has_form and ev is None:
        tier = 3 if support else 1
        conclusion = TREND
        reason = reason or 'มีฟอร์ม แต่ยังไม่มี EV ที่ยืนยันกับราคานี้ได้'
        if sample < 5:
            reason += ' · ยังไม่แนะนำ: ตัวอย่างฟอร์มน้อยกว่า 5 นัด'
    elif support and ev is not None:
        tier, conclusion = 2, 'ยังไม่แนะนำ'
        reason = 'EV ไม่เป็นบวก แม้มีหลักฐานฟอร์ม; ไม่ยืนยันความคุ้มค่าราคา'
    else:
        tier, conclusion = 0, 'ยังไม่แนะนำ'
        reason = reason or 'หลักฐาน A ยังไม่สนับสนุน หรือข้อมูลฟอร์มไม่พอ'
    return {'row': row, 'a': a, 'b': b, 'model_valid': valid, 'ev': ev,
            'conclusion': conclusion, 'reason': reason, 'tier': tier,
            'rank': (tier, min(10, max(0, sample)), ev if ev is not None else -float('inf'),
                     timestamp(row), score if finite(score) else -1, row_id(row))}


def group_fixture_recommendations(rows, choose_c):
    """Group by event ID and use the latest saved quote per market/side/bookmaker."""
    fixtures = {}
    for position, row in enumerate(rows):
        event_id = row.get('event_id')
        # Missing IDs are isolated; never merge by a possibly duplicated team name.
        key = ('event', event_id) if isinstance(event_id, str) and event_id.strip() else ('row', position)
        fixtures.setdefault(key, []).append(dict(row))
    output = []
    for event_rows in fixtures.values():
        anchor = max(event_rows, key=lambda r: (timestamp(r), row_id(r)))
        identity = tuple(anchor.get(k) for k in ('home_team', 'away_team', 'league'))
        # Conflicting identities must not be combined even if legacy event_id was reused.
        coherent = [r for r in event_rows if tuple(r.get(k) for k in ('home_team', 'away_team', 'league')) == identity]
        markets, alternatives = {}, {}
        for market in MARKETS:
            latest = {}
            for row in coherent:
                if row.get('market') != market:
                    continue
                if row.get('side') not in (('home', 'away') if market == MARKETS[0] else ('over', 'under')):
                    continue
                if not row.get('bookmaker') or not finite(row.get('point')) or not finite(row.get('odds')) or row['odds'] <= 1:
                    continue
                key = (row['side'], row['bookmaker'])
                if key not in latest or (timestamp(row), row_id(row)) > (timestamp(latest[key]), row_id(latest[key])):
                    latest[key] = row
            choices = sorted((assess(row, choose_c) for row in latest.values()), key=lambda item: item['rank'], reverse=True)
            markets[market] = choices[0] if choices else None
            alternatives[market] = choices
        output.append({'event_id': anchor.get('event_id'), 'row': anchor, 'markets': markets,
                       'alternatives': alternatives, 'identity_conflict': len(coherent) != len(event_rows)})
    return sorted(output, key=lambda item: (str(item['row'].get('kickoff_utc') or ''), str(item.get('event_id') or '')))
