import os
import math
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="Super Quant VIP - Permanent Save", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Super Quant VIP (Permanent Save Engine)</h3>",
    unsafe_allow_html=True,
)
st.caption("ระบบวิเคราะห์อัจฉริยะ: บันทึกโพยลงไฟล์ถาวร ปิดแอปข้ามคืนข้อมูลไม่หาย")

API_KEY = "9b01dce091987a5fc57447a84e05badc"
SAVE_FILE = "saved_slips.csv"  # ไฟล์สำหรับเซฟโพยถาวร


# ==============================================================================
# 2. HELPER FUNCTIONS FOR PERMANENT STORAGE
# ==============================================================================
def save_scan_to_file(df):
  """บันทึกโพยลงไฟล์ CSV ถาวรบนเซิร์ฟเวอร์"""
  try:
    df.to_csv(SAVE_FILE, index=False, encoding="utf-8-sig")
  except Exception:
    pass


def load_scan_from_file():
  """โหลดโพยที่เคยเซฟไว้จากไฟล์ CSV"""
  if os.path.exists(SAVE_FILE):
    try:
      return pd.read_csv(SAVE_FILE, encoding="utf-8-sig")
    except Exception:
      return pd.DataFrame()
  return pd.DataFrame()


# ==============================================================================
# 3. EXPANDED LEAGUE DIVERSITY & STATS ENGINE
# ==============================================================================
@st.cache_data(ttl=1800)
def get_active_leagues():
  csv_map = {
      "soccer_epl": {"csv": "E0", "city": "London"},
      "soccer_spain_la_liga": {"csv": "SP1", "city": "Madrid"},
      "soccer_italy_serie_a": {"csv": "I1", "city": "Rome"},
      "soccer_germany_bundesliga": {"csv": "D1", "city": "Munich"},
      "soccer_france_league1": {"csv": "F1", "city": "Paris"},
      "soccer_netherlands_eredivisie": {"csv": "N1", "city": "Amsterdam"},
      "soccer_portugal_primeira_liga": {"csv": "P1", "city": "Lisbon"},
      "soccer_england_championship": {"csv": "E1", "city": "Birmingham"},
      "soccer_england_league_one": {"csv": "E2", "city": "Manchester"},
      "soccer_england_league_two": {"csv": "E3", "city": "Liverpool"},
  }

  leagues_dict = {}
  try:
    res = requests.get(
        f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
    )
    if res.status_code == 200:
      for item in res.json():
        if item.get("group") == "Soccer" and item.get("active"):
          k = item["key"]
          meta = csv_map.get(k, {"csv": None, "city": "London"})
          leagues_dict[k] = {
              "name": item["title"],
              "key": k,
              "csv": meta["csv"],
              "city": meta["city"],
          }
  except Exception:
    pass

  if not leagues_dict:
    leagues_dict = {
        "soccer_epl": {
            "name": "Premier League (พรีเมียร์ลีก อังกฤษ)",
            "key": "soccer_epl",
            "csv": "E0",
            "city": "London",
        },
        "soccer_spain_la_liga": {
            "name": "La Liga (ลาลีกา สเปน)",
            "key": "soccer_spain_la_liga",
            "csv": "SP1",
            "city": "Madrid",
        },
        "soccer_italy_serie_a": {
            "name": "Serie A (กัลโช่ เซเรีย อา อิตาลี)",
            "key": "soccer_italy_serie_a",
            "csv": "I1",
            "city": "Rome",
        },
        "soccer_germany_bundesliga": {
            "name": "Bundesliga (บุนเดสลีกา เยอรมนี)",
            "key": "soccer_germany_bundesliga",
            "csv": "D1",
            "city": "Munich",
        },
        "soccer_uefa_champs_league": {
            "name": "UEFA Champions League (ยูฟ่า แชมเปียนส์ลีก)",
            "key": "soccer_uefa_champs_league",
            "csv": None,
            "city": "London",
        },
    }

  return leagues_dict


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
        dfs.append(df)
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
    hg, ag = int(row.get("FTHG", 0)), int(row.get("FTAG", 0))
    if h not in table:
      table[h] = {"pts": 0, "gd": 0, "gf": 0, "ga": 0, "mp": 0}
    if a not in table:
      table[a] = {"pts": 0, "gd": 0, "gf": 0, "ga": 0, "mp": 0}
    table[h]["gd"] += hg - ag
    table[a]["gd"] += ag - hg
    table[h]["gf"] += hg
    table[h]["ga"] += ag
    table[a]["gf"] += ag
    table[a]["ga"] += hg
    table[h]["mp"] += 1
    table[a]["mp"] += 1
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
  res = {}
  for rank, (team, stats) in enumerate(sorted_teams):
    res[team] = {
        "rank": rank + 1,
        "avg_gf": stats["gf"] / max(stats["mp"], 1),
        "avg_ga": stats["ga"] / max(stats["mp"], 1),
    }
  return res


def get_advanced_team_metrics(csv_df, home_team, away_team, match_date):
  default_res = {
      "home_xg": 1.3,
      "away_xg": 1.2,
      "home_rest": 7,
      "away_rest": 7,
      "sot_str": "",
  }
  if csv_df is None or csv_df.empty:
    return default_res

  past_df = csv_df[csv_df["Date_dt"] < match_date]
  if past_df.empty:
    return default_res

  h_matches = past_df[
      (past_df["HomeTeam"] == home_team) | (past_df["AwayTeam"] == home_team)
  ].tail(5)
  a_matches = past_df[
      (past_df["HomeTeam"] == away_team) | (past_df["AwayTeam"] == away_team)
  ].tail(5)

  h_sot, a_sot = 0, 0
  if "HST" in past_df.columns:
    for _, r in h_matches.iterrows():
      h_sot += (
          int(r["HST"])
          if r["HomeTeam"] == home_team
          else int(r.get("AST", 0))
      )
    for _, r in a_matches.iterrows():
      a_sot += (
          int(r["HST"])
          if r["HomeTeam"] == away_team
          else int(r.get("AST", 0))
      )

  h_count = max(len(h_matches), 1)
  a_count = max(len(a_matches), 1)

  home_xg = (
      round((h_sot / h_count) * 0.26, 2)
      if h_sot > 0
      else round(h_count * 0.3, 2)
  )
  away_xg = (
      round((a_sot / a_count) * 0.26, 2)
      if a_sot > 0
      else round(a_count * 0.3, 2)
  )

  h_last = h_matches["Date_dt"].max() if not h_matches.empty else None
  a_last = a_matches["Date_dt"].max() if not a_matches.empty else None

  h_rest = (match_date - h_last).days if h_last is not None else 7
  a_rest = (match_date - a_last).days if a_last is not None else 7

  sot_info = (
      f" (ยิงเข้ากรอบเฉลี่ย {round(h_sot/h_count, 1)} vs"
      f" {round(a_sot/a_count, 1)} ครั้ง)"
      if h_sot > 0
      else ""
  )

  return {
      "home_xg": max(home_xg, 0.6),
      "away_xg": max(away_xg, 0.5),
      "home_rest": h_rest,
      "away_rest": a_rest,
      "sot_str": sot_info,
  }


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
# 4. SCANNER LOGIC
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

    sp_mkt, tot_mkt, h2h_mkt = None, None, None
    for bm in bm_list:
      for k in bm.get("markets", []):
        if k["key"] == "spreads" and not sp_mkt:
          sp_mkt = k
        if k["key"] == "totals" and not tot_mkt:
          tot_mkt = k
        if k["key"] == "h2h" and not h2h_mkt:
          h2h_mkt = k

    standings = get_temporal_standings(csv_df, m_dt) if csv_df is not None else {}
    h_data = standings.get(home, {})
    a_data = standings.get(away, {})
    hrank, arank = h_data.get("rank"), a_data.get("rank")
    rank_str = f" [อันดับ {hrank} vs {arank}]" if (hrank and arank) else ""

    metrics = get_advanced_team_metrics(csv_df, home, away, m_dt)
    candidates = []

    # --- 1. ตลาดแฮนดิแคป ---
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]

        rank_diff = (arank - hrank) if (hrank and arank) else (
            metrics["home_xg"] - metrics["away_xg"]
        ) * 2
        rest_bonus = (
            0.05
            if metrics["home_rest"] > metrics["away_rest"] + 2
            else (
                -0.05 if metrics["away_rest"] > metrics["home_rest"] + 2 else 0
            )
        )
        true_win_prob = min(
            max(0.50 + (rank_diff * 0.03) + rest_bonus, 0.20), 0.85
        )

        ev_h = (true_win_prob * hp) - 1.0
        ev_a = ((1.0 - true_win_prob) * ap) - 1.0

        if ev_h >= ev_a:
          candidates.append({
              "pick": format_handicap_pick(home, line),
              "odds": hp,
              "score": round(72.0 + (ev_h * 100), 1),
              "note": (
                  f"📈 EV+ (แฮนดิแคป): เรตราคา {line} | ค่าน้ำคุ้ม"
                  f" (+{round(ev_h*100, 1)}%)"
              ),
          })
        else:
          candidates.append({
              "pick": (
                  f"รอง {away} (+{-line})"
                  if line < 0
                  else format_handicap_pick(away, -line)
              ),
              "odds": ap,
              "score": round(72.0 + (ev_a * 100), 1),
              "note": (
                  f"📈 EV+ (แฮนดิแคป): เรตราคา {line} | ค่าน้ำคุ้มรอง"
                  f" (+{round(ev_a*100, 1)}%)"
              ),
          })

    # --- 2. ตลาดสูง/ต่ำ ---
    if tot_mkt:
      t_outcomes = tot_mkt.get("outcomes", [])
      over_obj = next(
          (o for o in t_outcomes if o["name"].lower() == "over"), None
      )
      under_obj = next(
          (o for o in t_outcomes if o["name"].lower() == "under"), None
      )

      if over_obj and under_obj:
        t_line = over_obj.get("point", 2.5)
        op, up = over_obj["price"], under_obj["price"]

        exp_goals = (metrics["home_xg"] + metrics["away_xg"]) / 2 * 2.1
        prob_over = min(
            max(0.45 + ((exp_goals - t_line) * 0.22), 0.15), 0.85
        )
        prob_under = 1.0 - prob_over

        ev_over = (prob_over * op) - 1.0
        ev_under = (prob_under * up) - 1.0

        if ev_over > ev_under and exp_goals > 2.6:
          candidates.append({
              "pick": f"🔥 สกอร์สูง (Over {t_line})",
              "odds": op,
              "score": round(74.0 + (ev_over * 100), 1),
              "note": (
                  f"⚽ EV+ (สูง/ต่ำ): xG คาดยิงรวม {round(exp_goals,1)} ลูก"
                  f"{metrics['sot_str']}"
              ),
          })
        elif ev_under > ev_over and exp_goals < 2.3:
          candidates.append({
              "pick": f"🛡️ สกอร์ต่ำ (Under {t_line})",
              "odds": up,
              "score": round(74.0 + (ev_under * 100), 1),
              "note": (
                  f"🛡️ EV+ (สูง/ต่ำ): เกมรับเหนียว คาดยิงรวม"
                  f" {round(exp_goals,1)} ลูก"
              ),
          })

    if candidates:
      best = max(candidates, key=lambda x: x["score"])
      ev_pick, ev_odds, ev_score, ev_note = (
          best["pick"],
          best["odds"],
          best["score"],
          best["note"],
      )
    else:
      ev_pick, ev_odds, ev_score, ev_note = (
          f"วาง {home} (เหย้า)",
          1.90,
          65.0,
          "ราคามาตรฐาน",
      )

    # --- 3. Smart Trap Hunter Filter ---
    trap_pick, trap_odds, trap_score, trap_note = (
        ev_pick,
        ev_odds,
        65.0,
        "ราคามาตรฐาน",
    )
    public_teams = [
        "Real Madrid",
        "Barcelona",
        "Manchester United",
        "Liverpool",
        "Arsenal",
        "Bayern Munich",
        "Paris Saint-Germain",
        "Manchester City",
    ]
    is_public = any(pt in home or pt in away for pt in public_teams)

    if sp_mkt and is_public:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]

        if (
            line <= -1.25
            and home in str(public_teams)
            and (metrics["home_rest"] < 4 or metrics["home_xg"] < 1.4)
        ):
          trap_pick = f"รอง {away} (+{-line})"
          trap_odds = ap
          trap_score = 92.0
          trap_note = (
              "🔥 Smart Trap: ทีมมหาชนต่อแพง + มีความล้า/ฟอร์มฝืด ->"
              " สวนรองได้เปรียบ"
          )
        elif (
            line >= 1.25
            and away in str(public_teams)
            and (metrics["away_rest"] < 4 or metrics["away_xg"] < 1.4)
        ):
          trap_pick = f"รอง {home} (+{line})"
          trap_odds = hp
          trap_score = 92.0
          trap_note = (
              "🔥 Smart Trap: ทีมเยือนมหาชนต่อแพง + มีความล้า ->"
              " สวนรองได้เปรียบ"
          )
        else:
          trap_pick = ev_pick
          trap_odds = ev_odds
          trap_score = 68.0
          trap_note = "🛡️ Standard Play: ทีมมหาชนเครื่องแรงตามหน้าเสื่อ (ไม่สวน)"

    match_res = check_match_result(csv_df, home, away, m_dt)

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        "csv_code": leg_info["csv"],
        "home_mapped": home,
        "away_mapped": away,
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
# 5. STREAMLIT UI DASHBOARD
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพย Smart 3-Engine")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 จัดบิลสเต็ป VIP (Smart 3-Engine)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 จัดบิลสเต็ป VIP (Smart 3-Engine)":
  st.sidebar.header("ตั้งค่าการสแกนลีก")
  if "quota_remaining" in st.session_state:
    st.sidebar.metric(
        label="โควตา API คงเหลือ",
        value=f"{st.session_state['quota_remaining']} ครั้ง",
    )

  options = {k: v["name"] for k, v in active_leagues.items()}
  selected = st.sidebar.multiselect(
      "เลือกรายการลีก (หลากหลายทั่วยุโรป)",
      options=list(options.keys()),
      default=list(options.keys())[:4],
      format_func=lambda x: options[x],
  )

  hours_limit = st.sidebar.selectbox("กรอบเวลาการแข่งขัน", options=[24, 48], index=0)
  scan_btn = st.sidebar.button(
      "🚀 สแกนและจัดบิล (Auto-Save ถาวร)", type="primary"
  )

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner("กำลังดึงข้อมูลลีกที่เลือก และประมวลผล xG + Smart Trap..."):
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

          # บันทึกลง Session และเซฟลงไฟล์ถาวร
          st.session_state["saved_scan_df"] = df
          save_scan_to_file(df)

          st.success(
              f"สแกนสำเร็จ! พบ {len(df)} คู่ (ระบบเซฟโพยไว้ให้ถาวรแล้ว)"
          )

  # โหลดข้อมูลจากไฟล์อัตโนมัติหากไม่มีใน Session
  saved_df = st.session_state.get("saved_scan_df", load_scan_from_file())

  if saved_df is not None and not saved_df.empty:
    df = saved_df

    tab_ev, tab_trap = st.tabs([
        "📈 สมองที่ 1 & 2: Best Value xG Signal (ทุกตลาด)",
        "🎯 สมองที่ 3: Smart Trap Hunter (กรองเฉพาะกับดักแท้)",
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
                f"**{idx+1}. [{row['league']}] {row['match']}**"
                f" (คะแนนความคุ้มค่า: `{row['ev_score']}%`)<br>👉 แนะนำ:"
                f" **`{row['ev_pick']}`** | ค่าน้ำ: `{row['ev_odds']}`<br><small"
                f" style='color:lightgreen;'>{row['ev_note']}</small> |"
                f" สถานะ: **{row['result']}**",
                unsafe_allow_html=True,
            )
          else:
            st.markdown(
                f"**{idx+1}. [{row['league']}] {row['match']}**"
                f" (คะแนนจับกับดัก: `{row['trap_score']}%`)<br>👉 แนะนำ:"
                f" **`{row['trap_pick']}`** | ค่าน้ำ: `{row['trap_odds']}`<br><small"
                f" style='color:orange;'>{row['trap_note']}</small> | สถานะ:"
                f" **{row['result']}**",
                unsafe_allow_html=True,
            )
        st.markdown("---")

    with tab_ev:
      st.subheader("📈 โพยสเต็ปสาย Best Value Signal (ผสมต่อ/รอง/สูง/ต่ำ)")
      df_ev_sorted = df.sort_values(by="ev_score", ascending=False).reset_index(
          drop=True
      )
      display_slips(df_ev_sorted, is_trap=False)

    with tab_trap:
      st.subheader("🎯 โพยสเต็ปสาย Smart Trap Hunter")
      df_trap_sorted = df.sort_values(
          by="trap_score", ascending=False
      ).reset_index(drop=True)
      display_slips(df_trap_sorted, is_trap=True)

  else:
    st.info("💡 กรุณาเลือกเลือกลีกที่เมนูด้านข้าง แล้วกดปุ่มสแกนเริ่มต้นสร้างโพย")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)":
  st.subheader("🏆 ตรวจผลบิลอัตโนมัติ (ดึงจากบันทึกถาวร)")

  # ดึงโพยล่าสุดจากไฟล์ถาวร
  df_saved = load_scan_from_file()

  if df_saved is not None and not df_saved.empty:
    with st.spinner("กำลังดึงข้อมูลสกอร์ล่าสุดมาอัปเดต..."):
      for idx, r in df_saved.iterrows():
        # ดึง CSV ล่าสุดมาเช็กผลจริงอีกรอบแบบเรียลไทม์
        csv_df = fetch_csv_stats(r.get("csv_code"))
        res_str = check_match_result(
            csv_df,
            r.get("home_mapped"),
            r.get("away_mapped"),
            r.get("match_date"),
        )
        st.write(
            f"⚽ **[{r['league']}] {r['match']}**<br>👉 แนะนำ:"
            f" `{r['ev_pick']}` | สรุปผล: **{res_str}**",
            unsafe_allow_html=True,
        )
        st.markdown("---")
  else:
    st.warning("⚠️ ยังไม่มีโพยในระบบที่บันทึกไว้ กรุณาสร้างโพยก่อน")
