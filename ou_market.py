"""Market-line estimation and symmetric side comparison; no Poisson/EV formula."""
import math
from datetime import datetime, timedelta, timezone

from readiness import instant, quote_freshness


MAIN_RULE = ('ประมาณ Main OU จากคู่ Over/Under เจ้ามือ เส้น และรอบบันทึกเดียวกัน; '
             'ผลรวม implied probability 0.98–1.20 และความห่างจากสมดุลหลังตัด margin ≤0.08; '
             'ดูจำนวนเจ้ามือที่รองรับเส้นก่อน แล้วดูความสมดุล ไม่ใช้ราคาใกล้ 2.00 เพียงอย่างเดียว; '
             'เส้นเสมอกันและความสมดุลต่าง <0.02 ถือว่ายืนยันไม่ได้')

METRIC_DEFINITIONS = {
    'implied_sum': 'S = 1/ราคาสูง + 1/ราคาต่ำ; เกณฑ์เดิม 0.98 ≤ S ≤ 1.20 (เดิมใช้ชื่อ margin)',
    'overround': 'Overround = S − 1; แสดงเป็น % โดยคูณ 100 ไม่ใช่ค่า S',
    'balance': 'Balance = |(1/ราคาสูง)/S − 0.5|; เกณฑ์เดิม ≤ 0.08',
    'boundary': 'เกณฑ์รวมค่าขอบเขต 0.98, 1.20 และ 0.08; ชดเชยเฉพาะการปัดเศษเลขทศนิยมของเครื่องไม่เกิน 1e−12',
    'interpretation': 'อัตรา implied และสัดส่วนหลัง normalize เป็นข้อมูลราคา ไม่ใช่โอกาสชนะจากโมเดลหรือค่าที่ผ่าน Calibration; AH/OU ที่มี push/quarter line ยิ่งไม่ควรตีความเป็นความน่าจะเป็นชนะเต็ม',
}


def finite(v):
    try:
        return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)
    except OverflowError:
        return False


def _stamp(value):
    parsed = instant(value)
    # Aware representations of the same instant are compatible. Unknown timestamps
    # remain in diagnostics, but never establish an eligible main-line snapshot.
    return parsed.isoformat() if parsed is not None else ('unverified', str(value))


def _quote_id(entry):
    value = entry['row'].get('id')
    return int(value) if finite(value) else -1


def _snapshot_ids(entries):
    ids = []
    for entry in entries:
        snapshot = entry.get('snapshot')
        values = [entry['row'].get('snapshot_id')]
        if isinstance(snapshot, dict):
            values.append(snapshot.get('id'))
        for value in values:
            if value is not None and value not in ids:
                ids.append(value)
    return ids


def _contract(entry):
    row = entry['row']
    provider = row.get('bookmaker_key')
    identity = ('key', provider) if provider else ('legacy_title', row.get('bookmaker'))
    return (row.get('event_id'),identity,row.get('bookmaker'),row.get('point'),
            _stamp(row.get('scanned_at')),_stamp(row.get('received_at')),_stamp(row.get('source_updated_at')))


def _summary(sides, reason_code, reason):
    entries = [entry for group in sides.values() for entry in group]
    row = entries[0]['row']
    return dict(event_id=row.get('event_id'),bookmaker=row.get('bookmaker'),bookmaker_key=row.get('bookmaker_key'),
                line=row.get('point'),scanned_at=row.get('scanned_at'),received_at=row.get('received_at'),
                source_updated_at=row.get('source_updated_at'),quote_ids=[e['row'].get('id') for e in entries],
                snapshot_ids=_snapshot_ids(entries),
                sides={side:[e['row'] for e in group] for side,group in sides.items()},
                reason_code=reason_code,reason=reason)


def select_main_line(choices, now=None):
    """Diagnose every saved pair, then estimate a line only from verified fresh pairs.

    S (legacy field ``margin``) and balance have the exact original thresholds.
    Overround is separately labelled S-1; changing that label must not accidentally
    apply the S thresholds to a different financial quantity. No xG/EV is required
    to inspect market evidence, but unknown/stale metadata cannot qualify as Main.
    """
    now = now or datetime.now(timezone.utc)
    batches, valid_rows, invalid, unpaired = {}, [], [], []
    diagnostics = dict(input_quotes=len(choices), valid_quotes=0, invalid_quotes=0,
                       unpaired_groups=0, complete_pairs=0, eligible_pairs=0,
                       numeric_eligible_pairs=0, fresh_pairs=0, stale_or_unverified_pairs=0,
                       candidate_lines=0, provider_key_conflicts=0, conflicting_duplicate_groups=0,
                       identical_duplicate_quotes=0)
    for entry in choices:
        row = entry['row']
        provider_key = row.get('bookmaker_key')
        if (row.get('bookmaker') in (None,'','Unknown','-') or not row.get('scanned_at') or
                row.get('side') not in ('over', 'under') or
                not finite(row.get('point')) or row['point'] < 0 or not finite(row['point']*4) or
                abs(row['point']*4-round(row['point']*4)) > 1e-9 or
                not finite(row.get('odds')) or row['odds'] <= 1 or
                (provider_key is not None and (not isinstance(provider_key,str) or not provider_key.strip()))):
            diagnostics['invalid_quotes'] += 1
            invalid.append(dict(row=row,reason_code='invalid_quote',reason='ชื่อเจ้ามือ/เส้น/ราคา/ฝั่ง/รอบบันทึกไม่ผ่านการตรวจสอบ'))
            continue
        key = _contract(entry)
        batches.setdefault(key,{}).setdefault(row['side'],[]).append(entry)
        valid_rows.append(row)
    diagnostics['valid_quotes'] = len(valid_rows)
    title_batches = {}
    for row in valid_rows:
        key = (row.get('event_id'),row['bookmaker'],row['point'],_stamp(row.get('scanned_at')))
        title_batches.setdefault(key,set()).add(row.get('bookmaker_key'))
    diagnostics['provider_key_conflicts'] = sum(len(keys)>1 for keys in title_batches.values())
    pairs = []
    for contract,groups in batches.items():
        if any(len({e['row']['odds'] for e in group})>1 for group in groups.values()):
            diagnostics['conflicting_duplicate_groups'] += 1
            unpaired.append(_summary(groups,'conflicting_duplicate_side','พบราคาฝั่งเดียวกันขัดแย้งในสัญญา/Snapshot เดียวกัน — ไม่เลือกแถวสุดท้ายแทนหลักฐาน'))
            continue
        if set(groups) != {'over','under'}:
            unpaired.append(_summary(groups,'missing_compatible_side','ขาดอีกฝั่งที่มีคู่แข่งขัน/เจ้ามือ/เส้น/เวลา Snapshot ตรงกัน'))
            continue
        sides = {side:max(group,key=_quote_id) for side,group in groups.items()}
        diagnostics['identical_duplicate_quotes'] += sum(len(group)-1 for group in groups.values())
        over,under = sides['over']['row']['odds'],sides['under']['row']['odds']
        implied_sum = 1/over+1/under
        balance = abs((1/over)/implied_sum-.5)
        # Keep inclusive mathematical boundaries despite binary float roundoff.
        # This tolerance is ~10 orders smaller than the market eligibility bands.
        sum_ok = ((implied_sum >= .98 or math.isclose(implied_sum,.98,rel_tol=0,abs_tol=1e-12)) and
                  (implied_sum <= 1.20 or math.isclose(implied_sum,1.20,rel_tol=0,abs_tol=1e-12)))
        balance_ok = balance <= .08 or math.isclose(balance,.08,rel_tol=0,abs_tol=1e-12)
        reasons = []
        if not sum_ok:
            reasons.append('ผลรวม implied (Margin/S) อยู่นอกช่วง 0.98–1.20')
        if not balance_ok:
            reasons.append('Balance สูงกว่า 0.08')
        freshness_reasons = []
        for side in ('over','under'):
            fresh,reason=quote_freshness(sides[side]['row'],now)
            scanned = instant(sides[side]['row'].get('scanned_at'))
            received = instant(sides[side]['row'].get('received_at'))
            if scanned is None:
                fresh,reason=False,'ไม่มีเวลาบันทึก Snapshot ที่มีเขตเวลายืนยันได้'
            elif scanned > now + timedelta(seconds=30) or (received is not None and scanned < received - timedelta(seconds=30)):
                fresh,reason=False,'ลำดับเวลาบันทึก Snapshot กับเวลารับราคาไม่ถูกต้อง'
            if not fresh:
                freshness_reasons.append(('Over' if side=='over' else 'Under')+': '+reason)
        fresh = not freshness_reasons
        reasons.extend(freshness_reasons)
        numeric_eligible = sum_ok and balance_ok
        row=sides['over']['row']
        pairs.append(dict(event_id=row.get('event_id'),bookmaker=row['bookmaker'],bookmaker_key=row.get('bookmaker_key'),
                          line=row['point'],scanned_at=row.get('scanned_at'),over=sides['over'],under=sides['under'],
                          bookmaker_identity=contract[1],received_at=row.get('received_at'),source_updated_at=row.get('source_updated_at'),
                          quote_ids={side:[e['row'].get('id') for e in groups[side]] for side in ('over','under')},
                          snapshot_ids={side:_snapshot_ids(groups[side]) for side in ('over','under')},
                          margin=implied_sum,implied_sum=implied_sum,overround=implied_sum-1,
                          overround_pct=(implied_sum-1)*100,balance=balance,normalized_over=(1/over)/implied_sum,
                          sum_passed=sum_ok,balance_passed=balance_ok,numeric_eligible=numeric_eligible,
                          fresh=fresh,freshness_reasons=freshness_reasons,eligible=numeric_eligible and fresh,
                          reasons=reasons,reason='ผ่านเกณฑ์ตัวเลข (รวมค่าขอบเขต) และข้อมูล Snapshot สดที่ยืนยันได้' if not reasons else ' · '.join(reasons)))
    candidates = {}
    for pair in pairs:
        if pair['eligible']:
            candidates.setdefault(pair['line'],[]).append(pair)
    support = lambda books: len({p['bookmaker'] for p in books})
    ranked = sorted(candidates.items(),key=lambda item:(-support(item[1]),sum(p['balance'] for p in item[1])/len(item[1]),item[0]))
    diagnostics.update(unpaired_groups=len(unpaired),complete_pairs=len(pairs),eligible_pairs=sum(p['eligible'] for p in pairs),
                       numeric_eligible_pairs=sum(p['numeric_eligible'] for p in pairs),fresh_pairs=sum(p['fresh'] for p in pairs),
                       stale_or_unverified_pairs=sum(not p['fresh'] for p in pairs),candidate_lines=len(candidates))
    result = dict(status='unconfirmed',line=None,pair=None,pairs=pairs,unpaired=unpaired,invalid=invalid,rule=MAIN_RULE,
                  metric_definitions=METRIC_DEFINITIONS,diagnostics=diagnostics,reason_code='missing_market',
                  reason='ไม่สามารถยืนยันเส้นหลักได้: ไม่พบราคา OU ที่บันทึกไว้')
    if not ranked:
        if not choices:
            return result
        if not valid_rows:
            result.update(reason_code='invalid_quotes',reason=f'ไม่สามารถยืนยันเส้นหลักได้: ราคา OU ไม่ผ่านการตรวจสอบ {diagnostics["invalid_quotes"]} รายการ')
        elif pairs:
            if diagnostics['numeric_eligible_pairs']:
                result.update(reason_code='stale_or_unverified_snapshots',reason=f'ไม่สามารถยืนยันเส้นหลักได้: พบคู่ราคา {len(pairs)} คู่ แต่คู่ที่ผ่านเกณฑ์ตัวเลขมี Snapshot เก่า/ไม่มีหลักฐานความสดที่ยืนยันได้')
            else:
                result.update(reason_code='unbalanced_pairs',reason=f'ไม่สามารถยืนยันเส้นหลักได้: พบคู่ราคา {len(pairs)} คู่ แต่ไม่มีคู่ผ่านเกณฑ์ margin 0.98–1.20 และความสมดุล ≤0.08')
        elif diagnostics['conflicting_duplicate_groups']:
            result.update(reason_code='conflicting_duplicates',reason='ไม่สามารถยืนยันเส้นหลักได้: ราคาฝั่งเดียวกันขัดแย้งใน Snapshot เดียวกัน — ยังไม่แนะนำ')
        else:
            sides = {row['side'] for row in valid_rows}
            if len(sides)==1:
                missing='Under (ต่ำ)' if 'over' in sides else 'Over (สูง)'
                result.update(reason_code='missing_side',reason=f'ไม่สามารถยืนยันเส้นหลักได้: มีราคา OU {len(valid_rows)} รายการ แต่ไม่มีฝั่ง {missing} ที่จับคู่ได้')
            elif diagnostics['provider_key_conflicts']:
                result.update(reason_code='provider_key_conflict',reason='ไม่สามารถยืนยันเส้นหลักได้: ชื่อเจ้ามือเหมือนกันแต่ bookmaker_key ต่างกัน — ไม่รวมราคาข้ามเจ้ามือ')
            else:
                result.update(reason_code='contract_mismatch',reason=f'ไม่สามารถยืนยันเส้นหลักได้: มีราคาทั้งสองฝั่ง {len(valid_rows)} รายการ แต่คู่แข่งขัน/เจ้ามือ/เส้น/Snapshot ไม่ตรงกัน ({diagnostics["unpaired_groups"]} กลุ่มที่จับคู่ไม่ได้)')
        return result
    line,books = ranked[0]
    if len(ranked)>1:
        _,runner = ranked[1]
        book_support = support(books)
        runner_support = support(runner)
        gap = sum(p['balance'] for p in runner)/len(runner)-sum(p['balance'] for p in books)/len(books)
        if book_support == runner_support and gap < .02:
            result['reason_code'] = 'ambiguous_lines'
            result['reason'] = 'ไม่สามารถยืนยันเส้นหลักได้: หลายเส้นมีหลักฐานใกล้เคียงกัน'
            return result
    pair = min(books,key=lambda p:(p['balance'],abs(p['margin']-1),p['bookmaker']))
    result.update(status='estimated',line=line,pair=pair,
                  reason_code='estimated',
                  reason=f'ประมาณจากราคาคู่จริง {support(books)} เจ้ามือในเส้นนี้; ไม่ยืนยันว่าเป็นเส้นหลักของทุกเว็บไซต์')
    return result


def compare_sides(main):
    """No directional default; a tie never resolves by row ID or API order."""
    pair = main.get('pair')
    if pair is None:
        return None,'ไม่สามารถยืนยันเส้นหลักได้ — ยังไม่เลือกฝั่ง'
    if not all(pair[s]['model_valid'] and pair[s]['ev'] is not None for s in ('over','under')):
        return None,'ตัวเลือกตลาดที่กำลังประเมิน — ไม่ใช่คำแนะนำเดิมพัน; EV สองฝั่งยังไม่ครบ'
    if pair['over']['b'].get('expected_goals') != pair['under']['b'].get('expected_goals'):
        return None,'ผลโมเดลสองฝั่งไม่สอดคล้องกัน — ยังไม่เลือกฝั่ง'
    qualified = [pair[s] for s in ('over','under') if pair[s]['tier']==4 and pair[s]['ev'] is not None and pair[s]['ev']>0]
    if not qualified:
        return None,'ตัวเลือกตลาดที่กำลังประเมิน — ไม่ใช่คำแนะนำเดิมพัน; ไม่มีฝั่งที่มี A สนับสนุนและ EV บวกที่ยืนยันได้'
    if len(qualified)==1:
        chosen = qualified[0]
    else:
        if abs(qualified[0]['ev']-qualified[1]['ev']) <= 1e-9:
            return None,'หลักฐาน EV สองฝั่งเท่ากัน — ไม่ตัดสินด้วยราคา คะแนน A หรือเลขแถว'
        chosen = max(qualified,key=lambda e:e['ev'])
    return chosen,('เลือก '+('สูง' if chosen['row']['side']=='over' else 'ต่ำ')+
                   f' เพราะ EV {chosen["ev"]:+.2f}% พร้อม A สนับสนุนในเส้นหลักที่ประมาณ; เปรียบเทียบทั้งสองฝั่งของเจ้ามือ/รอบเดียวกัน')


def selection_counts(fixtures):
    counts = {'over':0,'under':0,'neutral':0,'missing_main':0}
    for fixture in fixtures:
        main = fixture.get('ou_main',{})
        if main.get('status')!='estimated':
            counts['missing_main'] += 1
        elif fixture.get('ou_direction') in ('over','under'):
            counts[fixture['ou_direction']] += 1
        else:
            counts['neutral'] += 1
    total = counts['over']+counts['under']
    return dict(counts,over_pct=100*counts['over']/total if total else None,
                under_pct=100*counts['under']/total if total else None,scope='สัดส่วนคำแนะนำในการ์ดที่กำลังแสดง ไม่ใช่อัตราชนะหรือข้อมูลทั้งระบบ')
