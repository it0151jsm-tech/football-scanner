import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Value Bet Scanner Pro Analytics", page_icon="⚽", layout="wide"
)

st.title("⚽ ระบบสแกนบอล Value Bet Pro Analytics (24 ชั่วโมงวันต่อวัน)")
st.markdown(
    "วิเคราะห์แม่นยำเน้นบอลเตะภายใน **24 ชั่วโมง (1X2 + สูง/ต่ำ)** ด้วย **xG"
    " ถ่วงน้ำหนัก + H2H + วันพัก/ความล้า + Kelly Criterion**"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

KNOWN_LEAGUES = {
    # เนเธอร์แลนด์ (ฮอลแลนด์)
    "soccer_netherlands_eredivisie": {
        "name": "Eredivisie - Netherlands",
        "csv": "N1",
    },
    "soccer_netherlands_eerste_divisie": {
        "name": "Eerste Divisie - Netherlands (ลีกรองฮอลแลนด์)",
        "csv": None,
    },
    # อังกฤษ
    "soccer_epl": {"name": "Premier League - England", "csv": "E0"},
    "soccer_efl_champ": {"name": "Championship - England", "csv": "E1"},
    "soccer_england_league1": {"name": "League 1 - England", "csv": "E2"},
    "soccer_england_league2": {"name": "League 2 - England", "csv": "E3"},
    # สเปน
    "soccer_spain_la_liga": {"name": "La Liga - Spain", "csv": "SP1"},
    "soccer_spain_segunda_division": {
        "name": "Segunda Division - Spain",
        "csv": "SP2",
    },
    # อิตาลี
    "soccer_italy_serie_a": {"name": "Serie A - Italy", "csv": "I1"},
    "soccer_italy_serie_b": {"name": "Serie B - Italy", "csv": "I2"},
    # เยอรมนี
    "soccer_germany_bundesliga": {
        "name": "Bundesliga - Germany",
        "csv": "D1",
    },
    "soccer_germany_bundesliga2": {
        "name": "2. Bundesliga - Germany",
        "csv": "D2",
    },
    # ฝรั่งเศส
    "soccer_france_league1": {"name": "Ligue 1 - France", "csv": "F1"},
    "soccer_france_league2": {"name": "Ligue 2 - France", "csv": "F2"},
    # โปรตุเกส
    "soccer_portugal_primeira_liga": {
        "name": "Primeira Liga - Portugal",
        "csv": "P1",
    },
    "soccer_portugal_liga_pro": {
        "name": "Liga Portugal 2 (ลีกรองโปรตุเกส)",
        "csv": None,
    },
    # เบลเยียม / ตุรกี / กรีซ / สกอตแลนด์
    "soccer_belgium_first_div": {
        "name": "First Division A - Belgium",
        "csv": "B1",
    },
    "soccer_turkey_super_league": {
        "name": "Super Lig - Turkey",
        "csv": "T1",
    },
    "soccer_greece_super_league": {
        "name": "Super League - Greece",
        "csv": "G1",
    },
    "soccer_spl": {"name": "Premiership - Scotland", "csv": "SC0"},
    # ไอร์แลนด์
    "soccer_ireland_premier_division": {
        "name": "Premier Division - Ireland",
        "csv": None,
    },
    "soccer_ireland_first_division": {
        "name": "First Division - Ireland (ลีกรองไอร์แลนด์)",
        "csv": None,
    },
    # อเมริกาเหนือ / ใต้
    "soccer_brazil_campeonato": {
        "name": "Serie A - Brazil",
        "csv": "BRA",
    },
    "soccer_brazil_serie_b": {"name": "Serie B - Brazil", "csv": "BRA2"},
    "soccer_argentina_primera_division": {
        "name": "Primera Division - Argentina",
        "csv": "ARG",
    },
    "soccer_colombia_categoria_primera_a": {
        "name": "Primera A - Colombia",
        "csv": "COL",
    },
    "soccer_peru_liga_1": {"name": "Liga 1 - Peru", "csv": "PER"},
    "soccer_mexico_ligamx": {"name": "Liga MX - Mexico", "csv": "MEX"},
    "soccer_usa_mls": {"name": "MLS - USA", "csv": "USA"},
}


@st.cache_data(ttl=1800)
def get_active_soccer_leagues():
  soccer_leagues = {}
  for k, v in KNOWN_LEAGUES.items():
    soccer_leagues[k] = {"name": v["name"], "key": k, "csv": v["csv"]}

  url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
  try:
    res = requests.get(url)
    if res.status_code == 200:
      data = res.json()
      for item in data:
        if item.get("group") == "Soccer":
          key = item["key"]
          title = item["title"]
          if key not in soccer_leagues:
            soccer_leagues[key] = {"name": title, "key": key, "csv": None}
  except Exception:
    pass

  return soccer_leagues


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
  if not csv_teams:
    return api_name
  matches = get_close_matches(api_name, csv_teams, n=1, cutoff=0.4)
  return matches[0] if matches else api_name


def get_rest_days(df, team, current_match_date):
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
    return None, None, "ไม่มีไฟล์สถิติ", "ไม่มีไฟล์สถิติ"

  csv_teams = list(
      set(df["HomeTeam"].unique()).union(set(df["AwayTeam"].unique()))
  )
  home_team = match_team_name(api_home, csv_teams)
  away_team = match_team_name(api_away, csv_teams)

  home_rest = get_rest_days(df, home_team, match_date)
  away_rest = get_rest_days(df, away_team, match_date)

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
  # กำหนดช่วงเวลาดึงเฉพาะบอลเตะภายใน 24 ชั่วโมงข้างหน้า (วันต่อวัน)
  next_24h_utc = now_utc + timedelta(hours=24)
  tz_th = timezone(timedelta(hours=7))

  results = []
  for m in matches:
    commence_time = datetime.fromisoformat(
        m["commence_time"].replace("Z", "+00:00")
    )
    if not (now_utc - timedelta(hours=2) <= commence_time <= next_24h_utc):
      continue

    match_time_th = commence_time.astimezone(tz_th).strftime("%d/%m %H:%M น.")
    match_dt_naive = commence_time.astimezone(tz_th).replace(tzinfo=None)

    home = m["home_team"]
    away = m["away_team"]

    bookmakers = m.get("bookmakers", [])

    h2h_market = None
    totals_market = None
    if bookmakers:
      for bm in bookmakers:
        mk = next((k for k in bm.get("markets", []) if k["key"] == "h2h"), None)
        if mk and not h2h_market:
          h2h_market = mk

        mk_tot = next(
            (k for k in bm.get("markets", []) if k["key"] == "totals"), None
        )
        if mk_tot and not totals_market:
          totals_market = mk_tot

    h_xg, a_xg, h_info, a_info = calculate_advanced_xg(
        stats_df, home, away, match_dt_naive
    )

    # 1. วิเคราะห์ฝั่ง 1X2
    best_1x2_str = "รอค่าน้ำเปิด"
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

    # 2. วิเคราะห์ สูง/ต่ำ
    best_totals_str = "รอค่าน้ำเปิด"
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

    # 3. สรุปสถานะความน่าลงทุน
    is_1x2_value = ev_1x2_best > 2.0
    is_totals_value = ev_totals_best > 2.0

    if is_1x2_value and is_totals_value:
      status = "🔥 น่าเล่นทั้ง 2 ตลาด"
    elif is_1x2_value:
      status = "🔥 น่าเล่นฝั่ง (1X2)"
    elif is_totals_value:
      status = "🔥 น่าเล่น สูง/ต่ำ"
    elif not h2h_market and not totals_market:
      status = "⏳ ค่าน้ำยังไม่เปิด"
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
    default=["soccer_netherlands_eerste_divisie"],
    format_func=lambda x: options_dict[x],
)

only_value_bets = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV > 2%)", value=False
)
scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล Pro (24 ชม.)", type="primary")

if scan_btn:
  if not selected_leagues:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 รายการแข่งขัน")
  else:
    with st.spinner(
        "กำลังวิเคราะห์สถิติวันพัก xG H2H และค่าน้ำบอลเตะภายใน 24 ชม...."
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

        st.success(
            f"พบรายการแข่งขันเตะภายใน 24 ชม. ทั้งหมด {len(df_display)} รายการ"
        )
        st.dataframe(df_display, use_container_width=True, hide_index=True)
      else:
        st.warning("ไม่พบคู่แข่งขันที่เตะภายใน 24 ชั่วโมงในลีกที่เลือก")
