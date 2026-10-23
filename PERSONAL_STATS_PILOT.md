# PR #8 — Personal statistics import pilot

ทำงานต่อจาก `fe57aa162ef3f504e77223dd5748c3b07fb5d549` บน `feature/personal-stats-import-pilot` เท่านั้น ไม่แก้ production branch ไม่ Merge และไม่ Deploy

## สาเหตุ Actions เดิมล้ม

Run 39873902301 รัน 260 tests และล้มที่ `test_ui_templates_widgets_labels_css_and_model_formulas_unchanged` เพียงรายการเดียว: AST ของ test นับทั้ง conditional tab declaration 5/4 แท็บและเนื้อหาแท็บทดลองใหม่รวมกับสัญญาหน้าจอเดิม

แก้ test ให้ตรวจโครงสร้าง feature gate และ labels/assignment ของทั้งสอง branch อย่างเจาะจง แล้วแยกเฉพาะแท็บทดลองที่อนุญาตออกจากการเทียบ baseline เดิม ยังคงตรวจ controls เดิมทั้งหมด CSS, 14 renderer functions และ source hash ของสูตรเดิม 11 functions ไม่แก้ baseline ให้ยอมรับการเปลี่ยนหน้าจอโดยรวม

เมื่อปิด `VALUE_BET_PERSONAL_STATS_PILOT` หน้าจอเดิมยังมี 4 แท็บ และไม่มีการอ่าน/สร้างตารางสถิติส่วนตัวจากเส้นทางทดลอง เมื่อเปิดจึงเพิ่มแท็บคลังสถิติแยกต่างหากตาม PR #8 เดิม

## อัปโหลดและ AI extraction

- ตรวจเนื้อหาไฟล์จริงด้วย Pillow ก่อน preview หรือส่งไป AI: 1–6 ภาพ PNG/JPEG/WebP, ไม่เกิน 4 MB ต่อภาพ/12 MB รวม, 20 ล้าน pixels และความกว้าง/สูงไม่เกิน 10,000; ไม่รับภาพเสีย MIME ไม่ตรง ภาพเคลื่อนไหว หรือไฟล์อื่นปลอมเป็นภาพ
- อ่านเฉพาะเมื่อกดปุ่มและส่ง `user_requested=True` ไม่มี auto request/retry เมื่อเปิดแท็บหรือ rerun ไม่มีการใช้ The Odds API
- ผูกแต่ละชุดภาพด้วย SHA256/fingerprint และเก็บ filename/ขนาด/เวลาได้รับจริง เปลี่ยนภาพแล้วล้าง draft และ confirmation ที่ผูกกับชุดเก่า
- ผล AI เป็น **unverified draft** เสมอ ตรวจ schema ชนิดข้อมูล metric มาตรฐาน ค่าจำนวนเต็ม/ขอบเขต จำนวนยิงเข้ากรอบไม่เกินยิงรวม possession/xG–xGA ที่สอดคล้อง ไม่มีค่าทดแทนเมื่ออ่านไม่ได้
- ถ้าภาพหรือค่าใน alias ขัดแย้ง ให้กักค่าที่เกี่ยวข้องเป็น null พร้อมเหตุผล ไม่เลือกตัวเลขตามใจหรือเฉลี่ยให้ครบ ตัวเลขที่ยังขาดต้องกรอก/ตรวจเอง
- ผู้ใช้ต้องคัดลอกร่างไปแก้ไข และยืนยันข้อมูลหลังแข่งก่อนบันทึก ตรวจซ้ำฝั่ง server ไม่พึ่ง disabled button ใน Streamlit form (checkbox ใน form ไม่ทำให้ rerun ทันที)
- ไม่ส่ง kickoff/league/season จากการเดาในภาพไปใช้เอง ผู้ใช้ต้องยืนยันชื่อคู่ เวลา timezone ลีก ฤดูกาล แหล่งข้อมูลและสกอร์
- ข้อผิดพลาดจาก provider ไม่แสดง token, response body หรือ exception ที่อาจมี key

การทดสอบ AI ทั้งหมดใช้ transport mock และภาพที่สร้างใน test **ยังไม่ได้ส่งภาพจริงไป OpenAI** และไม่รับรองความแม่นของ OCR/vision กับภาพจริงทุกเว็บไซต์ การใช้งานจริงต้องมี `OPENAI_API_KEY` ของเจ้าของบนแอปทดลอง อาจมีค่าใช้จ่ายแยก; รอบพัฒนานี้ไม่ได้ใช้เครดิตผู้ให้บริการ AI หรือ Odds

## คลังข้อมูลและการป้องกัน leakage

เดิม `ON CONFLICT DO UPDATE` เขียนทับสถิติและเวลาบันทึก รวมทั้ง `kickoff_utc < ?` เทียบข้อความเวลา timezone ต่างกัน ซึ่งอาจเรียงผิดได้

ใหม่เพิ่ม `personal_match_stats_revisions` แบบ append-only พร้อม immutable triggers เก็บ revision เดิม ชื่อ/รหัสทีม ลีก ฤดูกาล แหล่ง URL image hashes และ supersedes ID เวลารับ/บันทึก/ตรวจใช้ UTC จริงจาก backend เท่านั้น ไม่รับเวลาย้อนหลังจาก caller

ไม่ย้าย ลบ หรือเขียนทับตาราง `personal_match_stats` เดิม ข้อมูลเก่ายังเปิดดูได้ แต่ไม่มีหลักฐานรับ/ตรวจที่พิสูจน์ได้จึงไม่กลายเป็น observed evidence อัตโนมัติ ข้อมูลที่ยังไม่ยืนยันหรือ research-only ไม่ผ่านเข้าระบบวิเคราะห์

`eligible_history` ใช้ UTC instant และ cutoff ที่ไม่เกินเวลาแข่งเป้าหมาย เลือกเฉพาะ revision ที่ระบบรู้แล้ว ณ cutoff, รับ/ตรวจจริงก่อน cutoff อย่างเคร่งครัด, ยืนยันว่าจบเกมแล้ว, ชื่อทีมตรง ลีก/ฤดูกาลตรง แยกเกมซ้ำและพักข้อมูลที่ขัดแย้ง การแก้ revision วันนี้จึงไม่ย้อนเปลี่ยนข้อมูลที่ backtest เมื่อวานรู้ได้

การนำผลเก่ามากรอกวันนี้หมายถึง **รู้ข้อมูลวันนี้** ไม่ได้หมายถึงพร้อมใช้ก่อนการแข่งขันเก่านั้น จะใช้สนับสนุนเกมในอนาคตได้เมื่อผ่านเกณฑ์ ห้ามนำไปอ้าง observed backtest ก่อนเวลารับจริง

## การเชื่อม A/B/C ที่ทำจริง

- Feature เปิดอยู่เท่านั้นจึงอ่านคลังจากการสแกนเดิม ไม่เพิ่มคำขอ Odds หรือ background task
- รองรับชื่อแบบตรงใน 5 domestic leagues: EPL, La Liga, Serie A, Bundesliga, Ligue 1 ซึ่งมีขอบเขตฤดูกาลที่ตรวจแล้ว ไม่เดาชื่อคล้ายหรือเดาลีกจากประเทศ
- สำหรับ UCL ใช้ลีกของแต่ละทีมที่ resolver เดิมยืนยันได้เท่านั้น ไม่ยืนยันทีมที่ resolver ยังจับคู่ไม่ได้
- **A:** ผลประตูที่ตรวจแล้วและผ่าน cutoff เติมแหล่งฟอร์มของทีมที่ไม่มีแหล่งเดิมใช้ได้ ผ่านฟังก์ชันฟอร์ม 5/10 และสูตร A เดิม ไม่ปนสถิติส่วนตัวเข้า team sample ของแหล่ง CSV ที่ใช้งานได้ เก็บ revision IDs และเหตุผลใน `analysis_a_json.personal_stats`
- ป้องกันชื่อทีมจากอีกลีก/ผลไม่ครบใน base ปนเข้า fallback และ namespace คู่แข่งที่ไม่ได้เป็น target ของ source นั้น หลักฐาน form audit ระบุ personal provider ตามจริง ไม่อ้างเป็น Football-Data
- **B:** personal xG/สถิติอื่นแนบเป็น context ที่ตรวจได้ แต่ **ไม่ใช้สร้าง Poisson/EV ทดแทน** เพราะยังไม่มี model validation สำหรับแหล่งนี้ ข้อมูลที่ขาดยังเป็น null/No Bet หาก Understat baseline เดิมพร้อม ยังคำนวณตามเส้นทางเดิม UCL B/EV ยังคงปิดตามข้อจำกัด cross-league เดิม
- **C:** ใช้สูตรเดิม; A มีฟอร์มแต่ B ไม่มี EV จะเป็นแนวโน้ม/ยังไม่แนะนำ ไม่เกิด Value Bet จากคะแนนฟอร์มอย่างเดียว

การออกแบบและเปิดใช้โมเดลจากสถิติส่วนตัว/xG ต่างผู้ให้บริการต้องทำ backtest แบบ point-in-time แยกลีกและ calibration ก่อน รอบนี้ไม่เปลี่ยนสูตร A/B/C, Poisson, AH/OU, EV หรือ Settlement

## Backup/Restore

ใช้ SQLite Backup API เดิมที่รวม WAL และ `PRAGMA integrity_check` ตรวจ Restore บนฐานใหม่เท่านั้น รักษา revisions, legacy stats และ hash ของบิล pending/finalized/JSON เสียในฐานทดลอง

พบและแก้บั๊ก: ไฟล์ว่างเคยผ่าน integrity check เพราะ SQLite ถือว่าเป็นฐานเปล่าใหม่ เพิ่มการตรวจชนิด payload/SQLite header ก่อนเปิดไฟล์ โดยคงชนิดข้อผิดพลาด SQLite เดิมและไม่เขียนทับปลายทางที่มีอยู่

ไม่เคยเปิดฐานบิลจริงระหว่างพัฒนา ทุก test ใช้ :memory: หรือ temporary directories ตัว feature เก็บสถิติใน **ตารางแยกของ configured SQLite เดิมตาม PR #8** ไม่ใช่ฐานถาวรภายนอกใหม่ Backup จึงมีข้อมูลส่วนตัวด้วย ต้องเก็บอย่างจำกัดสิทธิ์

## Tests และไฟล์

ชุดรวม: `PYTHONPATH=/workspace/pr8-deps python scripts/run_offline_tests.py` ปิด external Python sockets; browser wrapper ห้าม provider HTTP และ browser ยอมรับเฉพาะ localhost ทดสอบ synthetic images, mocked AI, SQLite ชั่วคราว ไม่ดาวน์โหลดฟุตบอลระหว่าง CI

เพิ่ม `test_stats_image_reader`, `test_personal_stats_integrity`, `test_personal_context`, `test_personal_scan`, `test_personal_backup`, `test_personal_streamlit`, `test_personal_browser` และ regression ใน `test_form_audit` พร้อมรักษา assertions เดิมใน `test_readiness`

ไฟล์หลัก: `app.py`, `personal_stats.py`, `personal_context.py`, `stats_image_reader.py`, `stats_quality.py`, `bill_backup.py`, `form_audit.py`; dependencies เพิ่ม Pillow แบบ pin; CI compile modules ใหม่ รายงานผลจำนวน tests/commit/run สุดท้ายอยู่ใน PR #8

## สิ่งที่เจ้าของต้องตรวจบน Streamlit Cloud/iPhone

1. ใช้แอปทดลองจาก feature branch และไฟล์ `VALUE_BET_DB` ของแอปทดลอง สำรองไฟล์เดิมก่อน อย่าชี้ไปฐานบิล production และจำกัดการเข้าถึงแอปเป็นส่วนตัว
2. เปิด pilot ด้วย `VALUE_BET_PERSONAL_STATS_PILOT=true`; ปิดค่านี้แล้วต้องกลับสู่ 4 แท็บเดิม ไม่ต้องตั้ง Odds key เพื่อทดสอบคลัง
3. หากจะตรวจ AI จริง จึงตั้ง `OPENAI_API_KEY` ใน Secrets และกดอ่านเอง ไม่วาง key ใน GitHub/JSON/ภาพ ทดสอบหลายภาพและตรวจทุกค่าก่อนยืนยัน ผลใน CI เป็น mock จึงไม่พิสูจน์ live access/model availability
4. บน Safari iPhone ตรวจเลือกภาพ 2–6 ภาพ อ่านร่าง แก้ตัวเลข ยืนยัน เปลี่ยนชุดภาพแล้ว draft เก่าหาย และเปิดประวัติ ภาพ HEIC ไม่รองรับ ต้อง export PNG/JPEG/WebP ก่อน
5. ดาวน์โหลด Backup จากแท็บประวัติไปที่เก็บภายนอก Cloud ที่จำกัดสิทธิ์หลังบันทึก และเก็บภาพต้นฉบับเอง ระบบเก็บ hashes/metadata **ไม่ได้เก็บไฟล์ภาพต้นฉบับ** จึงกู้ภาพจาก DB ไม่ได้
6. Restore ต้องทำ offline ลงไฟล์ใหม่ที่ยังไม่มี แล้วตรวจ integrity/รายการข้อมูลก่อนเปลี่ยน path ของแอปทดลอง ไม่กู้ทับฐานที่เปิดใช้งานอยู่

Browser CI ตรวจ Chromium 320/375/390/430 px light/dark ไม่ใช่ Safari เครื่องจริง SQLite บน Community Cloud ยังเป็น ephemeral และไม่มี multi-user isolation/auth ภายในแอปเอง จึงยังไม่พร้อมเป็นคลังบิลเงินจริงถาวรหรือเปิดให้สาธารณะร่วมกัน ไม่รับรองกำไรหรือความแม่นยำของโมเดล
