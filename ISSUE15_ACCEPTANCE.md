# Issue #15 — ฟอร์มพรีเมียร์ลีกและความพร้อมของสแกนจริง

ฐาน PR6: `fix/ou-pair-diagnostics-and-ucl-form-audit` ที่ `45f774edb5cca66d98ac315226c198d4480442c0` ซึ่งรวม PR #14 แล้ว งานแยกบน `fix/issue15-epl-coverage` ไม่ Merge main หรือ Deploy

## ข้อเท็จจริงและขอบเขต

Issue รายงานสแกน 24 ต.ค. 2569 เวลา 00:30 ไทย: API21คู่, ผ่าน48h10คู่, มีราคาทั้ง10, Aมีคะแนน2, Bพร้อม5, ฟอร์มตามสนามอย่างน้อย5นัด12/20ทีม และ API-Football `plan_unsupported` ข้อมูลนี้เป็นผลที่เจ้าของสังเกต ไม่ใช่ผลรันสดใน environment นี้

ไม่มี raw snapshot/event IDs/ชื่อ20ทีมของสแกนนั้นแนบใน Issue จึงยังไม่ระบุสาเหตุจริงของทั้ง8คู่รายชื่อได้จากจำนวนรวม ไม่อ้างว่าการจับชื่อเป็นสาเหตุทั้งหมด หรือว่าจำนวน A/B หลังแก้ต้องครบ10 ข้อมูลทดสอบจำลองแยกจาก live เสมอ

## สิ่งที่พิสูจน์จากโค้ด

- `estimate_form_signal` หยุดเมื่อจับชื่อเจ้าบ้านหรือทีมเยือนไม่ได้ แม้ราคาจริงและ xG อีกแหล่งมีอยู่ จึงอธิบายได้ว่าจำนวน A/B ไม่จำเป็นต้องเท่ากัน
- **A ไม่มีข้อบังคับว่าต้องครบ5นัดจึงมีคะแนน** สูตรเดิมอาจให้คะแนนจากตัวอย่างจริงน้อยกว่า5และลด/จำกัดคะแนนตามเดิม เกณฑ์5เป็นป้ายความพร้อมของฟอร์ม/ส่วนหนึ่งของคุณสมบัติ C; Bต้องมี xG ตามสนามอย่างน้อย5นัดต่อฝั่งจริง ไม่ลดเกณฑ์ใด
- Aใช้ประวัติ5/10/ทั้งหมดก่อนวันแข่ง ซึ่งอาจข้ามฤดูกาลตามนโยบายเดิม ไม่เปลี่ยนสูตรให้เอาเกมฤดูกาลก่อนมานับเป็น5นัดฤดูกาลปัจจุบัน
- `model_ready`/Bพร้อมหมายถึงมีผลโมเดลที่อ่านได้ ไม่ใช่ผ่าน Calibration; EVบวกยังไม่ใช่หลักฐานกำไร

## การแก้

1. เพิ่ม reviewed alias ของ Manchester City/Man City และ Manchester United/Man United พร้อมหลักฐาน official club และ mirror Football-Data ใน `fixtures/issue15_identity_evidence.json` ไม่เพิ่ม fuzzy/suffix-removal ไม่เพิ่ม Newcastle/Spurs/Wolves โดยไม่มีหลักฐานที่ผ่านการตรวจ ไม่ใช้ mirror เก่าเป็นสถิติเล่นจริง
2. เพิ่ม `form_readiness.py`: ชื่อที่จับคู่/วิธีจับ, เกมตามสนามทั้งหมด, ฤดูกาลปัจจุบัน/ก่อนหน้า/ไม่ยืนยันฤดูกาล, เกมที่ตัดเพราะวันแข่ง, ความเก่า และ provider/schema errors ตรวจวันที่เทียบ season อีกครั้งในการรายงาน ห้ามรายงานเกมปีเก่าเป็น current-season แม้ label ผิด
3. เพิ่มรายงานแยก fixture/team และเหตุ unavailable: alias/name, ไม่มีผลฤดูกาลปัจจุบัน, ตัวอย่างตามสนามน้อย, cutoff, provider failure, ข้อมูลต้นทางไม่ผ่าน รวมกรณีผลเทียบเส้นคืนทุนทั้งหมดและไม่มีราคาตลาด การมีคำเตือนไม่ทำให้คะแนน A ที่สูตรเดิมคำนวณได้ถูกลบ
4. Bแยก `xg_unavailable`, `model_gate_failed`, `no_valid_market`, `available_uncalibrated` ไม่เอา Calibration มาปนกับ availability ไม่สร้าง xG จากประตูจริงหรือ EV ทดแทน
5. API-Football: ปิดได้ด้วย `VALUE_BET_API_FOOTBALL_ENABLED=0` หรือ `false` ใน Secrets; ค่าเริ่มต้นคงพฤติกรรมเดิมเมื่อมี Key เพิ่ม backoffเฉพาะตอนจะเรียกจริง แพ็กเกจไม่รองรับ24h, HTTP401/40324h,4291h, ความล้มเหลวอื่น15m เก็บต้นทาง/receiptเดิม/เวลา retry ไม่เริ่มช่วงพักใหม่เมื่ออ่าน cache
6. Backoffใช้ SQLite ledger **แยก** `data/api_football_backoff.db` ไม่มีข้อมูลบิลหรือสถิติฟุตบอล เก็บ opaque scope hash/status เท่านั้น ปฏิเสธ foreign/bill database, symlink และ schema ผิดก่อนเขียน Atomic leaseป้องกันการเรียกพร้อมกันที่ใช้ไฟล์ร่วมกัน ไม่รับรอง quotaระหว่างคนละ filesystem/app
7. CSV-firstยังทำงานเมื่อ API-Footballปิดหรือแพ็กเกจไม่รองรับ แสดง CSV cache/fetch/unknown receipt, TTL900s และสถานะแหล่งจริง ไม่ทำให้ failureดูเป็น success ไม่เปลี่ยนเวลารับต้นทาง
8. รายงานความพร้อม A-ready/B-ready/C-supported ใช้เฉพาะ fixture/window/receipt ของสแกนที่ตรวจได้ Cใช้ policyเดิมกับราคาสด/EV/เส้นจริง ไม่เลือกเพราะ Aสูงหรือราคาแพง historical/settled/different snapshot ไม่เพิ่มยอดพร้อมสด การ replayด้วยเวลาห่างจาก wall clockเกิน30sไม่ประเมินCย้อนหลังโดยพลการ แต่เป็น unknown
9. แสดงข้อมูลผ่าน caption และ Data Health/รายละเอียดเดิม เพิ่มเฉพาะข้อมูล ไม่มี CSS/layout/controls/แท็บใหม่ ตารางและการ์ดเดิมอยู่ครบ4แท็บ ไม่เปลี่ยน `mobile_dashboard.py`
10. เก็บรายงานเต็มครั้งเดียวใน `league_health` และข้อมูลเฉพาะ fixtureใน Aแต่ละแถวเพื่อไม่เพิ่มข้อมูลซ้ำแบบกำลังสอง ข้อมูลวินิจฉัยเก่าเสียหายแสดง unknown ไม่เขียนทับ/ลบทิ้ง ไม่แก้ schemaฐานบิลหรือ Settlement

## สิ่งที่ทดสอบ

Baseline418 testsจากPR6ต้องยังผ่าน เพิ่ม tests สำหรับ10 fixture/20ทีมแบบจำลองที่มีเหตุไม่พร้อมต่างกัน ไม่ hardcodeจำนวน2/5ของผลจริง, Ipswich/Forest, aliasใหม่, เกมน้อย/เปลี่ยนฤดูกาล, บทบาทเกณฑ์5, cutoff/cache failure, B gates/uncalibrated, API plan/backoff/concurrency/ปิดด้วยSecrets/ฐานforeignไม่เปลี่ยน, scan48h/ไม่มีราคา, manifest persistence/บิลเก่า และ health JSONเสีย

เพิ่ม Streamlit AppTest อ่านรายงานที่บันทึกและสลับการ์ด/ตารางโดยห้าม HTTP พร้อม Chromiumที่320/375/390/430px ทั้งlight/dark เปิดรายละเอียดจาก scanจริงที่ใช้mockและทดสอบHTML escaping ผลรวมและ screenshotsของheadสุดท้ายอยู่ใน PR/Actions ไม่ใช้ mockไปยืนยัน livecoverage

Offline runnerบล็อก external sockets Testsไม่ดาวน์โหลดฟุตบอลหรือเรียก Odds/API-Football/Understat/OpenAIจริง ไม่ใช้เครดิต ไม่มี Keysใน commit/log/report Hashของ11สูตรเดิมและ original UI contractยังต้องผ่าน ไม่มีการลด assertionsหรือเพิ่ม timeoutเพื่อให้ testsผ่าน ส่วน testsเก่าที่วนหลาย failurecaseแยก temporary backoff scopeให้แต่ละ caseเป็นอิสระโดยรักษา assertionsเดิม

## ข้อจำกัดข้อมูลและการเข้าถึง

ดู `ISSUE15_SOURCE_AUDIT.md` และ evidence fixture: ตรวจ CSV E0 `2627/2526/2425/2324` กับ Understat EPL2026 ผ่าน connectorแบบจำกัด แต่ไม่ได้ rawpayload/headers ที่ตรวจได้ จึงยืนยันHTTP/columns/20team rosterปัจจุบันและ xGcoverageรอบนี้ไม่ได้ ไม่แปล connectorerrorเป็นข้อพิสูจน์ว่า Streamlitถูก providerปฏิเสธเช่นเดียวกัน

ใช้ mirrorฤดูกาล2324และ official clubเป็นหลักฐาน identityสองกลุ่มเท่านั้น ไม่ใช้ผลย้อนหลังจาก mirrorมาแทน sourceที่แอปโหลดไม่ได้ หลักฐานเก่า20ต.ค. cross-source16/20ก็ไม่ใช่จำนวนทีมพร้อมในวันนี้ ต้องแยก sourceตรวจไม่ได้/ไม่มีข้อมูล/จับชื่อไม่ได้/ข้อมูลเก่าให้ชัด

จำนวนทีมที่มีฟอร์มจริงและคู่ที่ A/B/Cพร้อมหลังแก้ **ยังไม่ได้วัดด้วย live scan** ไม่รับรองว่า8คู่หายครบ หากไม่มีผล/xGที่ผ่านเกณฑ์เดิมระบบยังต้องแสดงข้อมูลไม่พอ/No Bet ไม่บังคับให้มีคำแนะนำ

## ตรวจรับบน Streamlit Cloud แบบสแกนครั้งเดียว

1. ตรวจ Draft PR/CIของheadที่ส่งตรวจและอนุมัติก่อนรวมเข้า branchทดลอง PR6 ห้ามรวม main โดยอัตโนมัติ ตรวจ Manage app ว่า repo=`it0151jsm-tech/football-scanner`, branch=`fix/ou-pair-diagnostics-and-ucl-form-audit`, main file=`app.py` และ deployedSHAตรง commitหลังอนุมัติ ไม่มีสิทธิ์Manage appจากenvironmentนี้จึงยืนยันrunningSHAให้ไม่ได้
2. ใช้ `VALUE_BET_DB` ฐานทดลองแยก สำรองSQLiteด้วย Backup API/เก็บนอกCloud/ทดลองrestoreบนไฟล์ใหม่ตาม testsเดิม ไม่ใช้ฐานบิลเงินจริงรับงาน Ledgerแยกกำหนด `VALUE_BET_API_FOOTBALL_LEDGER` ได้ แต่ห้ามตั้งเป็นไฟล์เดียวกับฐานบิล
3. เพราะ Free planที่สังเกตไม่รองรับ ให้ตั้ง `VALUE_BET_API_FOOTBALL_ENABLED=false` ใน Secretsก่อนตรวจรับ ไม่ต้องซื้อหรืออัปเกรดแพ็กเกจ Keyคงไว้ในSecretsเท่านั้น ไม่ส่งผ่านIssueหรือภาพหน้าจอ หากเปิดfallbackในอนาคตและเห็น `retry_safety=not_persisted` ให้ปิดswitchก่อน เพราะไม่รับประกันbackoffข้ามrestart/หลายappหรือเมื่อเก็บledgerไม่ได้
4. ตรวจงบOddsเดิม (`VALUE_BET_API_DAILY_REQUESTS`, `VALUE_BET_API_DAILY_CREDITS`, credit reserve, cooldownอย่างน้อย300s) ไม่เพิ่มงบเพื่อให้testผ่าน จากSafariเลือกเฉพาะEPLและ48h แล้วกดscan **ครั้งเดียว** อ่านเครดิตจากheadersที่ระบบแสดง ไม่คาดว่าจำนวนคู่ต้องเท่ากับ21/10เสมอเพราะเวลาแข่งเดินต่อไปแล้ว
5. จดstart/endไทย, received_at, APIreturned/passed/excluded/reasons, A/B/C countsและ20ทีมที่สแกนได้จริง เปิดData Healthและการ์ดที่Aไม่มีคะแนน จดevent/team/mapping/current/prior/venue counts/provider/cutoffของแต่ละฝั่ง ตรวจเหตุBเป็นข้อมูลไม่พร้อมหรือmodelgate/uncalibrated ห้ามนับราคา/บิลเก่าเป็นสแกนสด
6. ตรวจManchester City/Unitedชื่อย่อกับsourceจริงเมื่อมีในwindow รวมIpswich/Forestถ้ามี ไม่เพิ่มคู่หรือเส้นสมมติเพื่อทดสอบ ตรวจAH/OUแยกเจ้ามือ/เส้น/เวลา Main Lineที่ไม่ผ่านยังNo Bet และคู่ไม่มีราคาไม่หาย
7. สลับการ์ด/ตาราง เปิดรายละเอียดและData HealthบนSafari320–430pxโดยไม่กดscanซ้ำ เครดิตต้องไม่ลดจากการแสดงผล หากราคาพ้น900sต้องไม่ผ่านคำแนะนำ C ไม่กดตรวจผล/แก้บิลจริงเพื่อรับงานนี้
8. เก็บ diagnosticsของคู่ไม่พร้อมจากสแกนครั้งเดียวโดยปิดบังKeys ให้ใช้ตรวจsource/aliasเพิ่มต่อ ห้ามสรุปทุกคู่พร้อมเพียงเพราะtestsผ่านหรือsnapshotมีราคา

## ประเมินพร้อมทดลอง

Goสำหรับการตรวจรับโค้ดเมื่อ CIผ่านครบ ส่วน livepilotยังขึ้นกับ runningSHA, sourceavailability/สิทธิ์แพ็กเกจ และผลสแกนจริงที่เจ้าของตรวจยืนยัน ไม่ใช่Goสำหรับความแม่นยำ/กำไรหรือบิลเงินจริงบนstorageที่ยังไม่พิสูจน์ความถาวร ไม่มี Merge/Deployในงานนี้
