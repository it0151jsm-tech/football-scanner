"""Read-only, mobile-first football facts; no model or provider dependencies.

Rendering never fetches. Only an explicit button invokes its supplied callback.
Every provider string is escaped before insertion into the scoped HTML markup.
"""
from collections import OrderedDict
from html import escape
import hashlib
import math


DATA_CENTER_CSS = '''<style>
.dc-card,.dc-card *,.dc-summary,.dc-summary *{box-sizing:border-box;min-width:0;overflow-wrap:anywhere;word-break:normal}
.dc-card{--dc-border:rgba(128,128,128,.22);--dc-accent:#12765c;--dc-tint:rgba(18,118,92,.08);width:100%;max-width:100%;margin:0 0 1rem;padding:1.1rem;border:1px solid var(--dc-border);border-radius:20px;background:var(--background-color,#fff);color:var(--text-color,#202123);font-family:inherit;line-height:1.5}
.dc-card h3{font-size:1.2rem;font-weight:650;margin:.35rem 0 .6rem;line-height:1.4;letter-spacing:-.02em}.dc-card h4{font-size:.95rem;margin:.85rem 0 .45rem}.dc-card p{margin:.3rem 0}.dc-card .dc-muted{font-size:.78rem;opacity:.76}.dc-card .dc-vs{font-size:.75rem;font-weight:400;opacity:.55;margin:0 .3rem}.dc-card .dc-name{display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:3;overflow:hidden}
.dc-card .dc-kickoff{font-size:.87rem;font-weight:550}.dc-card .dc-status{display:inline-block;padding:.2rem .55rem;border:1px solid var(--dc-border);border-radius:999px;font-size:.73rem;margin:.35rem .25rem .35rem 0;background:var(--dc-tint)}
.dc-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.55rem;margin:.5rem 0 1rem;color:var(--text-color,#202123);font-family:inherit}
.dc-summary .dc-summary-tile{padding:.8rem .45rem;border:1px solid rgba(128,128,128,.22);border-radius:15px;background:var(--secondary-background-color,#f7f7f8);text-align:center}.dc-summary strong{display:block;font-size:1.7rem;line-height:1.25;font-variant-numeric:tabular-nums;font-weight:650}.dc-summary span{display:block;font-size:.72rem;margin-top:.2rem}
.dc-card .dc-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.65rem}.dc-card .dc-team{padding:.65rem;border:1px solid var(--dc-border);border-radius:12px}
.dc-card .dc-team-overview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.55rem;margin:.5rem 0}.dc-card .dc-team-tile{border:1px solid var(--dc-border);border-radius:12px;padding:.65rem .55rem;background:var(--secondary-background-color,#f7f7f8)}.dc-card .dc-team-tile strong{display:block;font-size:.83rem}.dc-card .dc-venue-label{font-size:.69rem;opacity:.72}.dc-card .dc-form-chips{display:flex;gap:.18rem;flex-wrap:wrap;margin:.45rem 0}.dc-card .dc-form-chip{display:inline-flex;align-items:center;justify-content:center;min-width:1.25rem;height:1.3rem;border-radius:5px;border:1px solid var(--dc-border);font-size:.66rem;font-weight:650}.dc-card .dc-form-chip[data-result="W"]{background:rgba(18,118,92,.15)}.dc-card .dc-form-chip[data-result="L"]{background:rgba(179,72,72,.12)}.dc-card .dc-goal-pair{font-size:1.08rem;font-weight:650;font-variant-numeric:tabular-nums;margin:.2rem 0}.dc-card .dc-team-tile .dc-muted{font-size:.7rem}
.dc-card .dc-table{table-layout:fixed;border-collapse:collapse;width:100%;max-width:100%;font-size:.82rem}.dc-card .dc-table th,.dc-card .dc-table td{text-align:left;vertical-align:top;padding:.5rem .3rem;border-bottom:1px solid var(--dc-border);white-space:normal;overflow-wrap:anywhere}.dc-card .dc-table th{font-size:.7rem;font-weight:500;opacity:.75}.dc-card .dc-table td:last-child{font-variant-numeric:tabular-nums;font-weight:600}.dc-card .dc-table th:last-child,.dc-card .dc-table td:last-child{width:25%}.dc-card .dc-table th:first-child,.dc-card .dc-table td:first-child{width:43%}
.dc-card details{margin:.5rem 0;padding:.15rem .65rem;border:1px solid var(--dc-border);border-radius:12px}.dc-card summary{cursor:pointer;white-space:normal;font-size:.84rem;font-weight:550;min-height:44px;padding:.65rem 0}.dc-card details[open]>summary{margin-bottom:.25rem}.dc-card .dc-empty{padding:.65rem;border-radius:10px;border:1px dashed var(--dc-border);font-size:.84rem;background:var(--secondary-background-color,#f7f7f8)}
.dc-card .dc-price-note{border-left:2px solid var(--dc-border);padding-left:.6rem;margin:.65rem 0;font-size:.78rem}.dc-card .dc-preview{padding:.4rem .6rem .6rem;border:1px solid var(--dc-border);border-radius:12px;background:var(--secondary-background-color,#f7f7f8)}.dc-card .dc-preview .dc-muted{margin-top:.3rem}.dc-card .dc-market-help{font-size:.72rem;opacity:.7}
.dc-card .dc-market-switch{position:relative;margin:.8rem 0}.dc-card .dc-market-radio{position:absolute;width:1px;height:1px;clip-path:inset(50%);overflow:hidden;white-space:nowrap}.dc-card .dc-market-labels{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.3rem;padding:.25rem;margin-bottom:.65rem;background:var(--secondary-background-color,#f7f7f8);border-radius:12px;border:1px solid var(--dc-border)}.dc-card .dc-market-labels label{display:flex;justify-content:center;align-items:center;text-align:center;gap:.35rem;min-height:44px;padding:.5rem .25rem;cursor:pointer;font-size:.84rem;font-weight:550;border-radius:9px;border:1px solid transparent}.dc-card .dc-market-labels small{font-size:.66rem;font-weight:400;opacity:.7}.dc-card .dc-tab-ah:checked~.dc-market-labels [data-dc-tab="ah"],.dc-card .dc-tab-ou:checked~.dc-market-labels [data-dc-tab="ou"],.dc-card .dc-market-switch[data-dc-default="ah"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-market-labels [data-dc-tab="ah"],.dc-card .dc-market-switch[data-dc-default="ou"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-market-labels [data-dc-tab="ou"]{background:var(--background-color,#fff);border-color:var(--dc-border);box-shadow:0 1px 3px rgba(0,0,0,.06);color:var(--dc-accent)}.dc-card .dc-market-radio:focus-visible~.dc-market-labels{outline:2px solid var(--dc-accent);outline-offset:3px}.dc-card .dc-panel-ah,.dc-card .dc-panel-ou{display:none}.dc-card .dc-tab-ah:checked~.dc-panel-ah,.dc-card .dc-tab-ou:checked~.dc-panel-ou,.dc-card .dc-market-switch[data-dc-default="ah"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-panel-ah,.dc-card .dc-market-switch[data-dc-default="ou"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-panel-ou{display:block}
.dc-card .dc-source-footer{border-top:1px solid var(--dc-border);padding-top:.65rem;margin-top:.85rem}.dc-card .dc-source-footer p{font-size:.71rem;opacity:.75}.dc-card .dc-analysis{margin-top:.6rem}.dc-card .dc-analysis-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:.4rem}.dc-card .dc-analysis-item{padding:.55rem .4rem;border:1px solid var(--dc-border);border-radius:10px;font-size:.77rem}.dc-card .dc-analysis-item strong{display:block;font-size:1rem}.dc-card .dc-analysis-note,.dc-card .dc-analysis-contract{font-size:.73rem;opacity:.78}.dc-card .dc-analysis-reasons{font-size:.8rem}
@media(max-width:500px){.dc-card .dc-grid{grid-template-columns:minmax(0,1fr)}}
@media(max-width:350px){.dc-card{padding:.8rem;border-radius:16px}.dc-card .dc-grid{gap:.4rem}.dc-card .dc-team{padding:.4rem}.dc-card .dc-table{font-size:.78rem}.dc-card .dc-team-tile{padding:.5rem .4rem}.dc-summary{gap:.35rem}.dc-summary .dc-summary-tile{padding:.7rem .25rem}.dc-summary strong{font-size:1.5rem}.dc-summary span{font-size:.68rem}}
@media(prefers-color-scheme:dark){.dc-card{--dc-accent:#7acdb0;--dc-tint:rgba(122,205,176,.08);background:var(--background-color,#212121);color:var(--text-color,#ececec)}.dc-summary{color:var(--text-color,#ececec)}.dc-summary .dc-summary-tile{background:var(--secondary-background-color,#2f2f2f)}.dc-card .dc-team-tile,.dc-card .dc-preview,.dc-card .dc-market-labels,.dc-card .dc-empty{background:var(--secondary-background-color,#2f2f2f)}.dc-card .dc-tab-ah:checked~.dc-market-labels [data-dc-tab="ah"],.dc-card .dc-tab-ou:checked~.dc-market-labels [data-dc-tab="ou"],.dc-card .dc-market-switch[data-dc-default="ah"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-market-labels [data-dc-tab="ah"],.dc-card .dc-market-switch[data-dc-default="ou"] .dc-tab-ah:not(:checked)~.dc-tab-ou:not(:checked)~.dc-market-labels [data-dc-tab="ou"]{background:var(--background-color,#212121)}}
</style>'''


def _text(value, missing='ไม่มีข้อมูล'):
    if value is None or value == '':
        return escape(missing, quote=True)
    if isinstance(value,float) and not math.isfinite(value):
        return escape(missing,quote=True)
    return escape(str(value), quote=True)


def _dict(value):
    return value if isinstance(value, dict) else {}


def _list(value):
    return value if isinstance(value, list) else []


def _number(value, decimals=2, *, signed=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 'ไม่มีข้อมูล'
    try:
        if not math.isfinite(value):
            return 'ไม่มีข้อมูล'
        return f'{value:+.{decimals}f}' if signed else f'{value:.{decimals}f}'
    except (ValueError, OverflowError):
        return 'ไม่มีข้อมูล'


def _count(value):
    return str(value) if type(value) is int and value >= 0 else 'ไม่มีข้อมูล'


def _state(value):
    if not isinstance(value,str):
        return None
    return {'complete':'พร้อมตามขอบเขตที่ตรวจ','partial':'ข้อมูลบางส่วน','no_data':'ไม่มีข้อมูล',
        'success':'ใช้งานได้','available':'มีข้อมูล','error':'โหลดไม่สำเร็จ','unsupported':'ไม่มีแหล่งรองรับ',
        'unmatched':'จับคู่ชื่อทีมไม่ได้','not_loaded':'ยังไม่ได้โหลด','cache':'ใช้ข้อมูลจาก Cache',
        'not_called':'ไม่ได้เรียก','disabled':'ปิดไว้','plan_unsupported':'แพ็กเกจไม่รองรับ',
        'ok':'มีผลย้อนหลังตามเกณฑ์','name_unmatched':'จับคู่ชื่อทีมไม่ได้',
        'name_ambiguous':'ชื่อทีมกำกวม จึงไม่จับคู่','lossy_normalization':'ชื่อทีมยังไม่ผ่านการยืนยัน',
        'empty_history':'ไม่มีผลย้อนหลังที่ใช้ได้','empty_file':'ไฟล์ต้นทางว่าง',
        'empty':'ผู้ให้บริการส่งข้อมูลว่าง','missing_columns':'ไฟล์ขาดคอลัมน์ที่จำเป็น',
        'missing_xg_columns':'ไม่มีคอลัมน์ xG/xGA จริงที่จำเป็น','invalid_dates':'วันที่ผลย้อนหลังไม่ถูกต้อง',
        'invalid_scores':'ผลประตูย้อนหลังไม่ครบหรือไม่ถูกต้อง','invalid_season':'วันที่ไม่ตรงฤดูกาลต้นทาง',
        'conflicting_scores':'สกอร์ขัดแย้ง จึงแยกออกจากข้อมูลที่ใช้',
        'conflicting_results':'ผลย้อนหลังขัดแย้ง จึงแยกออกจากข้อมูลที่ใช้',
        'no_history_before_kickoff':'ไม่มีผลย้อนหลังที่ผ่านเกณฑ์ก่อนเวลาคัดข้อมูล',
        'insufficient_history':'มีผลย้อนหลังตามสนามน้อยกว่า 5 นัด',
        'invalid_kickoff':'เวลาแข่งขันหรือเขตเวลาไม่ถูกต้อง','invalid_response':'รูปแบบข้อมูลต้นทางใช้ไม่ได้',
        'unverified':'ข้อมูลยังไม่ผ่านการยืนยัน','unknown':'ยังยืนยันสถานะไม่ได้',
        'http_error':'ผู้ให้บริการตอบ HTTP ไม่สำเร็จ','download_error':'ดาวน์โหลดข้อมูลไม่สำเร็จ',
        'parse_error':'อ่านรูปแบบข้อมูลต้นทางไม่สำเร็จ','provider_error':'ผู้ให้บริการแจ้งข้อผิดพลาด',
        'results_source_error':'โหลดแหล่งผลย้อนหลังไม่สำเร็จ',
        'xg_source_error':'โหลดแหล่ง xG ไม่สำเร็จ',
        'optional_results_source_error':'โหลดแหล่งผลย้อนหลังสำรองไม่สำเร็จ',
        'domestic_source_error':'โหลดข้อมูลลีกในประเทศไม่สำเร็จ',
        'csv_source_not_configured':'ลีกนี้ยังไม่ได้ตั้งค่าแหล่งผลย้อนหลัง CSV',
        'xg_source_not_supported':'ลีกนี้ไม่มีแหล่ง xG ที่รองรับ',
        'unsupported_source':'ลีกนี้ไม่มีแหล่งผลย้อนหลังที่รองรับ',
        'unsupported_league':'ลีกนี้ไม่มีตัวเชื่อมข้อมูลที่รองรับ',
        'provider_metadata_unverified':'รหัสลีกหรือข้อมูลระบุตัวทีมจากผู้ให้บริการยังไม่ผ่านการยืนยัน',
        'provider_names_unmatched':'จับคู่ชื่อทีมกับผู้ให้บริการสำรองไม่ได้',
        'names_unmatched':'จับคู่ชื่อทีมไม่ได้','not_configured':'ยังไม่ได้ตั้งค่าผู้ให้บริการนี้',
        'missing_key':'ยังไม่มี API Key ที่ตั้งค่าไว้','invalid_key':'สิทธิ์ API Key ไม่ผ่านการตรวจ',
        'disabled_by_owner':'เจ้าของปิดการเรียกผู้ให้บริการนี้ไว้','not_requested':'ยังไม่ได้เรียกแหล่งข้อมูลนี้',
        'no_fixtures':'ไม่มีรายการคู่แข่งขันสำหรับการขอข้อมูลรอบนี้',
        'csv_sufficient':'ใช้ผลย้อนหลัง CSV ที่มีเพียงพอแล้ว จึงไม่ได้เรียกแหล่งสำรอง',
        'csv_ready':'ใช้ผลย้อนหลัง CSV ที่มีพร้อมแล้ว','csv_names_matched':'ชื่อทีมตรงกับผลย้อนหลัง CSV',
        'verified_optional_result_source':'ใช้แหล่งผลย้อนหลังสำรองที่ยืนยันแล้ว',
        'plan_backoff':'พักการเรียกเพราะแพ็กเกจไม่รองรับ',
        'auth_backoff':'พักการเรียกเพราะสิทธิ์เข้าถึงมีปัญหา',
        'quota_backoff':'พักการเรียกเพราะข้อจำกัดโควตา','failure_backoff':'พักการเรียกหลังเกิดข้อผิดพลาด',
        'request_in_progress':'กำลังมีคำขอข้อมูลนี้อยู่แล้ว',
        'incomplete_pagination':'ข้อมูลผลย้อนหลังยังมาไม่ครบทุกหน้า',
        'source_rows_failed_validation':'แถวข้อมูลต้นทางไม่ผ่านการตรวจคุณภาพ',
        'invalid_clock':'เวลาที่ใช้ตรวจข้อมูลไม่ถูกต้อง','exact':'ตรงชื่อทีมต้นทาง',
        'verified_alias':'ใช้ชื่ออีกแบบที่มีหลักฐานยืนยัน'}.get(value,value)


def _form_status(value, sample):
    if type(sample) is int and sample == 0 and value in ('ok','available','success'):
        return 'ไม่มีผลย้อนหลังที่ผ่านการกรองสำหรับรอบนี้'
    return _state(value)


def _quality_details(source):
    quality=_dict(source.get('quality'))
    if not quality:
        return ''
    output=[]
    if 'accepted_rows' in quality or 'input_rows' in quality:
        output.append('<p class="dc-muted">ผ่านการตรวจ '+_text(_count(quality.get('accepted_rows')))+' / '+_text(_count(quality.get('input_rows')))+' แถว</p>')
    if type(quality.get('accepted_rows')) is int and quality['accepted_rows']==0 and type(quality.get('input_rows')) is int and quality['input_rows']>0:
        output.append('<p class="dc-muted">ต้นทางมีข้อมูล แต่ไม่มีแถวผ่านการกรองสำหรับรอบนี้</p>')
    counters=(('late_rows','ไม่อยู่ก่อนเวลาคัดข้อมูล'),('conflicting_rows','ข้อมูลขัดแย้ง'),
        ('invalid_xg_rows','ค่า xG ไม่ถูกต้อง'),('late_or_invalid_receipt_rows','รับข้อมูลภายหลังหรือเวลารับไม่ถูกต้อง'),
        ('late_receipts','รับข้อมูลภายหลัง'),('duplicate_rows','แถวซ้ำ'),('self_match_rows','ชื่อเจ้าบ้านและทีมเยือนเป็นทีมเดียวกัน'))
    rejected=[f'{label} {quality[key]} แถว' for key,label in counters if type(quality.get(key)) is int and quality[key]>0]
    validation=_dict(quality.get('validation'))
    rejected.extend(f'{label} {validation[key]} แถว' for key,label in
        (('invalid_dates','วันที่ไม่ถูกต้อง'),('invalid_scores','สกอร์ไม่ถูกต้อง'))
        if type(validation.get(key)) is int and validation[key]>0)
    if rejected:
        output.append('<p class="dc-muted">เหตุผลที่แยกออก: '+_text(' · '.join(rejected))+'</p>')
    if quality.get('status') not in (None,'success','ok'):
        output.append('<p class="dc-muted">สถานะการตรวจแถว: '+_text(_state(quality.get('status')))+'</p>')
    return ''.join(output)


def _table(headings, rows, *, label):
    headers=''.join('<th scope="col">'+_text(value)+'</th>' for value in headings)
    body=''.join('<tr>'+''.join('<td>'+_text(value)+'</td>' for value in row)+'</tr>' for row in rows)
    return '<table class="dc-table" aria-label="'+_text(label)+'"><thead><tr>'+headers+'</tr></thead><tbody>'+body+'</tbody></table>'


def _selection(quote, card):
    if quote.get('market') == 'Asian Handicap':
        side=quote.get('side')
        team=card.get('home_team') if side == 'home' else card.get('away_team') if side == 'away' else None
        point=quote.get('point')
        valid=isinstance(point,(int,float)) and not isinstance(point,bool) and math.isfinite(point)
        kind='ต่อ' if valid and point < 0 else 'รอง' if valid and point > 0 else 'เสมอ' if valid else 'ไม่มีข้อมูลเส้น'
        return f'{team or "ไม่มีข้อมูลทีม"} ({kind})'
    return {'over':'สูง','under':'ต่ำ'}.get(quote.get('side'),'ไม่มีข้อมูลฝั่ง')


def _price_market(card, market, label):
    quotes=[q for q in _list(card.get('quotes')) if isinstance(q,dict) and q.get('market') == market]
    if not quotes:
        return '<section data-dc-market="'+_text(market)+'"><h4>'+_text(label)+'</h4><p class="dc-empty">ไม่มีราคาตลาด</p></section>'
    groups=OrderedDict()
    for quote in quotes:
        groups.setdefault((str(quote.get('bookmaker_key') or ''),str(quote.get('bookmaker') or 'ไม่มีข้อมูลเจ้ามือ')),[]).append(quote)
    output=['<section data-dc-market="'+_text(market)+'"><h4>'+_text(label)+'</h4>']
    output.append('<p class="dc-muted">แยกตามเจ้ามือ ไม่ใช่การจัดอันดับความคุ้มค่า</p>')
    # This is a short preview in source order, not a new market ranking or a
    # claim that the first bookmaker/line is the main line.
    preview=next(iter(groups.values()))[:2]
    preview_rows=[(_selection(q,card),_number(q.get('point'),signed=market=='Asian Handicap'),_number(q.get('odds'))) for q in preview]
    output.append('<div class="dc-preview">'+_table(('ตัวเลือก','เส้น','ราคา'),preview_rows,label=label+' · ตัวอย่าง Snapshot'))
    output.append('<p class="dc-muted">ตัวอย่าง '+str(len(preview))+' รายการแรกของเจ้ามือแรกตาม Snapshot · '+_text(preview[0].get('bookmaker'))+'</p>')
    output.append('<p class="dc-market-help">ไม่ใช่การยืนยันเส้นหลักหรือคำแนะนำเดิมพัน</p></div>')
    for index,((_,book), values) in enumerate(groups.items()):
        output.append('<details class="dc-book"><summary>'+_text(book)+'</summary>')
        rows=[(_selection(q,card),_number(q.get('point'),signed=market == 'Asian Handicap'),_number(q.get('odds'))) for q in values]
        output.append(_table(('ตัวเลือก','เส้น','ราคา'),rows,label=label+' · '+book))
        for quote in values:
            output.append('<div class="dc-price-note"><p>'+_text(_selection(quote,card))+' · เส้น '+_text(_number(quote.get('point'),signed=market == 'Asian Handicap'))+'</p>')
            output.append('<p>ราคาจาก Snapshot '+_text(_number(quote.get('odds')))+' · รับข้อมูล '+_text(quote.get('received_at') or card.get('received_at'))+'</p>')
            scope={'market':'เวลาอัปเดตตลาด','bookmaker':'เวลาอัปเดตข้อมูลระดับเจ้ามือ'}.get(quote.get('source_update_scope'),'เวลาอัปเดตจากผู้ให้บริการ')
            output.append('<p class="dc-muted">'+_text(scope)+': '+_text(quote.get('source_updated_at'))+'</p>')
            freshness=quote.get('freshness_status')
            if freshness=='unverified_or_stale':
                output.append('<p>ราคาเก่าหรือเวลาไม่ยืนยัน — ใช้ดู Snapshot เท่านั้น</p>')
            elif freshness=='recent_observed_snapshot':
                output.append('<p class="dc-muted">เวลา Snapshot อยู่ในเกณฑ์ที่ตรวจ ไม่ใช่การยืนยันราคาสด</p>')
            else:
                output.append('<p class="dc-muted">ความสดของราคา: ไม่มีข้อมูลยืนยัน</p>')
            if quote.get('freshness_reason'):
                output.append('<p class="dc-muted">'+_text(quote['freshness_reason'])+'</p>')
            first=_dict(quote.get('first_observed'))
            if first and quote.get('first_observed_status') not in ('ambiguous','missing_time'):
                output.append('<p>First Observed Price (Data Center): เส้น '+_text(_number(first.get('point'),signed=market == 'Asian Handicap'))+' · ราคา '+_text(_number(first.get('odds')))+' · '+_text(first.get('received_at'))+'</p>')
                if first.get('line_changed') is True:
                    output.append('<p>เส้นเปลี่ยน — ไม่เทียบค่าน้ำต่างเส้นโดยตรง</p>')
            else:
                output.append('<p>First Observed Price (Data Center): ไม่มีข้อมูลที่ยืนยันได้</p>')
            # GET odds supplies snapshots, not a documented true opening feed.
            output.append('<p class="dc-muted">ราคาเปิดจริง: ไม่มีข้อมูลที่ยืนยันแล้ว</p></div>')
        output.append('</details>')
    output.append('</section>')
    return ''.join(output)


def _market_switch(card, instance=None):
    identity='\x1f'.join(str(card.get(key) or '') for key in ('run_id','event_id','sport_key','kickoff_utc'))
    namespace='' if instance is None else str(instance)
    token=hashlib.sha256((identity+'\x1f'+namespace).encode()).hexdigest()[:24]
    name='dc-market-'+token
    quotes=[q for q in _list(card.get('quotes')) if isinstance(q,dict)]
    counts={market:sum(q.get('market')==market for q in quotes) for market in ('Asian Handicap','Over/Under')}
    use_ou=counts['Asian Handicap']==0 and counts['Over/Under']>0
    # Leave native inputs uncontrolled: Streamlit's React markdown renderer can
    # otherwise restore a server-rendered checked attribute after a local click.
    # CSS supplies the initial visible panel until the first real radio choice.
    return ('<div class="dc-market-switch" data-dc-default="'+('ou' if use_ou else 'ah')+'" role="group" aria-label="สลับตลาดราคา AH หรือ OU">'
        '<input class="dc-market-radio dc-tab-ah" type="radio" name="'+name+'" id="'+name+'-ah">'
        '<input class="dc-market-radio dc-tab-ou" type="radio" name="'+name+'" id="'+name+'-ou">'
        '<div class="dc-market-labels"><label for="'+name+'-ah" data-dc-tab="ah">AH <small>แฮนดิแคป</small></label>'
        '<label for="'+name+'-ou" data-dc-tab="ou">OU <small>สูง–ต่ำ</small></label></div>'
        '<div class="dc-panel-ah">'+_price_market(card,'Asian Handicap','แฮนดิแคป AH')+'</div>'
        '<div class="dc-panel-ou">'+_price_market(card,'Over/Under','สูง–ต่ำ OU')+'</div></div>')


def _form_window(window):
    window=_dict(window)
    sample=window.get('sample')
    if type(sample) is not int or sample <= 0:
        return 'ไม่มีข้อมูล', 'ไม่มีข้อมูล', _count(sample)
    form=' '.join(str(value) for value in _list(window.get('form')))
    goals=f'{_number(window.get("gf_avg"))} / {_number(window.get("ga_avg"))}'
    return form or 'ไม่มีข้อมูล',goals,str(sample)


def _team_overview(team, side, name):
    team=_dict(team)
    label='เจ้าบ้าน · เหย้า' if side=='home' else 'ทีมเยือน · เยือน'
    window=_dict(_dict(team.get('venue')).get('last5'))
    sample=window.get('sample')
    available=type(sample) is int and sample>0
    output=['<div class="dc-team-tile" data-dc-team="'+side+'"><span class="dc-venue-label">'+label+'</span><strong>'+_text(team.get('name') or name)+'</strong>']
    if available:
        chips=[]
        for result in _list(window.get('form')):
            chips.append('<span class="dc-form-chip" data-result="'+_text(result)+'">'+_text(result)+'</span>')
        output.append('<div class="dc-form-chips" aria-label="ฟอร์ม 5 นัดตามสนาม">'+(''.join(chips) or '<span class="dc-muted">ไม่มีข้อมูลผล W/D/L</span>')+'</div>')
        output.append('<p class="dc-goal-pair">'+_text(_number(window.get('gf_avg')))+' / '+_text(_number(window.get('ga_avg')))+'</p>')
        output.append('<p class="dc-muted">ยิง / เสียเฉลี่ย · '+_text(_count(sample))+' นัด</p>')
    else:
        output.append('<p class="dc-empty">ไม่มีข้อมูลฟอร์มตามสนาม</p>')
    xg=_dict(_dict(_dict(team.get('xg')).get('venue')).get('last5'))
    xg_sample=xg.get('sample')
    if type(xg_sample) is int and xg_sample>0:
        output.append('<p class="dc-muted">xG/xGA '+_text(_number(xg.get('xg_avg')))+' / '+_text(_number(xg.get('xga_avg')))+' · '+_text(_count(xg_sample))+' นัด</p>')
    else:
        output.append('<p class="dc-muted">xG/xGA: ไม่มีข้อมูล</p>')
    output.append('</div>')
    return ''.join(output)


def _team_statistics(team, label):
    team=_dict(team)
    output=['<div class="dc-team"><strong>'+_text(team.get('name') or label)+'</strong>']
    diagnostics=_dict(team.get('diagnostics'))
    overall_sample=_dict(_dict(team.get('overall')).get('last5')).get('sample')
    output.append('<p class="dc-muted">สถานะฟอร์ม: '+_text(_form_status(team.get('status'),overall_sample))+'</p>')
    if team.get('scope')=='domestic_context':
        output.append('<p class="dc-muted">บริบทถ้วย: ใช้สถิติลีกในประเทศที่ยืนยันของทีมนี้ ไม่ใช่ฟอร์มรวมทุกถ้วย</p>')
    last_games=_list(_dict(_dict(team.get('overall')).get('last5')).get('matches'))
    last_game=next((match for match in reversed(last_games) if isinstance(match,dict)),{})
    output.append('<p class="dc-muted">วันเกมล่าสุดในชุดฟอร์ม: '+_text(last_game.get('date'))+'</p>')
    for category,title in (('overall','ฟอร์มรวมเฉพาะลีก/ข้อมูลต้นทาง'),('venue','ตามสนาม: '+label)):
        data=_dict(team.get(category))
        rows=[]
        for key,period in (('last5','5 นัด'),('last10','10 นัด')):
            form,goals,sample=_form_window(data.get(key))
            rows.append((period+' · '+sample+' นัด',form,goals))
        status=diagnostics.get('overall_status' if category=='overall' else 'venue_status')
        sample=_dict(data.get('last5')).get('sample')
        output.append('<p>'+_text(title)+'</p><p class="dc-muted">'+_text(_form_status(status,sample))+'</p>'+_table(('ช่วง/ตัวอย่าง','ผล W/D/L','ยิง / เสียเฉลี่ย'),rows,label=title))
    xg=_dict(team.get('xg'))
    xg_sample=_dict(_dict(xg.get('overall')).get('last5')).get('sample')
    output.append('<p class="dc-muted">สถานะ xG: '+_text(_form_status(xg.get('status'),xg_sample))+'</p>')
    xg_rows=[]
    for key,period in (('last5','5 นัด'),('last10','10 นัด')):
        window=_dict(_dict(xg.get('venue')).get(key))
        sample=window.get('sample')
        xg_rows.append((period+' · '+_count(sample)+' นัด',_number(window.get('xg_avg')),_number(window.get('xga_avg'))))
    output.append('<p>xG/xGA ตามสนามจากข้อมูลจริง</p>'+_table(('ช่วง/ตัวอย่าง','xG','xGA'),xg_rows,label='xG/xGA '+label))
    output.append('<p class="dc-muted">จับคู่ชื่อ: '+_text(team.get('matched_name'))+' · '+_text(_state(team.get('mapping')))+'</p></div>')
    return ''.join(output)


def _matches_details(statistics):
    output=['<details><summary>ผลย้อนหลังและแหล่งข้อมูล</summary>']
    for side,label in (('home','เจ้าบ้าน'),('away','ทีมเยือน')):
        team=_dict(_dict(statistics.get('teams')).get(side))
        output.append('<h4>'+_text(team.get('name') or label)+'</h4>')
        for category,title in (('overall','รวม'),('venue','ตามสนาม')):
            window=_dict(_dict(team.get(category)).get('last10'))
            matches=[m for m in _list(window.get('matches')) if isinstance(m,dict)]
            if not matches:
                continue
            output.append('<p>'+_text(title)+'</p>')
            rows=[(m.get('date'),m.get('opponent'),f'{m.get("goals_for", "ไม่มีข้อมูล")}–{m.get("goals_against", "ไม่มีข้อมูล")}') for m in matches]
            output.append(_table(('วัน','คู่แข่ง','ยิง–เสีย'),rows,label='ผลย้อนหลัง '+title))
            for match in matches:
                output.append('<p class="dc-muted">ฤดูกาล '+_text(match.get('season'))+' · '+_text(match.get('source_url'))+' · รับ '+_text(match.get('source_received_at'))+'</p>')
    sources=_dict(statistics.get('sources'))
    names={'football_data':'Football-Data','understat':'Understat','api_football':'API-Football'}
    for key,source in sources.items():
        source=_dict(source)
        output.append('<p><strong>'+_text(source.get('provider') or names.get(key,key))+'</strong> · '+_text(_state(source.get('status')))+' · '+_text(_state(source.get('reason')))+'</p>')
        output.append(_quality_details(source))
        if source.get('provider_state') is not None:
            output.append('<p class="dc-muted">สถานะผู้ให้บริการ: '+_text(_state(source['provider_state']))+'</p>')
        if source.get('http_status') is not None:
            output.append('<p class="dc-muted">HTTP '+_text(source['http_status'])+'</p>')
        if source.get('next_retry_at'):
            output.append('<p class="dc-muted">พักการเรียกจนถึง '+_text(source['next_retry_at'])+'</p>')
        stamps=source.get('received_at')
        output.append('<p class="dc-muted">เวลาได้รับต้นทาง: '+_text(' · '.join(str(x) for x in stamps) if isinstance(stamps,list) else stamps)+' · Cache: '+_text(source.get('cache_status'))+'</p>')
        for evidence in _list(source.get('sources')):
            if isinstance(evidence,dict):
                output.append('<p class="dc-muted">'+_text(evidence.get('url') or evidence.get('source_url'))+' · ฤดูกาล '+_text(evidence.get('season'))+' · '+_text(evidence.get('received_at'))+'</p>')
    output.append('</details>')
    return ''.join(output)


def _statistics(card):
    statistics=card.get('statistics')
    if not isinstance(statistics,dict):
        return '<section data-dc-statistics="not_loaded"><h4>สถิติฟุตบอล</h4><p class="dc-empty">ยังไม่ได้โหลดสถิติ — โปรแกรมและราคาดูได้ทันทีโดยไม่ต้องรอ A/B/C</p><p>ฟอร์ม 5/10 นัด · เหย้า–เยือน · ยิง/เสียเฉลี่ย · H2H · xG/xGA: ยังไม่มีข้อมูลที่โหลดสำหรับรอบนี้</p><p>อันดับตาราง: ไม่มีข้อมูล</p></section>'
    teams=_dict(statistics.get('teams'))
    output=['<section data-dc-statistics="loaded"><h4>สถิติฟุตบอล</h4><p class="dc-muted">ความครบถ้วน: '+_text(_state(statistics.get('status')))+' · ประเมินข้อมูล ณ '+_text(statistics.get('as_of'))+'</p><div class="dc-team-overview">']
    output.append(_team_overview(teams.get('home'),'home',card.get('home_team')))
    output.append(_team_overview(teams.get('away'),'away',card.get('away_team')))
    output.append('</div><details class="dc-form-details"><summary>ดูฟอร์ม 5/10 นัดและสถิติเหย้า–เยือน</summary><div class="dc-grid">')
    output.append(_team_statistics(teams.get('home'),'เจ้าบ้าน'))
    output.append(_team_statistics(teams.get('away'),'ทีมเยือน'))
    output.append('</div></details>')
    h2h=_dict(statistics.get('h2h'))
    matches=[m for m in _list(h2h.get('matches')) if isinstance(m,dict)]
    output.append('<details class="dc-h2h"><summary>ดู H2H · '+str(len(matches))+' นัดที่มีข้อมูล</summary>')
    if matches:
        rows=[(m.get('date'),f'{m.get("home_team", "ไม่มีข้อมูล")} vs {m.get("away_team", "ไม่มีข้อมูล")}',f'{m.get("home_goals", "ไม่มีข้อมูล")}–{m.get("away_goals", "ไม่มีข้อมูล")}') for m in matches]
        output.append(_table(('วัน','คู่แข่งขัน','สกอร์'),rows,label='H2H'))
    else:
        output.append('<p class="dc-empty">ไม่มีข้อมูล H2H ที่ผ่านการตรวจ</p>')
    output.append('</details>')
    standings=_dict(statistics.get('standings'))
    output.append('<h4>อันดับตาราง</h4>')
    if standings.get('status')=='success' and all(type(standings.get(side)) is int and standings[side]>0 for side in ('home','away')) and standings.get('source'):
        output.append('<p>เจ้าบ้าน '+_text(standings['home'])+' · ทีมเยือน '+_text(standings['away'])+' · '+_text(standings['source'])+'</p>')
    else:
        output.append('<p>ไม่มีข้อมูลอันดับตารางจากแหล่งที่รองรับ</p>')
    output.append(_matches_details(statistics))
    for limitation in _list(statistics.get('limitations')):
        output.append('<p class="dc-muted">'+_text(limitation)+'</p>')
    output.append('</section>')
    return ''.join(output)


def _source_footer(card):
    statistics=_dict(card.get('statistics'))
    output=['<footer class="dc-source-footer"><p>แหล่งโปรแกรม/ราคา: '+_text(card.get('source') or card.get('provider') or 'The Odds API')+'</p>']
    output.append('<p>รับราคา '+_text(card.get('received_at'))+'</p>')
    sources=_dict(statistics.get('sources'))
    names={'football_data':'Football-Data','understat':'Understat','api_football':'API-Football'}
    for key,source in sources.items():
        source=_dict(source)
        stamps=source.get('received_at')
        stamp=' · '.join(str(value) for value in stamps) if isinstance(stamps,list) else stamps
        output.append('<p>'+_text(source.get('provider') or names.get(key,key))+' · '+_text(_state(source.get('status')))+' · รับ '+_text(stamp)+'</p>')
    if not sources:
        output.append('<p>แหล่งสถิติ: ยังไม่มีข้อมูลที่โหลดสำหรับรอบนี้</p>')
    output.append('</footer>')
    return ''.join(output)


def dashboard_summary_html(cards):
    from data_center_presentation import summary_counts
    counts=summary_counts(cards)
    labels=(('fixtures','คู่แข่งขัน'),('priced','คู่ที่มีราคา'),('statistics','คู่ที่มีสถิติ'))
    return '<div class="dc-summary" role="group" aria-label="สรุปข้อมูลรอบสแกน">'+''.join(
        '<div class="dc-summary-tile" data-dc-count="'+key+'"><strong>'+_text(_count(counts.get(key)))+'</strong><span>'+label+'</span></div>'
        for key,label in labels)+'</div>'


def data_center_card_html(card, *, instance=None):
    card=_dict(card)
    status={'available':'มีโปรแกรมและราคา','no_prices':'มีโปรแกรม แต่ไม่มีราคาตลาด','missing_timestamp':'เวลาได้รับข้อมูลยังยืนยันไม่ได้'}.get(card.get('data_status'),'ข้อมูลยังไม่ครบ')
    output=['<article class="dc-card" data-center-fixture="'+_text(card.get('event_id'))+'"><header><p class="dc-muted">'+_text(card.get('league'))+'</p><h3><span class="dc-name" title="'+_text(card.get('home_team'))+'">'+_text(card.get('home_team'))+'</span><span class="dc-vs">vs</span><span class="dc-name" title="'+_text(card.get('away_team'))+'">'+_text(card.get('away_team'))+'</span></h3><p class="dc-kickoff">เวลาไทย '+_text(card.get('kickoff_th'))+'</p><span class="dc-status">'+_text(status)+'</span></header>']
    output.append('<p class="dc-muted">ราคาเป็นข้อมูล ณ Snapshot ไม่ใช่การยืนยันราคาสดหรือคำแนะนำเดิมพัน</p>')
    for reason in _list(card.get('data_quality_reasons')):
        output.append('<p class="dc-muted">'+_text(reason)+'</p>')
    output.append(_market_switch(card,instance))
    output.append(_statistics(card))
    from data_center_presentation import analysis_summary_html
    output.append(analysis_summary_html(card))
    output.append(_source_footer(card))
    output.append('</article>')
    return ''.join(output)


def mode_selector():
    import streamlit as st
    return st.toggle('Football Data Center',value=True,key='football_data_center_mode')


def analysis_panel(active):
    import streamlit as st
    return st.expander('เปิดดูระบบวิเคราะห์ A/B/C เดิม',expanded=not active or bool(st.session_state.get('dc_analysis_requested')))


def show_scan_issue(reason):
    """Use fixed messages; never render a request exception or provider body."""
    import streamlit as st
    if reason=='missing_key':
        st.error('ไม่พบ API Key กรุณาตั้งใน Streamlit Secrets หรือกรอกในช่องรหัสผ่าน')
    elif reason=='no_leagues':
        st.warning('เลือกลีกก่อนกดสแกน ข้อมูลที่บันทึกไว้ยังไม่ถูกลบ')
    else:
        st.warning('ดำเนินการสแกนไม่สำเร็จ ดูสถานะรายลีกและงบ API ข้อมูลที่บันทึกไว้ยังอยู่')


def run_price_scan(scan_callback, load_cards, load_report, load_budget):
    """Stream each persisted league before waiting for the next API response.

    ``scan_callback(progress)`` owns requests and storage. This helper only reads
    already committed records in its progress callback and renders them without
    statistics/model action buttons. No background scan or retry is introduced.
    """
    import streamlit as st
    holder=st.empty()
    progress=st.progress(0)
    def show(index,total,entry):
        progress.progress(index/total if total else 0)
        with holder.container():
            render_center(load_cards(),load_report(),load_budget())
    try:
        return scan_callback(show)
    except Exception:
        show_scan_issue('scan_error')
        return {'status':'error','reason':'scan_or_presentation_failed'}
    finally:
        progress.empty()
        holder.empty()


def _action(st, callback, cards, action):
    try:
        result=callback(cards)
    except Exception:
        st.warning('ดำเนินการเพิ่มเติมไม่สำเร็จ — โปรแกรมและราคาที่บันทึกไว้ยังอยู่ ดูสถานะแหล่งข้อมูลแล้วลองใหม่เมื่อพร้อม')
        return
    status=result.get('status') if isinstance(result,dict) else 'completed'
    st.session_state['dc_last_action_summary']={'action':action,'status':status if isinstance(status,str) else 'unknown'}
    if action=='analysis':
        st.session_state['dc_analysis_requested']=True
    st.rerun()


def render_center(cards, report, budget, *, on_load_statistics=None, on_analyze=None, analysis_rows=None):
    """Render saved facts first; callbacks only run on the corresponding click."""
    import streamlit as st
    from scan_status import render_scan_status
    from data_center_presentation import project_cards
    cards=project_cards(list(cards or []),analysis_rows)
    st.subheader('⚽ Football Data Center')
    st.caption('โปรแกรม ราคา และสถิติที่มีจริง แสดงได้โดยไม่ต้องรอ A/B/C · เปลี่ยนหน้าจอไม่เรียก Odds API')
    st.markdown(DATA_CENTER_CSS,unsafe_allow_html=True)
    st.markdown(dashboard_summary_html(cards),unsafe_allow_html=True)
    with st.expander('สถานะรอบสแกนและเครดิต API',expanded=not cards):
        render_scan_status(report,budget)
    if not cards:
        st.info('ยังไม่มีคู่จากรอบสแกนที่ตรงลีก ภูมิภาค และช่วงเวลาปัจจุบัน ดูสถานะรายลีกเพื่อแยก API error ออกจากข้อมูลที่ผู้ให้บริการไม่ได้ส่ง')
        return
    for index,card in enumerate(cards):
        st.markdown(data_center_card_html(card,instance=index),unsafe_allow_html=True)
    if on_load_statistics is not None:
        if st.button('โหลดสถิติที่มีสำหรับคู่ที่แสดง (ไม่เรียก Odds API)',key='dc_stats_load',use_container_width=True):
            _action(st,on_load_statistics,cards,'statistics')
    with st.expander('วิเคราะห์เพิ่มเติมจากข้อมูลที่บันทึกไว้',expanded=False):
        st.caption('ระบบ A/B/C และ EV ยังอยู่แยกจากข้อมูลฟุตบอล ไม่มีการเรียก Odds API ใหม่จากปุ่มนี้')
        if on_analyze is not None:
            if st.button('วิเคราะห์ A/B/C จากข้อมูลที่บันทึกไว้',key='dc_analyze',use_container_width=True):
                _action(st,on_analyze,cards,'analysis')
