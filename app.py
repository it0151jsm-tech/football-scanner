import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (DUAL-ENGINE PRO EDITION)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - Dual Engine VIP", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (Dual Engine VIP)</h3>",
    unsafe_allow_html=True,
)
st.caption(
    "ระบบวิเคราะห์คู่บอล 2 มิติ: สายคณิตศาสตร์จ๋า (Quant) VS สายอ่านกับดักราคาบ่อน"
    " (Trap Hunter)"
)

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & TEMPORAL STANDINGS
# ==============================================================================
@st.cache_data(ttl=1800)
def get_active_leagues():
  csv_map = {
      "soccer_epl": "E0",
      "soccer_england_championship": "E1",
      "soccer_england_league_one": "E2",
      "soccer_england_league_two": "E3",
      "soccer_spain_la_liga": "SP1",
      "soccer_italy_serie_a": "I1",
      "soccer_germany_bundesliga": "D1",
      "soccer_france_league1": "F1",
      "soccer_netherlands_eredivisie": "N1",
      "soccer_portugal_primeira_liga": "P1",
      "soccer_uefa_champs_league": None,
  }

  sorted_leagues = {
      "soccer_uefa_champs_league": {
          "name": "UEFA Champions League (ยูฟ่า แชมเปียนส์ลีก)",
          "key": "soccer_uefa_champs_league",
          "csv": None,
      },
      "soccer_epl": {
          "name": "Premier League (พรีเมียร์ลีก อังกฤษ)",
          "key": "soccer_epl",
          "csv": "E0",
      },
      "soccer_england_championship": {
          "name": "Championship (เดอะแชมเปียนชิพ อังกฤษ)",
          "key": "soccer_england_championship",
          "csv": "E1",
      },
      "soccer_england_league_one": {
          "name": "League One (ลีกวัน อังกฤษ)",
          "key": "soccer_england_league_one",
          "csv": "E2",
      },
      "soccer_england_league_two": {
          "name": "League Two (ลีกทู อังกฤษ)",
          "key": "soccer_england_league_two",
          "csv": "E3",
      },
  }

  other_leagues = {}
  try:
    res = requests.get(
        f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
    )
    if res.status_code == 200:
      for item in res.json():
        if item.get("group") == "Soccer" and item.get("active"):
          k = item["key"]
          if k not in sorted_leagues:
            other_leagues[k] = {
                "name": item["title"],
                "key": k,
                "csv": csv_map.get(k),
            }
  except Exception:
    pass

  return {**sorted_leagues, **other_leagues}


@st.cache_data(ttl=1800)
def fetch_csv_stats(csv_code):
  if not csv_code:
    return None
  dfs = []
  for season in ["2627", "2526"]:
    try:
      df = pd.read_csv(
          f"https://www.football-data.co.uk/mmz4281/{season}/{csv_code}.csv"
      )
      if "HomeTeam" in df.columns:
        df["Date_dt"] = pd.to_datetime(
            df["Date"], format="%d/%m/%Y", errors="coerce"
        )
        dfs.append(
            df[["Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
        )
    except Exception:
      pass
  if dfs:
    res_df = pd.concat(dfs, ignore_index=True)
    return res_df.sort_values("Date_dt").reset_index(drop=True)
  return None


def get_temporal_standings(df, match_date):
  if df is None or df.empty:
    return {}
  past_df = df[df["Date_dt"] < match_date]
  if past_df.empty:
    return {}
  table = {}
  for _, row in past_df.iterrows():
    h, a = row["HomeTeam"], row["AwayTeam"]
    hg, ag = int(row["FTHG"]), int(row["FTAG"])
    if h not in table:
      table[h] = {"pts": 0, "gd": 0}
    if a not in table:
      table[a] = {"pts": 0, "gd": 0}
    table[h]["gd"] += hg - ag
    table[a]["gd"] += ag - hg
    if hg > ag:
      table[h]["pts"] += 3
    elif ag > hg:
      table[a]["pts"] += 3
    else:
      table[h]["pts"] += 1
      table[a]["pts"] += 1
  sorted_teams = sorted(
      table.items(), key=lambda x: (x[1]["pts"], x[1]["gd"]), reverse=True
  )
  return {team[0]: rank + 1 for rank, team in enumerate(sorted_teams)}


# ==============================================================================
# 3. RESULT CHECKER ENGINE
# ==============================================================================
def check_match_result(csv_df, home_mapped, away_mapped, pick, match_date=None):
  if csv_df is None or csv_df.empty:
    return "⏳ ยังไม่มีผลสกอร์"
  match_row = pd.DataFrame()
  if match_date is not None:
    try:
      match_dt_pd = pd.to_datetime(match_date)
      match_row = csv_df[
          (csv_df["HomeTeam"] == home_mapped)
          & (csv_df["AwayTeam"] == away_mapped)
          & (abs((csv_df["Date_dt"] - match_dt_pd).dt.days) <= 1)
      ]
    except Exception:
      pass
  if match_row.empty:
    return "⏳ รอผลการแข่ง / ยังไม่เตะ"
  latest = match_row.iloc[-1]
  fthg, ftag = int(latest["FTHG"]), int(latest["FTAG"])
  return f"จบเกม ({fthg}-{ftag})"


# ==============================================================================
# 4. DUAL ANALYSIS ENGINE (QUANT vs TRAP HUNTER)
# ==============================================================================
def scan_league(leg_info, hours_limit):
  csv_df = fetch_csv_stats(leg_info["csv"])
  url = f"https://api.the-odds-api.com/v4/sports/{leg_info['key']}/odds/"
  params = {
      "apiKey": API_KEY,
      "regions": "eu",
      "markets": "h2h,spreads,totals",
      "oddsFormat": "decimal",
  }
  try:
    res = requests.get(url, params=params)
    if "x-requests-remaining" in res.headers:
      st.session_state["quota_remaining"] = res.headers.get(
          "x-requests-remaining"
      )
    if res.status_code == 429:
      return "QUOTA_EXCEEDED"
    if res.status_code != 200:
      return []
    matches = res.json()
  except Exception:
    return []

  now = datetime.now(timezone.utc)
  target_time = now + timedelta(hours=hours_limit)
  tz_th = timezone(timedelta(hours=7))
  results = []

  for m in matches:
    commence = datetime.fromisoformat(
        m["commence_time"].replace("Z", "+00:00")
    )
    if not (now - timedelta(hours=3) <= commence <= target_time):
      continue
    m_time = commence.astimezone(tz_th).strftime("%d/%m %H:%M น.")
    m_dt = commence.astimezone(tz_th).replace(tzinfo=None)
    home, away = m["home_team"], m["away_team"]

    bm_list = m.get("bookmakers", [])
    if not bm_list:
      continue
    sp_mkt = next(
        (
            k
            for bm in bm_list
            for k in bm.get("markets", [])
            if k["key"] == "spreads"
        ),
        None,
    )

    standings = get_temporal_standings(csv_df, m_dt) if csv_df is not None else {}
    hrank, arank = standings.get(home), standings.get(away)
    rank_str = f" [อันดับ {hrank} vs {arank}]" if (hrank and arank) else ""

    # ค่าเริ่มต้นสำหรับสองบิล
    quant_pick, quant_odds, quant_conf = "รอเปิด", 1.90, 75.0
    trap_pick, trap_odds, trap_note = "รอเปิด", 1.90, "วิเคราะห์ปกติ"

    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]

        # --- 1. บิลสายคณิตศาสตร์ (Quant Engine: เน้นอันดับและสถิติพื้นฐาน) ---
        if hrank and arank:
          if hrank < arank:
            quant_pick = (
                f"ต่อ {home} ({line})"
                if line < 0
                else f"วาง {home} (เสมอ)"
            )
            quant_odds = hp
            quant_conf = 82.5
          else:
            quant_pick = f"รอง {home} (+{line})" if line > 0 else f"รอง {home} (0)"
            quant_odds = hp
            quant_conf = 78.0
        else:
          quant_pick = f"วาง {home} (เสมอ)" if line == 0 else f"ต่อ {home} ({line})"
          quant_odds = hp
          quant_conf = 72.0

        # --- 2. บิลสายอ่านกับดักราคา (Trap Hunter Engine: จับไต๋บ่อน) ---
        if hrank and arank:
          rank_diff = hrank - arank  # ถ้าบวกเยอะ แปลว่าเจ้าบ้านอันดับแย่กว่าทีมเยือนมาก
          if rank_diff >= 6 and line >= -0.5:
            # เข้าข่ายกับดัก: อันดับห่างกันเยอะ แต่บ่อนเปิดต่อน้อย หรือเปิดเสมอ/รอง
            trap_pick = f"วาง {home} (สเกาท์กับดัก: เปิดราคาข่ม/ต่อน้อยผิดปกติ)"
            trap_odds = hp
            trap_note = (
                "🎯 Trap Detected: อันดับห่างแต่ต่อน้อย -> มั่นใจเจ้าบ้านมีของ"
            )
          elif line <= -1.75:
            # เข้าข่ายกับดัก: ต่อแพงเวอร์ ทีมใหญ่มักยิงไม่ขาด
            trap_pick = (
                f"รองทีมเยือน (สเกาท์กับดัก: ต่อแพงเกิน 1.75 เสี่ยงกินเต็มยาก)"
            )
            trap_odds = ap
            trap_note = (
                "🎯 Trap Detected: ต่อแพงลิบลิ่ว -> สวนรองปลอดภัยกว่า"
            )
          else:
            trap_pick = (
                f"ต่อ {home} ({line})" if line < 0 else f"รอง {home} (+{line})"
            )
            trap_odds = hp
            trap_note = "🛡️ Standard Playตามเรตตลาดปกติ"
        else:
          trap_pick = (
              f"วาง {home} (เสมอ)" if line == 0 else f"ต่อ {home} ({line})"
          )
          trap_odds = hp
          trap_note = "🛡️ บอลไม่มีตารางคะแนนอ้างอิง เล่นตามทรงราคา"

    match_res = check_match_result(csv_df, home, away, quant_pick, m_dt)

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        # ข้อมูลบิลคณิตศาสตร์
        "quant_pick": quant_pick,
        "quant_odds": quant_odds,
        "quant_conf": quant_conf,
        # ข้อมูลบิลอ่านกับดักราคา
        "trap_pick": trap_pick,
        "trap_odds": trap_odds,
        "trap_note": trap_note,
        "result": match_res,
    })
  return results


# ==============================================================================
# 5. STREAMLIT UI (DUAL TABS DASHBOARD)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพยสเต็ป Dual-Engine")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 สแกนและจัดโพย (แยก 2 บิล)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 สแกนและจัดโพย (แยก 2 บิล)":
  st.sidebar.header("ตั้งค่าการสแกน")
  if "quota_remaining" in st.session_state:
    st.sidebar.metric(
        label="โควตา API คงเหลือ",
        value=f"{st.session_state['quota_remaining']} ครั้ง",
    )
  else:
    st.sidebar.info("โควตา API: กดสร้างโพยเพื่ออัปเดต")

  options = {k: v["name"] for k, v in active_leagues.items()}
  selected = st.sidebar.multiselect(
      "เลือกรายการลีกคืนนี้",
      options=list(options.keys()),
      default=["soccer_uefa_champs_league", "soccer_england_championship"],
      format_func=lambda x: options[x],
  )

  hours_limit = st.sidebar.selectbox("กรอบเวลาการแข่งขัน", options=[24, 48], index=0)
  budget = st.sidebar.number_input(
      "งบลงทุนรวมวันนี้ (บาท)", value=1000, step=100
  )
  st.session_state["budget"] = budget

  scan_btn = st.sidebar.button("🚀 สแกนหาคู่เด็ด (Dual-Engine)", type="primary")

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner("กำลังประมวลผลคู่แข่งขันแยกตามกลยุทธ์..."):
        res_all = []
        quota_err = False
        for l_key in selected:
          if l_key in active_leagues:
            r = scan_league(active_leagues[l_key], hours_limit)
            if r == "QUOTA_EXCEEDED":
              quota_err = True
              break
            elif isinstance(r, list):
              res_all.extend(r)

        if quota_err:
          st.error("⚠️ โควตา API หมดแล้ว")
        elif res_all:
          df = pd.DataFrame(res_all)
          df = df.drop_duplicates(subset=["match"]).reset_index(drop=True)
          st.session_state["saved_scan_df"] = df
          st.success(f"สแกนสำเร็จ! รวบรวมมาให้เลือก {len(df)} คู่")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    budg = st.session_state.get("budget", 1000)

    # แยกเมนู/แท็บแสดงผลออกเป็น 2 บิลชัดเจนตามที่พี่ต้องการ
    tab_quant, tab_trap = st.tabs(
        ["📊 บิลสายคณิตศาสตร์จ๋า (Pure Quant)", "🧠 บิลสายอ่านกับดักราคา (Trap Hunter)"]
    )

    # --- TAB 1: สายคณิตศาสตร์จ๋า ---
    with tab_quant:
      st.subheader("📊 โพยสายคณิตศาสตร์ & สถิติตารางคะแนน (Pure Quant)")
      st.caption("คัดเลือกโดยยึดอันดับตารางและสถิติความเป็นจริงเป็นหลัก")
      st.write(
          f"• **งบจัดบิลหลัก (สเต็ป 5):** {round(budg * 0.6)} บาท | **บิลต่อยอด"
          f" (สเต็ป 8):** {round(budg * 0.4)} บาท"
      )
      st.markdown("---")

      def render_quant_slip(title, data_sub, target_count):
        st.markdown(f"#### {title}")
        for _, row in data_sub.head(target_count).iterrows():
          st.markdown(
              f"✅ **{row['match']}**<br>👉 ทีเด็ด (Quant):"
              f" `{row['quant_pick']}` | ค่าน้ำ: `{row['quant_odds']}` | ความมั่นใจ:"
              f" `{row['quant_conf']}%`<br>สถานะ: **{row['result']}**",
              unsafe_allow_html=True,
          )
        st.markdown("---")

      render_quant_slip("🔥 บิลหลัก Quant (สเต็ป 5 คู่)", df, 5)
      render_quant_slip("📈 บิลต่อยอด Quant (สเต็ป 8 คู่)", df, 8)

    # --- TAB 2: สายอ่านกับดักราคา ---
    with tab_trap:
      st.subheader("🧠 โพยสายอ่านกับดักราคาบ่อน (Trap Hunter)")
      st.caption(
          "เจาะลึกเรตราคาผิดปกติ ต่อน้อยเกินไป หรือต่อแพงเวอร์เพื่อสวนทางกับดักบ่อน"
      )
      st.write(
          f"• **งบจัดบิลล่ากับดัก (สเต็ป 5):** {round(budg * 0.6)} บาท |"
          f" **บิลเสริม (สเต็ป 8):** {round(budg * 0.4)} บาท"
      )
      st.markdown("---")

      def render_trap_slip(title, data_sub, target_count):
        st.markdown(f"#### {title}")
        for _, row in data_sub.head(target_count).iterrows():
          st.markdown(
              f"🎯 **{row['match']}**<br>👉 ทีเด็ด (Trap):"
              f" `
