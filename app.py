import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (VIP BETTOR EDITION)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - VIP Bettor Edition",
    page_icon="⚽",
    layout="centered",
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ โพยเด็ดสเต็ป VIP (คืนนี้จัดเต็ม)</h3>",
    unsafe_allow_html=True,
)
st.caption("ระบบคัดสรรทีมฟอร์มดี เรตราคาพาเฮ พร้อมจัดสเต็ปเน้นๆ")

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & LEAGUES
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
# 3. PRAGMATIC PICK ENGINE (เน้นทีเด็ดน่าลุ้น ฟอร์มดี)
# ==============================================================================
def check_match_result_advanced(
    csv_df, home_mapped, away_mapped, pick, match_date=None
):
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
  score_str = f"({fthg}-{ftag})"
  return f"จบเกม {score_str}"


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
    tot_mkt = next(
        (
            k
            for bm in bm_list
            for k in bm.get("markets", [])
            if k["key"] == "totals"
        ),
        None,
    )

    standings = get_temporal_standings(csv_df, m_dt) if csv_df is not None else {}
    hrank, arank = standings.get(home), standings.get(away)
    rank_str = f" [อันดับ {hrank} vs {arank}]" if (hrank and arank) else ""

    # เลือกตัวทีเด็ดจากราคาต่อรองและค่าน้ำที่น่าสนใจ (เน้นฝั่งต่อความมั่นใจสูง หรือรองมีลุ้น)
    pick_label, odds_val = "รอเปิด", 1.90
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]
        # ถ้าเจ้าบ้านต่อราคาไม่แพง หรือราคาเสมอ เลือกทีมเจ้าบ้านเป็นหลักเพื่อความอุ่นใจ
        if line <= 0:
          pick_label = (
              f"ต่อ {home} ({line})"
              if line < 0
              else f"วาง {home} (เสมอ)"
          )
          odds_val = hp
        else:
          pick_label = f"รอง {home} (+{line})"
          odds_val = hp

    confidence = 78.5 if hrank and arank and hrank < arank else 72.0

    match_res = check_match_result_advanced(
        csv_df, home, away, pick_label, m_dt
    )

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        "pick": pick_label,
        "odds": odds_val,
        "confidence": confidence,
        "result": match_res,
    })
  return results


# ==============================================================================
# 4. STREAMLIT UI (READY TO BET SLIPS)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพยสเต็ป VIP")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 จัดโพยสเต็ปเน้นๆ (ลุ้นเฮคืนนี้)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 จัดโพยสเต็ปเน้นๆ (ลุ้นเฮคืนนี้)":
  st.sidebar.header("ตั้งค่าโพยวันนี้")
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

  scan_btn = st.sidebar.button("🚀 สร้างโพยสเต็ป VIP ทันที", type="primary")

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner("กำลังรวบรวมคู่เด็ดจัดทำโพยสเต็ป..."):
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
          df = (
              df.sort_values(by=["confidence"], ascending=False)
              .drop_duplicates(subset=["match"])
              .reset_index(drop=True)
          )
          st.session_state["saved_scan_df"] = df
          st.success(f"สร้างโพยสำเร็จ! รวบรวมมาให้ลุ้น {len(df)} คู่")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    budg = st.session_state.get("budget", 1000)

    st.subheader("🎯 โพยสเต็ปจัดเต็ม (พร้อมกาชุดใหญ่)")
    st.write(
        f"• **บิลหลัก VIP (สเต็ป 5):** {round(budg * 0.6)} บาท (จัดเต็ม 60%)"
    )
    st.write(f"• **บิลต่อยอด (สเต็ป 8):** {round(budg * 0.2)} บาท (ลุ้นยาว 20%)")
    st.write(
        f"• **บิลโบนัส (สเต็ป 13):** {round(budg * 0.1)} บาท (ล่ารางวัล 10%)"
    )
    st.markdown("---")

    def render_slip(title, data_sub, target_count):
      st.markdown(f"#### {title}")
      if len(data_sub) == 0:
        st.write("- ไม่มีคู่แข่งขัน -")
        return
      for _, row in data_sub.head(target_count).iterrows():
        st.markdown(
            f"✅ **{row['match']}**<br>👉 จัดไป: `{row['pick']}` | ค่าน้ำ:"
            f" `{row['odds']}` | สถานะ: **{row['result']}**",
            unsafe_allow_html=True,
        )
      st.markdown("---")

    render_slip("🔥 บิลหลัก VIP (สเต็ป 5 คู่ทองคำ)", df, 5)
    render_slip("📈 บิลต่อยอด (สเต็ป 8 คู่)", df, 8)
    render_slip("🌟 บิลโบนัส (สเต็ป 13 คู่)", df, 13)
  else:
    st.info("💡 กรุณากดปุ่ม '🚀 สร้างโพยสเต็ป VIP ทันที' ที่เมนูด้านข้าง")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)":
  st.subheader("🏆 ตรวจผลบิลอัตโนมัติ")
  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    for _, r in df.iterrows():
      st.write(
          f"⚽ **{r['match']}** ➔ ทีเด็ด: `{r['pick']}` | ผลสรุป: **{r['result']}**"
      )
  else:
    st.warning("⚠️ ยังไม่มีโพยในระบบ กรุณาสร้างโพยก่อน")
