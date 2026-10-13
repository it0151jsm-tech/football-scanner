import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (DEEP FORM DUAL-ENGINE)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - Deep Form VIP", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (Deep Form Dual-Engine)"
    "</h3>",
    unsafe_allow_html=True,
)
st.caption("ระบบวิเคราะห์ 2 สมอง: ผสานตารางคะแนน + ฟอร์ม 5 นัดหลัง + ฟอร์มเหย้า/เยือน")

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & ADVANCED STATS ENGINE
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


def get_team_recent_form(df, team, match_date, n=5):
  """คำนวณฟอร์ม 5 นัดหลังสุด (แต้ม, ได้, เสีย)"""
  if df is None or df.empty:
    return {"pts": 0, "gf": 0, "ga": 0, "form_str": "-"}
  past_df = df[
      (df["Date_dt"] < match_date)
      & ((df["HomeTeam"] == team) | (df["AwayTeam"] == team))
  ].sort_values("Date_dt", ascending=False)
  recent = past_df.head(n)
  if recent.empty:
    return {"pts": 0, "gf": 0, "ga": 0, "form_str": "-"}

  pts, gf, ga = 0, 0, 0
  form_chars = []
  for _, row in recent.iterrows():
    is_home = row["HomeTeam"] == team
    hg, ag = int(row["FTHG"]), int(row["FTAG"])
    t_g = hg if is_home else ag
    o_g = ag if is_home else hg
    gf += t_g
    ga += o_g
    if t_g > o_g:
      pts += 3
      form_chars.append("W")
    elif t_g == o_g:
      pts += 1
      form_chars.append("D")
    else:
      form_chars.append("L")
  return {
      "pts": pts,
      "gf": gf,
      "ga": ga,
      "form_str": "".join(form_chars),
  }


def get_team_home_away_form(df, team, is_home, match_date, n=3):
  """คำนวณฟอร์มเฉพาะในบ้านหรือนอกบ้าน 3 นัดหลังสุด"""
  if df is None or df.empty:
    return {"pts": 0, "gf": 0, "ga": 0}
  col = "HomeTeam" if is_home else "AwayTeam"
  past_df = df[(df["Date_dt"] < match_date) & (df[col] == team)].sort_values(
      "Date_dt", ascending=False
  )
  recent = past_df.head(n)
  if recent.empty:
    return {"pts": 0, "gf": 0, "ga": 0}
  pts, gf, ga = 0, 0, 0
  for _, row in recent.iterrows():
    hg, ag = int(row["FTHG"]), int(row["FTAG"])
    t_g = hg if is_home else ag
    o_g = ag if is_home else hg
    gf += t_g
    ga += o_g
    if t_g > o_g:
      pts += 3
    elif t_g == o_g:
      pts += 1
  return {"pts": pts, "gf": gf, "ga": ga}


# ==============================================================================
# 3. RESULT CHECKER ENGINE
# ==============================================================================
def check_match_result(csv_df, home_mapped, away_mapped, match_date=None):
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
# 4. DEEP FORM DUAL-ENGINE SCANNER
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

    # ดึงสถิติฟอร์ม 5 นัดหลัง และฟอร์มเหย้า/เยือน
    h_form = get_team_recent_form(csv_df, home, m_dt, 5)
    a_form = get_team_recent_form(csv_df, away, m_dt, 5)
    h_home_form = get_team_home_away_form(csv_df, home, True, m_dt, 3)
    a_away_form = get_team_home_away_form(csv_df, away, False, m_dt, 3)

    quant_pick, quant_odds, quant_conf = "รอเปิด", 1.90, 70.0
    trap_pick, trap_odds, trap_score, trap_note = (
        "รอเปิด",
        1.90,
        50.0,
        "วิเคราะห์ปกติ",
    )

    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]

        # --- 1. สมองที่ 1: Pure Quant (ผสานอันดับ + ฟอร์ม 5 นัด + ฟอร์มเหย้าเยือน) ---
        if hrank and arank:
          rank_diff = arank - hrank  # บวกแปลว่าเจ้าบ้านอันดับดีกว่า
          form_diff = (
              h_form["pts"] - a_form["pts"]
          )  # แต้มฟอร์ม 5 นัดหลังต่างกันเท่าไหร่
          quant_conf = min(
              max(70.0 + (rank_diff * 1.5) + (form_diff * 1.2), 55.0), 96.0
          )

          if hrank < arank and h_form["pts"] >= a_form["pts"]:
            quant_pick = (
                f"ต่อ {home} ({line})"
                if line < 0
                else f"วาง {home} (เสมอ)"
            )
            quant_odds = hp
          elif hrank > arank:
            quant_pick = f"รอง {home} (+{line})" if line > 0 else f"รอง {home} (0)"
            quant_odds = hp
          else:
            quant_pick = (
                f"ต่อ {home} ({line})"
                if line < 0
                else f"วาง {home} (เสมอ)"
            )
            quant_odds = hp
        else:
          quant_pick = f"ต่อ {home} ({line})" if line < 0 else f"วาง {home} (เสมอ)"
          quant_odds = hp
          quant_conf = 70.0

        # --- 2. สมองที่ 2: Trap Hunter (อ่านกับดักราคา + เช็กฟอร์มตบตา) ---
        if hrank and arank:
          r_diff = hrank - arank  # ถ้าบวกเยอะ เจ้าบ้านอันดับแย่กว่ามาก
          if r_diff >= 6 and line >= -0.5 and h_home_form["pts"] >= 5:
            # อันดับห่างแต่ต่อน้อย และฟอร์มในบ้านช่วงนี้เหนียวแน่น -> กับดักเจ้าบ้านมีของ
            trap_pick = f"วาง {home} (ต่อน้อยผิดปกติ แต่ฟอร์มในบ้านแกร่ง)"
            trap_odds = hp
            trap_score = 92.0
            trap_note = (
                "🔥 Trap Detected: อันดับแย่แต่ต่อน้อย + ฟอร์มในบ้านดี -> ลุยเจ้าบ้าน"
            )
          elif line <= -1.75 and a_away_form["pts"] >= 4:
            # ต่อแพงเวอร์ แต่ทีมเยือนฟอร์มนอกบ้านช่วงหลังเริ่มเหนียว
            trap_pick = f"รองทีมเยือน (สวนเรตต่อแพงเวอร์ > 1.75)"
            trap_odds = ap
            trap_score = 88.0
            trap_note = (
                "🔥 Trap Detected: ต่อแพงเกินไป + ทีมเยือนนอกบ้านสู้ได้ -> สวนรอง"
            )
          else:
            trap_pick = (
                f"ต่อ {home} ({line})" if line < 0 else f"รอง {home} (+{line})"
            )
            trap_odds = hp
            trap_score = 65.0
            trap_note = (
                f"🛡️ Standard Play (ฟอร์ม 5 นัด: {home}[{h_form['form_str']}] vs"
                f" {away}[{a_form['form_str']}])"
            )
        else:
          trap_pick = (
              f"ต่อ {home} ({line})" if line < 0 else f"วาง {home} (เสมอ)"
          )
          trap_odds = hp
          trap_score = 60.0
          trap_note = "🛡️ บอลเปิดเรตปกติ ไม่มีข้อมูลตารางเปรียบเทียบ"

    match_res = check_match_result(csv_df, home, away, m_dt)

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        "form_info": (
            f"ฟอร์ม 5 นัด: {home} [{h_form['form_str']} - {h_form['pts']}แต้ม]"
            f" vs {away} [{a_form['form_str']} - {a_form['pts']}แต้ม]"
        ),
        "quant_pick": quant_pick,
        "quant_odds": quant_odds,
        "quant_conf": round(quant_conf, 1),
        "trap_pick": trap_pick,
        "trap_odds": trap_odds,
        "trap_score": trap_score,
        "trap_note": trap_note,
        "result": match_res,
    })
  return results


# ==============================================================================
# 5. STREAMLIT UI (DEEP FORM GOLDEN SLIPS)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพยสเต็ป 2 สมอง")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 จัดบิล 5 คู่ทองคำ (เจาะลึกสถิติ)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 จัดบิล 5 คู่ทองคำ (เจาะลึกสถิติ)":
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

  scan_btn = st.sidebar.button(
      "🚀 สแกนและจัดบิล 5 คู่ทองคำ (วิเคราะห์ฟอร์มลึก)", type="primary"
  )

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner("กำลังประมวลผลสถิติ 5 นัดและเรตราคาตลาด..."):
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
          st.success("สแกนสำเร็จ! ดึงสถิติฟอร์มและจัดเรียงบิลเรียบร้อย")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    budg = st.session_state.get("budget", 1000)

    tab_quant, tab_trap = st.tabs(
        [
            "📊 บิลหลัก Quant (สเต็ป 5 คู่ทองคำ + ฟอร์ม)",
            "🧠 บิลหลัก Trap Hunter (สเต็ป 5 คู่ทองคำ + จับกับดัก)",
        ]
    )

    # --- TAB 1: Quant Golden Slip ---
    with tab_quant:
      st.subheader(
          "🔥 บิลหลัก VIP (สเต็ป 5 คู่ทองคำ - Pure Quant + Deep Form)"
      )
      st.write(
          f"• **งบลงทุนจัดบิล:** {budg} บาท (เรียงตามคะแนนความมั่นใจจากสถิติจริง)"
      )
      st.markdown("---")

      df_quant_sorted = df.sort_values(
          by="quant_conf", ascending=False
      ).reset_index(drop=True)

      for _, row in df_quant_sorted.head(5).iterrows():
        st.markdown(
            f"✅ **{row['match']}** (ความมั่นใจ:"
            f" `{row['quant_conf']}%`)<br><small"
            f" style='color:gray;'>📊 {row['form_info']}</small><br>👉 เลือก:"
            f" `{row['quant_pick']}` | ค่าน้ำ: `{row['quant_odds']}` | สถานะ:"
            f" **{row['result']}**",
            unsafe_allow_html=True,
        )
        st.markdown("---")

    # --- TAB 2: Trap Hunter Golden Slip ---
    with tab_trap:
      st.subheader(
          "🎯 บิลหลัก VIP (สเต็ป 5 คู่ทองคำ - Trap Hunter + Form Check)"
      )
      st.write(
          f"• **งบลงทุนจัดบิล:** {budg} บาท (เรียงตามความคมในการจับกับดักราคาและฟอร์ม)"
      )
      st.markdown("---")

      df_trap_sorted = df.sort_values(
          by="trap_score", ascending=False
      ).reset_index(drop=True)

      for _, row in df_trap_sorted.head(5).iterrows():
        st.markdown(
            f"🎯 **{row['match']}** (คะแนนจับกับดัก:"
            f" `{row['trap_score']}%`)<br><small"
            f" style='color:gray;'>📊 {row['form_info']}</small><br>👉 เลือก:"
            f" `{row['trap_pick']}` | ค่าน้ำ: `{row['trap_odds']}`<br><small"
            f" style='color:orange;'>{row['trap_note']}</small> | สถานะ:"
            f" **{row['result']}**",
            unsafe_allow_html=True,
        )
        st.markdown("---")
  else:
    st.info("💡 กรุณากดปุ่มสแกนที่เมนูด้านข้างเพื่อเริ่มต้นใช้งาน")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)":
  st.subheader("🏆 ตรวจผลบิลอัตโนมัติ")
  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    for _, r in df.iterrows():
      st.write(
          f"⚽ **{r['match']}** ➔ Quant: `{r['quant_pick']}` | Trap:"
          f" `{r['trap_pick']}` | ผลสรุป: **{r['result']}**"
      )
  else:
    st.warning("⚠️ ยังไม่มีโพยในระบบ กรุณาสร้างโพยก่อน")
