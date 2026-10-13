import math
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (BOOKMAKER EV+ & TRAP DUAL-ENGINE)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - Bookmaker VIP", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (Bookmaker EV+ & Trap"
    " Hunter)</h3>",
    unsafe_allow_html=True,
)
st.caption(
    "ระบบ 2 สมองระดับสูง: ผสมวิธีคิดแบบโต๊ะ (Expected Value) + อ่านราคาดักมหาชน"
)

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & STATS ENGINE
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


def format_handicap_pick(home, line):
  if line < 0:
    return f"ต่อ {home} ({line})"
  elif line > 0:
    return f"รอง {home} (+{line})"
  else:
    return f"วาง {home} (เสมอ)"


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
# 3. BOOKMAKER THINKING & TRAP SCANNER
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

    # สมอง 1: Bookmaker EV+
    ev_pick, ev_odds, ev_score, ev_note = (
        format_handicap_pick(home, 0),
        1.90,
        70.0,
        "ค่าน้ำมาตรฐาน",
    )
    # สมอง 2: Trap Hunter
    trap_pick, trap_odds, trap_score, trap_note = (
        format_handicap_pick(home, 0),
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

        # --- 1. สมองคิดแบบโต๊ะ (Bookmaker EV+) ---
        if hrank and arank:
          # คำนวณความน่าจะเป็นจริงจากอันดับตารางคะแนน (Win Probability)
          rank_diff = arank - hrank
          true_win_prob = min(max(0.50 + (rank_diff * 0.03), 0.25), 0.85)

          # คำนวณค่าคาดหวัง Expected Value (EV) = (Prob * Odds) - 1
          # ถ้าเจ้าบ้านเป็นต่อ (line < 0) คำนวณ EV ฝั่งเจ้าบ้าน
          ev_h = (true_win_prob * hp) - 1.0
          ev_a = ((1.0 - true_win_prob) * ap) - 1.0

          if ev_h > ev_a:
            ev_pick = format_handicap_pick(home, line)
            ev_odds = hp
            ev_score = round(70.0 + (ev_h * 100), 1)
            ev_note = f"📈 Bookie EV+: มีมูลค่ากำไรคาดหวัง (+{round(ev_h*100, 1)}%)"
          else:
            ev_pick = (
                f"รองทีมเยือน (+{-line})"
                if line < 0
                else format_handicap_pick(away, -line)
            )
            ev_odds = ap
            ev_score = round(70.0 + (ev_a * 100), 1)
            ev_note = f"📈 Bookie EV+: ฝั่งรองคุ้มค่าน้ำมากกว่า (+{round(ev_a*100, 1)}%)"
        else:
          ev_pick = format_handicap_pick(home, line)
          ev_odds = hp
          ev_score = 72.0
          ev_note = "📈 Bookie EV+: เรตค่าน้ำตลาดสมดุล"

        # --- 2. สมองจับกับดักดักมหาชน (Trap Hunter) ---
        public_teams = [
            "Real Madrid",
            "Barcelona",
            "Manchester United",
            "Liverpool",
            "Arsenal",
            "Bayern Munich",
            "Paris Saint-Germain",
        ]
        is_public = any(pt in home or pt in away for pt in public_teams)

        if is_public and line <= -1.25:
          # โต๊ะชอบเปิดทีมมหาชนต่อแพง -> สวนรอง
          trap_pick = (
              f"รอง {away} (+{-line})"
              if home in str(public_teams)
              else f"รอง {home} (+{line})"
          )
          trap_odds = ap if home in str(public_teams) else hp
          trap_score = 90.0
          trap_note = "🔥 Public Bias Trap: โต๊ะเปิดราคาบีบทีมมหาชน -> สวนรองได้เปรียบ"
        elif hrank and arank and (hrank - arank >= 5) and line >= -0.25:
          # อันดับห่างแต่ต่อน้อย
          trap_pick = format_handicap_pick(home, line)
          trap_odds = hp
          trap_score = 88.0
          trap_note = "🔥 Sharp Trap: อันดับต่างแต่ต่อน้อยผิดปกติ -> โต๊ะซ่อนของ"
        else:
          trap_pick = format_handicap_pick(home, line)
          trap_odds = hp
          trap_score = 65.0
          trap_note = "🛡️ Standard Play: ราคาเปิดตามกรอบมาตรฐาน"

    match_res = check_match_result(csv_df, home, away, m_dt)

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        "ev_pick": ev_pick,
        "ev_odds": ev_odds,
        "ev_score": min(max(ev_score, 60.0), 98.0),
        "ev_note": ev_note,
        "trap_pick": trap_pick,
        "trap_odds": trap_odds,
        "trap_score": trap_score,
        "trap_note": trap_note,
        "result": match_res,
    })
  return results


# ==============================================================================
# 4. STREAMLIT UI (BOOKMAKER VIP DASHBOARD)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพย 2 สมอง (Bookmaker)")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 จัดบิลสเต็ป VIP (5, 9, 13, 20 คู่)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 จัดบิลสเต็ป VIP (5, 9, 13, 20 คู่)":
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
      default=[
          "soccer_uefa_champs_league",
          "soccer_england_championship",
          "soccer_epl",
      ],
      format_func=lambda x: options[x],
  )

  hours_limit = st.sidebar.selectbox("กรอบเวลาการแข่งขัน", options=[24, 48], index=0)
  budget = st.sidebar.number_input(
      "งบลงทุนรวมวันนี้ (บาท)", value=1000, step=100
  )
  st.session_state["budget"] = budget

  scan_btn = st.sidebar.button(
      "🚀 สแกนและจัดบิลสเต็ป (คิดแบบโต๊ะ Bookmaker)", type="primary"
  )

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner("กำลังคำนวณมูลค่าความคุ้มค่า (EV+) และสแกนกับดัก..."):
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
          st.success(f"สแกนสำเร็จ! ดึงข้อมูลมาทั้งหมด {len(df)} คู่")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]

    tab_ev, tab_trap = st.tabs([
        "📈 สมองที่ 1: Bookmaker EV+ (เน้นความคุ้มค่าน้ำ)",
        "🎯 สมองที่ 2: Trap Hunter (เน้นอ่านกับดักมหาชน)",
    ])

    def display_slips(df_sorted, is_trap=False):
      total_matches = len(df_sorted)
      steps = [5, 9, 13, 20]

      for n in steps:
        if total_matches < n:
          st.warning(
              f"⚠️ มีคู่เตะเพียง {total_matches} คู่ (ไม่พอจัดสเต็ป {n} คู่)"
          )
          continue

        title_icon = (
            "🔥"
            if n == 5
            else ("📈" if n == 9 else ("🌟" if n == 13 else "👑"))
        )
        st.markdown(f"### {title_icon} บิลสเต็ป {n} คู่ VIP")

        for idx, row in df_sorted.head(n).iterrows():
          if not is_trap:
            st.markdown(
                f"**{idx+1}. {row['match']}** (คะแนน EV+:"
                f" `{row['ev_score']}%`)<br>👉 เลือก: `{row['ev_pick']}` |"
                f" ค่าน้ำ: `{row['ev_odds']}`<br><small"
                f" style='color:lightgreen;'>{row['ev_note']}</small> |"
                f" สถานะ: **{row['result']}**",
                unsafe_allow_html=True,
            )
          else:
            st.markdown(
                f"**{idx+1}. {row['match']}** (คะแนนจับกับดัก:"
                f" `{row['trap_score']}%`)<br>👉 เลือก: `{row['trap_pick']}` |"
                f" ค่าน้ำ: `{row['trap_odds']}`<br><small"
                f" style='color:orange;'>{row['trap_note']}</small> | สถานะ:"
                f" **{row['result']}**",
                unsafe_allow_html=True,
            )
        st.markdown("---")

    # --- TAB 1: EV+ Slips ---
    with tab_ev:
      st.subheader("📈 โพยสเต็ปสาย Bookmaker EV+ (คัดคู่ที่ได้เปรียบค่าน้ำสูง)")
      df_ev_sorted = df.sort_values(by="ev_score", ascending=False).reset_index(
          drop=True
      )
      display_slips(df_ev_sorted, is_trap=False)

    # --- TAB 2: Trap Hunter Slips ---
    with tab_trap:
      st.subheader("🎯 โพยสเต็ปสาย Trap Hunter (คัดคู่สวนกระแสมหาชน)")
      df_trap_sorted = df.sort_values(
          by="trap_score", ascending=False
      ).reset_index(drop=True)
      display_slips(df_trap_sorted, is_trap=True)

  else:
    st.info("💡 กรุณากดปุ่มสแกนที่เมนูด้านข้างเพื่อเริ่มต้นสร้างโพย")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)":
  st.subheader("🏆 ตรวจผลบิลอัตโนมัติ")
  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    for _, r in df.iterrows():
      st.write(
          f"⚽ **{r['match']}** ➔ EV+: `{r['ev_pick']}` | Trap:"
          f" `{r['trap_pick']}` | ผลสรุป: **{r['result']}**"
      )
  else:
    st.warning("⚠️ ยังไม่มีโพยในระบบ กรุณาสร้างโพยก่อน")
