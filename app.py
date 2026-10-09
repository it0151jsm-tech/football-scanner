import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Value Bet Scanner Pro Analytics", page_icon="⚽", layout="wide"
)

st.title("⚽ ระบบสแกนบอล Value Bet Pro Analytics (Full Version)")
st.markdown(
    "วิเคราะห์แม่นยำครบทุกตลาด **(1X2 + สูง/ต่ำ)** ด้วย **xG ถ่วงน้ำหนัก + H2H + วันพัก/ความล้า (Fatigue Index) + Kelly Criterion**"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

# แผนผังรหัส CSV ลีกหลักและลีกรองทั่วโลก
CSV_MAPPING = {
    # อังกฤษ
    "soccer_epl": "E0",
    "soccer_efl_champ": "E1",
    "soccer_england_league1": "E2",
    "soccer_england_league2": "E3",
    # สเปน
    "soccer_spain_la_liga": "SP1",
    "soccer_spain_segunda_division": "SP2",
    # อิตาลี
    "soccer_italy_serie_a": "I1",
    "soccer_italy_serie_b": "I2",
    # เยอรมนี
    "soccer_germany_bundesliga": "D1",
    "soccer_germany_bundesliga2": "D2",
    # ฝรั่งเศส
    "soccer_france_league1": "F1",
    "soccer_france_league2": "F2",
    # เนเธอร์แลนด์ (ฮอลแลนด์)
    "soccer_netherlands_eredivisie": "N1",
    "soccer_netherlands_eerste_divisie": None,  # เออร์สเตอ ดีวีซี (ลีกรองฮอลแลนด์)
    # โปรตุเกส
    "soccer_portugal_primeira_liga": "P1",
    "soccer_portugal_liga_pro": None,  # ลีก้า โปรตุเกส 2
    # เบลเยียม / ตุรกี / กรีซ / สกอตแลนด์
    "soccer_belgium_first_div": "B1",
    "soccer_turkey_super_league": "T1",
    "soccer_greece_super_league": "G1",
    "soccer_spl": "SC0",
    # ไอร์แลนด์
    "soccer_ireland_premier_division": None,
    "soccer_ireland_first_division": None,  # ลีกออฟไอร์แลนด์ เฟิร์สดีวิชั่น
    # อเมริกาเหนือ / ใต้
    "soccer_brazil_campeonato": "BRA",
    "soccer_brazil_serie_b": "BRA2",
    "soccer_argentina_primera_division": "ARG",
    "soccer_colombia_categoria_primera_a": "COL",
    "soccer_peru_liga_1": "PER",
    "soccer_mexico_ligamx": "MEX",
    "soccer_usa_mls": "USA",
}


@st.cache_data(ttl=1800)
def get_active_soccer_leagues():
  url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
  try:
    res = requests.get(url)
    if res.status_code == 200:
      data = res.json()
      soccer_leagues = {}
      for item in data:
        if item.get("group") == "Soccer" and item.get("active"):
          key = item["key"]
          title = item["title"]
          csv_code = CSV_MAPPING.get(key, None)
          soccer_leagues[key] = {
              "name": title,
              "key": key,
              "csv": csv_code,
          }
      return soccer_leagues
  except Exception:
    pass
  return {}


@st.cache_data(ttl=3600)
def fetch_historical_stats(csv_code):
  if not csv_code:
    return None
  seasons = ["2526", "2425"]
  for s in seasons:
    url = f"https://www.football-data.co.uk/mmz4281/{s}/{csv_code}.csv"
    try:
      df = pd.read_csv(url)
      if "HomeTeam" in df.columns:
        df["Date_dt"] = pd.to_datetime(
            df["Date"], format="%d/%m/%Y", errors="coerce"
        )
        df = df[["Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
        if len(df) > 0:
          return df
    except Exception:
      pass
  return None


def match_team_name(api_name, csv_teams):
  """จับคู่ชื่อทีมจาก API และ CSV อัตโนมัติด้วย Fuzzy Matching"""
  if not csv_teams:
    return api_name
  matches = get_close_matches(api_name, csv_teams, n=1, cutoff=0.4)
  return matches[0] if matches else api_name


def get_rest_days(df, team, current_match_date):
  """คำนวณจำนวนวันพักนับจากแมตช์ล่าสุด"""
  team_df = df[(df["HomeTeam"] == team) | (df["AwayTeam"] == team)]
  if team_df.empty:
    return 7
  team_df = team_df.sort_values("Date_dt")
  past_matches = team_df[team_df["Date_dt"] < current_match_date]
  if past_matches.empty:
    return 7
  last_date = past_matches.iloc[-1]["Date_dt"]
  delta_days = (current_match_date - last_date).days
  return max(delta_days, 1)


def calculate_advanced_xg(df, api_home, api_away, match_date):
  if df is None or len(df) == 0:
    return None, None, "สถิติไม่พอ", "สถิติไม่พอ"

  csv_teams = list(
      set(df["HomeTeam"].unique()).union(set(df["AwayTeam"].unique()))
  )
  home_team = match_team_name(api_home, csv_teams)
  away_team = match_team_name(api_away, csv_teams)

  # 1. คำนวณวันพัก (Rest Days)
  home_rest = get_rest_days(df, home_team, match_date)
  away_rest = get_rest_days(df, away_team, match_date)

  # 2. คำนวณ xG แบบถ่วงน้ำหนัก (60% นัด 1-3, 40% นัด 4-10)
  h_df = df[df["HomeTeam"] == home_team].tail(10)
  a_df = df[df["AwayTeam"] == away_team].tail(10)

  if len(h_df) < 3 or len(a_df) < 3:
    return None, None, f"พัก {home_rest} วัน", f"พัก {away_rest} วัน"

  league_avg = (df["FTHG"].mean() + df["FTAG"].mean()) / 2

  def weighted_avg(series):
    if len(series) >= 5:
      recent = series.tail(3).mean()
      older = series.iloc[:-3].mean()
      return (recent * 0.6) + (older * 0.4)
    return series.mean()

  h_att = weighted_avg(h_df["FTHG"])
  h_def = weighted_avg(h_df["FTAG"])
  a_att = weighted_avg(a_df["FTAG"])
  a_def = weighted_avg(a_df["FTHG"])

  base_h_xg = (h_att / league_avg) * (a_def / league_avg) * league_avg
  base_a_xg = (a_att / league_avg) * (h_def / league_avg) * league_avg

  # 3. คำนวณสถิติ H2H (พบกันย้อนหลัง)
  h2h_df = df[
      ((df["HomeTeam"] == home_team) & (df["AwayTeam"] == away_team))
      | ((df["HomeTeam"] == away_team) & (df["AwayTeam"] == home_team))
  ].tail(5)

  h2h_h_adj, h2h_a_adj = 1.0, 1.0
  if len(h2h_df) >= 2:
    h2h_h_goals, h2h_a_goals = 0, 0
    for _, row in h2h_df.iterrows():
      if row["HomeTeam"] == home_team:
        h2h_h_goals += row["FTHG"]
        h2h_a_goals += row["FTAG"]
      else:
        h2h_h_goals += row["FTAG"]
        h2h_a_goals += row["FTHG"]

    avg_h2h_h = h2h_h_goals / len(h2h_df)
    avg_h2h_a = h2h_a_goals / len(h2h_df)
    if avg_h2h_h > avg_h2h_a:
      h2h_h_adj = 1.08
    elif avg_h2h_a > avg_h2h_h:
      h2h_a_adj = 1.08

  # 4. ตัวคูณความล้า (Fatigue Factor)
  fatigue_h = 0.88 if home_rest <= 3 else (1.05 if home_rest >= 6 else 1.0)
  fatigue_a = 0.88 if away_rest <= 3 else (1.05 if away_rest >= 6 else 1.0)

  final_h_xg = round(base_h_xg * fatigue_h * h2h_h_adj, 2)
  final_a_xg = round(base_a_xg * fatigue_a * h2h_a_adj, 2)

  h_info = (
      f"พัก {home_rest} วัน {'⚠️เตะถี่' if home_rest <= 3 else '✅ฟิตเต็มร้อย'}"
  )
  a_info = (
      f"พัก {away_rest} วัน {'⚠️เตะถี่' if away_rest <= 3 else '✅ฟิตเต็มร้อย'}"
  )

  return final_h_xg, final_a_xg, h_info, a_info


def poisson_prob(lmbda, k):
  return (math.pow(lmbda, k) * math.exp(-lmbda)) / math.factorial(k)


def get_1x2_probabilities(home_xg, away_xg):
  p_home, p_draw, p_away = 0.0, 0.0, 0.0
  for i in range(8):
    for j in range(8):
      prob = poisson_prob(home_xg, i) * poisson_prob(away_xg, j)
      if i > j:
        p_home += prob
      elif i == j:
        p_draw += prob
      else:
        p_away += prob
  return p_home, p_draw, p_away


def get_totals_probabilities(home_xg, away_xg, line):
  p_over = 0.0
  for i in range(8):
    for j in range(8):
      prob = poisson_prob(home_xg, i) * poisson_prob(away_xg, j)
      if (i + j) > line:
        p_over += prob
  p_under = 1.0 - p_over
  return p_over, p_under


def calculate_kelly(prob, decimal_odds, fraction=0.25):
  b = decimal_odds - 1.0
  p = prob
  q = 1.0 - p
  kelly_full = (p * b - q) / b
  if kelly_full <= 0:
    return 0.0
  return round(kelly_full * fraction * 100, 1)


def scan_league(league_info):
  league_key = league_info["key"]
  csv_code = league_info["csv"]
  league_name = league_info["name"]

  stats_df = fetch_historical_stats(csv_code)
  url = f"https://api.the-odds-api.com/v4/sports/{league_key}/odds/"
  params = {
      "apiKey": API_KEY,
      "regions": "eu",
      "markets": "h2h,totals",
      "oddsFormat": "decimal",
  }

  try:
    res = requests.get(url, params=params)
    if res.status_code != 200:
      return []
    matches = res.json()
  except Exception:
    return []

  now_utc = datetime.now(timezone.utc)
  next_36h_utc = now_utc + timedelta(hours=36)
  tz_th = timezone(timedelta(hours=7))

  results = []
  for m in matches:
    commence_time = datetime.fromisoformat(
        m["commence_time"].replace("Z", "+00:00")
    )
    if not (now_utc - timedelta(hours=2) <= commence_time <= next_36h_utc):
      continue

    match_time_th = commence_time.astimezone(tz_th).strftime("%d/%m %H:%M น.")
    match_dt_naive = commence_time.astimezone(tz_th).replace(tzinfo=None)

    home = m["home_team"]
    away = m["away_team"]

    bookmakers = m.get("bookmakers", [])
    if not bookmakers:
      continue

    # วนลูปหาตลาด h2h และ totals จากทุกค่ายเจ้ามือที่มีอยู่ใน API
    h2h_market = None
    for bm in bookmakers:
      mk = next((k for k in bm.get("markets", []) if k["key"] == "h2h"), None)
      if mk:
        h2h_market = mk
        break

    totals_market = None
    for bm in bookmakers:
      mk = next((k for k in bm.get("markets", []) if k["key"] == "totals"), None)
      if mk:
        totals_market = mk
        break

    h_xg, a_xg, h_info, a_info = calculate_advanced_xg(
        stats_df, home, away, match_dt_naive
    )

    # ----------------------------------------------------
    # 1. วิเคราะห์ฝั่งแพ้/ชนะ/ต่อรอง (1X2)
    # ----------------------------------------------------
    best_1x2_str = "N/A"
    ev_1x2_best = -999.0
    kelly_1x2_str = "-"

    if h2h_market:
      odds_list = h2h_market.get("outcomes", [])
      h_odds = next((o["price"] for o in odds_list if o["name"] == home), None)
      d_odds = next(
          (o["price"] for o in odds_list if o["name"] == "Draw"), None
      )
      a_odds = next((o["price"] for o in odds_list if o["name"] == away), None)

      if h_odds and a_odds:
        if h_xg is not None and a_xg is not None:
          p_home, p_draw, p_away = get_1x2_probabilities(h_xg, a_xg)
        else:
          total_prob = (
              (1 / h_odds) + (1 / d_odds if d_odds else 0) + (1 / a_odds)
          )
          p_home = (1 / h_odds) / total_prob
          p_away = (1 / a_odds) / total_prob

        ev_home = round(((p_home * h_odds) - 1) * 100, 2)
        ev_away = round(((p_away * a_odds) - 1) * 100, 2)

        if ev_home >= ev_away:
          ev_1x2_best = ev_home
          p_best_1x2 = p_home
          odds_best_1x2 = h_odds
          side_name = f"เจ้าบ้าน ({home})"
        else:
          ev_1x2_best = ev_away
          p_best_1x2 = p_away
          odds_best_1x2 = a_odds
          side_name = f"ทีมเยือน ({away})"

        k_pct = calculate_kelly(p_best_1x2, odds_best_1x2)
        best_1x2_str = f"{side_name} @ {odds_best_1x2} (EV: {'+' if ev_1x2_best > 0 else ''}{ev_1x2_best}%)"
        kelly_1x2_str = f"{k_pct}%" if ev_1x2_best > 2.0 and k_pct > 0 else "0%"

    # ----------------------------------------------------
    # 2. วิเคราะห์ สกอร์ สูง/ต่ำ (Totals)
    # ----------------------------------------------------
    best_totals_str = "N/A"
    ev_totals_best = -999.0
    kelly_totals_str = "-"

    if totals_market:
      odds_list = totals_market.get("outcomes", [])
      over_obj = next((o for o in odds_list if o["name"] == "Over"), None)
      under_obj = next((o for o in odds_list if o["name"] == "Under"), None)

      if over_obj and under_obj:
        line = over_obj.get("point", 2.5)
        over_odds = over_obj["price"]
        under_odds = under_obj["price"]

        if h_xg is not None and a_xg is not None:
          p_over, p_under = get_totals_probabilities(h_xg, a_xg, line)
        else:
          total_prob = (1 / over_odds) + (1 / under_odds)
          p_over = (1 / over_odds) / total_prob
          p_under = (1 / under_odds) / total_prob

        ev_over = round(((p_over * over_odds) - 1) * 100, 2)
        ev_under = round(((p_under * under_odds) - 1) * 100, 2)

        if ev_over >= ev_under:
          ev_totals_best = ev_over
          p_best_tot = p_over
          odds_best_tot = over_odds
          tot_side = f"สูง {line}"
        else:
          ev_totals_best = ev_under
          p_best_tot = p_under
          odds_best_tot = under_odds
          tot_side = f"ต่ำ {line}"

        k_pct_tot = calculate_kelly(p_best_tot, odds_best_tot)
        best_totals_str = f"{tot_side} @ {odds_best_tot} (EV: {'+' if ev_totals_best > 0 else ''}{ev_totals_best}%)"
        kelly_totals_str = (
            f"{k_pct_tot}%" if ev_totals_best > 2.0 and k_pct_tot > 0 else "0%"
        )

    # ----------------------------------------------------
    # 3. สรุปสถานะความน่าลงทุน
    # ----------------------------------------------------
    is_1x2_value = ev_1x2_best > 2.0
    is_totals_value = ev_totals_best > 2.0

    if is_1x2_value and is_totals_value:
      status = "🔥 น่าเล่นทั้ง 2 ตลาด"
    elif is_1x2_value:
      status = "🔥 น่าเล่นฝั่ง (1X2)"
    elif is_totals_value:
      status = "🔥 น่าเล่น สูง/ต่ำ"
    else:
      status = "➖ ไม่คุ้ม/สูสี"

    xg_display = (
        f"H: {h_xg} | A: {a_xg}" if h_xg is not None else "ไม่มีข้อมูล xG"
    )

    results.append({
        "เวลาเตะ (ไทย)": match_time_th,
        "รายการ/ลีก": league_name,
        "คู่แข่งขัน": f"{home} vs {away}",
        "สภาพความฟิต (วันพัก)": f"เจ้าบ้าน: {h_info} | เยือน: {a_info}",
        "xG ประเมิน": xg_display,
        "แนะนำฝั่ง (1X2)": best_1x2_str,
        "แนะนำ สูง/ต่ำ": best_totals_str,
        "ทุนแนะนำ (Kelly)": (
            f"ฝั่ง: {kelly_1x2_str} | สูงต่ำ: {kelly_totals_str}"
        ),
        "สถานะ": status,
        "is_value": is_1x2_value or is_totals_value,
    })

  return results


active_leagues = get_active_soccer_leagues()

st.sidebar.header("🔍 ตัวเลือกการสแกน")

options_dict = {
    "all": f"🔥 เลือกทุกลีก/บอลถ้วยทั้งหมด ({len(active_leagues)} รายการ)"
}
for k, v in active_leagues.items():
  options_dict[k] = v["name"]

selected_leagues = st.sidebar.multiselect(
    "เลือกรายการแข่งขัน/บอลถ้วย (เลือกได้มากกว่า 1 ลีก)",
    options=list(options_dict.keys()),
    default=["all"],
    format_func=lambda x: options_dict[x],
)

only_value_bets = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV > 2%)", value=False
)
scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล Pro", type="primary")

if scan_btn:
  if not selected_leagues:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 รายการแข่งขัน")
  else:
    with st.spinner(
        "กำลังวิเคราะห์สถิติความล้า xG H2H และค่าน้ำบอลแบบ Real-time..."
    ):
      all_results = []
      if "all" in selected_leagues:
        for k, league in active_leagues.items():
          all_results.extend(scan_league(league))
      else:
        for league_id in selected_leagues:
          if league_id in active_leagues:
            all_results.extend(scan_league(active_leagues[league_id]))

      if all_results:
        df = pd.DataFrame(all_results)
        if only_value_bets:
          df = df[df["is_value"] == True]

        df_display = df.drop(columns=["is_value"])

        st.success(f"พบรายการแข่งขันทั้งหมด {len(df_display)} รายการ")
        st.dataframe(df_display, use_container_width=True, hide_index=True)
      else:
        st.warning("ไม่พบคู่แข่งขันในช่วงเวลานี้")
