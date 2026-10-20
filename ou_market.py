"""Market-line estimation and symmetric side comparison; no Poisson/EV formula."""
import math


MAIN_RULE = ('ประมาณ Main OU จากคู่ Over/Under เจ้ามือ เส้น และรอบบันทึกเดียวกัน; '
             'ผลรวม implied probability 0.98–1.20 และความห่างจากสมดุลหลังตัด margin ≤0.08; '
             'ดูจำนวนเจ้ามือที่รองรับเส้นก่อน แล้วดูความสมดุล ไม่ใช้ราคาใกล้ 2.00 เพียงอย่างเดียว; '
             'เส้นเสมอกันและความสมดุลต่าง <0.02 ถือว่ายืนยันไม่ได้')


def finite(v):
    return isinstance(v,(int,float)) and not isinstance(v,bool) and math.isfinite(v)


def select_main_line(choices):
    batches = {}
    for entry in choices:
        row = entry['row']
        if row.get('bookmaker') in (None,'','Unknown','-') or not row.get('scanned_at') or not finite(row.get('point')) or row['point'] < 0 or abs(row['point']*4-round(row['point']*4)) > 1e-9:
            continue
        key = (row['bookmaker'],row['point'],row['scanned_at'])
        batches.setdefault(key,{})[row['side']] = entry
    pairs = []
    for (book,line,at),sides in batches.items():
        if set(sides) != {'over','under'}:
            continue
        over,under = sides['over']['row']['odds'],sides['under']['row']['odds']
        if not all(finite(p) and p>1 for p in (over,under)):
            continue
        margin = 1/over+1/under
        balance = abs((1/over)/margin-.5)
        pairs.append(dict(bookmaker=book,line=line,scanned_at=at,over=sides['over'],under=sides['under'],
                          margin=margin,balance=balance,eligible=.98 <= margin <= 1.20 and balance <= .08))
    candidates = {}
    for pair in pairs:
        if pair['eligible']:
            candidates.setdefault(pair['line'],[]).append(pair)
    ranked = sorted(candidates.items(),key=lambda item:(-len({p['bookmaker'] for p in item[1]}),sum(p['balance'] for p in item[1])/len(item[1]),item[0]))
    result = dict(status='unconfirmed',line=None,pair=None,pairs=pairs,reason='ไม่สามารถยืนยันเส้นหลักได้: ไม่มีคู่ราคาที่ครบ/สมดุลเพียงพอ',rule=MAIN_RULE)
    if not ranked:
        return result
    line,books = ranked[0]
    if len(ranked)>1:
        _,runner = ranked[1]
        support = len({p['bookmaker'] for p in books})
        runner_support = len({p['bookmaker'] for p in runner})
        gap = sum(p['balance'] for p in runner)/len(runner)-sum(p['balance'] for p in books)/len(books)
        if support == runner_support and gap < .02:
            result['reason'] = 'ไม่สามารถยืนยันเส้นหลักได้: หลายเส้นมีหลักฐานใกล้เคียงกัน'
            return result
    pair = min(books,key=lambda p:(p['balance'],abs(p['margin']-1),p['bookmaker']))
    result.update(status='estimated',line=line,pair=pair,
                  reason=f'ประมาณจากราคาคู่จริง {len(books)} เจ้ามือในเส้นนี้; ไม่ยืนยันว่าเป็นเส้นหลักของทุกเว็บไซต์')
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
