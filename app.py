import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro Framework", page_icon="⚽", layout="wide"
)

st.title("⚽ ระบบวิเคราะห์บอล Value Bet Pro (ต่อ-รอง & สูง-ต่ำ)")
st.caption(
    "วิเคราะห์เจาะลึกราคาต่อรอง **Asian Handicap** และ **Over/Under** ด้วยโมเดล"
    " **Market Blended xG + Fatigue + Kelly Criterion**"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

KNOWN_LEAGUES = {
    "soccer_brazil_campeonato": {
        "name": "Serie A - Brazil (บราซิล)",
        "csv": "BRA",
    },
    "soccer_brazil_serie_b": {
        "name": "Serie B - Brazil (บราซิล ลีกรอง)",
        "csv": "BRA2",
    },
    "soccer_argentina_primera_division": {
        "name": "Primera Division - Argentina (อาร์เจนตินา)",
        "csv": "ARG",
    },
    "soccer_colombia_categoria_primera_a": {
        "name": "Primera A - Colombia (โคลอมเบีย)",
        "csv": "COL",
    },
    "soccer_peru_liga_1": {"name": "Liga 1 - Peru (เปรู)", "csv": "PER"},
    "soccer_mexico_ligamx": {
        "name": "Liga MX - Mexico (เม็กซิโก)",
        "csv": "MEX",
    },
    "soccer_usa_mls": {"name": "MLS - USA (สหรัฐอเมริกา)", "csv": "USA"},
    "soccer_epl": {"name": "Premier League - England", "csv": "E0"},
    "soccer_efl_champ": {"name": "Championship - England", "csv": "E1"},
    "soccer_england_league1": {"name": "League 1 - England", "csv": "E2"},
    "soccer_england_league2": {"name": "League 2 - England", "csv": "E3"},
    "soccer_spain_la_liga": {"name": "La Liga - Spain", "csv": "SP1"},
    "soccer_spain_segunda_division": {
        "name": "Segunda Division - Spain",
        "csv": "SP2",
    },
    "soccer_italy_serie_a": {"name": "Serie A - Italy", "csv": "I1"},
    "soccer_italy_serie_b": {"name": "Serie B - Italy", "csv": "I2"},
    "soccer_germany_bundesliga": {
        "name": "Bundesliga - Germany",
        "csv": "D1",
    },
    "soccer_germany_bundesliga2": {
        "name": "2. Bundesliga - Germany",
        "csv": "D2",
    },
    "soccer_france_league1": {"name": "Ligue 1 - France", "csv": "F1"},
    "soccer_france_league2": {"name": "Ligue 2 - France", "csv": "F2"},
    "soccer_netherlands_eredivisie": {
        "name": "Eredivisie - Netherlands",
        "csv": "N1",
    },
    "soccer_portugal_primeira_liga": {
        "name": "Primeira Liga - Portugal",
        "csv": "P1",
    },
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
}


# ==============================================================================
# 2. DATA FETCHING FUNCTIONS
# ==============================================================================
@st.cache_data(ttl=1800)
def get_active_leagues():
  leagues = {}
  for k, v in KNOWN_LEAGUES.items():
    leagues[k] = {"name": v["name"], "key": k, "csv": v["csv"]}

  url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
  try:
    res = requests.get(url)
    if res.status_code == 200:
      for item in res.json():
        if item.get("group") == "Soccer":
          key = item["key"]
          if key not in leagues:
            leagues[key] = {"name": item["title"], "key": key, "csv": None}
  except Exception:
    pass
  return leagues


@st.cache_data(ttl=3600)
def fetch_csv_stats(csv_code):
  if not csv_code:
    return None
  for season in ["2526", "2425"]:
    url = f"https://www.football-data.co.uk/mmz4281/{season}/{csv_code}.csv"
    try:
      df = pd.read_csv(url)
      if "HomeTeam" in df.columns:
        df["Date_dt"] = pd.to_datetime(
            df["Date"], format="%d/%m/%Y", errors="coerce"
        )
        return df[["Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
    except Exception:
      pass
  return None


# ==============================================================================
# 3. STATISTICAL & MATHEMATICAL MODELS
# ==============================================================================
def poisson_prob(lmbda, k):
  return (math.pow(lmbda, k) * math.exp(-lmbda)) / math.factorial(k)


def calculate_advanced_xg(df, home_api, away_api, match_date):
  if df is None or df.empty:
    return None, None, "พักปกติ (7 วัน)", "พักปกติ (7 วัน)", 20

  csv_teams = list(
      set(df["HomeTeam"].unique()).union(set(df["AwayTeam"].unique()))
  )
  home = get_close_matches(home_api, csv_teams, n=1, cutoff=0.35)
  away = get_close_matches(away_api, csv_teams, n=1, cutoff=0.35)

  home_team = home[0] if home else home_api
  away_team = away[0] if away else away_api

  def get_rest(team):
    t_df = df[(df["HomeTeam"] == team) | (df["AwayTeam"] == team)]
    past = t_df[t_df["Date_dt"] < match_date].sort_values("Date_dt")
    if past.empty:
      return 7
    days = (match_date - past.iloc[-1]["Date_dt"]).days
    if days > 14 or days < 1:
      return 7
    return days

  h_rest, a_rest = get_rest(home_team), get_rest(away_team)

  h_df = df[df["HomeTeam"] == home_team].tail(10)
  a_df = df[df["AwayTeam"] == away_team].tail(10)

  if len(h_df) < 3 or len(a_df) < 3:
    return None, None, f"พัก {h_rest} วัน", f"พัก {a_rest} วัน", 40

  league_avg = (df["FTHG"].mean() + df["FTAG"].mean()) / 2

  def weighted_avg(series):
    return (
        (series.tail(3).mean() * 0.6) + (series.iloc[:-3].mean() * 0.4)
        if len(series) >= 5
        else series.mean()
    )

  base_h_xg = (
      (weighted_avg(h_df["FTHG"]) / league_avg)
      * (weighted_avg(a_df["FTAG"]) / league_avg)
      * league_avg
  )
  base_a_xg = (
      (weighted_avg(a_df["FTAG"]) / league_avg)
      * (weighted_avg(h_df["FTHG"]) / league_avg)
      * league_avg
  )

  fatigue_h = 0.90 if h_rest <= 3 else (1.04 if h_rest >= 6 else 1.0)
  fatigue_a = 0.90 if a_rest <= 3 else (1.04 if a_rest >= 6 else 1.0)

  final_h_xg = round(base_h_xg * fatigue_h, 2)
  final_a_xg = round(base_a_xg * fatigue_a, 2)

  h_info = f"พัก {h_rest} วัน {'⚠️เตะถี่' if h_rest <= 3 else '✅ฟิต'}"
  a_info = f"พัก {a_rest} วัน {'⚠️เตะถี่' if a_rest <= 3 else '✅ฟิต'}"

  score = 60
  if h_rest > 3 and a_rest > 3:
    score += 15

  return final_h_xg, final_a_xg, h_info, a_info, score


def devig_odds(odds_1, odds_2):
  implied_1 = 1.0 / odds_1
  implied_2 = 1.0 / odds_2
  total_margin = implied_1 + implied_2
  fair_p1 = implied_1 / total_margin
  fair_p2 = implied_2 / total_margin
  return fair_p1, fair_p2


def calculate_asian_handicap_prob(home_xg, away_xg, h_line):
  p_home_win_cover = 0.0
  p_away_win_cover = 0.0

  for i in range(8):
    for j in range(8):
      p = poisson_prob(home_xg, i) * poisson_prob(away_xg, j)
      diff = (i - j) + h_line

      if diff > 0.1:
        p_home_win_cover += p
      elif abs(diff - 0.25) < 0.1:
        p_home_win_cover += p * 0.75
        p_away_win_cover += p * 0.25
      elif abs(diff) < 0.1:
        p_home_win_cover += p * 0.5
        p_away_win_cover += p * 0.5
      elif abs(diff + 0.25) < 0.1:
        p_home_win_cover += p * 0.25
        p_away_win_cover += p * 0.75
      else:
        p_away_win_cover += p

  return p_home_win_cover, p_away_win_cover


def calculate_kelly(ev_pct, odds):
  if ev_pct <= 0 or odds <= 1.0:
    return "0%"
  b = odds - 1.0
  p = ((ev_pct / 100.0) + 1.0) / odds
  q = 1.0 - p
  k = (p * b - q) / b
  return f"{round(max(k * 0.25 * 100, 0), 1)}%"


def format_hdc_label(point, team_name):
  """แก้ไขป้ายกำกับ ต่อ / รอง / เสมอ ให้ถูกต้องตามราคาจริง"""
  if point < 0:
    return f"ต่อ {team_name} ({point})"
  elif point > 0:
    return f"รอง {team_name} (+{point})"
  else:
    return f"เสมอ/เลือก {team_name} (0.0)"


# ==============================================================================
# 4. SCANNER CORE LOGIC
# ==============================================================================
def scan_league(league_info):
  csv_df = fetch_csv_stats(league_info["csv"])
  url = f"https://api.the-odds-api.com/v4/sports/{league_info['key']}/odds/"
  params = {
      "apiKey": API_KEY,
      "regions": "eu",
      "markets": "spreads,totals",
      "oddsFormat": "decimal",
  }

  try:
    res = requests.get(url, params=params)
    if res.status_code == 429:
      st.error("⚠️ โควตา API รายเดือนของคุณหมดแล้ว (429 Exceeded Quota)")
      return []
    if res.status_code != 200:
      return []
    matches = res.json()
  except Exception:
    return []

  now = datetime.now(timezone.utc)
  next_36h = now + timedelta(hours=36)
  tz_th = timezone(timedelta(hours=7))

  results = []
  for m in matches:
    commence = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
    if not (now - timedelta(hours=2) <= commence <= next_36h):
      continue

    match_time = commence.astimezone(tz_th).strftime("%d/%m %H:%M น.")
    match_dt = commence.astimezone(tz_th).replace(tzinfo=None)
    home, away = m["home_team"], m["away_team"]

    bookmakers = m.get("bookmakers", [])
    if not bookmakers:
      continue

    sp_mkt = next(
        (
            k
            for bm in bookmakers
            for k in bm.get("markets", [])
            if k["key"] == "spreads"
        ),
        None,
    )
    tot_mkt = next(
        (
            k
            for bm in bookmakers
            for k in bm.get("markets", [])
            if k["key"] == "totals"
        ),
        None,
    )

    h_xg, a_xg, h_info, a_info, base_score = calculate_advanced_xg(
        csv_df, home, away, match_dt
    )

    # 1. วิเคราะห์ราคาต่อรอง (Asian Handicap)
    best_hdc_str, ev_hdc, k_hdc = "รอค่าน้ำเปิด", -999.0, "0%"
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)

      if h_obj and a_obj:
        h_line = h_obj.get("point", 0.0)
        h_odds = h_obj["price"]
        a_line = a_obj.get("point", 0.0)
        a_odds = a_obj["price"]

        mkt_p_h, mkt_p_a = devig_odds(h_odds, a_odds)

        if h_xg is not None and a_xg is not None:
          poisson_p_h, poisson_p_a = calculate_asian_handicap_prob(
              h_xg, a_xg, h_line
          )
          final_p_h = (poisson_p_h * 0.5) + (mkt_p_h * 0.5)
          final_p_a = (poisson_p_a * 0.5) + (mkt_p_a * 0.5)
        else:
          final_p_h, final_p_a = mkt_p_h, mkt_p_a

        ev_h = round(((final_p_h * h_odds) - 1.0) * 100, 2)
        ev_a = round(((final_p_a * a_odds) - 1.0) * 100, 2)

        if ev_h >= ev_a:
          ev_hdc = ev_h
          label = format_hdc_label(h_line, home)
          best_hdc_str = f"{label} @ {h_odds} (EV: {ev_h}%)"
          k_hdc = calculate_kelly(ev_h, h_odds)
        else:
          ev_hdc = ev_a
          label = format_hdc_label(a_line, away)
          best_hdc_str = f"{label} @ {a_odds} (EV: {ev_a}%)"
          k_hdc = calculate_kelly(ev_a, a_odds)

    # 2. วิเคราะห์ราคาสูง/ต่ำ (Totals - Over/Under)
    best_tot_str, ev_tot, k_tot = "รอค่าน้ำเปิด", -999.0, "0%"
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)

      if over and under:
        line = over.get("point", 2.5)
        o_odds, u_odds = over["price"], under["price"]
        mkt_p_o, mkt_p_u = devig_odds(o_odds, u_odds)

        if h_xg is not None and a_xg is not None:
          p_o_poisson = sum(
              poisson_prob(h_xg, i) * poisson_prob(a_xg, j)
              for i in range(8)
              for j in range(8)
              if (i + j) > line
          )
          p_u_poisson = 1.0 - p_o_poisson
          final_p_o = (p_o_poisson * 0.5) + (mkt_p_o * 0.5)
          final_p_u = (p_u_poisson * 0.5) + (mkt_p_u * 0.5)
        else:
          final_p_o, final_p_u = mkt_p_o, mkt_p_u

        ev_o = round(((final_p_o * o_odds) - 1.0) * 100, 2)
        ev_u = round(((final_p_u * u_odds) - 1.0) * 100, 2)

        if ev_o >= ev_u:
          ev_tot = ev_o
          best_tot_str = f"สูง {line} @ {o_odds} (EV: {ev_o}%)"
          k_tot = calculate_kelly(ev_o, o_odds)
        else:
          ev_tot = ev_u
          best_tot_str = f"ต่ำ {line} @ {u_odds} (EV: {ev_u}%)"
          k_tot = calculate_kelly(ev_u, u_odds)

    # 3. สรุปสถานะความคุ้มค่า
    is_hdc_v = ev_hdc >= 1.5
    is_tot_v = ev_tot >= 1.5

    if is_hdc_v and is_tot_v:
      status = "🔥 น่าเล่นทั้ง 2 ตลาด"
    elif is_hdc_v:
      status = "🔥 น่าเล่น ต่อ/รอง"
    elif is_tot_v:
      status = "🔥 น่าเล่น สูง/ต่ำ"
    else:
      status = "➖ ผ่าน / ไม่มีความได้เปรียบ"

    final_score = base_score
    if sp_mkt and tot_mkt:
      final_score += 15
    if is_hdc_v or is_tot_v:
      final_score += 10

    total_xg_display = (
        f"{round(h_xg + a_xg, 2)} ลูก (H:{h_xg} | A:{a_xg})"
        if h_xg is not None
        else "N/A"
    )

    results.append({
        "เวลาเตะ (ไทย)": match_time,
        "รายการ/ลีก": league_info["name"],
        "คู่แข่งขัน": f"{home} vs {away}",
        "ความฟิต (วันพัก)": f"เจ้าบ้าน: {h_info} | เยือน: {a_info}",
        "ประตูคาดหมาย (xG)": total_xg_display,
        "แนะนำ ราคาต่อรอง (HDC)": best_hdc_str,
        "แนะนำ สูง/ต่ำ": best_tot_str,
        "ทุนแนะนำ (Kelly)": f"ต่อ/รอง: {k_hdc} | สูงต่ำ: {k_tot}",
        "ความสมบูรณ์ข้อมูล": f"{min(final_score, 100)}/100",
        "สถานะ": status,
        "is_value": is_hdc_v or is_tot_v,
    })

  return results


# ==============================================================================
# 5. USER INTERFACE (STREAMLIT)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("🔍 ตัวเลือกการสแกน")
options = {"all": f"🔥 ทุกลีกทั้งหมด ({len(active_leagues)} รายการ)"}
for k, v in active_leagues.items():
  options[k] = v["name"]

selected = st.sidebar.multiselect(
    "เลือกรายการแข่งขัน",
    options=list(options.keys()),
    default=["all"],
    format_func=lambda x: options[x],
)

only_value = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV >= 1.5%)", value=False
)
scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล Pro Framework", type="primary")

if scan_btn:
  if not selected:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
  else:
    with st.spinner("กำลังวิเคราะห์ตามหลักการ 10 ขั้นตอน แบบ Real-time..."):
      results = []
      target_leagues = (
          list(active_leagues.keys()) if "all" in selected else selected
      )
      for leg_key in target_leagues:
        if leg_key in active_leagues:
          results.extend(scan_league(active_leagues[leg_key]))

      if results:
        df = pd.DataFrame(results)
        if only_value:
          df = df[df["is_value"] == True]

        st.success(
            f"พบรายการแข่งขันเตะภายใน 36 ชม. ทั้งหมด {len(df)} รายการ"
        )
        st.dataframe(
            df.drop(columns=["is_value"]),
            use_container_width=True,
            hide_index=True,
        )
      else:
        st.warning("ไม่พบคู่แข่งขันที่เตะภายใน 36 ชั่วโมงในลีกที่เลือก")
