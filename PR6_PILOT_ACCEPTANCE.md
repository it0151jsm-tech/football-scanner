# PR6 pilot — ช่วง 24/48 ชั่วโมงและแหล่งฟอร์ม/xG

ฐานตรวจ: `fix/ou-pair-diagnostics-and-ucl-form-audit` ที่ `09264e24bae06a3f510bd7c5cb7569a21c65ddb2` (PR #6); เสนอผ่าน branch ใหม่ `fix/pr6-48h-source-readiness` โดยยังไม่ Merge/Deploy

## เปรียบเทียบ PR #12 / #13 และการรวมโค้ด

- PR #12 `cf9a929620063e7f1f6a52685356dd8c4bef0be9`: นำเฉพาะ identity, Understat loader/validation, diagnostics, evidence และ regression tests มาใช้ ไม่รวม personal-stats features/UI/dependencies จากฐาน PR #8 ทั้งชุด
- PR #13 `cd4b6a9267ae42c91d19328c7c233595693e8670`: มี caption จำนวนคู่และ alias EPL เพิ่มเติม เก็บแนวทางรายงานจำนวนคู่และ Nottingham Forest ที่ PR #12 มีหลักฐานอยู่แล้ว จึงไม่เพิ่ม alias ซ้ำ อีก 7 กลุ่มยังไม่มีหลักฐานเฉพาะทีมใน PR #13 จึงไม่รับมาเดาจากชื่อ
- Reconcile `app.py` ด้วยมือในจุดที่ PR #12 มีบริบท personal-stats แต่ PR6 มี API-Football fallback; คง fallback และการแก้ scan target ของ PR #11 ไว้ แก้จุดกรองที่ยังจำกัด 24 ชั่วโมงตามด้านล่าง ไม่มี conflict markers และไม่เขียนทับ branch เดิม

## สาเหตุและสิ่งที่แก้

1. แม้ PR #11 แก้ scan target แล้ว `analysis_scope` ที่การ์ด/ตาราง/จัดบิลและตรวจซ้ำตอนบันทึกยัง hardcode 24 ชั่วโมง และ `load_cup_sources` ยังใช้ `min(hours_limit,24)` แก้ให้ส่งค่าที่เลือก 24/48 ทุกจุด คิดเป็นชั่วโมงจริงจาก instant ปัจจุบันและแสดงขอบเขต UTC/ไทย ไม่ตัดตามเที่ยงคืนไทย
2. กรองคู่ก่อนโหลดแหล่งสถิติ: ต้องยังไม่เริ่ม มีชื่อ/ID/เวลา aware อยู่ในลีกและช่วงที่เลือก จัดประเภทข้อมูลผิด คู่ซ้ำ และ ID ขัดแย้ง ไม่เลือกแถวแรกเมื่อหลักฐานขัดกัน จึงไม่ดาวน์โหลดสถิติเมื่อไม่มีคู่ผ่าน
3. Caption วินิจฉัยที่ PR #13 ขอไว้แสดง API ส่งมา/ผ่าน/ตัดออก/เหตุผล พร้อมจำนวนคู่มีราคา A/B พร้อม และทีม/สนามที่มีฟอร์มอย่างน้อย 5 นัด เก็บรายคู่และเหตุผลใน `league_health.payload_json` และ `analysis_a_json.scan_diagnostics` แสดงได้ในส่วนรายละเอียดเดิมโดยไม่เรียก API ใหม่
4. ชื่อที่มีหลักฐานจาก Issue #9: VfB Stuttgart↔Stuttgart, Borussia Monchengladbach↔M'gladbach↔Borussia M.Gladbach, Ipswich Town↔Ipswich, Nottingham Forest↔Nott'm Forest ปฏิเสธชื่อคล้าย ทีม U21/สำรอง Unicode ที่สูญหาย และ roster กำกวม
5. API-Football เดิมทิ้ง diagnostic เมื่อ fallback ไม่สำเร็จและแยกข้อจำกัดแพ็กเกจใน HTTP200 ไม่ได้ แก้ให้เก็บ/แสดง not_called, cache พร้อมสถานะต้นทาง, HTTP error, plan_unsupported, empty, success และเหตุผลไม่ใช้ข้อมูล โดยไม่สะท้อน error body หรือ Key
6. ตรวจ API-Football FT, epoch UTC/date, ลีก/ฤดูกาล, ประตู, เกมซ้ำ/ID ขัดแย้ง และชื่อ↔ID กำกวม เก็บ ID เฉพาะ provider พร้อม URL/checksum/เวลารับ ไม่ใช้ numeric ID เป็นสะพานจับทีมข้ามแหล่ง แถวที่ไม่มี metadata ยังอ่านได้ตามสัญญา parser เดิม แต่ app ไม่ใช้คำนวณฟอร์มจนกว่า league/season และ team IDs จะผ่าน
7. CSV ที่มีเพียงชื่อในฤดูกาลเก่า/ฟอร์มตามสนามไม่ถึง 5 นัด/เก่า ไม่ปิดทาง fallback อีกต่อไป ต้องมีหลักฐานสมาชิกฤดูกาลปัจจุบันและฟอร์มที่ใช้ได้ก่อนข้าม optional source ใช้สูตรฟอร์มเดิม ไม่กำหนดว่าทั้ง 5 นัดต้องมาจากฤดูกาลเดียวกัน
8. Understat ใช้การแก้ PR #12: ไม่ข้าม HTTP/schema failures เงียบ ตรวจ result flag, วัน/ฤดูกาล, xG จริง/ค่าติดลบ/ค่าขัดแย้ง กรองก่อน kickoff และเวลาวิเคราะห์ ไม่สมมติ timezone ของ datetime ที่ไม่มี offset; ต้องพ้นขอบเขตเผื่อ date+2 วันจึงยอมรับ แสดงเหตุผลเมื่อข้อมูลถูกพัก
9. Understat และ API-Football cache สำเร็จ 6 ชั่วโมง; failure/partial ผ่าน cache 15 นาทีพร้อม receipt เดิม ไม่ทำให้ข้อมูลเก่าดูใหม่ ไม่เปิด background scan การเปิดรายละเอียด/สลับมุมมองไม่เรียก provider

## หลักฐานและขอบเขตจริง

| สิ่งที่ตรวจ | ผลที่อ้างได้ |
|---|---|
| ชื่อ Issue #9 ↔ CSV | 4 ทีม มี URL/HTTP/checksum/receipt ใน `fixtures/team_identity_evidence.json` จากการตรวจ CSV เล็ก 2 ไฟล์รอบก่อน |
| Understat labels | หลักฐาน saved audit เดิม 3 ทีม; ยังไม่ยืนยัน Ipswich coverage ปัจจุบัน |
| API-Football name↔ID จาก live response | ยังไม่มีหลักฐานใน workspace; adapter/tests ใช้ mock ที่ระบุชัด |
| ฟอร์ม/คู่จาก live scan รอบนี้ | **ยังไม่ได้วัด** ไม่ถือว่าศูนย์หรือครบทุกทีม ไม่มีการใช้ Key/เครดิตจริงระหว่าง tests |
| การตรวจรับออฟไลน์ | 3 คู่ / 6 ทีม: ฟอร์มตามสนามครบ 5 นัด, A มีคะแนน 3 คู่, B พร้อม 2 คู่ในลีกในประเทศ; UCL B/EV ปิดต่อ ใช้ synthetic fixtures เท่านั้น |

รายงานเก่า 20 ต.ค. ตรวจ cross-source roster ห้าลีกได้ 71/96 ทีม (EPL16/20, LaLiga11/20, SerieA18/20, Bundesliga9/18, Ligue1 17/18) ไม่ใช่จำนวนทีมที่มีฟอร์ม/xG พร้อมวันนี้ และไม่มี Odds/API-Football club census จึงไม่ใช้ตัวเลขนี้อ้าง readiness ปัจจุบัน

Tests สร้าง `pilot-offline-acceptance.json` ใน artifact ของ CI โดยแยก `synthetic_test_fixture` ออกจาก live counts ซึ่งเป็น null ไม่ commit สถิติจำลองเข้าแหล่งข้อมูลใช้งานจริง

## การคงระบบเดิมและการทดสอบ

- ไม่แก้ `mobile_dashboard.py` ทั้งไฟล์ รวม CSS/การ์ด/รายละเอียดเดิม ไม่เปลี่ยน controls/layout/4 แท็บ เพิ่มเพียงข้อความวินิจฉัยที่ได้รับอนุญาตใน caption ของ PR #13 และ fields รายละเอียดเดิม
- Test contract ยังตรวจ 11 สูตรเดิมและทุก control/renderer ที่ป้องกันไว้ ตรวจ caption วินิจฉัยหนึ่งจุดแยกอย่างชัดเจน ไม่ regenerate contract เพื่อให้โค้ดใหม่ผ่าน
- ไม่แก้ Poisson/EV/A/B/C/Settlement ไม่เติม xG จากประตูจริง; ไม่มี xG ที่ผ่านเกณฑ์เดิม B/EV unavailable; UCL ยังคง domestic context เท่านั้น
- ไม่เพิ่ม schema migration ไม่เปิด/แก้ฐานบิลจริง ไม่แตะ PR #3/main ใช้ SQLite ชั่วคราวและ Backup/Restore tests เดิม
- Regression tests: 24/48 และข้ามเที่ยงคืนไทย, actual 36-hour scan/บันทึกบิล, UCL domestic form48h, คู่ไม่มีราคา, stale snapshot, dedup/conflict, รูปแบบ receipt ต่างกัน, alias/เลื่อนชั้น/ข้อมูลเก่า, source HTTP/error/cache, metadata/API Key redaction, Streamlit AppTest และ browser มือถือ
- CI ใช้ fixtures/mock และ runner ปิด socket ภายนอก ไม่มี Odds/Understat/API-Football/OpenAI จริง; ผลรวม tests และ browser screenshots อยู่ใน PR/Actions ของ commit ที่ส่งตรวจ
- ใช้ dependencies เดิม: Python3.11 ใน CI, Streamlit1.66.0, pandas2.3.3, requests2.34.2 ไม่เพิ่มบริการหรือค่าใช้จ่าย

## เจ้าของต้องตรวจใน Streamlit Cloud ก่อนใช้งานวันที่ 24 ต.ค.

1. Manage app ของ `value-bet-pro-ucl-pr6.streamlit.app`: repository=`it0151jsm-tech/football-scanner`, branch=`fix/ou-pair-diagnostics-and-ucl-form-audit`, main file=`app.py`; ตรวจ deployed commit/log ให้ตรง SHA **หลังอนุมัติรวม PR นี้เข้า branch ทดลอง** ปัจจุบัน GitHub head ของ branch คือ `09264e2` แต่ไม่มีสิทธิ์ Manage app จึงยืนยัน running SHA ไม่ได้ ชื่อ URL หรือ HTTP200 ไม่พิสูจน์ commit/backend
2. ตรวจ diff/Actions ของ PR ใหม่นี้ก่อนอนุมัติ ไม่ Merge main ไม่ Deploy ให้อัตโนมัติ ไม่ใช่ทุกการแก้บน PR #12/#13 จะขึ้นแอปเอง
3. ใช้ฐานทดลองแยกผ่าน `VALUE_BET_DB`; สำรองด้วย SQLite Backup API และทดสอบกู้คืนบนไฟล์ใหม่ เก็บ backup นอก Cloud ข้อจำกัด local disk บน Community Cloud ยังอยู่ ไม่ใช้เก็บบิลเงินจริงจนมี persistence/backup ที่พิสูจน์ได้
4. ตั้ง Key ใน Secrets ของแอปเท่านั้น: `THE_ODDS_API_KEY`; `API_FOOTBALL_KEY` เป็น optional ไม่ส่ง Key ผ่าน Issue/log/report ตรวจสิทธิ์ฤดูกาลของแพ็กเกจจาก diagnostic ไม่ซื้อ/อัปเกรดอัตโนมัติ
5. ทบทวนงบเดิม `VALUE_BET_API_DAILY_REQUESTS` (10), `VALUE_BET_API_DAILY_CREDITS` (20), `VALUE_BET_API_CREDIT_RESERVE` (10), cooldown อย่างน้อย300s และอายุราคาไม่เกิน900s ตัวเลขในวงเล็บเป็นค่าเริ่มต้น ไม่รับประกันเพดานเครดิตรวมหลายแอป/หลาย instance ที่ใช้คนละ ledger
6. บน Safari iPhone 320–430px เลือกลีกที่ source รองรับและ48h กด scan ด้วยตนเองหนึ่งครั้งหลังอนุมัติ ตรวจขอบเขตไทย/counts/reasons/เวลาเจ้ามือและเวลารับ ตรวจเกม36–48h ถ้า provider ส่งมา ห้ามอ้างว่าต้องมีเกมในช่วงนั้นเสมอ
7. ตรวจ A 5/10 นัดกับวันที่/ทีม/สกอร์และ source จริง ตรวจ B/EV เฉพาะ xG ที่ผ่าน sample/freshness; ถ้า source ถูกบล็อก/ไม่มีสิทธิ์/ไม่มีตัวอย่าง ต้องยัง No Bet แล้วสลับการ์ด/ตาราง/รายละเอียดโดยเครดิตไม่ลด

## Go / No-Go

พร้อมส่งตรวจโค้ดและการทำงานออฟไลน์ได้เมื่อ CI ของ head ผ่านครบ ยังยืนยันความพร้อม live ทุกลีก/ทุกคู่ภายในวันที่24ไม่ได้ เพราะไม่มี running SHA, live provider response/entitlement และ xG coverage ปัจจุบันที่พิสูจน์ได้ ต้องผ่านข้อ1–7ก่อนทดลองเฉพาะลีกที่ข้อมูลพร้อมจริง ไม่มีหลักฐาน calibration/กำไร และยังไม่พร้อมสำหรับบิลเงินจริงบน storage ที่ไม่ถาวร
