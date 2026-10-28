# คลังหลักฐานสำหรับ Backtest ที่เริ่มเก็บจากปัจจุบัน

คลังนี้แยกจากฐานบิล ไม่มีการเรียก API เพิ่ม ไม่มีระบบดึงอัตโนมัติ และปิดไว้เป็นค่าเริ่มต้น แอปเรียก hook เฉพาะหลังคำขอเดิมได้รับ response จริง ส่วน cache ใช้ receipt เดิม ไม่บันทึกเวลารับใหม่ให้ข้อมูลเก่า

## เปิดใช้หลังตรวจสิทธิ์และพื้นที่จัดเก็บ

ตั้ง `VALUE_BET_OBSERVATION_DB` เป็นฐานใหม่ เช่น `data/football_observations.db` ต้องเป็นคนละไฟล์กับ `VALUE_BET_DB` รวมถึงห้ามชี้ผ่าน symlink/hardlink ไปฐานบิล ระบบปฏิเสธฐาน SQLite ที่มีอยู่แต่ไม่มี schema ของ observer

ตั้ง `VALUE_BET_OBSERVATION_RIGHTS_JSON` เป็น JSON แยกตามชื่อ provider ที่แอปใช้: `the_odds_api`, `football_data`, `understat`, `api_football` ก่อนอนุมัติจริงให้ใช้สถานะ pending เท่านั้น:

```json
{
  "the_odds_api": {
    "rights_status": "pending",
    "permissions": {"store_raw": false, "model_research": false}
  }
}
```

การเก็บ raw body ต้องมีหลักฐานสิทธิ์ที่เจ้าของตรวจแล้ว โดย policy ระบุ `rights_status: approved`, `reviewed_by`, `reviewed_at` แบบมี timezone, `rights_evidence_url` ที่ตรวจย้อนหลังได้ และ `permissions.store_raw` กับ `permissions.model_research` เป็น true ทั้งคู่ ห้ามเปลี่ยนเป็น approved เพียงเพื่อให้ข้อมูลผ่าน ชื่อแหล่งข้อมูลและสถานะสิทธิ์ไม่ได้ยืนยันสิทธิ์ใช้ข้อมูลของผู้ให้บริการแทนเจ้าของ

หากยังไม่มีสิทธิ์ชัดเจน ระบบเก็บเฉพาะ checksum/เวลารับ/metadata ใน quarantine ไม่เก็บเนื้อหา raw ที่สิทธิ์ไม่ชัดเจน และไม่ส่งข้อมูลนั้นเข้า Backtest การอนุมัติภายหลังไม่เปลี่ยนแถว quarantine เดิมให้เป็น observed โดยเงียบ ๆ

SQLite บน Streamlit Community Cloud ยังเสี่ยงหายเมื่อแอปถูกสร้างใหม่ การเปิด collector ไม่ใช่หลักฐานว่ามีพื้นที่เก็บถาวรแล้ว ต้องใช้ Backup API และนำสำรองออกไปเก็บในพื้นที่ส่วนตัวที่ตรวจคืนได้ การทดสอบใช้ฐานชั่วคราวเท่านั้น ไม่แตะฐานบิลจริง

## สิ่งที่เก็บและสิ่งที่ยังไม่ครบ

- Raw response เก็บเป็น bytes เดิมพร้อม SHA-256 และเวลาที่ hook ได้รับจริง ไม่สร้าง JSON ย้อนแทน bytes จาก provider
- `record_batch()` รับแถวที่ parser ตรวจแล้วจาก Snapshot เดียวกัน ระบบวัดเวลา availability เองและเก็บ `recorded_at` แยกจาก `source_received_at` ห้าม caller กำหนด receipt ย้อนหลัง
- การแปลง odds ผ่าน `capture_completed_scan()` ตรวจ event/team/sport key กับ raw response เดิม และแปลงทุกฝั่ง AH/OU ที่มีจริง ไม่สร้างราคา เส้น หรือ bookmaker ที่ขาด
- ต้องมี `_observation_raw_id` และ `_observation_context` ต่อ event ซึ่งระบุ `identity_verified`, `season`, `period: FT90`, `season_evidence_url`, `identity_evidence_url`, `period_evidence_url` ที่ผ่านการตรวจ จึง normalize ได้ ไม่เดาฤดูกาลหรือ FT90 จากประเทศ/ชื่อทีม/วันที่
- หาก source ไม่มี manifest ดังกล่าว สถานะเป็น `raw_captured_pending_verified_context` ไม่อ้างว่าได้ cohort ที่พร้อมวัดผลแล้ว
- ฟอร์ม/xG ต้องมีแถวรายเกมที่ผูกกับ raw source และเวลารับจริง ไม่ขยายค่าเฉลี่ยของการ์ดให้เป็นประวัติเกม ไม่ใช้ประตูแทน xG ข้อมูลปัจจุบันที่ยังไม่มี row-level manifest จะรายงาน `missing_row_bound_feature_observations`
- เวลาราคาใช้ `quote_update()` เช่นเดียวกับระบบหลัก: ใช้ market timestamp เมื่อมี; หากมีแต่ผิดจะไม่ปิดบังด้วย bookmaker timestamp หาก market field ไม่มีจึงใช้ bookmaker scope พร้อมบันทึกระดับแหล่งเวลา

สำหรับการทำนายใหม่ context ต้องมี `model_version` ที่ตรงกับ baseline ที่ตรึงไว้ การเพิ่ม `log_decision()` จะบันทึกจุดตัดสินใจจริงและรายชื่อ/hash ของ input ที่ได้รับก่อนแข่ง ห้ามใส่ผลเป้าหมายหรือ Closing เป็น input

## Export และ Backtest

```bash
python scripts/export_observed_backtest.py --archive-db data/football_observations.db --output observer-export.json
python scripts/run_backtest.py --archive observer-export.json --output observer-backtest.json
```

ทั้งสองคำสั่งต้องสร้างไฟล์ใหม่ ไม่เขียนทับรายงานเดิม Export เปิด observer แบบ read-only และใช้ transaction เดียว หากไฟล์ไม่ใช่ observer หรือหลักฐานเสียหายจะหยุดโดยไม่เผยข้อความ provider/API key

Export ใช้สองรอบ: สร้าง source proof ของ event/history/quotes ก่อน แล้วผูก `input_observation_ids` และ `input_normalized_sha256` เดิมเข้ากับ `input_source_payload_sha256` ของแถวที่ส่งออกจริง ข้อมูล input ขาด/เปลี่ยน hash จะทำให้ export ล้มเหลวอย่างชัดเจน ไม่ข้ามหลักฐานที่ขาดเพื่อให้ผลดูครบ เวลา decision และ receipt เดิมไม่เปลี่ยน

Raw/normalized evidence มี SQLite triggers ป้องกัน update/delete การแก้ข้อมูลต้องเพิ่ม revision ใหม่และรักษาแถวเดิม Checksum รับรองความสอดคล้องของเนื้อหา แต่ไม่สามารถยืนยันความแท้ของ provider/clock หรือผลกำไรได้ด้วยตัวเอง

ขณะนี้ยังไม่มีข้อมูลจริงครบก่อนแข่งที่ผ่านเกณฑ์สำหรับสรุป ROI/Calibration เครื่องมือนี้เริ่มกระบวนการสะสมหลักฐาน ไม่ได้สร้างผลงานย้อนหลังขึ้นใหม่ ข้อมูลทดสอบใน Tests เป็น fixtures ที่ผู้พัฒนาเขียนและ mock clock เท่านั้น ไม่มีการนำผลนั้นมาอ้างเป็นความแม่นยำจริง
