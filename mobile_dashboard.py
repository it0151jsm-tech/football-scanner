"""Presentation only: render saved analysis; no model, database, or network calls."""
import json
import math
from html import escape


DASHBOARD_CSS = """<style>
.vbp-dashboard,.vbp-details{--vbp-line:rgba(128,145,170,.25);--vbp-soft:rgba(128,145,170,.08);--vbp-blue:#3578e5;--vbp-teal:#15947c;--vbp-gold:#be8a2d;color:var(--text-color,#192b43);font-family:inherit;box-sizing:border-box;width:100%;max-width:100%;min-width:0;overflow-wrap:anywhere;word-break:normal}
.vbp-dashboard *,.vbp-details *{box-sizing:border-box;min-width:0}
.vbp-dashboard{background:var(--background-color,#fff);border:1px solid var(--vbp-line);border-radius:22px;overflow:hidden;margin:8px 0 4px;box-shadow:0 6px 24px rgba(12,24,42,.05)}
.vbp-dashboard h3,.vbp-dashboard p,.vbp-details p{margin:0}
.vbp-header{padding:20px;background:linear-gradient(135deg,rgba(53,120,229,.12),rgba(21,148,124,.04))}
.vbp-meta{font-size:.78rem;opacity:.78;line-height:1.6}
.vbp-title{font-size:1.35rem!important;line-height:1.4!important;font-weight:750;margin:8px 0 12px!important;padding:0!important}
.vbp-badges{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.vbp-badge{border:1px solid var(--vbp-line);border-radius:9px;padding:4px 8px;font-size:.76rem;background:var(--vbp-soft)}
.vbp-quote{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-top:12px}
.vbp-selection{font-weight:650;font-size:.95rem}
.vbp-odds{font-size:1.65rem;font-weight:800;font-variant-numeric:tabular-nums;flex-shrink:0;color:var(--vbp-blue)}
.vbp-section{padding:18px 20px;border-top:1px solid var(--vbp-line)}
.vbp-label{font-size:.72rem;letter-spacing:.06em;font-weight:750;text-transform:uppercase;opacity:.8;margin-bottom:10px!important}
.vbp-score-row{display:flex;gap:16px;align-items:center;margin-bottom:14px}
.vbp-score{font-size:3.1rem;font-weight:800;line-height:1.05;letter-spacing:-.04em;font-variant-numeric:tabular-nums;color:var(--vbp-blue)}
.vbp-score small{font-size:.85rem;opacity:.65;letter-spacing:0;font-weight:500}
.vbp-small{font-size:.76rem;opacity:.75;line-height:1.65}
.vbp-result{font-size:.92rem;font-weight:650;line-height:1.5}
.vbp-grid2{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}
.vbp-tile{padding:12px;border-radius:13px;background:var(--vbp-soft);border:1px solid var(--vbp-line)}
.vbp-team{font-size:.83rem;font-weight:650;line-height:1.45;margin:3px 0 10px!important}
.vbp-statline{display:flex;flex-wrap:wrap;justify-content:space-between;gap:4px;font-size:.8rem;line-height:1.8}
.vbp-statline b{font-variant-numeric:tabular-nums;font-size:1rem}
.vbp-goals{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(0,1fr);gap:10px;text-align:center;align-items:center;margin:12px 0 18px}
.vbp-goal{font-size:2.45rem;font-weight:800;line-height:1.2;font-variant-numeric:tabular-nums}
.vbp-vs{opacity:.45;font-size:.8rem}
.vbp-bar{height:12px;border-radius:12px;display:flex;overflow:hidden;background:var(--vbp-soft);margin:8px 0 10px}
.vbp-bar span{height:100%;display:block}
.vbp-home{background:var(--vbp-blue)}.vbp-draw{background:var(--vbp-gold)}.vbp-away{background:var(--vbp-teal)}
.vbp-grid3{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:6px;text-align:center;margin-bottom:18px}
.vbp-percent{font-size:1.22rem;font-weight:750;font-variant-numeric:tabular-nums;margin:4px 0!important}
.vbp-ou{text-align:center}.vbp-ou .vbp-percent{font-size:1.6rem}
.vbp-status{border:1px dashed var(--vbp-line);padding:18px 14px;border-radius:13px;background:var(--vbp-soft);text-align:center}
.vbp-price{font-size:1.45rem;font-weight:750;font-variant-numeric:tabular-nums;margin:5px 0!important}
.vbp-change{font-size:.8rem;line-height:1.65;margin-top:10px!important}
.vbp-conclusion{background:rgba(53,120,229,.07);border-left:3px solid var(--vbp-blue);border-radius:12px;padding:14px}
.vbp-conclusion .vbp-result{margin-bottom:8px!important}
.vbp-details{font-size:.84rem;line-height:1.8}
.vbp-details h4{font-size:.9rem;margin:14px 0 8px}
.vbp-details .vbp-tile{margin-bottom:8px}
@media(prefers-color-scheme:dark){.vbp-dashboard,.vbp-details{color:var(--text-color,#e8eef8);--vbp-blue:#82b2ff;--vbp-teal:#52c6ad;--vbp-gold:#ddb465;--vbp-soft:rgba(156,175,202,.09)}.vbp-dashboard{background:var(--background-color,#111827)}}
@media(max-width:430px){.vbp-header{padding:16px}.vbp-section{padding:16px}.vbp-title{font-size:1.2rem!important}.vbp-grid2{gap:8px}.vbp-tile{padding:10px}.vbp-score{font-size:2.7rem}.vbp-goal{font-size:2.15rem}.vbp-percent{font-size:1.1rem}.vbp-ou .vbp-percent{font-size:1.35rem}}
</style>"""


def text(value, fallback="ไม่มีข้อมูล"):
    """Escape every external string, including quotes, before HTML interpolation."""
    if value is None or (isinstance(value, float) and not math.isfinite(value)):
        value = fallback
    return escape(str(value) if str(value).strip() else fallback, quote=True)


def number(value, digits=1, suffix=""):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "ไม่มีข้อมูล"
    return f"{value:.{digits}f}{suffix}"


def object_json(raw):
    try:
        value = json.loads(raw) if isinstance(raw, str) else None
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def mapping(value):
    return value if isinstance(value, dict) else {}


def model_ready(b):
    """Validate presentation fields; never invent or recompute model output."""
    goals, hda = b.get("expected_goals"), mapping(b.get("hda"))
    if b.get("available") is not True or not isinstance(goals, list) or len(goals) != 2:
        return False
    values = goals + [hda.get(key) for key in ("home", "draw", "away")]
    return all(type(v) in (int, float) and math.isfinite(v) and v >= 0 for v in values) and all(hda[k] <= 100 for k in ("home", "draw", "away"))


def form_tile(row, a, side):
    profile = mapping(a.get(side + "_form"))
    diagnostic = mapping(profile.get('diagnostics'))
    label = "เจ้าบ้าน · เหย้า" if side == "home" else "ทีมเยือน · เยือน"
    return (f'<div class="vbp-tile"><p class="vbp-small">{label}</p>'
            f'<p class="vbp-team">{text(row.get(side + "_team"))}</p>'
            f'<div class="vbp-statline"><span>ยิงเฉลี่ย</span><b>{number(profile.get("last5_gf"), 2)}</b></div>'
            f'<div class="vbp-statline"><span>เสียเฉลี่ย</span><b>{number(profile.get("last5_ga"), 2)}</b></div>'
            f'<p class="vbp-small">5 นัดล่าสุดตามสนาม · มีข้อมูล {text(profile.get("sample5", 0))} นัด</p>'
            f'<p class="vbp-small">{text(diagnostic.get("message"), "สถานะโหลด/ชื่อทีม: ยังไม่มีข้อมูลวินิจฉัย")}</p>'
            + ('<p class="vbp-small">ตัวอย่าง 5 นัดครอบคลุมช่วงเกิน 180 วัน — รวมเกมเก่า</p>' if diagnostic.get('sample5_span_days', 0) > 180 else '') + '</div>')


def model_html(row, b, include_ou=True):
    if not model_ready(b):
        return ('<div class="vbp-status" data-model="unavailable"><p class="vbp-result">ข้อมูลโมเดลไม่เพียงพอ</p>'
                '<p class="vbp-small">ยังไม่แสดงประตูคาดการณ์ เปอร์เซ็นต์ หรือ EV ทดแทน</p></div>')
    goals, hda = b["expected_goals"], b["hda"]
    content = '<div data-model="available"><p class="vbp-small">ประตูคาดการณ์ · ไม่ใช่สกอร์ฟันธง</p><div class="vbp-goals">'
    content += f'<div><p class="vbp-small">เจ้าบ้าน</p><div class="vbp-goal">{number(goals[0], 2)}</div></div><span class="vbp-vs">VS</span>'
    content += f'<div><p class="vbp-small">ทีมเยือน</p><div class="vbp-goal">{number(goals[1], 2)}</div></div></div>'
    labels = [("home", "เจ้าบ้านชนะ"), ("draw", "เสมอ"), ("away", "ทีมเยือนชนะ")]
    alt = " | ".join(f"{label} {number(hda[key], 2, '%')}" for key, label in labels)
    content += f'<div class="vbp-bar" role="img" aria-label="{text(alt)}">'
    for key, _ in labels:
        # Only validated finite numbers enter CSS; no API string becomes a style.
        content += f'<span class="vbp-{key}" style="width:{hda[key]:.4f}%"></span>'
    content += '</div><div class="vbp-grid3">'
    for key, label in labels:
        content += f'<div><p class="vbp-small">{label}</p><p class="vbp-percent">{number(hda[key], 2, "%")}</p></div>'
    content += '</div>'
    if not include_ou:
        return content + '<p class="vbp-small">ยังไม่ผ่าน Calibration</p></div>'
    ou = mapping(b.get("ou"))
    same_market = row.get("market") == "Over/Under" and isinstance(row.get("point"), (int, float)) and row.get("point") == b.get("ou_line")
    line_label = "เส้นตลาดจริง" if same_market else "เส้นอ้างอิงโมเดล · ไม่ใช่ราคาตลาดนี้"
    content += f'<p class="vbp-small">สูง/ต่ำ {text(b.get("ou_line"))} · {line_label}</p><div class="vbp-grid2">'
    for key, label in [("over_full", "สูง · ชนะเต็ม"), ("under_full", "ต่ำ · ชนะเต็ม")]:
        content += f'<div class="vbp-tile vbp-ou"><p class="vbp-small">{label}</p><p class="vbp-percent">{number(ou.get(key), 2, "%")}</p></div>'
    content += f'</div><p class="vbp-small">ผลครึ่ง/คืนทุน {number(ou.get("partial_or_push"), 2, "%")} · ยังไม่ผ่าน Calibration</p></div>'
    return content


def price_html(a):
    price = mapping(a.get("price"))
    if not price:
        return '<div class="vbp-status"><p class="vbp-small">ยังไม่มีประวัติราคาเปรียบเทียบ</p></div>'
    content = '<div class="vbp-grid2">'
    for prefix, label in [("first", "First Observed Price"), ("current", "ราคาปัจจุบัน ณ สแกน")]:
        content += (f'<div class="vbp-tile"><p class="vbp-small">{label}</p>'
                    f'<p class="vbp-price">{number(price.get(prefix + "_odds"), 2)}</p>'
                    f'<p class="vbp-small">เส้น {number(price.get(prefix + "_point"), 2)}</p></div>')
    first, current = price.get("first_point"), price.get("current_point")
    if type(first) in (int, float) and type(current) in (int, float) and math.isfinite(first) and math.isfinite(current) and abs(first-current) > 1e-9:
        change = f'เส้นเปลี่ยน {number(first, 2)} → {number(current, 2)} · ไม่เทียบค่าน้ำตรง ๆ'
    else:
        change = f'เส้นเดิม · เปลี่ยนค่าน้ำ {number(price.get("odds_delta"), 3)}'
    return content + f'</div><p class="vbp-change">{change}</p><p class="vbp-small">ราคาแรกที่พบ ไม่ใช่ราคาเปิดจริง</p>'


def dashboard_html(row, recommendation):
    """Render immutable saved values. Recommendation is supplied by existing C."""
    a, b = object_json(row.get("analysis_a_json")), object_json(row.get("analysis_b_json"))
    selection = mapping(b.get("selection"))
    ev = number(selection.get("ev_pct"), 2, "%") if model_ready(b) else "ยังประเมินไม่ได้"
    score = number(a.get("score"))
    return (f'<article class="vbp-dashboard" aria-label="ผลวิเคราะห์ {text(row.get("match"))}">'
            f'<header class="vbp-header"><p class="vbp-meta">{text(row.get("league"))} · {text(row.get("kickoff_th"))} (ไทย)</p>'
            f'<h3 class="vbp-title">{text(row.get("match"))}</h3>'
            f'<div class="vbp-badges"><span class="vbp-badge">{text(row.get("market"))}</span><span class="vbp-badge">{text(row.get("bookmaker"))}</span></div>'
            f'<div class="vbp-quote"><span class="vbp-selection">{text(row.get("selection"))}</span><span class="vbp-odds">{number(row.get("odds"), 2)}</span></div></header>'
            f'<section class="vbp-section"><p class="vbp-label">ระบบ A: หลักฐานฟอร์ม</p><div class="vbp-score-row">'
            f'<div class="vbp-score">{score}<small> /100</small></div><div><p class="vbp-result">{text(a.get("result"))}</p>'
            f'<p class="vbp-small">คะแนนหลักฐาน ไม่ใช่โอกาสชนะ</p></div></div><div class="vbp-grid2">'
            + form_tile(row, a, "home") + form_tile(row, a, "away") + '</div></section>'
            + '<section class="vbp-section"><p class="vbp-label">ระบบ B: แบบจำลอง</p>' + model_html(row, b) + '</section>'
            + '<section class="vbp-section"><p class="vbp-label">ราคาไหล</p>' + price_html(a) + '</section>'
            + f'<section class="vbp-section"><p class="vbp-label">ระบบ C: สรุป A/B</p><div class="vbp-conclusion">'
            f'<p class="vbp-result">{text(recommendation)}</p><p class="vbp-small">A: {text(a.get("result"))} · B: EV {ev}</p>'
            '<p class="vbp-small">คะแนน A ไม่ใช่ความน่าจะเป็น · B ยังไม่ผ่าน Calibration</p></div></section></article>')


def details_html(row):
    a, b = object_json(row.get("analysis_a_json")), object_json(row.get("analysis_b_json"))
    content = '<div class="vbp-details"><h4>ฟอร์ม 10 นัดและสถิติทั้งหมด</h4><div class="vbp-grid2">'
    for side, label in [("home", "เจ้าบ้าน"), ("away", "ทีมเยือน")]:
        profile = mapping(a.get(side + "_form"))
        opponent = mapping(mapping(a.get("opponents")).get(side))
        xg = mapping(mapping(a.get("xg")).get(side))
        content += (f'<div class="vbp-tile"><b>{label}</b><p>10 นัด: ยิง {number(profile.get("last10_gf"), 2)} / เสีย {number(profile.get("last10_ga"), 2)}</p>'
                    f'<p>ตัวอย่าง {text(profile.get("sample10", 0))} นัด</p><p>{text(profile.get("summary"))}</p>'
                    f'<p>ทั้งหมด: ยิง {number(profile.get("season_gf"), 2)} / เสีย {number(profile.get("season_ga"), 2)} · {text(profile.get("season_n", 0))} นัด</p>'
                    f'<p>คุณภาพคู่แข่ง PPG {number(opponent.get("mean_ppg"), 2)} · {text(opponent.get("sample", 0))} นัด</p><p>{text(opponent.get("status"))}</p>'
                    f'<p>xG/xGA จริง {number(xg.get("xg"), 2)} / {number(xg.get("xga"), 2)} · {text(xg.get("sample", 0))} นัด</p></div>')
        diagnostic = mapping(profile.get('diagnostics'))
        if diagnostic:
            content += (f'<div class="vbp-tile"><b>วินิจฉัยฟอร์ม {label}</b><p>{text(diagnostic.get("message"))}</p>'
                        f'<p>ชื่อ API/แถว: {text(diagnostic.get("input_name"))} → CSV: {text(diagnostic.get("matched_name"))} · วิธี {text(diagnostic.get("mapping"))}</p>'
                        f'<p>ตัดก่อนวันที่ {text(diagnostic.get("cutoff_date"))} · รวมก่อนแข่ง {text(diagnostic.get("all_before_count"))} นัด · ตามสนาม {text(diagnostic.get("venue_before_count"))} นัด</p>'
                        f'<p>ฤดูกาล/จำนวนตามสนาม: {text(diagnostic.get("season_counts"))}</p><p>วันที่ในตัวอย่าง 5 นัด: {text(diagnostic.get("sample5_dates"))}</p>'
                        f'<p>แหล่ง alias: {text(diagnostic.get("alias_source"))}</p></div>')
            for source in mapping(diagnostic.get('source')).get('sources', []):
                source = mapping(source)
                content += (f'<div class="vbp-tile"><p>CSV {text(source.get("csv_code"))} / {text(source.get("season"))} · HTTP {text(source.get("http_status"))} · {text(source.get("status"))}</p>'
                            f'<p>{text(source.get("url"))}</p><p>ตรวจเมื่อ {text(source.get("checked_at"))} · ใช้ได้ {text(source.get("valid_rows", 0))} แถว · วันที่ผิด {text(source.get("invalid_dates", 0))} · สกอร์ผิด/ขาด {text(source.get("invalid_scores", 0))}</p></div>')
    content += '</div><h4>ฟอร์มเทียบเส้นในอดีต (ไม่ใช่ความน่าจะเป็น)</h4>'
    for key, label in [("form5", "5 นัด"), ("form10", "10 นัด"), ("form_all", "ทั้งหมด")]:
        form = mapping(a.get(key))
        content += f'<p>{label}: {number(form.get("form_rate"), 1, "%")} · {text(form.get("form_sample", 0))} ตัวอย่าง</p><p>{text(form.get("form_summary"))}</p>'
    price, selection = mapping(a.get("price")), mapping(b.get("selection"))
    content += (f'<h4>ผลตัวเลือกและข้อมูลเชิงเทคนิค</h4><p>ชนะเต็ม {number(selection.get("p_win"), 2, "%")} · ผลครึ่ง/คืนทุน {number(selection.get("p_partial"), 2, "%")} · แพ้เต็ม {number(selection.get("p_loss"), 2, "%")}</p>'
                f'<p>EV {number(selection.get("ev_pct"), 2, "%")}</p><p>{text(a.get("method"))}</p><p>{text(a.get("status"))}</p>'
                f'<p>{text(mapping(a.get("xg")).get("status"))}</p><p>{text(b.get("status"))}</p><p>{text(row.get("model_status"))}</p>'
                f'<h4>เวลาที่บันทึก</h4><p>First Observed Price: {text(price.get("first_at"))}</p><p>ราคาปัจจุบัน: {text(price.get("current_at"))}</p>'
                f'<p>สแกน: {text(row.get("scanned_at"))}</p><p>Kickoff UTC: {text(row.get("kickoff_utc"))}</p>'
                f'<p>H2H: {text(row.get("h2h_summary"))}</p></div>')
    return content


def market_panel_html(entry, market):
    label = 'คำแนะนำแฮนดิแคป (AH)' if market == 'Asian Handicap' else 'คำแนะนำสูง–ต่ำ (OU)'
    content = f'<section class="vbp-section" data-market="{text(market)}"><p class="vbp-label">{label}</p>'
    if entry is None:
        return content + '<div class="vbp-status"><p class="vbp-result">ไม่มีราคาตลาด</p><p class="vbp-small">ยังไม่แนะนำ — ไม่มีเส้นและราคาที่บันทึกไว้สำหรับตลาดนี้</p></div></section>'
    row, a, b = entry['row'], entry['a'], entry['b']
    if market == 'Asian Handicap':
        team = row.get('home_team') if row['side'] == 'home' else row.get('away_team')
        kind = 'ทีมต่อ' if row['point'] < 0 else 'ทีมรอง' if row['point'] > 0 else 'เสมอ (AH 0)'
        selection = f'{kind}: {team}'
    else:
        selection = 'สูง (Over)' if row['side'] == 'over' else 'ต่ำ (Under)'
    snapshot = entry.get('snapshot') or {}
    snapshot_note = (f'Snapshot #{text(snapshot.get("id"))} · {text(snapshot.get("captured_at"))}' if snapshot else
                     'ไม่พบ Snapshot ที่ยืนยันตรงแถวนี้ · เวลาบันทึกคำแนะนำ ' + text(row.get('scanned_at')))
    content += (f'<div class="vbp-quote"><span class="vbp-selection">{text(selection)} · เส้น {number(row["point"], 2)}</span>'
                f'<span class="vbp-odds">{number(row["odds"], 2)}</span></div><p class="vbp-small">เจ้ามือ {text(row.get("bookmaker"))} · แถว #{text(row.get("id"))}</p>'
                f'<p class="vbp-small">{snapshot_note}</p><div class="vbp-score-row" style="margin-top:14px">'
                f'<div class="vbp-score">{number(a.get("score"))}<small> /100</small></div><div><p class="vbp-result">ระบบ A: {text(a.get("result"))}</p>'
                '<p class="vbp-small">คะแนนหลักฐานของตัวเลือกนี้ ไม่ใช่โอกาสชนะ</p></div></div><p class="vbp-label">ระบบ B</p>')
    content += model_html(row, b if entry['model_valid'] else {}, include_ou=market == 'Over/Under')
    if entry['model_valid']:
        selection_b = mapping(b.get('selection'))
        content += (f'<p class="vbp-small">ตัวเลือกนี้: ชนะเต็ม {number(selection_b.get("p_win"), 2, "%")} · '
                    f'ผลครึ่ง/คืนทุน {number(selection_b.get("p_partial"), 2, "%")} · แพ้เต็ม {number(selection_b.get("p_loss"), 2, "%")}</p>')
    content += f'<p class="vbp-result">EV {number(entry.get("ev"), 2, "%")}</p><p class="vbp-small">สถานะข้อมูล: {text(row.get("model_status"))}</p>'
    content += '<p class="vbp-label" style="margin-top:14px">ราคาไหลของตัวเลือกนี้</p>'
    price = mapping(a.get('price'))
    # Never display first/current prices taken from a different saved quote.
    price_matches = (isinstance(price.get('current_point'), (int, float)) and isinstance(price.get('current_odds'), (int, float)) and
                     price['current_point'] == row['point'] and price['current_odds'] == row['odds'])
    content += price_html(a if price_matches else {})
    content += '<p class="vbp-small">First Observed Price = ราคาแรกที่พบ ไม่ใช่ราคาเปิดจริง</p>'
    content += (f'<div class="vbp-conclusion" style="margin-top:14px"><p class="vbp-result">ระบบ C: {text(entry["conclusion"])}</p>'
                f'<p class="vbp-small">{text(entry["reason"])}</p><p class="vbp-small">A ไม่ใช่ความน่าจะเป็น · B ยังไม่ผ่าน Calibration</p></div></section>')
    return content


def fixture_dashboard_html(fixture):
    row, markets = fixture['row'], fixture['markets']
    content = (f'<article class="vbp-dashboard" data-event="{text(fixture.get("event_id"))}" aria-label="ผลวิเคราะห์ {text(row.get("match"))}">'
               f'<header class="vbp-header"><p class="vbp-meta">{text(row.get("league"))} · {text(row.get("kickoff_th"))} (ไทย)</p>'
               f'<h3 class="vbp-title">{text(row.get("match"))}</h3><div class="vbp-badges"><span class="vbp-badge">AH + OU · คู่เดียวกัน</span></div></header>')
    anchor = markets.get('Asian Handicap') or markets.get('Over/Under')
    if anchor:
        source = anchor['row']
        content += ('<section class="vbp-section"><p class="vbp-label">ฟอร์มตามสนาม</p>'
                    f'<p class="vbp-small">อ้างอิงแถว #{text(source.get("id"))} · {text(source.get("bookmaker"))} · {text(source.get("scanned_at"))}</p>'
                    '<div class="vbp-grid2">' + form_tile(source, anchor['a'], 'home') + form_tile(source, anchor['a'], 'away') + '</div></section>')
    for market in ('Asian Handicap', 'Over/Under'):
        content += (ou_panel_html(fixture) if market=='Over/Under' else market_panel_html(markets.get(market), market))
    if fixture.get('identity_conflict'):
        content += '<section class="vbp-section"><p class="vbp-small">พบชื่อทีม/ลีกขัดแย้งใน event_id เดียวกัน จึงไม่รวมแถวที่ขัดแย้งกับข้อมูลล่าสุด</p></section>'
    return content + '</article>'


def fixture_details_html(fixture, ranking):
    content = f'<div class="vbp-details"><h4>หลักเกณฑ์จัดอันดับ</h4><p>{text(ranking)}</p>'
    if fixture.get('ou_main'):
        content += '<h4>Main OU Line และเส้นทางเลือก</h4><p>'+text(fixture['ou_main']['rule'])+'</p>'
        for pair in fixture['ou_main']['pairs']:
            content += (f'<div class="vbp-tile"><p>เส้น {number(pair["line"],2)} · {text(pair["bookmaker"])}</p>'
                        f'<p>Over {number(pair["over"]["row"]["odds"],2)} / Under {number(pair["under"]["row"]["odds"],2)} · {text(pair["scanned_at"])}</p>'
                        f'<p>ความห่างจากสมดุล {number(pair["balance"],3)} · ผลรวม implied {number(pair["margin"],3)} · ผ่านเกณฑ์ตลาด {text(pair["eligible"])}</p></div>')
    for market, alternatives in fixture['alternatives'].items():
        content += f'<h4>{text(market)} · ตัวเลือกจากข้อมูลที่บันทึกไว้</h4>'
        for index, entry in enumerate(alternatives, 1):
            row = entry['row']
            content += (f'<div class="vbp-tile"><p>อันดับ {index} · แถว #{text(row.get("id"))} · {text(row.get("selection"))}</p>'
                        f'<p>เส้น {number(row.get("point"), 2)} · ราคา {number(row.get("odds"), 2)} · {text(row.get("bookmaker"))}</p>'
                        f'<p>เวลาแถวคำแนะนำ {text(row.get("scanned_at"))} · A {number(entry["a"].get("score"))} · EV {number(entry.get("ev"), 2, "%")}</p>'
                        f'<p>{text(entry["conclusion"])} · {text(entry["reason"])}</p></div>')
        selected = fixture['markets'].get(market)
        if selected:
            content += details_html(selected['row'])
    return content + '</div>'


def ou_panel_html(fixture):
    main = fixture.get('ou_main',{})
    content = '<section class="vbp-section" data-market="Over/Under"><p class="vbp-label">คำแนะนำสูง–ต่ำ (OU) · Main Line</p>'
    if main.get('status')!='estimated':
        missing = not fixture['alternatives'].get('Over/Under')
        details='ข้อมูลโมเดลไม่เพียงพอ · ' if not any(e['model_valid'] for e in fixture['alternatives'].get('Over/Under',[])) else ''
        return content + '<div class="vbp-status"><p class="vbp-result">'+('ไม่มีราคาตลาด' if missing else 'ไม่สามารถยืนยันเส้นหลักได้')+'</p><p class="vbp-small">'+text(main.get('reason','ไม่มีราคาคู่ Over/Under ที่ครบ'))+'</p><p class="vbp-small">'+details+'ตัวเลือกตลาดที่กำลังประเมิน — ไม่ใช่คำแนะนำเดิมพัน · ยังไม่แนะนำ · เปิดดูแถวราคาที่มีจริงในรายละเอียด</p></div></section>'
    pair = main['pair']
    content += (f'<p class="vbp-result">เส้นหลักที่ระบบประมาณ {number(main["line"],2)}</p><p class="vbp-small">{text(main["reason"])}</p>'
                f'<p class="vbp-small">API · เจ้ามือ {text(pair["bookmaker"])} · รอบบันทึก {text(pair["scanned_at"])}</p><div class="vbp-grid2">')
    for side,label in [('over','สูง (Over)'),('under','ต่ำ (Under)')]:
        e=pair[side]
        content += (f'<div class="vbp-tile vbp-market-price"><p class="vbp-small">{label} {number(main["line"],2)}</p><p class="vbp-price">{number(e["row"]["odds"],2)}</p>'
                    f'<p class="vbp-small">แถว #{text(e["row"].get("id"))} · A {number(e["a"].get("score"))}/100</p>'
                    f'<p class="vbp-small">B: EV {number(e.get("ev"),2,"%")}</p><p class="vbp-small">{text(e["reason"])}</p></div>')
    content += '</div><div class="vbp-conclusion" style="margin-top:12px"><p class="vbp-result">ระบบ C: '+text(fixture.get('ou_reason'))+'</p></div>'
    direction=fixture.get('ou_direction')
    anchor=pair[direction] if direction in ('over','under') else pair['over']
    content += '<p class="vbp-label" style="margin-top:14px">ระบบ B · เส้นตลาดจริง</p>'
    content += model_html(anchor['row'],anchor['b'] if anchor['model_valid'] else {})
    content += '<p class="vbp-label">First Observed Price · แยกตามฝั่ง</p>'
    for side,label in [('over','Over'),('under','Under')]:
        entry=pair[side]
        snap=entry.get('snapshot') or {}
        content += f'<p class="vbp-small">{label} · เจ้ามือ {text(pair["bookmaker"])} · Snapshot #{text(snap.get("id"))} เวลา {text(snap.get("captured_at"))}</p>'
        content += price_html(entry['a'])
    return content+'</section>'


def manual_quotes_html(quotes):
    content='<div class="vbp-details"><h4>Manual OU Quotes · ข้อมูลที่กรอกเอง</h4>'
    for quote in quotes:
        result=object_json(quote.get('model_result_json'))
        content+=(f'<div class="vbp-tile"><p>MANUAL #{text(quote.get("id"))} · {text(quote.get("bookmaker"))} · {text(quote.get("created_at"))}</p>'
                  f'<p>เส้น {number(quote.get("point"),2)} · สูง {number(quote.get("odds_over"),2)} / ต่ำ {number(quote.get("odds_under"),2)}</p>'
                  f'<p>{text(result.get("reason"))}</p>')
        if result.get('available'):
            for side in ('over','under'):
                stats=mapping(mapping(result.get('results')).get(side))
                content+=f'<p>{side} · EV {number(stats.get("ev_pct"),2,"%")}, ชนะเต็ม {number(stats.get("p_win"),2,"%")}, ผลครึ่ง/คืนทุน {number(stats.get("p_partial"),2,"%")}</p>'
            content+=f'<p>โมเดลจาก pick #{text(result.get("model_pick_id"))} · {text(result.get("model_scanned_at"))} · ไม่ใช่คำแนะนำ C และไม่ใช่บิลเดิมพัน</p>'
        content+='</div>'
    return content+'</div>'
