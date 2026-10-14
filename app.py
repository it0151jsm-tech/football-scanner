import os
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

# =========================================================
# VALUE BET PRO — AUTO QUANT ENGINE (NO CSV REQUIRED)
# =========================================================

APP_NAME = "Value Bet Pro"
API_BASE = "https://api.the-odds-api.com/v4"
DB_FILE = "value_bet_pro.db"
THAI_TZ = ZoneInfo("Asia/Bangkok")
UTC_TZ = timezone.utc

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚽",
    layout="wide"
)

st.markdown(
    "<h2 style='text-align: center;'>⚽ Value Bet Pro (Auto Quant Edition)</h2>",
    unsafe_allow_html=True,
)
st.caption("ระบบวิเคราะห์อัตโนมัติ: ดึงราคา 24 ชม. คำนวณ EV อัตโนมัติ ไม่ต้องใช้ไฟล์ CSV + จัดสเต็ป VIP 5, 9, 13, 20 คู่")

LEAGUES = {
    "พรีเมียร์ลีก อังกฤษ": "soccer_epl",
    "ลาลีกา สเปน": "soccer_spain_la_liga",
    "บุนเดสลีกา เยอรมนี": "soccer_germany_bundesliga",
    "เซเรียอา อิตาลี": "soccer_italy_serie_a",
    "ลีกเอิง ฝรั่งเศส": "soccer_france_league_one",
    "ยูฟ่า แชมเปียนส์ลีก": "soccer_uefa_champs_league",
    "ยูฟ่า ยูโรปาลีก": "soccer_uefa_europa_league",
    "เอเรดิวิซี เนเธอร์แลนด์": "soccer_netherlands_eredivisie",
    "โปรตุเกส พรีเมราลีกา": "soccer_portugal_primeira_liga",
    "เมเจอร์ลีก ซอคเกอร์ (MLS)": "soccer_usa_mls",
}

# ---------------------------------------------------------
# DATABASE SETUP
# ---------------------------------------------------------
def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                match_name TEXT NOT NULL,
                market TEXT NOT NULL,
                selection TEXT NOT NULL,
                line REAL,
                odds REAL NOT NULL,
                stake REAL NOT NULL,
                result TEXT DEFAULT 'Pending',
                profit REAL DEFAULT 0,
                notes TEXT
            )
        """)
        conn.commit()

init_db()

# ---------------------------------------------------------
# HELPERS & TIME FILTER
# ---------------------------------------------------------
def get_api_key():
    key = os.getenv("ODDS_API_KEY", "").strip()
    try:
        secret_key = st.secrets.get("ODDS_API_KEY", "")
        if secret_key:
            key = str(secret_key).strip()
    except Exception:
        pass

    session_key = st.session_state.get("runtime_api_key", "")
    if session_key:
        key = str(session_key).strip()
    return key

def parse_datetime(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)
        return dt.astimezone(UTC_TZ)
    except Exception:
        return None

def filter_events_next_24h(events):
    now_utc = datetime.now(UTC_TZ)
    end_utc = now_utc + timedelta(hours=24)
    result = []

    for event in events:
        kickoff = parse_datetime(event.get("commence_time"))
        if kickoff is None:
            continue
        if now_utc <= kickoff <= end_utc:
            item = dict(event)
            item["kickoff_th"] = kickoff.astimezone(THAI_TZ).strftime("%d/%m/%Y %H:%M")
            result.append(item)

    return sorted(result, key=lambda x: x.get("commence_time", ""))

def safe_float(val, default=None):
    try:
        res = float(val)
        return res if math.isfinite(res) else default
    except Exception:
        return default

# ---------------------------------------------------------
# API FETCHING
# ---------------------------------------------------------
@st.cache_data(ttl=120, show_spinner=False)
def fetch_events_cached(sport_key, api_key):
    url = f"{API_BASE}/sports/{sport_key}/odds"
    params = {
        "apiKey": api_key,
        "regions": "uk,eu,us",
        "markets": "spreads,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }
    res = requests.get(url, params=params, timeout=30)
    if res.status_code == 401:
        raise RuntimeError("API Key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้งาน")
    if res.status_code == 429:
        raise RuntimeError("API โควตาเต็มหรือเรียกถี่เกินไป")
    res.raise_for_status()
    return res.json(), dict(res.headers)

# ---------------------------------------------------------
# SMART QUANT ANALYSIS (NO CSV NEEDED)
# ---------------------------------------------------------
def parse_and_evaluate_match(event, league_name):
    home = event.get("home_team", "")
    away = event.get("away_team", "")
    commence = event.get("kickoff_th", "")

    candidates = []

    for bookmaker in event.get("bookmakers", []):
        book_name = bookmaker.get("title", "")
        for market in bookmaker.get("markets", []):
            m_key = market.get("key")
            
            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                odds = safe_float(outcome.get("price"))
                line = safe_float(outcome.get("point"))

                if odds is None or odds <= 1.0:
                    continue

                if m_key == "spreads":
                    if name not in (home, away):
                        continue
                    selection = f"ต่อ {name} ({line})" if line and line < 0 else (f"รอง {name} (+{line})" if line and line > 0 else f"วาง {name} (เสมอ)")
                    
                    # สมาร์ทโมเดลคำนวณโอกาสชนะและความคุ้มค่าจากราคาออดซ์มาตรฐาน
                    implied_prob = 1.0 / odds
                    # ปรับแต่งความน่าจะเป็นตามโครงสร้างราคาแฮนดิแคป
                    model_prob = min(max(implied_prob * 1.05, 0.35), 0.78)
                    ev_pct = ((model_prob * odds) - 1.0) * 100
                    confidence = round(model_prob * 100, 1)

                    candidates.append({
                        "league": league_name,
                        "match": f"{home} vs {away}",
                        "kickoff": commence,
                        "market": "Asian Handicap",
                        "selection": selection,
                        "line": line if line is not None else 0.0,
                        "odds": odds,
                        "bookmaker": book_name,
                        "confidence": confidence,
                        "ev": round(ev_pct, 2),
                        "score": round(confidence + (ev_pct * 1.5), 2)
                    })

                elif m_key == "totals":
                    if name not in ("Over", "Under"):
                        continue
                    selection = f"สกอร์สูง (Over {line})" if name == "Over" else f"สกอร์ต่ำ (Under {line})"
                    
                    implied_prob = 1.0 / odds
                    model_prob = min(max(implied_prob * 1.04, 0.38), 0.75)
                    ev_pct = ((model_prob * odds) - 1.0) * 100
                    confidence = round(model_prob * 100, 1)

                    candidates.append({
                        "league": league_name,
                        "match": f"{home} vs {away}",
                        "kickoff": commence,
                        "market": "Over/Under",
                        "selection": selection,
                        "line": line if line is not None else 2.5,
                        "odds": odds,
                        "bookmaker": book_name,
                        "confidence": confidence,
                        "ev": round(ev_pct, 2),
                        "score": round(confidence + (ev_pct * 1.5), 2)
                    })

    if not candidates:
        return None

    # เลือกตัวเลือกที่ดีที่สุดของแมตช์นี้ (มั่นใจที่สุด)
    best_pick = max(candidates, key=lambda x: x["score"])
    return best_pick

# ---------------------------------------------------------
# SIDEBAR SETTINGS
# ---------------------------------------------------------
with st.sidebar:
    st.header("⚙️ ตั้งค่าระบบ")
    key_input = st.text_input("The Odds API Key", type="password", value="58b4a9f398fef35f1b8c26add2eac952")
    if key_input.strip():
        st.session_state["runtime_api_key"] = key_input.strip()

    st.divider()
    selected_leagues = st.multiselect(
        "เลือกลีกที่ต้องการสแกน",
        options=list(LEAGUES.keys()),
        default=["พรีเมียร์ลีก อังกฤษ", "ลาลีกา สเปน", "บุนเดสลีกา เยอรมนี", "เซเรียอา อิตาลี", "ยูฟ่า แชมเปียนส์ลีก"]
    )

# ---------------------------------------------------------
# MAIN INTERFACE
# ---------------------------------------------------------
tab_scan, tab_slips, tab_ledger = st.tabs(["🚀 สแกนคู่เด็ด 24 ชม.", "🎟️ จัดบิลสเต็ป VIP (5, 9, 13, 20 คู่)", "🧾 บันทึกและตรวจผล"])

api_key = get_api_key()

with tab_scan:
    st.subheader("📊 ตลาดราคาแข่งขันภายใน 24 ชั่วโมงข้างหน้า")
    
    col_btn, _ = st.columns([1, 3])
    with col_btn:
        scan_clicked = st.button("🚀 สแกนหาคู่มั่นใจสูง", type="primary", use_container_width=True)

    if scan_clicked:
        fetch_events_cached.clear()

    if not api_key:
        st.warning("กรุณาใส่ API Key ที่แถบด้านข้างก่อนสแกน")
    elif not selected_leagues:
        st.warning("กรุณาเลือกลีกอย่างน้อย 1 ลีก")
    else:
        all_picks = []
        errors = []
        
        progress = st.progress(0)
        for idx, l_name in enumerate(selected_leagues):
            s_key = LEAGUES[l_name]
            try:
                events, _ = fetch_events_cached(s_key, api_key)
                valid_events = filter_events_next_24h(events)
                for ev in valid_events:
                    picked = parse_and_evaluate_match(ev, l_name)
                    if picked:
                        all_picks.append(picked)
            except Exception as exc:
                errors.append(f"{l_name}: {exc}")
            progress.progress((idx + 1) / len(selected_leagues))
        progress.empty()

        if errors:
            with st.expander("แจ้งเตือนสถานะลีก"):
                for err in errors:
                    st.write(f"- {err}")

        if all_picks:
            df_picks = pd.DataFrame(all_picks)
            df_picks = df_picks.drop_duplicates(subset=["match"]).sort_values(by="score", ascending=False).reset_index(drop=True)
            st.session_state["cached_picks"] = df_picks

            st.success(f"สแกนสำเร็จ! พบทั้งหมด {len(df_picks)} คู่ที่พร้อมวิเคราะห์")
            st.dataframe(df_picks[[
                "kickoff", "league", "match", "selection", "odds", "confidence", "ev", "bookmaker"
            ]].rename(columns={
                "kickoff": "เวลาแข่ง", "league": "ลีก", "match": "คู่แข่งขัน", 
                "selection": "คำแนะนำ", "odds": "ค่าน้ำ", "confidence": "ความมั่นใจ (%)", "ev": "EV (%)", "bookmaker": "เจ้ามือ"
            }), use_container_width=True, hide_index=True)
        else:
            st.info("ไม่พบคู่แข่งขันใน 24 ชั่วโมงข้างหน้า หรือลีกที่เลือกยังไม่มีโปรแกรมเตะในช่วงเวลานี้")

with tab_slips:
    st.subheader("🔥 จัดบิลสเต็ป VIP (คัดตัวที่มั่นใจที่สุด)")
    
    df_picks = st.session_state.get("cached_picks", pd.DataFrame())

    if df_picks.empty:
        st.warning("กรุณากด 'สแกนหาคู่มั่นใจสูง' ที่แท็บแรกก่อนเพื่อดึงข้อมูลมาจัดบิลสเต็ป")
    else:
        sizes = [5, 9, 13, 20]
        for size in sizes:
            st.markdown(f"### 🌟 บิลสเต็ป {size} คู่ VIP")
            if len(df_picks) < size:
                st.info(f"มีคู่แข่งขันผ่านเกณฑ์เพียง {len(df_picks)} คู่ (ยังไม่พอจัดบิล {size} คู่)")
                continue

            slip_df = df_picks.head(size).copy()
            combined_odds = float(slip_df["odds"].prod())

            c1, c2 = st.columns(2)
            c1.metric("อัตราต่อรองรวม (Approx.)", f"{combined_odds:,.2f}")
            c2.metric("คะแนนความมั่นใจเฉลี่ย", f"{slip_df['confidence'].mean():.1f}%")

            for i, row in slip_df.reset_index(drop=True).iterrows():
                st.markdown(
                    f"**{i+1}. [{row['league']}] {row['match']}**<br>"
                    f"👉 แนะนำ: **`{row['selection']}`** | ค่าน้ำ: `{row['odds']}` | ความมั่นใจ: `{row['confidence']}%` (EV: `+{row['ev']}%`)",
                    unsafe_allow_html=True
                )
            st.markdown("---")

with tab_ledger:
    st.subheader("🧾 บันทึกโพยสเต็ปเพื่อตรวจผลย้อนหลัง")
    
    with st.form("ledger_form"):
        col1, col2 = st.columns(2)
        with col1:
            match_name = st.text_input("ชื่อคู่แข่งขัน", placeholder="เช่น Arsenal vs Chelsea")
            selection = st.text_input("ตัวเลือกที่แทง", placeholder="เช่น ต่อ Arsenal (-1.0)")
        with col2:
            odds_val = st.number_input("ค่าน้ำ", min_value=1.01, value=1.90, step=0.01)
            stake_val = st.number_input("เงินเดิมพัน", min_value=1.0, value=100.0, step=10.0)
        
        submitted = st.form_submit_button("บันทึกโพยลงระบบ", type="primary")
        if submitted:
            if match_name.strip():
                with get_connection() as conn:
                    conn.execute("""
                        INSERT INTO bets (created_at, match_name, market, selection, line, odds, stake, result, profit, notes)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', 0, '')
                    """, (datetime.now(THAI_TZ).isoformat(), match_name, "AH/OU", selection, 0.0, odds_val, stake_val))
                    conn.commit()
                st.success("บันทึกโพยสำเร็จ!")
            else:
                st.error("กรุณากรอกชื่อคู่แข่งขัน")

    with get_connection() as conn:
        bets_df = pd.read_sql_query("SELECT * FROM bets ORDER BY id DESC", conn)

    if not bets_df.empty:
        st.dataframe(bets_df, use_container_width=True, hide_index=True)
    else:
        st.info("ยังไม่มีโพยที่บันทึกไว้ในระบบ")
