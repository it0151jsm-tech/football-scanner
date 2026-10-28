# UCL domestic evidence — ต่อจาก PR #4

Base `b66341351986a52c84c4322c331c5a4597eafd38`, branch `fix/ucl-domestic-context`.
No merge, deployment, Foundation integration or real bill database access.

## สาเหตุที่ยืนยันจากโค้ดก่อนแก้

- PR #4 ตั้ง UCL/ถ้วยยุโรปเป็น `csv_code=None` แต่ `scan_league()` เรียก
  `fetch_history(None)` แล้วใช้ frame ว่างกับโปรไฟล์ฟอร์มและคะแนน A ทั้ง 5/10/all
  การโหลด Understat รวมหลายลีกเดิมไม่ได้แก้เส้นทาง CSV นี้ จึงแสดง 0 นัด
- ระบบ B ของถ้วยถูกบังคับ `metrics=None` โดยตั้งใจ `estimate_goals()` ใช้ baseline
  xG เหย้า/เยือนของลีกเดียว การรวม Bundesliga และ EPL แล้วส่งเข้าโมเดลเดิมจะเป็น
  การเปลี่ยนความหมายของโมเดล แม้ไม่ได้แก้บรรทัดสูตร จึงไม่ทำเช่นนั้น
- Main OU Line ใช้คู่ราคาตลาด ไม่ต้องมี B/EV การขาด xG ไม่ใช่สาเหตุโดยตัวมันเอง
  ที่ทำให้ Main Line ยืนยันไม่ได้
- พบข้อบกพร่องเพิ่มเติมจากการจำลอง: ชื่อแสดงเจ้ามือเดียวกันแต่ bookmaker_key ต่างกัน
  เคยจับคู่ราคาได้ รวมทั้ง lookup ราคาแรก/Snapshot อาจปะปนกันตามชื่อแสดง

ข้อมูลที่ผู้ใช้รายงาน: Bayern Munich–Arsenal, 22 ต.ค. 2026 02:00 ไทย
(21 ต.ค. 19:00 UTC), AH Bayern -0.25 ราคา 2.00 เราไม่ได้ยืนยันโปรแกรม/ราคา
จากผู้ให้บริการจริง และไม่มี OU payload/ฐานบิลจริงที่ได้รับอนุญาตให้ตรวจ
จึงไม่อ้างว่ายืนยันสาเหตุ OU ของการแข่งขันจริงได้แล้ว

## สิ่งที่แก้

1. เมื่อสั่งสแกนถ้วยอย่างชัดเจน ตรวจ current-season CSV roster ของ 5 ลีกที่มีตัวเชื่อมเดิม:
   Premier League E0, La Liga SP1, Serie A I1, Bundesliga D1, Ligue 1 F1
   ใช้ผลที่มีจริงก่อนวันแข่งขันและ season ที่ตรวจได้ ไม่เดาลีกจากประเทศ/ชื่อสนาม
2. โหลด roster เฉพาะฤดูกาลปัจจุบัน 1 ไฟล์ต่อลีกและ cache 6 ชั่วโมง
   จากนั้นโหลดประวัติเต็มและ Understat เฉพาะลีกที่จับคู่ทีมสำเร็จ โดยใช้ cache เดิม
   ตัวอย่างทดสอบ Bayern/Arsenal ใช้ D1/E0 เท่านั้น ไม่โหลด Understat ครบทุกลีกอีก
   Cold scan นี้อาจต้องโหลด CSV สูงสุด 5 roster + 4 CSV ต่อ matched league;
   ไม่รับประกันว่าทุก scan จะใช้เวลาเท่ากัน โดยเฉพาะเมื่อผู้ให้บริการช้า/บล็อก
3. ใช้ exact/verified aliases เดิม พร้อมตรวจ Unicode ที่ไม่สูญเสียตัวอักษร
   ไม่เพิ่ม alias จริงจากการเดาว่าชื่อคล้ายกัน ชื่อกำกวม/หลายลีก/ฤดูกาลเก่าจะไม่ถูกจับคู่
   ทีมเลื่อนชั้นต้องมีหลักฐานใน roster ฤดูกาลปัจจุบัน ไม่ใช้สมาชิกเก่ามายืนยันลีกวันนี้
4. แยก namespace ชื่อทีมตามแหล่งก่อนรวม evidence เฉพาะสองทีมเป้าหมายถูก remap กลับ
   เป็นชื่อ API จึงไม่ดึงคู่แข่งชื่อเหมือนกันจากอีกลีกมารวมสถิติ
5. คงสูตร A เดิม คำนวณฟอร์มเหย้า/เยือน 5/10/all จากผลลีกภายในประเทศ
   โปรไฟล์/เหตุผลเดิมระบุลีก ฤดูกาล ชื่อ source สถานะโหลด/จับคู่/จำนวนตัวอย่าง
   ถ้า roster พบแต่ full history โหลดไม่ได้ แสดงเหตุโหลดจริง ไม่บอกเพียง 0 นัด
6. ใช้เฉพาะ xG/xGA ที่มีใน Understat ลีกที่ยืนยัน แล้วกรองฤดูกาล ผล/วันที่ และค่า xG
   รูปแบบไม่ถูกต้อง/ค่าติดลบ/ข้อมูลเก่า/ชื่อไม่ตรงไม่ถูกเติมแทนด้วยประตูจริง
   สถิติ xG บนรายละเอียดเป็นค่าเฉลี่ย venue-tail10 ตามสูตร evidence เดิม
   legacy all-venue context ถูกระงับเมื่อ venue xG ไม่ผ่านความสด เพื่อลดความขัดแย้ง
7. มี domestic xG แล้วจะบอกว่า “มี xG ลีกในประเทศจริง แต่ยังไม่มีโมเดล UCL/ถ้วยข้ามลีก
   ที่ตรวจสอบแล้ว” ไม่กล่าวว่าไม่มี xG และไม่สร้าง expected goals/probability/EV ถ้วย
   ระบบ C แสดงแนวโน้มหรือ No Bet ตามข้อมูล ไม่แสดง Value Bet จากคะแนน A อย่างเดียว
8. Main OU Line แยกเหตุผล: ไม่พบ OU / ขาดฝั่ง / ราคาไม่ถูกต้อง / คู่แข่งขัน-เจ้ามือ-
   เส้น-Snapshot ไม่ตรง / คู่ราคาไม่สมดุล / หลายเส้นใกล้กัน
   ถ้ามีคู่ราคาที่ครบและผ่านเกณฑ์เดิม ยังคงประมาณ Main ได้แม้ไม่มี B แต่ไม่เลือกฝั่งเดิมพัน
9. การคัด latest quote, persistence, First Observed Price, Snapshot lookup และประวัติราคา
   แยก provider key ด้วย ป้องกันข้อมูลคนละเจ้ามือที่ใช้ชื่อแสดงเหมือนกัน
   ไม่ลบ snapshot เดิม ไม่แก้บิลหรือผลที่ตัดสินแล้ว

## สูตรและ UI

- ไม่แก้สูตร A/B/C, Poisson, EV, Settlement ทั้ง 11 ฟังก์ชันใน contract ของ PR #4
- `mobile_dashboard.py` รวม HTML/CSS ไม่เปลี่ยน byte เดียว ปุ่ม/4 แท็บ/รูปแบบตารางเดิม
  ยังผ่าน UI contract เดิม ข้อความวินิจฉัยเติมใน field เดิม ไม่เพิ่ม layout/ปุ่ม/หน้า
- ไม่แก้ PR #3 และไม่เชื่อม Foundation
- ไม่มีการเรียก Odds เพิ่มเพื่อหา domestic source หรือ OU เสริม คง request spreads,totals
  เดียวของ UCL ตามการสั่งสแกนเดิม ไม่เพิ่ม event/alternate endpoint หรือ background scan
- เปิดหน้า/สลับมุมมองอ่าน SQLite เท่านั้น ไม่โหลด CSV/Understat/Odds

## การทดสอบ

เพิ่ม 40 tests: domestic resolver 18, OU 10, scan/persistence 11, Streamlit UCL 1
ทั้งหมดใช้ข้อมูลจำลองที่สร้างสำหรับทดสอบ ไม่ใช่สถิติจริงของ Bayern/Arsenal
ใช้ฐาน SQLite ชั่วคราวเท่านั้น ไม่มี football provider calls ในการพัฒนา/CI

ครอบคลุม current season/promotion, alias เดิม, Unicode/no fuzzy, หลายลีก/ชื่อซ้ำ,
partial source failure, wrong dates/seasons/conflicting scores, stale/missing xG,
ฟอร์มครบ 5/10, no UCL EV, main OU โดยไม่มี B, missing/quarter/mismatched quotes,
หลาย provider key, immutable old bill, cached source reuse, 24h filter,
และการเปิด/สลับมุมมอง UCL โดยไม่ทำ HTTP หรือแก้ข้อมูลเดิม

Local offline runner: Ran 169 tests; OK (skipped=1 browser class เพราะ sandbox)
GitHub CI ต้องตรวจชุดเต็มบน Python 3.11 + Chromium; ดูผลสุดท้ายใน Draft PR
CI source crawlers ยังปิดอยู่ ติดตั้ง dependencies/browser ระหว่าง setup ได้ตามเดิม

## ข้อจำกัด / สิ่งที่ต้องตรวจรับ

- รองรับ domestic evidence เฉพาะ 5 ลีกหลักที่มี connector อยู่ ไม่ใช่ทุกทีมใน UCL
  ถ้าทีมอยู่นอกขอบเขต/ยังไม่มีผลใน current season/ชื่อไม่มี alias พร้อมหลักฐาน
  ต้องแสดง unresolved และไม่สร้างข้อมูล เมื่อบางแหล่งโหลดไม่ได้ จะระบุ coverage ไม่ครบ
- ฟุตบอลหลัง kickoff day ไม่ถูกนำเข้าฟอร์ม แต่ historical form ยังเป็นสถิติประกอบ
  ไม่ใช่หลักฐานว่าข้อมูลมีอยู่ก่อนเกมสำหรับ backtest แบบ observed
- B/EV ของ UCL ยังไม่เปิด การออกแบบโมเดลข้ามลีก ปรับความแข็งแกร่งลีกและ calibration
  ต้องผ่านงานวิจัย/ทดสอบและการอนุมัติแยก ไม่สามารถรับรองกำไรจาก domestic form
- ไม่ได้ตรวจ live provider availability, ชื่อจริงทุกทีม, raw OU ของคู่ที่รายงาน
  หรือ Safari จริงบนเครื่องเจ้าของ เพราะรอบนี้ใช้ mock และฐานทดลองตามข้อห้าม
- Owner ตรวจรับข้อมูลที่บันทึกบนแอปทดลองได้ก่อน หากจะสแกน provider จริงต้องอนุญาตแยก
  โดยสำรอง/ใช้ฐานทดลองและ quota ตาม PR #4 ห้ามสลับแอปหลักมาที่ branch นี้เอง
- ข้อจำกัด persistence/สิทธิ์เข้าถึง/งบข้ามหลาย instance ของ PR #4 ยังคงเดิม
  ยังไม่พร้อมใช้เก็บบิลเงินจริงเพียงเพราะ tests ของงานนี้ผ่าน
