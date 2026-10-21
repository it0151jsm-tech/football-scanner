"""Market-line estimation and symmetric side comparison; no Poisson/EV formula."""
import math


MAIN_RULE = ('ประมาณ Main OU จากคู่ Over/Under เจ้ามือ เส้น และรอบบันทึกเดียวกัน; '
             'ผลรวม implied probability 0.98–1.20 และความห่างจากสมดุลหลังตัด margin ≤0.08; '
             'ดูจำนวนเจ้ามือที่รองรับเส้นก่อน แล้วดูความสมดุล ไม่ใช้ราคาใกล้ 2.00 เพียงอย่างเดียว; '
             'เส้นเสมอกันและความสมดุลต่าง <0.02 ถือว่ายืนยันไม่ได้')


def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def select_main_line(choices):
    """Estimate a line from saved quote contracts, independently of model coverage.

    The numerical main-line criteria are unchanged. Provider identity and receipt
    metadata prevent two real contracts being joined merely because titles match.
    Legacy rows without keys may still describe market information; the existing
    freshness gate separately prevents such rows entering a new value bet.
    """
    batches = {}
    valid_rows = []
    diagnostics = dict(input_quotes=len(choices), valid_quotes=0, invalid_quotes=0,
                       unpaired_groups=0, complete_pairs=0, eligible_pairs=0,
                       candidate_lines=0, provider_key_conflicts=0)
    for entry in choices:
        row = entry['row']
        provider_key = row.get('bookmaker_key')
        if (row.get('bookmaker') in (None,'','Unknown','-') or not row.get('scanned_at') or
                row.get('side') not in ('over', 'under') or
                not finite(row.get('point')) or row['point'] < 0 or
                abs(row['point']*4-round(row['point']*4)) > 1e-9 or
                not finite(row.get('odds')) or row['odds'] <= 1 or
                (provider_key is not None and (not isinstance(provider_key,str) or not provider_key.strip()))):
            diagnostics['invalid_quotes'] += 1
            continue
        identity = ('key',provider_key) if provider_key else ('legacy_title',row['bookmaker'])
        key = (row.get('event_id'),identity,row['bookmaker'],row['point'],row['scanned_at'],
               row.get('received_at'),row.get('source_updated_at'))
        batches.setdefault(key,{})[row['side']] = entry
        valid_rows.append((row,identity))
    diagnostics['valid_quotes'] = len(valid_rows)
    # A title collision is evidence to preserve, never permission to combine keys.
    title_batches = {}
    for row,_ in valid_rows:
        key = (row.get('event_id'),row['bookmaker'],row['point'],row['scanned_at'])
        title_batches.setdefault(key,set()).add(row.get('bookmaker_key'))
    diagnostics['provider_key_conflicts'] = sum(len(keys)>1 for keys in title_batches.values())
    pairs = []
    for (_,identity,book,line,at,received,updated),sides in batches.items():
        if set(sides) != {'over','under'}:
            diagnostics['unpaired_groups'] += 1
            continue
        over,under = sides['over']['row']['odds'],sides['under']['row']['odds']
        margin = 1/over+1/under
        balance = abs((1/over)/margin-.5)
        pairs.append(dict(bookmaker=book,line=line,scanned_at=at,over=sides['over'],under=sides['under'],
                          bookmaker_identity=identity,received_at=received,source_updated_at=updated,
                          margin=margin,balance=balance,eligible=.98 <= margin <= 1.20 and balance <= .08))
    candidates = {}
    for pair in pairs:
        if pair['eligible']:
            candidates.setdefault(pair['line'],[]).append(pair)
    support = lambda books: len({p['bookmaker'] for p in books})
    ranked = sorted(candidates.items(),key=lambda item:(-support(item[1]),sum(p['balance'] for p in item[1])/len(item[1]),item[0]))
    diagnostics.update(complete_pairs=len(pairs),eligible_pairs=sum(p['eligible'] for p in pairs),candidate_lines=len(candidates))
    result = dict(status='unconfirmed',line=None,pair=None,pairs=pairs,rule=MAIN_RULE,
                  diagnostics=diagnostics,reason_code='missing_market',
                  reason='ไม่สามารถยืนยันเส้นหลักได้: ไม่พบราคา OU ที่บันทึกไว้')
    if not ranked:
        if not choices:
            return result
        if not valid_rows:
            result.update(reason_code='invalid_quotes',reason=f'ไม่สามารถยืนยันเส้นหลักได้: ราคา OU ไม่ผ่านการตรวจสอบ {diagnostics["invalid_quotes"]} รายการ')
        elif pairs:
            result.update(reason_code='unbalanced_pairs',reason=f'ไม่สามารถยืนยันเส้นหลักได้: พบคู่ราคา {len(pairs)} คู่ แต่ไม่มีคู่ผ่านเกณฑ์ margin 0.98–1.20 และความสมดุล ≤0.08')
        else:
            sides = {row['side'] for row,_ in valid_rows}
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
