# Issue #9 — ฟอร์มและ xG ของ Stuttgart–Gladbach / Ipswich–Forest

งานแก้แยกบน `fix/issue9-form-source-diagnostics` ต่อจาก PR #8 commit `6a1939862f0e01461d8c59c8cfe651fb5accf733` เพื่อรักษาชุดเดิม 345 tests ไม่แก้ PR #6/#8 เดิม ไม่ Merge หรือ Deploy

## ขอบเขตที่ตรวจได้

Issue รายงานจากแอป `value-bet-pro-ucl-pr6.streamlit.app` เวลา 23/10/2026 12:20 ไทย ไม่ใช่หลักฐานว่าแอปนั้นรัน PR #8 ซึ่งยังไม่ Merge/Deploy ต้องแยก code บน GitHub ออกจาก SHA ที่รันจริงบน Cloud

ตรวจ GitHub PR #6 ได้ head `236f76d4990abab0042d277999f8a449d13565bc` และ PR #8 head `6a1939862f0e01461d8c59c8cfe651fb5accf733` แต่ไม่มีสิทธิ์ Manage app/Cloud deployment logs จึงไม่ยืนยัน running SHA จากชื่อ URL เพียงอย่างเดียว เจ้าของต้องตรวจ repository/branch/main file และ commit ในแอปทดลองก่อนตรวจรับ Safari ไม่ได้ใช้ผลทดสอบ mock ไปอ้างว่าแอปที่ Deploy แล้วหายแน่นอน

ตรวจ public GET `/` และ `/_stcore/health` เวลา 2026-10-23T05:52:23Z ได้ `URLError` ไม่ได้รับ HTTP status/HTML/deployment metadata ที่ยืนยัน SHA ได้ การลองผ่าน network permission เพิ่มถูกยกเลิกก่อนมีผลลัพธ์ ไม่มี WebSocket/การกดสแกนหรือการใช้เครดิต API

## สาเหตุที่พิสูจน์จากโค้ดและ fixtures

1. `history_data.match_team` เดิมมี reviewed alias เฉพาะ Oxford/Wigan ชื่อทั้งสี่จาก Issue จับคู่กับชื่อ CSV ไม่ได้จริงในการรัน deterministic ก่อนแก้ ทำให้ A มี 0 นัดแม้โหลด CSV ได้ และกระทบชื่อข้าม source ของ Understat ด้วย
2. `fetch_understat_history` เดิมข้าม HTTP/JSON/แถวเสียทุกกรณีแบบเงียบ จึงแยกไม่ได้ว่าไม่มี xG เพราะ provider ปฏิเสธ รูปแบบเปลี่ยน ชื่อไม่ตรง เกมตามสนามไม่ถึง 5 หรือข้อมูลเก่า
3. Cache เดิมอาจเก็บ empty failure 6 ชั่วโมงเหมือนข้อมูลสำเร็จ และข้อมูลไม่มี timestamp/checksum ของ source สำหรับวินิจฉัย การแก้ชื่อไม่สามารถสร้างเกม xG ที่ provider ไม่มีให้ได้
4. เดิมอนุโลม `isResult` ที่หายเป็น True และ dedup เก็บแถวสุดท้ายก่อนตรวจความขัดแย้ง จึงเพิ่ม validation ก่อนส่งข้อมูลเข้าโมเดลแทนเปลี่ยนสูตร

ยังไม่มี raw failed-source logs หรือ saved analysis ของการสแกน 12:20 จึงไม่อ้างว่ามี HTTP 403 จริงในเหตุการณ์นั้น หรือว่าข้อมูล xG ปัจจุบันของ Ipswich มีครบแล้ว สิ่งที่ยืนยันคือบั๊กจับชื่อและการสูญเสีย diagnostics ในโค้ด พร้อม reproduction ที่ระบุแหล่ง input ชัดเจน

## หลักฐานชื่อจริงและลีก

ตรวจ CSV สาธารณะโดยตรงเพียงสองไฟล์ รวมประมาณ 61 KB เพื่อยืนยัน labels/columns ไม่ได้นำผลหรือราคาจริงเข้า tests/ฐานโมเดล:

| Source | HTTP / รับจริง UTC | ชื่อที่พบ |
|---|---|---|
| Football-Data `2627/D1.csv` | 200 / 2026-10-23 05:53:38 | `Stuttgart`, `M'gladbach` |
| Football-Data `2627/E0.csv` | 200 / 2026-10-23 05:53:40 | `Ipswich`, `Nott'm Forest` |

- `VfB Stuttgart` ↔ `Stuttgart`
- `Borussia Monchengladbach` ↔ `M'gladbach` ↔ `Borussia M.Gladbach`
- `Ipswich Town` ↔ `Ipswich`
- `Nottingham Forest` ↔ `Nott'm Forest`

ชื่อ API ใน mapping เป็นหลักฐานจากรายงานผู้ใช้ Issue #9 ไม่ใช่ API payload ที่ดึงใหม่ ส่วนชื่อ Understat ใช้ saved source audit เดิมที่ระบุ provider ไว้ ไม่อ้างว่าตรวจ live Understat วันนี้

หลักฐาน URL/ฤดูกาล/HTTP/SHA256/เวลารับและขอบเขตชื่อแต่ละ source อยู่ใน `fixtures/team_identity_evidence.json` ไม่ commit CSV เต็มหรือสถิติแข่งขันจริง

Ipswich ปรากฏใน E0 ฤดูกาล 2627 ที่ตรวจครั้งนี้ จึงไม่ควรอนุมานจากลีกฤดูกาลเก่าหรือสมมติว่าอยู่ E1 เสมอ อย่างไรก็ตาม CSV ที่ตรวจวันนี้ไม่พิสูจน์ label ของ Odds event หรือสถานะ provider ณ 12:20 การ resolve ภายในโปรแกรมยังต้องใช้ผลฤดูกาลปัจจุบันก่อนวันแข่งขัน ไม่ย้ายทีมกลับลีกเก่าจาก alias

## การแก้

- Reviewed alias เฉพาะสี่ทีมจากหลักฐาน ไม่เพิ่ม fuzzy similarity, ไม่ตัดอักขระ Unicode ให้ชื่ออีกทีมตรงโดยบังเอิญ และ fail closed เมื่อมีชื่อที่ทำให้กำกวมใน roster เดียวกัน ชื่อ U21/ทีมอื่น/รูปสะกดที่ยังไม่ยืนยันไม่ถูกยืมสถิติ
- ระบุ source ของ alias ตามทีมจริง ไม่อ้าง URL Oxford ให้ทีมอื่น
- แยก Understat loader เป็น `understat_history.py` พร้อม per-season HTTP/status/reason, columns/schema, counts ของแถวที่ไม่ผ่าน, URL/receipt จริง/checksum และประวัติข้อมูลขัดแย้ง
- ต้องมี `isResult=True` ชัดเจน ผลประตู/xG ไม่ติดลบ ไม่เป็น NaN/inf มีวันและฤดูกาลสอดคล้อง ผล/ID/ชื่อ/xG ที่ขัดแย้งกักก่อน dedup ไม่เลือกแถวสุดท้าย
- เจอ 401/403/429 หยุดวนขอฤดูกาลที่เหลือ ไม่ bypass provider หรือสร้าง fallback HTML/ข้อมูลแทน
- Successful source cache 6 ชั่วโมงตามเดิม; failure diagnostics cache 15 นาที เฉพาะ explicit scan ไม่มี background scan/การโหลดจากการสลับมุมมอง และไม่เปลี่ยนเวลารับเดิมของ cache ให้ดูใหม่
- เวลา Understat ที่ไม่มี timezone เก็บเป็น source calendar date ไม่สมมติเป็น UTC ใช้ขอบเขตเผื่อ date+2 วัน UTC ที่ต้องก่อน kickoff และเวลาวิเคราะห์อย่างเคร่งครัดก่อนยอมรับ เพื่อกันการใช้ข้อมูลที่อาจยังไม่จบเมื่อเทียบเขตเวลา ส่วนเวลาที่ระบุ offset ใช้ instant จริง
- ตรวจเวลารับกับเวลาวิเคราะห์หลัง fetch ไม่ใช้ scan-start timestamp ตัดข้อมูลที่เพิ่งรับทั้งหมดโดยผิดลำดับ ข้อมูลที่รับหลังเวลาวิเคราะห์ย้อนหลังไม่ถูกใช้เป็น observed evidence ในอดีต
- เพิ่ม structured diagnostics ใน analysis ที่บันทึก และส่งข้อความเหตุผลผ่าน fields/expander เดิมบนมือถือ รวม HTTP/ชื่อ/จำนวนตามสนาม/ฤดูกาล/ความเก่า/URL/เวลารับ ไม่แก้ layout/CSS/widgets หรือ 4 แท็บเดิม
- A ใช้ผลจริงที่ผ่านการตรวจและ cutoff ตามสูตรเดิม B/EV ใช้ xG จริงเฉพาะเมื่อผ่าน sample/freshness/model requirements เดิม ถ้าผลอย่างเดียวหรือ xG ไม่ครบยังเป็น unavailable/No Bet แม้มีราคา AH/OU แล้ว
- UCL B/EV ยังคงปิดตามข้อจำกัดโมเดลข้ามลีก ไม่เอา A หรือราคามาแปลงเป็นความน่าจะเป็น ไม่ใช้ CSV `HxG/AxG` เป็น Understat ทดแทนโดยไม่มี source/model validation

## Tests

เพิ่ม offline regressions สำหรับชื่อทั้งสองคู่, source 403/429/404/JSON เสีย, xG ขั้นต่ำ 5 เทียบ 4 นัด, xG เก่า, form 5/10, alias/Unicode/ชื่อกำกวม, เกมวันเดียวกัน/อนาคต/หลัง cutoff, ทีมเลื่อนชั้น–ตกชั้นและลีกปัจจุบัน, การเก็บ provenance, ข้อความบน Streamlit การ์ด/ตาราง และบิลเดิม

Source loader tests ใช้ mocked HTTP; scan tests ใช้ synthetic fixtures ที่ระบุว่าเป็นข้อมูลทดสอบกับ SQLite ชั่วคราวเท่านั้น CI ไม่ดาวน์โหลด CSV/Understat จริง ไม่มี The Odds/OpenAI API หรือเครดิตจริง ไม่เปิดฐานบิลจริง ผลรวมที่ commit สุดท้ายและลิงก์ CI ระบุใน PR

สูตรเดิม 11 functions, renderer functions/CSS และ UI contract เดิมต้องยังผ่าน มีเพียง input validation/identity/provenance/diagnostic paths ที่แก้ ไม่มี schema migration หรือการแก้ผลบิลย้อนหลัง

## ไฟล์และตรวจรับ

- `history_data.py` / evidence fixture: alias และหลักฐานแต่ละ source
- `understat_history.py`: source loader, validation, eligibility bounds, failure report
- `source_diagnostics.py`, `data_health.py`: แยกเหตุ provider/name/sample/freshness/membership
- `app.py`: cache policy และต่อ diagnostics/eligible data เข้าสูตรเดิม
- tests ใหม่และ `.github/workflows/tests.yml`: regression/compile modules ใหม่

หลัง CI ผ่าน เจ้าของต้องยืนยัน branch/SHA ใน Manage app และทดสอบบน staging ด้วยตนเองหลังอนุมัติ ไม่เปลี่ยน main หรือ Deploy ให้อัตโนมัติ ตรวจ Safari ว่า A แสดงฟอร์มพร้อม source time เมื่อ source มีจริง และเมื่อไม่มีให้เห็นเหตุผลเฉพาะพร้อม unavailable B/No Bet การมี alias ไม่รับรองว่า provider จะพร้อมหรือ xG จะครบ และไม่ใช่หลักฐาน Calibration/กำไร
