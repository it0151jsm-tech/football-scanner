# Issue #15 — ขอบเขตตรวจแหล่งฟอร์มและชื่อทีม

ตรวจบนฐาน `45f774e` โดยไม่เรียก The Odds API, API-Football หรือบริการเสียเงิน ไม่เปิดฐานบิลจริง และไม่สร้างข้อมูลฟอร์ม/xG/EV ทดแทน

## สิ่งที่ตรวจได้จริง

สั่งเปิด URL สาธารณะผ่าน web connector แบบจำกัดเพียงหนึ่งชุด: Football-Data E0 ฤดูกาล 2627, 2526, 2425, 2324 และ Understat EPL/2026 หนึ่ง URL ไม่มี raw CSV/JSON หรือ provider response headers ส่งกลับมา จึงยังตรวจคอลัมน์ จำนวนแถว วันที่ ชื่อทีมปัจจุบัน และ checksum ของ payload รอบนี้ไม่ได้

| URL ต้นทาง | ผลที่ connector รายงาน | ข้อสรุปที่อนุญาต |
|---|---|---|
| `https://www.football-data.co.uk/mmz4281/2627/E0.csv` | `(429) Too Many Requests` | เครื่องมือนี้เข้าถึงไม่ได้; ไม่ยืนยันว่า provider ตอบ 429 ต่อแอป Streamlit |
| `https://www.football-data.co.uk/mmz4281/2526/E0.csv` | redirect ไป non-www ที่เครื่องมือเข้าไม่ได้ | ไม่ได้ payload; ไม่ยืนยันว่าไฟล์ว่างหรือไม่มีฤดูกาลนี้ |
| `https://www.football-data.co.uk/mmz4281/2425/E0.csv` | redirect ไป non-www ที่เครื่องมือเข้าไม่ได้ | ไม่ได้ payload; ไม่ยืนยันว่าไฟล์ว่างหรือไม่มีฤดูกาลนี้ |
| `https://www.football-data.co.uk/mmz4281/2324/E0.csv` | เครื่องมือไม่รองรับ `text/csv` | ไม่ได้ payload ที่ตรวจเนื้อหาได้ |
| `https://understat.com/getLeagueData/EPL/2026` | URL เข้าไม่ได้ผ่านเครื่องมือ | ไม่ได้ตรวจ live xG และไม่ยืนยัน HTTP 403/429 จาก provider |

การลอง shell ก่อนหน้านี้ถูกยกเลิกระหว่างรอ network permission ก่อนรันจริง ตรวจแล้วไม่มี directory หรือ metadata รับข้อมูลจากคำสั่งนั้น จึงไม่ถือว่าได้ดาวน์โหลดหรือได้ HTTP status จาก shell ไม่มี retry หรือการหลบข้อจำกัด provider

หลักฐานอยู่ใน `fixtures/issue15_identity_evidence.json` แยก `connector_status` จาก `provider_http_status` ซึ่งยังเป็น `null` และแยก checksum ของข้อความข้อผิดพลาดจาก checksum ของ source payload ซึ่งยังไม่มี เก็บเวลาบันทึกหลักฐานจริง ไม่สมมติเวลารับข้อมูลฟุตบอล

## ขอบเขตชื่อทีม

- รอบนี้ตรวจ roster ปัจจุบันจาก source payload สำเร็จ **0 ทีม** แต่ตรวจ identity จากแหล่งประวัติและเอกสารสโมสรเพิ่มได้ **2 กลุ่ม** ตามหลักฐานด้านล่าง ไม่ถือว่าเป็นการยืนยันลีกปัจจุบัน
- Issue #15 มีจำนวนคู่รวม แต่ไม่มี raw payload ของชื่อ 20 ทีม/ชื่อทีมที่ล้มเหลว จึงระบุทีมที่ขาดเป็นรายชื่อจากตัวเลขรวมไม่ได้
- `fixtures/team_identity_evidence.json` ยังคงเป็นหลักฐานเดิมของ Stuttgart, Gladbach, Ipswich และ Forest; ส่วนชื่อ Understat เดิมยืนยันไว้เฉพาะสามทีม ไม่ยืนยัน Ipswich Understat ปัจจุบัน
- `reports/league-source-audit.json` เป็นการตรวจวันที่ 20 ตุลาคม: EPL cross-source 16/20 ชื่อ และห้าลีก 71/96 ชื่อ ไม่ใช่จำนวนทีมที่มีฟอร์มพร้อมหรือ B/EV พร้อมในวันนี้
- ไม่เพิ่ม Newcastle/Spurs/Wolves หรือชื่อย่ออื่นเพียงเพราะชื่อคล้าย เอกสาร Newcastle ที่อ่านยืนยันชื่อเต็ม แต่คำสั้น Newcastle อาจหมายถึงเมือง จึงยังไม่เพิ่มกลุ่มนั้น
- ไม่มี cross-provider numeric team ID ของ API-Football ที่ยืนยันจาก live payload รอบนี้

## Alias สองกลุ่มที่มีหลักฐานเพิ่มจริง

อ่าน historical mirror เพียงไฟล์เดียว 30,383 bytes ฤดูกาล 2324 จำนวน 380 แถว จาก `datasets/football-datasets` ซึ่ง README ระบุแหล่ง Football Data UK พบชื่อ `Man City` และ `Man United` จริง ยืนยัน bytes กับ Git blob `080a33fc535bf75e4c3baeca32ce425760985758` และ SHA256 `f64a0999d037a8d1bcd0346f5d7532bbc3d2ec3434f9a46591713ea23c47aa15` ไม่ commit ผลการแข่งขันดิบและไม่นำ mirror นี้เข้าโมเดลหรือใช้เป็น fallback ดาวน์โหลดอัตโนมัติ

- `Manchester City` ↔ `Man City`: [Terms of Use ของสโมสร](https://www.mancity.com/terms-of-use) นิยามชื่อสั้นโดยตรง คู่กับชื่อเต็ม Understat ใน audit เดิมและชื่อ CSV ใน mirror
- `Manchester United` ↔ `Man United`: [บทความสโมสรเกี่ยวกับ supporters' clubs](https://www.manutd.com/en/news/focus-on-man-utd-supporters-clubs-in-china-seattle-switzerland) มีชื่อเต็มและชื่อสั้นใน search-index excerpt ของแหล่งทางการ ขณะเปิดหน้า live เครื่องมือได้รับเพียง navigation/footer จึงเก็บข้อจำกัดนี้ชัดเจน ไม่อ้างว่าได้ raw article HTML ครบ

หลักฐานเอกสารเก็บ URL เวลาที่ตรวจจริง ขอบเขตข้อความ และ checksum เฉพาะข้อความที่เก็บไว้ ไม่สวมเป็น checksum ของ HTML ต้นทาง กลุ่ม alias ทั้งสองยืนยันเฉพาะตัวตนสโมสร การตรวจฤดูกาล ลีกปัจจุบัน sample/freshness ชื่อกำกวม และทีมเยาวชนยังใช้เกณฑ์เดิม ไม่อนุมานว่า 20 ทีม/คู่ที่รายงานใน Issue เป็นชื่อเหล่านี้ทั้งหมด

## สิ่งที่ยังต้องตรวจเพื่อรับงานบนข้อมูลจริง

เจ้าของต้องเก็บ scan diagnostics ของสแกนที่มีปัญหา พร้อมเวลา สถานะแหล่งข้อมูล รหัสลีก/ฤดูกาล และชื่อทีมจาก payload โดยลบ API Key ออกก่อนส่ง เมื่อแหล่ง CSV/Understat เข้าถึงได้จึงตรวจ roster และจับคู่หลักฐานเพิ่มเติม การเข้าถึงไม่ได้ใน environment นี้ไม่พิสูจน์ว่าสาเหตุเดียวกันเกิดใน Streamlit Cloud

ห้ามแปลผลการตรวจออฟไลน์เป็นคำยืนยันว่า 20 ทีมมีข้อมูลพร้อม หรือยืนยันจำนวนคู่ A/B ที่ใช้งานจริงได้แล้ว ผล mock และผล live ต้องรายงานแยกกัน

การลองผ่าน default sandbox อีกหนึ่งครั้งได้ `URLError` โดยไม่มีเนื้อหาหรือ HTTP response บันทึกไว้ใน `additional_sandbox_probe` แยกจากคำสั่งที่ถูกยกเลิกและ web connector ไม่ถือว่าเป็น provider HTTP error
