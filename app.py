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
    page_title="Value Bet Pro + ATS, BTTS & 4 Slips",
    page_icon="⚽",
    layout="wide",
)

st.title(
    "⚽ ระบบวิเคราะห์บอล Value Bet Pro (ATS + BTTS + จัดสเต็ป 4 ระดับ)"
)
st.caption(
    "วิเคราะห์เจาะลึกราคาต่อรอง **Asian Handicap** และ **Over/Under** "
    "ถอดค่าต๋งเจ้ามือ + xG + ฟอร์มสุทธิ + สถิติ ATS & BTTS พร้อมจัดบิลสเต็ป"
    " (6, 9, 13, 20 คู่) อัตโนมัติ"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

KNOWN_LEAGUES = {
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
}


# ==============================================================================
# 2. DATA FETCHING & STATS CALCULATION
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


def norm_cdf(x):
  return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def calculate_advanced_metrics(df, home_api, away_api, match_date):
  if df is None or df.empty:
    return (
        None,
        None,
        None,
        None,
        "พักปกติ (7 วัน)",
        "พักปกติ (7 วัน)",
        "N/A",
        "N/A",
        25,
    )

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

  h_df = df[
      (df["HomeTeam"] == home_team) | (df["AwayTeam"] == home_team)
  ].tail(7)
  a_df = df[
      (df["HomeTeam"] == away_team) | (df["AwayTeam"] == away_team)
  ].tail(7)

  if len(h_df) < 3 or len(a_df) < 3:
    return (
        None,
        None,
        None,
        None,
        f"พัก {h_rest} วัน",
        f"พัก {a_rest} วัน",
        "N/A",
        "N/A",
        40,
    )

  h_btts_count, a_btts_count = 0, 0
  h_cover_count = 0

  for _, row in h_df.iterrows():
    if row["FTHG"] > 0 and row["FTAG"] > 0:
      h_btts_count += 1
    diff = (
        (row["FTHG"] - row["FTAG"])
        if row["HomeTeam"] == home_team
        else (row["FTAG"] - row["FTHG"])
    )
    if diff >= 1:
      h_cover_count += 1

  for _, row in a_df.iterrows():
    if row["FTHG"] > 0 and row["FTAG"] > 0:
      a_btts_count += 1

  ats_pct = round((h_cover_count / len(h_df)) * 100)
  btts_avg_pct = round(
      ((h_btts_count / len(h_df)) + (a_btts_count / len(a_df))) / 2 * 100
  )

  ats_str = f"ชนะ {h_cover_count}/{len(h_df)} ({ats_pct}%)"
  btts_str = f"{btts_avg_pct}% ({'สูง' if btts_avg_pct >= 55 else 'ต่ำ'})"

  def get_stats(t_df, team):
    scored, conceded = [], []
    for _, r in t_df.iterrows():
      if r["HomeTeam"] == team:
        scored.append(r["FTHG"])
        conceded.append(r["FTAG"])
      else:
        scored.append(r["FTAG"])
        conceded.append(r["FTHG"])
    return pd.Series(scored).mean(), pd.Series(conceded).mean()

  h_att, h_def = get_stats(h_df, home_team)
  a_att, a_def = get_stats(a_df, away_team)

  fatigue_h = 0.90 if h_rest <= 3 else (1.04 if h_rest >= 6 else 1.0)
  fatigue_a = 0.90 if a_rest <= 3 else (1.04 if a_rest >= 6 else 1.0)

  h_net_form = round((h_att - h_def) * fatigue_h, 2)
  a_net_form = round((a_att - a_def) * fatigue_a, 2)
  h_xg = round(h_att * fatigue_h, 2)
  a_xg = round(a_att * fatigue_a, 2)

  h_info = f"พัก {h_rest} วัน {'⚠️เตะถี่' if h_rest <= 3 else '✅ฟิต'}"
  a_info = f"พัก {a_rest} วัน {'⚠️เตะถี่' if a_rest <= 3 else '✅ฟิต'}"

  score = 75 if (h_rest > 3 and a_rest > 3) else 60

  return (
      h_net_form,
      a_net_form,
      h_xg,
      a_xg,
      h_info,
      a_info,
      ats_str,
      btts_str,
      score,
  )


def devig_odds(odds_1, odds_2):
  imp1, imp2 = 1.0 / odds_1, 1.0 / odds_2
  tot = imp1 + imp2
  return imp1 / tot, imp2 / tot


def calculate_kelly(ev_pct, odds):
  if ev_pct <= 0 or odds <= 1.0:
    return "0%"
  b = odds - 1.0
  p = ((ev_pct / 100.0) + 1.0) / odds
  q = 1.0 - p
  k = (p * b - q) / b
  return f"{round(max(k * 0.25 * 100, 0), 1)}%"


# ==============================================================================
# 3. SCANNER CORE LOGIC
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

    (
        h_net_form,
        a_net_form,
        h_xg,
        a_xg,
        h_info,
        a_info,
        ats_str,
        btts_str,
        base_score,
    ) = calculate_advanced_metrics(csv_df, home, away, match_dt)

    # 1. Asian Handicap
    best_hdc_str, ev_hdc, k_hdc = "รอค่าน้ำเปิด", -999.0, "0%"
    hdc_selection, hdc_odds_val = "", 1.0
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)

      if h_obj and a_obj:
        h_line = h_obj.get("point", 0.0)
        a_line = a_obj.get("point", 0.0)
        h_odds, a_odds = h_obj["price"], a_obj["price"]
        mkt_p_h, mkt_p_a = devig_odds(h_odds, a_odds)

        if h_net_form is not None and a_net_form is not None:
          form_delta = h_net_form - a_net_form
          edge = form_delta + h_line
          prob_shift = (norm_cdf(edge / 1.1) - 0.5) * 0.35
          final_p_h = max(min(mkt_p_h + prob_shift, 0.85), 0.15)
          final_p_a = 1.0 - final_p_h
        else:
          final_p_h, final_p_a = mkt_p_h, mkt_p_a

        ev_h = round(((final_p_h * h_odds) - 1.0) * 100, 2)
        ev_a = round(((final_p_a * a_odds) - 1.0) * 100, 2)

        if ev_h >= ev_a:
          ev_hdc = ev_h
          label = (
              f"ต่อ {home} ({h_line})"
              if h_line < 0
              else (
                  f"รอง {home} (+{h_line})"
                  if h_line > 0
                  else f"เสมอ {home} (0.0)"
              )
          )
          best_hdc_str = (
              f"{label} @ {h_odds} (EV: {'+' if ev_h > 0 else ''}{ev_h}%)"
          )
          k_hdc = calculate_kelly(ev_h, h_odds)
          hdc_selection, hdc_odds_val = label, h_odds
        else:
          ev_hdc = ev_a
          label = (
              f"ต่อ {away} ({a_line})"
              if a_line < 0
              else (
                  f"รอง {away} (+{a_line})"
                  if a_line > 0
                  else f"เสมอ {away} (0.0)"
              )
          )
          best_hdc_str = (
              f"{label} @ {a_odds} (EV: {'+' if ev_a > 0 else ''}{ev_a}%)"
          )
          k_hdc = calculate_kelly(ev_a, a_odds)
          hdc_selection, hdc_odds_val = label, a_odds

    # 2. Totals
    best_tot_str, ev_tot, k_tot = "รอค่าน้ำเปิด", -999.0, "0%"
    tot_selection, tot_odds_val = "", 1.0
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)

      if over and under:
        line = over.get("point", 2.5)
        o_odds, u_odds = over["price"], under["price"]
        mkt_p_o, mkt_p_u = devig_odds(o_odds, u_odds)

        if h_xg is not None and a_xg is not None:
          total_expected_goals = h_xg + a_xg
          xg_edge = total_expected_goals - line
          prob_shift = (norm_cdf(xg_edge / 1.0) - 0.5) * 0.35
          final_p_o = max(min(mkt_p_o + prob_shift, 0.85), 0.15)
          final_p_u = 1.0 - final_p_o
        else:
          final_p_o, final_p_u = mkt_p_o, mkt_p_u

        ev_o = round(((final_p_o * o_odds) - 1.0) * 100, 2)
        ev_u = round(((final_p_u * u_odds) - 1.0) * 100, 2)

        if ev_o >= ev_u:
          ev_tot = ev_o
          best_tot_str = (
              f"สูง {line} @ {o_odds} (EV: {'+' if ev_o > 0 else ''}{ev_o}%)"
          )
          k_tot = calculate_kelly(ev_o, o_odds)
          tot_selection, tot_odds_val = f"สูง {line}", o_odds
        else:
          ev_tot = ev_u
          best_tot_str = (
              f"ต่ำ {line} @ {u_odds} (EV: {'+' if ev_u > 0 else ''}{ev_u}%)"
          )
          k_tot = calculate_kelly(ev_u, u_odds)
          tot_selection, tot_odds_val = f"ต่ำ {line}", u_odds

    # 3. Status & Blending
    is_hdc_v = ev_hdc >= 2.0
    is_tot_v = ev_tot >= 2.0

    if is_hdc_v and is_tot_v:
      status = "🔥 น่าเล่นทั้ง 2 ตลาด"
    elif is_hdc_v:
      status = "🔥 น่าเล่น HDC"
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

    if ev_hdc >= ev_tot:
      best_pick_label, best_pick_odds, best_ev = (
          hdc_selection,
          hdc_odds_val,
          ev_hdc,
      )
    else:
      best_pick_label, best_pick_odds, best_ev = (
          tot_selection,
          tot_odds_val,
          ev_tot,
      )

    results.append({
        "เวลาเตะ (ไทย)": match_time,
        "รายการ/ลีก": league_info["name"],
        "คู่แข่งขัน": f"{home} vs {away}",
        "ความฟิต (วันพัก)": f"เจ้าบ้าน: {h_info} | เยือน: {a_info}",
        "ประตูคาดหมาย (xG)": total_xg_display,
        "สถิติ ATS (7 นัด)": ats_str,
        "โอกาส BTTS": btts_str,
        "แนะนำ ราคาต่อรอง (HDC)": best_hdc_str,
        "แนะนำ สูง/ต่ำ": best_tot_str,
        "ทุนแนะนำ (Kelly)": f"ต่อ/รอง: {k_hdc} | สูงต่ำ: {k_tot}",
        "ความสมบูรณ์ข้อมูล": f"{min(final_score, 100)}/100",
        "สถานะ": status,
        "is_value": is_hdc_v or is_tot_v,
        "max_ev": best_ev,
        "data_score_num": min(final_score, 100),
        "best_pick_label": best_pick_label,
        "best_pick_odds": best_pick_odds,
    })

  return results


# ==============================================================================
# 4. USER INTERFACE (STREAMLIT)
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
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV >= 2.0%)", value=False
)
budget_input = st.sidebar.number_input(
    "งบประมาณลงทุนรวมวันนี้ (บาท)", value=1000, step=100
)

scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล Pro + จัด 4 บิล", type="primary")

if scan_btn:
  if not selected:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
  else:
    with st.spinner("กำลังวิเคราะห์ข้อมูลเชิงลึก Real-time..."):
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

        tab1, tab2 = st.tabs(
            ["📊 ผลการวิเคราะห์ทั้งหมด", "🎫 ระบบจัดบิลสเต็ป 4 ระดับ"]
        )

        with tab1:
          df_display = df.drop(
              columns=[
                  "is_value",
                  "max_ev",
                  "data_score_num",
                  "best_pick_label",
                  "best_pick_odds",
              ]
          )
          st.dataframe(df_display, use_container_width=True, hide_index=True)

        with tab2:
          st.subheader("🎯 บิลสเต็ปแนะนำ 4 ระดับ (เรียงตามคะแนน +EV สูงสุด)")
          candidates = df[df["is_value"] == True].sort_values(
              by=["max_ev", "data_score_num"], ascending=[False, False]
          )

          if len(candidates) < 6:
            st.warning(
                f"พบคู่ที่มี +EV เพียง {len(candidates)} คู่ (ต้องการอย่างน้อย 6"
                " คู่เพื่อจัดบิล)"
            )
          else:
            top20 = candidates.head(20).reset_index(drop=True)

            slip_6 = top20.head(6)
            slip_9 = top20.head(min(9, len(top20)))
            slip_13 = top20.head(min(13, len(top20)))
            slip_20 = top20.head(min(20, len(top20)))

            st.markdown("### 💰 การแบ่งเงินลงทุนแนะนำ (Staking Plan)")
            stake_data = [
                {
                    "บิล": "บิลที่ 1 (สเต็ป 6 - บิลหลัก)",
                    "สัดส่วน": "50%",
                    "จำนวนเงิน (บาท)": round(budget_input * 0.50),
                    "เป้าหมาย": "บิลหลักเน้นทำกำไร/คืนทุน",
                },
                {
                    "บิล": "บิลที่ 2 (สเต็ป 9 - บิลต่อยอด)",
                    "สัดส่วน": "25%",
                    "จำนวนเงิน (บาท)": round(budget_input * 0.25),
                    "เป้าหมาย": "บิลต่อยอดกำไร",
                },
                {
                    "บิล": "บิลที่ 3 (สเต็ป 13 - บิลโบนัส)",
                    "สัดส่วน": "15%",
                    "จำนวนเงิน (บาท)": round(budget_input * 0.15),
                    "เป้าหมาย": "บิลลุ้นโบนัสค่าน้ำสูง",
                },
                {
                    "บิล": "บิลที่ 4 (สเต็ป 20 - บิลแจ็คพอต)",
                    "สัดส่วน": "10%",
                    "จำนวนเงิน (บาท)": round(budget_input * 0.10),
                    "เป้าหมาย": "บิลแจ็คพอต (ขำๆ)",
                },
            ]
            st.table(pd.DataFrame(stake_data))
            st.markdown("---")

            col1, col2 = st.columns(2)
            with col1:
              st.markdown("#### 🟢 บิลที่ 1: สเต็ป 6 คู่ (บิลหลัก)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.50)} บาท")
              st.dataframe(
                  slip_6[
                      [
                          "เวลาเตะ (ไทย)",
                          "คู่แข่งขัน",
                          "best_pick_label",
                          "best_pick_odds",
                          "สถิติ ATS (7 นัด)",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "best_pick_label": "ตัวเลือกแนะนำ",
                          "best_pick_odds": "ค่าน้ำ",
                          "max_ev": "EV (%)",
                      }
                  ),
                  hide_index=True,
                  use_container_width=True,
              )

              st.markdown("#### 🔵 บิลที่ 2: สเต็ป 9 คู่ (บิลต่อยอด)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.25)} บาท")
              st.dataframe(
                  slip_9[
                      [
                          "เวลาเตะ (ไทย)",
                          "คู่แข่งขัน",
                          "best_pick_label",
                          "best_pick_odds",
                          "สถิติ ATS (7 นัด)",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "best_pick_label": "ตัวเลือกแนะนำ",
                          "best_pick_odds": "ค่าน้ำ",
                          "max_ev": "EV (%)",
                      }
                  ),
                  hide_index=True,
                  use_container_width=True,
              )

            with col2:
              st.markdown("#### 🟡 บิลที่ 3: สเต็ป 13 คู่ (บิลโบนัส)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.15)} บาท")
              st.dataframe(
                  slip_13[
                      [
                          "เวลาเตะ (ไทย)",
                          "คู่แข่งขัน",
                          "best_pick_label",
                          "best_pick_odds",
                          "สถิติ ATS (7 นัด)",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "best_pick_label": "ตัวเลือกแนะนำ",
                          "best_pick_odds": "ค่าน้ำ",
                          "max_ev": "EV (%)",
                      }
                  ),
                  hide_index=True,
                  use_container_width=True,
              )

              st.markdown("#### 🔴 บิลที่ 4: สเต็ป 20 คู่ (บิลแจ็คพอต)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.10)} บาท")
              st.dataframe(
                  slip_20[
                      [
                          "เวลาเตะ (ไทย)",
                          "คู่แข่งขัน",
                          "best_pick_label",
                          "best_pick_odds",
                          "สถิติ ATS (7 นัด)",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "best_pick_label": "ตัวเลือกแนะนำ",
                          "best_pick_odds": "ค่าน้ำ",
                          "max_ev": "EV (%)",
                      }
                  ),
                  hide_index=True,
                  use_container_width=True,
              )

      else:
        st.warning("ไม่พบคู่แข่งขันที่เตะภายใน 36 ชั่วโมงในลีกที่เลือก")
