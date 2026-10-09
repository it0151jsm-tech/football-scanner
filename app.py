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
    page_title="Value Bet Pro - Ultimate Edition", page_icon="⚽", layout="wide"
)

st.title("⚽ ระบบวิเคราะห์บอล Value Bet Pro (สไตล์แอป + จัดสเต็ป 4 บิล)")
st.caption(
    "วิเคราะห์เจาะลึกราคา 1X2, แฮนดิแคป, สูง/ต่ำ + สถิติ 7 นัดล่าสุด + H2H"
    " พร้อมระบบจัดบิลสเต็ป (6, 9, 13, 20 คู่) อัตโนมัติ"
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
# 2. DATA FETCHING & CALCULATIONS
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


def devig_odds(odds_1, odds_2):
  imp1, imp2 = 1.0 / odds_1, 1.0 / odds_2
  tot = imp1 + imp2
  return imp1 / tot, imp2 / tot


def get_team_7matches_stats(df_team, team_name):
  if df_team is None or len(df_team) == 0:
    return "ไม่มีข้อมูลสถิติ", 0, 0, 0, 0, 0

  w, d, l, gf, ga = 0, 0, 0, 0, 0
  for _, r in df_team.iterrows():
    if r["HomeTeam"] == team_name:
      hg, ag = r["FTHG"], r["FTAG"]
    else:
      hg, ag = r["FTAG"], r["FTHG"]

    gf += hg
    ga += ag
    if hg > ag:
      w += 1
    elif hg == ag:
      d += 1
    else:
      l += 1

  return f"ชนะ {w} เสมอ {d} แพ้ {l} | ยิง {gf} เสีย {ga}", w, d, l, gf, ga


def get_h2h_stats(df, home_team, away_team):
  if df is None or df.empty:
    return "ไม่มีข้อมูล H2H ย้อนหลัง"

  h2h_df = df[
      ((df["HomeTeam"] == home_team) & (df["AwayTeam"] == away_team))
      | ((df["HomeTeam"] == away_team) & (df["AwayTeam"] == home_team))
  ].tail(7)

  if h2h_df.empty:
    return "ไม่เคยพบกันใน 2 ฤดูกาลล่าสุด"

  h_w, dr, a_w = 0, 0, 0
  for _, r in h2h_df.iterrows():
    if r["FTHG"] == r["FTAG"]:
      dr += 1
    elif (r["HomeTeam"] == home_team and r["FTHG"] > r["FTAG"]) or (
        r["HomeTeam"] == away_team and r["FTAG"] > r["FTHG"]
    ):
      h_w += 1
    else:
      a_w += 1

  return (
      f"พบกัน {len(h2h_df)} นัดล่าสุด: {home_team} ชนะ {h_w} | เสมอ {dr} |"
      f" {away_team} ชนะ {a_w}"
  )


def calculate_advanced_metrics(df, home_api, away_api, match_date):
  if df is None or df.empty:
    return (
        None,
        None,
        None,
        None,
        "พักปกติ (7 วัน)",
        "พักปกติ (7 วัน)",
        "ไม่มีข้อมูล",
        "ไม่มีข้อมูล",
        "ไม่มีข้อมูล H2H",
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

  h_7matches_str, _, _, _, _, _ = get_team_7matches_stats(h_df, home_team)
  a_7matches_str, _, _, _, _, _ = get_team_7matches_stats(a_df, away_team)
  h2h_str = get_h2h_stats(df, home_team, away_team)

  if len(h_df) < 3 or len(a_df) < 3:
    return (
        None,
        None,
        None,
        None,
        f"พัก {h_rest} วัน",
        f"พัก {a_rest} วัน",
        h_7matches_str,
        a_7matches_str,
        h2h_str,
        40,
    )

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

  score = 80 if (h_rest > 3 and a_rest > 3) else 65

  return (
      h_net_form,
      a_net_form,
      h_xg,
      a_xg,
      h_info,
      a_info,
      h_7matches_str,
      a_7matches_str,
      h2h_str,
      score,
  )


# ==============================================================================
# 3. SCANNER CORE LOGIC
# ==============================================================================
def scan_league(league_info):
  csv_df = fetch_csv_stats(league_info["csv"])
  url = f"https://api.the-odds-api.com/v4/sports/{league_info['key']}/odds/"
  params = {
      "apiKey": API_KEY,
      "regions": "eu",
      "markets": "h2h,spreads,totals",
      "oddsFormat": "decimal",
  }

  try:
    res = requests.get(url, params=params)
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

    h2h_mkt = next(
        (
            k
            for bm in bookmakers
            for k in bm.get("markets", [])
            if k["key"] == "h2h"
        ),
        None,
    )
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
        h_7m_str,
        a_7m_str,
        h2h_str,
        base_score,
    ) = calculate_advanced_metrics(csv_df, home, away, match_dt)

    # 1. ราคา 1X2
    odds_1, odds_x, odds_2 = "N/A", "N/A", "N/A"
    if h2h_mkt:
      for o in h2h_mkt.get("outcomes", []):
        if o["name"] == home:
          odds_1 = o["price"]
        elif o["name"] == away:
          odds_2 = o["price"]
        elif o["name"] == "Draw":
          odds_x = o["price"]

    # 2. Asian Handicap
    hdc_label, hdc_odds, ev_hdc = "รอเปิด", 1.0, -999.0
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)

      if h_obj and a_obj:
        h_line, a_line = h_obj.get("point", 0.0), a_obj.get("point", 0.0)
        h_p, a_p = h_obj["price"], a_obj["price"]
        mkt_p_h, mkt_p_a = devig_odds(h_p, a_p)

        if h_net_form is not None and a_net_form is not None:
          edge = (h_net_form - a_net_form) + h_line
          p_shift = (norm_cdf(edge / 1.1) - 0.5) * 0.35
          final_p_h = max(min(mkt_p_h + p_shift, 0.85), 0.15)
          final_p_a = 1.0 - final_p_h
        else:
          final_p_h, final_p_a = mkt_p_h, mkt_p_a

        ev_h = round(((final_p_h * h_p) - 1.0) * 100, 2)
        ev_a = round(((final_p_a * a_p) - 1.0) * 100, 2)

        if ev_h >= ev_a:
          ev_hdc = ev_h
          hdc_label = (
              f"ต่อ {home} ({h_line})"
              if h_line < 0
              else (
                  f"รอง {home} (+{h_line})"
                  if h_line > 0
                  else f"เสมอ {home} (0.0)"
              )
          )
          hdc_odds = h_p
        else:
          ev_hdc = ev_a
          hdc_label = (
              f"ต่อ {away} ({a_line})"
              if a_line < 0
              else (
                  f"รอง {away} (+{a_line})"
                  if a_line > 0
                  else f"เสมอ {away} (0.0)"
              )
          )
          hdc_odds = a_p

    # 3. สูง/ต่ำ
    tot_label, tot_odds, ev_tot = "รอเปิด", 1.0, -999.0
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)

      if over and under:
        line_val = over.get("point", 2.5)
        o_p, u_p = over["price"], under["price"]
        mkt_p_o, mkt_p_u = devig_odds(o_p, u_p)

        if h_xg is not None and a_xg is not None:
          total_xg = h_xg + a_xg
          edge = total_xg - line_val
          p_shift = (norm_cdf(edge / 1.0) - 0.5) * 0.35
          final_p_o = max(min(mkt_p_o + p_shift, 0.85), 0.15)
          final_p_u = 1.0 - final_p_o
        else:
          final_p_o, final_p_u = mkt_p_o, mkt_p_u

        ev_o = round(((final_p_o * o_p) - 1.0) * 100, 2)
        ev_u = round(((final_p_u * u_p) - 1.0) * 100, 2)

        if ev_o >= ev_u:
          ev_tot = ev_o
          tot_label = f"สูงกว่า {line_val}"
          tot_odds = o_p
        else:
          ev_tot = ev_u
          tot_label = f"ต่ำกว่า {line_val}"
          tot_odds = u_p

    # กำหนดตัวเลือกและคำแนะนำที่ดีที่สุดสำหรับจัดบิล
    is_value = (ev_hdc >= 2.0) or (ev_tot >= 2.0)
    if ev_hdc >= ev_tot:
      best_label, best_odds, max_ev = hdc_label, hdc_odds, ev_hdc
    else:
      best_label, best_odds, max_ev = tot_label, tot_odds, ev_tot

    if "🎯" in best_label or max_ev >= 2.0:
      rec_str = f"🎯 แนะนำ: {best_label} @ {best_odds} (EV: +{max_ev}%)"
    else:
      rec_str = "➖ ไม่มีความได้เปรียบ ค่าน้ำไม่คุ้มเสี่ยง"

    results.append({
        "time": match_time,
        "league": league_info["name"],
        "match": f"{home} vs {away}",
        "home": home,
        "away": away,
        "h_7m": h_7m_str,
        "a_7m": a_7m_str,
        "h2h": h2h_str,
        "h_info": h_info,
        "a_info": a_info,
        "xg": f"{round(h_xg + a_xg, 2)} ลูก" if h_xg else "N/A",
        "odds_1": odds_1,
        "odds_x": odds_x,
        "odds_2": odds_2,
        "hdc_label": hdc_label,
        "hdc_odds": hdc_odds,
        "tot_label": tot_label,
        "tot_odds": tot_odds,
        "rec": rec_str,
        "best_pick_label": best_label,
        "best_pick_odds": best_odds,
        "is_value": is_value,
        "max_ev": max_ev,
        "data_score": min(base_score, 100),
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

scan_btn = st.sidebar.button(
    "🚀 เริ่มสแกนบอล Pro + จัด 4 บิลสเต็ป", type="primary"
)

if scan_btn:
  if not selected:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
  else:
    with st.spinner(
        "กำลังสแกนสถิติ Real-time ค่าน้ำ และจัดชุดสเต็ปอัตโนมัติ..."
    ):
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
            f"วิเคราะห์เรียบร้อยพบทั้งหมด {len(df)} คู่ที่เตะภายใน 36 ชม."
        )

        tab1, tab2 = st.tabs(
            ["📱 การ์ดวิเคราะห์รายแมตช์", "🎫 ระบบจัดบิลสเต็ป 4 ระดับ"]
        )

        # TAB 1: MATCH CARD UI
        with tab1:
          for _, match in df.iterrows():
            with st.container():
              st.markdown(
                  f"### ⚽ {match['home']} vs {match['away']}"
                  f" ({match['time']})"
              )
              st.caption(f"🏆 รายการ: **{match['league']}**")

              col1, col2 = st.columns(2)

              with col1:
                st.markdown("#### 📈 สถิติ 7 นัดล่าสุด & H2H")
                st.write(f"🏠 **{match['home']}:** {match['h_7m']}")
                st.write(f"✈️ **{match['away']}:** {match['a_7m']}")
                st.info(f"🤝 **ประวัติการพบกัน:**\n{match['h2h']}")
                st.write(
                    f"⚡ **ความสด:** {match['h_info']} | {match['a_info']}"
                )

              with col2:
                st.markdown("#### 💰 ตารางราคาและค่าน้ำ (Odds)")

                o1, ox, o2 = st.columns(3)
                o1.metric(f"1 ({match['home']})", match["odds_1"])
                ox.metric("X (เสมอ)", match["odds_x"])
                o2.metric(f"2 ({match['away']})", match["odds_2"])

                st.write(
                    f"⚖️ **ราคาแฮนดิแคปแนะนำ:** {match['hdc_label']} @"
                    f" {match['hdc_odds']}"
                )
                st.write(
                    f"⚽ **ราคาสูง/ต่ำแนะนำ:** {match['tot_label']} @"
                    f" {match['tot_odds']}"
                )

                if "🎯" in match["rec"]:
                  st.success(f"**{match['rec']}**")
                else:
                  st.warning(f"**{match['rec']}**")

              st.markdown("---")

        # TAB 2: SLIP GENERATOR (6, 9, 13, 20 MATCHES)
        with tab2:
          st.subheader("🎯 บิลสเต็ปแนะนำ 4 ระดับ (คัดจากคู่ที่ +EV สูงสุด)")

          candidates = df[df["is_value"] == True].sort_values(
              by=["max_ev", "data_score"], ascending=[False, False]
          )

          if len(candidates) < 6:
            st.warning(
                f"พบคู่ที่มีค่า +EV เพียง {len(candidates)} คู่ (ต้องการอย่างน้อย"
                " 6 คู่เพื่อจัดบิลสเต็ป)"
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

            col_a, col_b = st.columns(2)

            with col_a:
              st.markdown("#### 🟢 บิลที่ 1: สเต็ป 6 คู่ (บิลหลัก)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.50)} บาท")
              st.dataframe(
                  slip_6[
                      [
                          "time",
                          "match",
                          "best_pick_label",
                          "best_pick_odds",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "time": "เวลาเตะ",
                          "match": "คู่แข่งขัน",
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
                          "time",
                          "match",
                          "best_pick_label",
                          "best_pick_odds",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "time": "เวลาเตะ",
                          "match": "คู่แข่งขัน",
                          "best_pick_label": "ตัวเลือกแนะนำ",
                          "best_pick_odds": "ค่าน้ำ",
                          "max_ev": "EV (%)",
                      }
                  ),
                  hide_index=True,
                  use_container_width=True,
              )

            with col_b:
              st.markdown("#### 🟡 บิลที่ 3: สเต็ป 13 คู่ (บิลโบนัส)")
              st.caption(f"งบแนะนำ: {round(budget_input * 0.15)} บาท")
              st.dataframe(
                  slip_13[
                      [
                          "time",
                          "match",
                          "best_pick_label",
                          "best_pick_odds",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "time": "เวลาเตะ",
                          "match": "คู่แข่งขัน",
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
                          "time",
                          "match",
                          "best_pick_label",
                          "best_pick_odds",
                          "max_ev",
                      ]
                  ].rename(
                      columns={
                          "time": "เวลาเตะ",
                          "match": "คู่แข่งขัน",
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
