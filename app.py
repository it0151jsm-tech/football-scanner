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

st.title("⚽ ระบบสแกนบอล Value Bet Pro (ตามหลักการ 10 ขั้นตอน)")
st.caption(
    "เน้นค้นหาค่าน้ำที่มี **+EV (Expected Value)** ด้วย **xG + Fatigue Index +"
    " H2H + Asian Handicap / Totals Poisson Model**"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

# ฐานข้อมูลลีกและรหัส CSV สำหรับดึงสถิติ
KNOWN_LEAGUES = {
    # อเมริกาเหนือ / ใต้
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
    # ยุโรปหลัก
    "soccer_epl": {"name": "Premier League - England", "csv": "E0"},
    "soccer_efl_champ": {"name": "Championship - England", "csv": "E1"},
    "soccer_spain_la_liga": {"name": "La Liga - Spain", "csv": "SP1"},
    "soccer_italy_serie_a": {"name": "Serie A - Italy", "csv": "I1"},
    "soccer_germany_bundesliga": {
        "name": "Bundesliga - Germany",
        "csv": "D1",
    },
    "soccer_france_league1": {"name": "Ligue 1 - France", "csv": "F1"},
    "soccer_netherlands_eredivisie": {
        "name": "Eredivisie - Netherlands",
        "csv": "N1",
    },
    "soccer_portugal_primeira_liga": {
        "name": "Primeira Liga - Portugal",
        "csv": "P1",
    },
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
# 3. MATHEMATICAL & POISSON BETTING MODELS
# ==============================================================================
def poisson_prob(lmbda, k):
  return (math.pow(lmbda, k) * math.exp(-lmbda)) / math.factorial(k)


def calculate_advanced_xg(df, home_api, away_api, match_date):
  """คำนวณ xG ถ่วงน้ำหนักฟอร์ม + วันพักล้า + สถิติ H2H"""
  if df is None or df.empty:
    return None, None, "ไม่มีไฟล์สถิติ", "ไม่มีไฟล์สถิติ", 0

  csv_teams = list(
      set(df["HomeTeam"].unique()).union(set(df["AwayTeam"].unique()))
  )
  home = get_close_matches(home_api, csv_teams, n=1, cutoff=0.4)
  away = get_close_matches(away_api, csv_teams, n=1, cutoff=0.4)

  home_team = home[0] if home else home_api
  away_team = away[0] if away else away_api

  # 1. วันพัก (Rest Days)
  def get_rest(team):
    t_df = df[(df["HomeTeam"] == team) | (df["AwayTeam"] == team)]
    past = t_df[t_df["Date_dt"] < match_date].sort_values("Date_dt")
    if past.empty:
      return 7
    return max((match_date - past.iloc[-1]["Date_dt"]).days, 1)

  h_rest, a_rest = get_rest(home_team), get_rest(away_team)

  # 2. ฟอร์ม 10 นัดหลังสุด (ถ่วงน้ำหนัก 3 นัดหลัง 60%)
  h_df = df[df["HomeTeam"] == home_team].tail(10)
  a_df = df[df["AwayTeam"] == away_team].tail(10)

  if len(h_df) < 3 or len(a_df) < 3:
    return None, None, f"พัก {h_rest} วัน", f"พัก {a_rest} วัน", 30

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

  # 3. สถิติพบกัน H2H
  h2h_df = df[
      ((df["HomeTeam"] == home_team) & (df["AwayTeam"] == away_team))
      | ((df["HomeTeam"] == away_team) & (df["AwayTeam"] == home_team))
  ].tail(5)

  h2h_h_adj, h2h_a_adj = 1.0, 1.0
  if len(h2h_df) >= 2:
    h_g = sum(
        r["FTHG"] if r["HomeTeam"] == home_team else r["FTAG"]
        for _, r in h2h_df.iterrows()
    )
    a_g = sum(
        r["FTAG"] if r["HomeTeam"] == home_team else r["FTHG"]
        for _, r in h2h_df.iterrows()
    )
    if h_g > a_g:
      h2h_h_adj = 1.08
    elif a_g > h_g:
      h2h_a_adj = 1.08

  # 4. ตัวคูณความล้า (Fatigue Factor)
  fatigue_h = 0.88 if h_rest <= 3 else (1.05 if h_rest >= 6 else 1.0)
  fatigue_a = 0.88 if a_rest <= 3 else (1.05 if a_rest >= 6 else 1.0)

  final_h_xg = round(base_h_xg * fatigue_h * h2h_h_adj, 2)
  final_a_xg = round(base_a_xg * fatigue_a * h2h_a_adj, 2)

  h_info = f"พัก {h_rest} วัน {'⚠️เตะถี่' if h_rest <= 3 else '✅ฟิต'}"
  a_info = f"พัก {a_rest} วัน {'⚠️เตะถี่' if a_rest <= 3 else '✅ฟิต'}"

  # คำนวณคะแนนความสมบูรณ์ของข้อมูลตั้งต้น
  data_score = 55  # สถิติพื้นฐาน + xG + H2H
  if h_rest > 3 and a_rest > 3:
    data_score += 15

  return final_h_xg, final_a_xg, h_info, a_info, data_score


def calculate_asian_handicap_ev(home_xg, away_xg, h_line, odds):
  """คำนวณ EV สำหรับ Asian Handicap (รองรับราคาควบ 0.25, 0.75, 1.25)"""
  net_ev = 0.0
  for i in range(8):
    for j in range(8):
      p = poisson_prob(home_xg, i) * poisson_prob(away_xg, j)
      diff = (i - j) + h_line

      if diff > 0.1:
        profit = odds - 1.0  # ชนะเต็ม
      elif abs(diff) < 0.1:
        profit = 0.0  # เสมอ/คืนทุน
      elif abs(diff - 0.25) < 0.1:
        profit = 0.5 * (odds - 1.0)  # ได้ครึ่ง
      elif abs(diff + 0.25) < 0.1:
        profit = -0.5  # เสียครึ่ง
      else:
        profit = -1.0  # เสียเต็ม

      net_ev += p * profit
  return round(net_ev * 100, 2)


def calculate_totals_ev(home_xg, away_xg, line, over_odds, under_odds):
  """คำนวณ EV สำหรับ Over/Under (รองรับราคาควบ 2.25, 2.75)"""
  p_over = 0.0
  for i in range(8):
    for j in range(8):
      p = poisson_prob(home_xg, i) * poisson_prob(away_xg, j)
      goals = i + j
      diff = goals - line

      if diff > 0.1:
        p_over += p
      elif abs(diff - 0.25) < 0.1:
        p_over += p * 0.5  # ชนะครึ่ง

  p_under = 1.0 - p_over

  ev_over = round(((p_over * over_odds) - 1) * 100, 2)
  ev_under = round(((p_under * under_odds) - 1) * 100, 2)

  return ev_over, ev_under, p_over, p_under


def calculate_kelly(ev_pct, odds):
  if ev_pct <= 0 or odds <= 1.0:
    return "0%"
  b = odds - 1.0
  p = ((ev_pct / 100.0) + 1.0) / odds
  q = 1.0 - p
  k = (p * b - q) / b
  return f"{round(max(k * 0.25 * 100, 0), 1)}%"


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
      st.error("⚠️ โควตา API รายเดือนของคุณหมดแล้ว")
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

    # 1. วิเคราะห์ Asian Handicap
    hdc_str, ev_hdc, k_hdc = "รอค่าน้ำเปิด", -999.0, "0%"
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)

      if h_obj and a_obj:
        if h_xg is not None and a_xg is not None:
          ev_h = calculate_asian_handicap_ev(
              h_xg, a_xg, h_obj.get("point", 0), h_obj["price"]
          )
          ev_a = calculate_asian_handicap_ev(
              a_xg, h_xg, a_obj.get("point", 0), a_obj["price"]
          )
        else:
          imp_h, imp_a = 1 / h_obj["price"], 1 / a_obj["price"]
          tot_imp = imp_h + imp_a
          ev_h = round((((imp_h / tot_imp) * h_obj["price"]) - 1) * 100, 2)
          ev_a = round((((imp_a / tot_imp) * a_obj["price"]) - 1) * 100, 2)

        if ev_h >= ev_a:
          ev_hdc = ev_h
          pt = h_obj.get("point", 0)
          line_str = f"+{pt}" if pt > 0 else f"{pt}"
          hdc_str = f"เจ้าบ้าน ({home}) {line_str} @ {h_obj['price']} (EV: {ev_h}%)"
          k_hdc = calculate_kelly(ev_h, h_obj["price"])
        else:
          ev_hdc = ev_a
          pt = a_obj.get("point", 0)
          line_str = f"+{pt}" if pt > 0 else f"{pt}"
          hdc_str = f"ทีมเยือน ({away}) {line_str} @ {a_obj['price']} (EV: {ev_a}%)"
          k_hdc = calculate_kelly(ev_a, a_obj["price"])

    # 2. วิเคราะห์ สูง/ต่ำ (Totals)
    tot_str, ev_tot, k_tot = "รอค่าน้ำเปิด", -999.0, "0%"
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)

      if over and under:
        line = over.get("point", 2.5)
        if h_xg is not None and a_xg is not None:
          ev_o, ev_u, _, _ = calculate_totals_ev(
              h_xg, a_xg, line, over["price"], under["price"]
          )
        else:
          imp_o, imp_u = 1 / over["price"], 1 / under["price"]
          tot_imp = imp_o + imp_u
          ev_o = round((((imp_o / tot_imp) * over["price"]) - 1) * 100, 2)
          ev_u = round((((imp_u / tot_imp) * under["price"]) - 1) * 100, 2)

        if ev_o >= ev_u:
          ev_tot = ev_o
          tot_str = f"สูง {line} @ {over['price']} (EV: {ev_o}%)"
          k_tot = calculate_kelly(ev_o, over["price"])
        else:
          ev_tot = ev_u
          tot_str = f"ต่ำ {line} @ {under['price']} (EV: {ev_u}%)"
          k_tot = calculate_kelly(ev_u, under["price"])

    # 3. คำนวณคะแนนความสมบูรณ์ของข้อมูล (Score 0-100)
    final_score = base_score
    if ev_hdc > 2.0 or ev_tot > 2.0:
      final_score += 20  # ได้คะแนนหมวด EV เพิ่ม
    if sp_mkt and tot_mkt:
      final_score += 10  # ตลาดเปิดครบถ้วน

    # สรุปสถานะความน่าลงทุน
    is_hdc_v, is_tot_v = ev_hdc > 2.0, ev_tot > 2.0
    if is_hdc_v and is_tot_v:
      status = "🔥 น่าเล่นทั้ง 2 ตลาด"
    elif is_hdc_v:
      status = "🔥 น่าเล่น ราคาต่อรอง"
    elif is_tot_v:
      status = "🔥 น่าเล่น สูง/ต่ำ"
    else:
      status = "➖ ผ่าน / ไม่มีความได้เปรียบ"

    total_xg_str = (
        f"{round(h_xg + a_xg, 2)} ลูก (H:{h_xg} | A:{a_xg})"
        if h_xg is not None
        else "N/A"
    )

    results.append({
        "เวลาเตะ (ไทย)": match_time,
        "รายการ/ลีก": league_info["name"],
        "คู่แข่งขัน": f"{home} vs {away}",
        "ความฟิต (วันพัก)": f"เจ้าบ้าน: {h_info} | เยือน: {a_info}",
        "ประตูคาดหมาย (xG)": total_xg_str,
        "แนะนำ ราคาต่อรอง (HDC)": hdc_str,
        "แนะนำ สูง/ต่ำ": tot_str,
        "ทุนแนะนำ (Kelly)": f"ต่อรอง: {k_hdc} | สูงต่ำ: {k_tot}",
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
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV > 2%)", value=False
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
