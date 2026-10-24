"""Minimal scan status in an expander; no providers or model calculations."""
from scan_control import scan_summary_text


def render_scan_status(report, budget=None):
    import streamlit as st
    if report is None:
        return
    if report.get('invalid'):
        st.warning('อ่านรายงานสแกนล่าสุดไม่ได้ — ข้อมูลเดิมยังอยู่ในส่วนย้อนหลัง')
        return
    st.caption(scan_summary_text(report))
    with st.expander('สถานะลีกจากรอบสแกนล่าสุด',expanded=False):
        st.caption(f"ช่วง {report['hours_limit']} ชั่วโมง · ภูมิภาค {report['regions']} · รับรอบ {report.get('started_at','ไม่ทราบ')}")
        st.caption('source_update_scope: market = เวลาอัปเดตตลาด; bookmaker = เวลาอัปเดตข้อมูลระดับเจ้ามือที่ API ให้มา ไม่ยืนยันว่าทุกตลาดอัปเดตแยกกันในวินาทีนั้น ตรวจเวลาเดิมได้ในราคาทางเลือก')
        if report.get('stop_reason'):
            st.warning('หยุดสแกนเพื่อความปลอดภัย: '+str(report['stop_reason']))
        labels={'success':'ใช้งานสำเร็จ','empty_api':'API ตอบสำเร็จแต่ส่ง 0 คู่',
            'filtered_out':'คู่ทั้งหมดอยู่นอกเงื่อนไข','fixtures_without_rows':'พบคู่แต่ประมวลผลไม่ครบ',
            'success_no_rows':'ตอบสำเร็จ แต่ยังไม่มีจำนวนคู่ยืนยัน','league_error':'API/การประมวลผลผิดพลาด',
            'cooldown':'อยู่ในช่วงพักคำขอ','safety_stop':'หยุดเพื่อความปลอดภัย',
            'skipped_safety_stop':'ไม่ได้เรียกเพราะหยุดเพื่อความปลอดภัย','incomplete':'รอบสแกนยังไม่ครบ',
            'invalid_league':'รหัสลีกไม่รองรับ','skipped_duplicate':'เลือกลีกซ้ำ'}
        for entry in report['leagues']:
            number=lambda key:str(entry[key]) if entry.get(key) is not None else 'ไม่ทราบ'
            st.write(f"{entry.get('league') or entry.get('sport_key')} · {labels.get(entry['status'],entry['status'])}")
            st.caption(f"API {number('api_returned')} คู่ · ผ่าน {number('passed_filter')} · AH {number('ah_fixtures')} / OU {number('ou_fixtures')} · ไม่มีราคา {number('no_price_fixtures')}")
            if entry.get('reason') or entry.get('excluded_by_reason'):
                reasons={'daily_budget_reached':'ถึงงบรายวัน','monthly_budget_reached':'ถึงงบรายเดือน',
                    'provider_balance_reserve':'เครดิตผู้ให้บริการเหลือไม่พอหลังหักสำรอง',
                    'provider_safety_stop':'สิทธิ์/โควตา/ต้นทุนไม่ผ่านเกณฑ์','provider_blocked':'สิทธิ์/โควตายังต้องตรวจ',
                    'network_error':'เชื่อมต่อไม่สำเร็จหรือหมดเวลา','http_error':'ผู้ให้บริการตอบ HTTP ไม่สำเร็จ',
                    'invalid_json':'ข้อมูลตอบกลับไม่ใช่รายการที่อ่านได้','cooldown':'เพิ่งเรียกลีกนี้ อยู่ในช่วงพัก',
                    'unexpected_scan_error':'ประมวลผลลีกนี้ไม่สำเร็จ ไม่ได้แปลว่าไม่มีโปรแกรม',
                    'price_persistence_failed':'บันทึกราคาไม่สำเร็จ','scan_journal_unavailable':'บันทึกสถานะสแกนไม่สำเร็จ'}
                reason=entry.get('reason')
                http=f"HTTP {entry['http_status']} · " if entry.get('http_status') is not None else ''
                st.caption(http+str(reasons.get(reason,reason) if reason else entry['excluded_by_reason']))
        if budget:
            show=lambda key:budget[key] if budget.get(key) is not None else 'ไม่ทราบ'
            st.caption(f"เครดิตจาก header: ใช้ {show('provider_used')} / เหลือ {show('provider_remaining')} · บัญชีในเครื่องเดือนนี้ {show('credits_month')}/{show('monthly_credit_limit')} · สำรอง {show('credit_reserve')}")
            st.caption('งบร่วมได้เฉพาะ instance ที่ใช้ ledger เดียวกัน ไม่รับประกันเพดานรวมระหว่างหลายแอปบน Cloud')

