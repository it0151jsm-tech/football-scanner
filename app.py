import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (QUANT PROFESSIONAL EDITION)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - Quant Pro Edition",
    page_icon="⚽",
    layout="centered",
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (Quant Pro Edition)</h3>",
    unsafe_allow_html=True,
)
st.caption(
    "โมเดลสถิติขั้นสูง: ป้องกัน Look-ahead Bias & รองรับเอเชียนแฮนดิแคปเต็มรูปแบบ"
)

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & TEMPORAL STANDINGS ENGINE (NO LOOK-AHEAD BIAS)
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


# คำนวณอันดับตามเวลาจริง (ตัดแมตช์ในอนาคตออกเพื่อป้องกัน Look-ahead bias)
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
# 3. QUANT MATH & QUARTER-LINE RESULT CHECKER ENGINE
# ==============================================================================
def devig_odds(o1, o2):
  i1, i2 = 1.0 / o1, 1.0 / o2
  tot = i1 + i2
  return i1 / tot, i2 / tot


def calculate_ev(prob, odds):
  if odds <= 1.0 or prob <= 0:
    return -100.0
  return round(((prob * odds) - 1.0) * 100, 2)


def poisson_pmf(lmbda, k):
  return (lmbda**k * math.exp(-lmbda)) / math.factorial(k)


def calculate_over_probability(lambda_h, lambda_a, line=2.5):
  prob_under = 0.0
  for h in range(7):
    for a in range(7):
      if (h + a) < line:
        prob_under += poisson_pmf(lambda_h, h) * poisson_pmf(lambda_a, a)
  return max(min(1.0 - prob_under, 0.95), 0.05)


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
  fthg = int(latest["FTHG"])
  ftag = int(latest["FTAG"])
  tot_goals = fthg + ftag
  score_str = f"({fthg}-{ftag})"

  # ประเมินผลสูง-ต่ำ (รองรับเส้นเอเชียน .25 / .75 / .5)
  if "สูงกว่า" in pick or "ต่ำกว่า" in pick:
    try:
      parts = pick.split()
      line = float(parts[1])
      is_over = "สูงกว่า" in pick

      if line % 1 == 0.5:  # เช่น 2.5, 3.5
        if is_over:
          return (
              f"✅ เข้าเป้า {score_str}"
              if tot_goals > line
              else f"❌ ตาย {score_str}"
          )
        else:
          return (
              f"✅ เข้าเป้า {score_str}"
              if tot_goals < line
              else f"❌ ตาย {score_str}"
          )
      elif line % 1 == 0.25:  # เช่น 2.25 (แบ่งครึ่ง 2.0 และ 2.5)
        lower_line = line - 0.25
        if is_over:
          if tot_goals > line:
            return f"✅ เข้าเป้าเต็ม {score_str}"
          elif tot_goals == lower_line:
            return f"🟡 ได้ครึ่ง / เสียครึ่ง {score_str}"
          else:
            return f"❌ ตาย {score_str}"
        else:
          if tot_goals < lower_line:
            return f"✅ เข้าเป้าเต็ม {score_str}"
          elif tot_goals == line:
            return f"🟡 ได้ครึ่ง / เสียครึ่ง {score_str}"
          else:
            return f"❌ ตาย {score_str}"
      elif line % 1 == 0.75:  # เช่น 2.75 (แบ่งครึ่ง 2.5 และ 3.0)
        upper_line = line + 0.25
        if is_over:
          if tot_goals >= upper_line:
            return f"✅ เข้าเป้าเต็ม {score_str}"
          elif tot_goals == line:
            return f"🟡 ได้ครึ่ง / เสียครึ่ง {score_str}"
          else:
            return f"❌ ตาย {score_str}"
        else:
          if tot_goals <= line:
            return f"✅ เข้าเป้าเต็ม {score_str}"
          elif tot_goals == upper_line:
            return f"🟡 ได้ครึ่ง / เสียครึ่ง {score_str}"
          else:
            return f"❌ ตาย {score_str}"
    except Exception:
      pass

  # ประเมินผลแฮนดิแคป
  if "ต่อ" in pick or "รอง" in pick or "เสมอ" in pick:
    try:
      # ดึงตัวเลขแต้มต่อจากข้อความ เช่น ต่อ ทีมA (-0.75)
      import re

      match_line = re.search(r"\(([+-]?\d+\.?\d*)\)", pick)
      handicap = float(match_line.group(1)) if match_line else 0.0

      is_home_pick = home_mapped in pick
      goal_diff = (fthg - ftag) if is_home_pick else (ftag - fthg)
      adjusted_diff = goal_diff + handicap

      if "ต่อ" in pick:
        if adjusted_diff > 0.25:
          return f"✅ เข้าเป้าเต็ม {score_str}"
        elif adjusted_diff == 0.25:
          return f"🟡 ได้ครึ่ง {score_str}"
        elif adjusted_diff == 0:
          return f"➖ เสมอคืนทุน {score_str}"
        elif adjusted_diff == -0.25:
          return f"🟠 เสียครึ่ง {score_str}"
        else:
          return f"❌ ตาย {score_str}"
      elif "รอง" in pick:
        if adjusted_diff > 0.25:
          return f"✅ เข้าเป้าเต็ม {score_str}"
        elif adjusted_diff == 0.25:
          return f"🟡 ได้ครึ่ง {score_str}"
        elif adjusted_diff == 0:
          return f"➖ เสมอคืนทุน {score_str}"
        elif adjusted_diff == -0.25:
          return f"🟠 เสียครึ่ง {score_str}"
        else:
          return f"❌ ตาย {score_str}"
    except Exception:
      pass

  return f"จบเกม {score_str}"


# ==============================================================================
# 4. MATCH ANALYSIS ENGINE (TEMPORAL FILTERED)
# ==============================================================================
def analyze_match_quant(df, h_api, a_api, m_date):
  res = {
      "lambda_h": None,
      "lambda_a": None,
      "hr": 7,
      "ar": 7,
      "h2h": "ไม่พบประวัติพบกันล่าสุด",
      "home_mapped": h_api,
      "away_mapped": a_api,
      "h_rank": None,
      "a_rank": None,
  }
  if df is None or df.empty:
    return res

  past_df = df[df["Date_dt"] < m_date]
  teams = list(set(past_df["HomeTeam"]).union(set(past_df["AwayTeam"])))
  h_t = get_close_matches(h_api, teams, n=1, cutoff=0.35)
  a_t = get_close_matches(a_api, teams, n=1, cutoff=0.35)
  home_t = h_t[0] if h_t else h_api
  away_t = a_t[0] if a_t else a_api

  res["home_mapped"] = home_t
  res["away_mapped"] = away_t

  standings = get_temporal_standings(df, m_date)
  res["h_rank"] = standings.get(home_t)
  res["a_rank"] = standings.get(away_t)

  # คำนวณวันพักจริงจากอดีตก่อนวันเตะ
  def rest_days(t):
    p = past_df[(past_df["HomeTeam"] == t) | (past_df["AwayTeam"] == t)]
    if p.empty:
      return 7
    d = (m_date - p.iloc[-1]["Date_dt"]).days
    return d if 1 <= d <= 14 else 7

  hr, ar = rest_days(home_t), rest_days(away_t)
  res["hr"], res["ar"] = hr, ar

  # H2H ย้อนหลังก่อนวันเตะ
  h2h_sub = past_df[
      ((past_df["HomeTeam"] == home_t) & (past_df["AwayTeam"] == away_t))
      | ((past_df["HomeTeam"] == away_t) & (past_df["AwayTeam"] == home_t))
  ].tail(5)
  if not h2h_sub.empty:
    res["h2h"] = (
        f"H2H ล่าสุด ({len(h2h_sub)} นัด): เจอกันก่อนหน้าเรียบร้อย"
    )

  home_home_df = past_df[past_df["HomeTeam"] == home_t].tail(5)
  away_away_df = past_df[past_df["AwayTeam"] == away_t].tail(5)

  if len(home_home_df) < 2 or len(away_away_df) < 2:
    return res

  h_scored = home_home_df["FTHG"].mean()
  a_scored = away_away_df["FTAG"].mean()

  fat_h = 0.9 if hr <= 3 else (1.04 if hr >= 6 else 1.0)
  fat_a = 0.9 if ar <= 3 else (1.04 if ar >= 6 else 1.0)

  res["lambda_h"] = max(round(h_scored * fat_h, 2), 0.2)
  res["lambda_a"] = max(round(a_scored * fat_a, 2), 0.2)
  return res


# ==============================================================================
# 5. SCANNER CORE
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
    tot_mkt = next(
        (
            k
            for bm in bm_list
            for k in bm.get("markets", [])
            if k["key"] == "totals"
        ),
        None,
    )

    m_data = analyze_match_quant(csv_df, home, away, m_dt)
    lambda_h, lambda_a = m_data["lambda_h"], m_data["lambda_a"]
    hrank, arank = m_data["h_rank"], m_data["a_rank"]
    rank_str = f" [อันดับ {hrank} vs {arank}]" if (hrank and arank) else ""

    hdc_label, hdc_odds, ev_hdc = "รอเปิด", 1.0, -999.0
    line_val = 0.0
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        line_val = abs(line)
        hp, ap = h_obj["price"], a_obj["price"]
        mp_h, mp_a = devig_odds(hp, ap)
        ev_h, ev_a = calculate_ev(mp_h, hp), calculate_ev(mp_a, ap)

        if ev_h >= ev_a:
          ev_hdc, hdc_odds = ev_h, hp
          hdc_label = (
              f"ต่อ {home} ({line})"
              if line < 0
              else (f"รอง {home} (+{line})" if line > 0 else f"เสมอ {home} (0)")
          )
        else:
          ev_hdc, hdc_odds = ev_a, ap
          aline = a_obj.get("point", 0.0)
          hdc_label = (
              f"ต่อ {away} ({aline})"
              if aline < 0
              else (
                  f"รอง {away} (+{aline})" if aline > 0 else f"เสมอ {away} (0)"
              )
          )

    tot_label, tot_odds, ev_tot = "รอเปิด", 1.0, -999.0
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)
      if over and under:
        t_line = over.get("point", 2.5)
        op, up = over["price"], under["price"]

        if leg_info["csv"] is None or lambda_h is None:
          mp_over, mp_under = devig_odds(op, up)
          ev_o, ev_u = calculate_ev(mp_over, op), calculate_ev(mp_under, up)
        else:
          p_over = calculate_over_probability(lambda_h, lambda_a, line=t_line)
          ev_o, ev_u = calculate_ev(p_over, op), calculate_ev(1.0 - p_over, up)

        if ev_o >= ev_u:
          ev_tot, tot_odds, tot_label = ev_o, op, f"สูงกว่า {t_line}"
        else:
          ev_tot, tot_odds, tot_label = ev_u, up, f"ต่ำกว่า {t_line}"

    if ev_hdc >= ev_tot and ev_hdc != -999.0:
      best_pick, best_odds, best_ev = hdc_label, hdc_odds, ev_hdc
      is_v = ev_hdc >= 2.0
    elif ev_tot != -999.0:
      best_pick, best_odds, best_ev = tot_label, tot_odds, ev_tot
      is_v = ev_tot >= 2.0
    else:
      continue

    # Warning flags ตามสไตล์บ้านผลบอล
    warning_flag = None
    if hrank and arank:
      if abs(hrank - arank) >= 5 and line_val <= 0.25:
        warning_flag = (
            "⚠️ ราคาแปลก/ต่อน้อย: อันดับห่างกันเกิน 5 อันดับ"
            " แต่เปิดราคาต่อแค่ปป./เสมอ"
        )
    if (
        not warning_flag
        and "ต่ำกว่า" in best_pick
        and hrank
        and arank
        and (hrank >= 15 or arank >= 15)
    ):
      warning_flag = "⚠️ ระวังยิงแลก: ทีมท้ายตารางเจอกัน เสี่ยงเปิดหน้าแลก"

    confidence = min(round(50.0 + max(best_ev, 0) * 1.5, 1), 99.0)
    match_res = check_match_result_advanced(
        csv_df, m_data["home_mapped"], m_data["away_mapped"], best_pick, m_dt
    )

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
        "h2h": m_data["h2h"],
        "pick": best_pick,
        "odds": best_odds,
        "ev": best_ev,
        "confidence": confidence,
        "warning": warning_flag,
        "result": match_res,
        "is_value": is_v,
    })
  return results


# ==============================================================================
# 6. STREAMLIT UI
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูหลัก (Quant Pro)")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔍 สแกนหา Value Bet (+EV)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี 100%)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔍 สแกนหา Value Bet (+EV)":
  st.sidebar.header("ตั้งค่าระบบสแกน")
  if "quota_remaining" in st.session_state:
    st.sidebar.metric(
        label="โควตา API คงเหลือ",
        value=f"{st.session_state['quota_remaining']} ครั้ง",
    )
  else:
    st.sidebar.info("โควตา API: กดสแกนเพื่ออัปเดต")

  options = {k: v["name"] for k, v in active_leagues.items()}
  selected = st.sidebar.multiselect(
      "เลือกรายการลีก",
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
      "🔥 เริ่มสแกนด้วย Quant Pro Engine", type="primary"
  )

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner(
          "กำลังวิเคราะห์สถิติ (กรอง Temporal Data ป้องกัน Bias)..."
      ):
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
              df.sort_values(by=["confidence", "ev"], ascending=[False, False])
              .drop_duplicates(subset=["match"])
              .reset_index(drop=True)
          )
          st.session_state["saved_scan_df"] = df
          st.success(f"วิเคราะห์สำเร็จ! พบ {len(df)} คู่")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    tab1, tab2 = st.tabs(["วิเคราะห์รายคู่", "🎯 โพยบิลจัดเต็ม VIP"])

    with tab1:
      for _, m in df.iterrows():
        with st.container():
          st.markdown(
              f"**{m['match']}** — 🏆 ความมั่นใจ: **{m['confidence']}%**"
          )
          st.caption(f"{m['time']} | {m['league']}")
          st.info(f"{m['h2h']}")
          st.markdown(
              f"👉 **ทีเด็ด:** `{m['pick']}` | ค่าน้ำ: `{m['odds']}` | EV:"
              f" **{m['ev']}%**"
          )
          if m.get("warning"):
            st.warning(f"{m['warning']}")
          st.markdown(f"สถานะ: **{m['result']}**")
          st.markdown("---")

    with tab2:
      budg = st.session_state.get("budget", 1000)
      st.subheader("📊 แผนจัดบิลสเต็ป (Staking Tiers)")
      st.write(
          f"• **บิลหลัก VIP (สเต็ป 5):** {round(budg * 0.6)} บาท (ฐานทำเงิน 60%)"
      )
      st.write(
          f"• **บิลต่อยอด (สเต็ป 8):** {round(budg * 0.2)} บาท (เติบโต 20%)"
      )
      st.write(
          f"• **บิลโบนัส (สเต็ป 13):** {round(budg * 0.1)} บาท (ลุ้นรางวัล 10%)"
      )
      st.write(
          f"• **บิลแจ็คพอต (สเต็ป 20):** {round(budg * 0.1)} บาท (แจ็คพอต 10%)"
      )
      st.markdown("---")

      def render_slip(title, data_sub, target_count):
        st.markdown(f"#### {title}")
        if len(data_sub) == 0:
          st.write("- ไม่มีคู่แข่งขัน -")
          return
        for _, row in data_sub.head(target_count).iterrows():
          warn_str = (
              f" <br><small style='color:orange;'>{row['warning']}</small>"
              if row.get("warning")
              else ""
          )
          st.markdown(
              f"✅ **{row['match']}** (ความมั่นใจ:"
              f" `{row['confidence']}%`)<br>👉 เลือก: `{row['pick']}` |"
              f" ค่าน้ำ: `{row['odds']}` | EV: `{row['ev']}%`{warn_str} | ผล:"
              f" **{row['result']}**",
              unsafe_allow_html=True,
          )
        st.markdown("---")

      render_slip("🔥 บิลหลัก VIP (สเต็ป 5 คู่ทองคำ)", df, 5)
      render_slip("📈 บิลต่อยอด (สเต็ป 8 คู่)", df, 8)
      render_slip("🌟 บิลโบนัส (สเต็ป 13 คู่)", df, 13)
      render_slip("💰 บิลแจ็คพอต (สเต็ป 20 คู่)", df, 20)
  else:
    st.info("💡 กรุณากดสแกนที่เมนูด้านข้างเพื่อสร้างโพย")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี 100%)":
  st.subheader("🏆 ระบบสรุปผลบิลออโต้ (0% API Quota)")
  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df_saved = st.session_state["saved_scan_df"]
    with st.spinner("กำลังอัปเดตผลการแข่งขัน..."):
      updated_rows = []
      for _, r in df_saved.iterrows():
        csv_code = None
        for lk, lv in active_leagues.items():
          if lv["name"] == r["league"]:
            csv_code = lv["csv"]
            break
        csv_df = fetch_csv_stats(csv_code) if csv_code else None

        raw_match_name = r["match"].split(" [อันดับ")[0]
        parts = raw_match_name.split(" vs ")
        if len(parts) == 2:
          h_name, a_name = parts[0], parts[1]
          teams = (
              list(set(csv_df["HomeTeam"]).union(set(csv_df["AwayTeam"])))
              if csv_df is not None and not csv_df.empty
              else []
          )
          h_t = get_close_matches(h_name, teams, n=1, cutoff=0.35)
          a_t = get_close_matches(a_name, teams, n=1, cutoff=0.35)
          r["result"] = check_match_result_advanced(
              csv_df,
              h_t[0] if h_t else h_name,
              a_t[0] if a_t else a_name,
              r["pick"],
              r.get("match_date"),
          )
        updated_rows.append(r)
      df = pd.DataFrame(updated_rows)
  else:
    df = pd.DataFrame()

  if not df.empty:
    for _, r in df.iterrows():
      st.write(
          f"⚽ **{r['match']}** ➔ เลือก: `{r['pick']}` | ผลสรุป: **{r['result']}**"
      )
  else:
    st.warning("⚠️ ยังไม่มีข้อมูลโพยในระบบ")
