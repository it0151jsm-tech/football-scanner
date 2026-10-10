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
    page_title="Value Bet Pro - Ultimate Bookmaker Engine",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ ระบบวิเคราะห์บอล Value Bet Pro (Ultimate Edition)")
st.caption(
    "โมเดลออกราคาแฟร์ + ระบบบาลานซ์สัดส่วน + เช็กโควตา API และป้องกันบั๊กสมบูรณ์แบบ"
)

# อัปเดต API Key ใหม่ล่าสุดของคุณที่นี่
API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DYNAMIC LEAGUES & MATH FUNCTIONS
# ==============================================================================
@st.cache_data(ttl=1800)
def get_active_leagues():
  csv_mapping = {
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
  }

  leagues = {}
  url = f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
  try:
    res = requests.get(url)
    if res.status_code == 200:
      for item in res.json():
        if item.get("group") == "Soccer" and item.get("active"):
          key = item["key"]
          title = item["title"]
          csv_code = csv_mapping.get(key, None)
          leagues[key] = {"name": title, "key": key, "csv": csv_code}
  except Exception:
    pass

  if not leagues:
    leagues = {
        "soccer_epl": {
            "name": "Premier League - England",
            "key": "soccer_epl",
            "csv": "E0",
        },
        "soccer_england_championship": {
            "name": "Championship - England",
            "key": "soccer_england_championship",
            "csv": "E1",
        },
    }
  return leagues


def norm_cdf(x):
  return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def devig_odds(odds_1, odds_2):
  imp1, imp2 = 1.0 / odds_1, 1.0 / odds_2
  tot = imp1 + imp2
  return imp1 / tot, imp2 / tot


def calculate_ev(prob, odds):
  if odds <= 1.0 or prob <= 0:
    return -100.0
  return round(((prob * odds) - 1.0) * 100, 2)


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
def scan_league(league_info, hours_limit):
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
    commence = datetime.fromisoformat(m["commence_time"].replace("Z", "+00:00"))
    if not (now - timedelta(hours=3) <= commence <= target_time):
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

    odds_1, odds_x, odds_2 = "N/A", "N/A", "N/A"
    if h2h_mkt:
      for o in h2h_mkt.get("outcomes", []):
        if o["name"] == home:
          odds_1 = o["price"]
        elif o["name"] == away:
          odds_2 = o["price"]
        elif o["name"] == "Draw":
          odds_x = o["price"]

    # 1. Asian Handicap
    hdc_label, hdc_odds, ev_hdc = "รอเปิด", 1.0, -999.0
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)

      if h_obj and a_obj:
        mkt_hdc_line = h_obj.get("point", 0.0)
        h_p, a_p = h_obj["price"], a_obj["price"]
        mkt_p_h, mkt_p_a = devig_odds(h_p, a_p)

        if h_net_form is not None and a_net_form is not None:
          edge = (h_net_form - a_net_form) + mkt_hdc_line
          p_shift = (norm_cdf(edge / 1.1) - 0.5) * 0.35
          final_p_h = max(min(mkt_p_h + p_shift, 0.85), 0.15)
          final_p_a = 1.0 - final_p_h
        else:
          final_p_h, final_p_a = mkt_p_h, mkt_p_a

        ev_h = calculate_ev(final_p_h, h_p)
        ev_a = calculate_ev(final_p_a, a_p)

        if ev_h >= ev_a:
          ev_hdc = ev_h
          hdc_label = (
              f"ต่อ {home} ({mkt_hdc_line})"
              if mkt_hdc_line < 0
              else (
                  f"รอง {home} (+{mkt_hdc_line})"
                  if mkt_hdc_line > 0
                  else f"เสมอ {home} (0.0)"
              )
          )
          hdc_odds = h_p
        else:
          ev_hdc = ev_a
          a_line = a_obj.get("point", 0.0)
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

    # 2. Totals
    tot_label, tot_odds, ev_tot = "รอเปิด", 1.0, -999.0
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)

      if over and under:
        mkt_tot_line = over.get("point", 2.5)
        o_p, u_p = over["price"], under["price"]
        mkt_p_o, mkt_p_u = devig_odds(o_p, u_p)

        if h_xg is not None and a_xg is not None:
          total_xg = h_xg + a_xg
          edge = total_xg - mkt_tot_line
          p_shift = (norm_cdf(edge / 1.0) - 0.5) * 0.35
          final_p_o = max(min(mkt_p_o + p_shift, 0.85), 0.15)
          final_p_u = 1.0 - final_p_o
        else:
          final_p_o, final_p_u = mkt_p_o, mkt_p_u

        ev_o = calculate_ev(final_p_o, o_p)
        ev_u = calculate_ev(final_p_u, u_p)

        if ev_o >= ev_u:
          ev_tot = ev_o
          tot_label = f"สูงกว่า {mkt_tot_line}"
          tot_odds = o_p
        else:
          ev_tot = ev_u
          tot_label = f"ต่ำกว่า {mkt_tot_line}"
          tot_odds = u_p

    is_hdc_v = ev_hdc >= 2.0
    is_tot_v = ev_tot >= 2.0

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
        "odds_1": odds_1,
        "odds_x": odds_x,
        "odds_2": odds_2,
        "hdc_label": hdc_label,
        "hdc_odds": hdc_odds,
        "ev_hdc": ev_hdc,
        "tot_label": tot_label,
        "tot_odds": tot_odds,
        "ev_tot": ev_tot,
        "is_hdc_v": is_hdc_v,
        "is_tot_v": is_tot_v,
        "is_value": is_hdc_v or is_tot_v,
        "data_score": min(base_score, 100),
    })

  return results


# ==============================================================================
# 4. USER INTERFACE (STREAMLIT)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("🔍 ตัวเลือกระบบสแกน")

if "quota_remaining" in st.session_state:
  st.sidebar.metric(
      label="🎫 โควตา API คงเหลือ",
      value=f"{st.session_state['quota_remaining']} ครั้ง",
  )
else:
  st.sidebar.info("🎫 โควตา API: กดปุ่มสแกนด้านล่างเพื่อเช็กสถานะ")

options = {"all": f"🔥 ทุกลีกทั้งหมด ({len(active_leagues)} รายการ)"}
for k, v in active_leagues.items():
  options[k] = v["name"]

safe_default = ["all"] if "all" in options else list(options.keys())[:1]

selected = st.sidebar.multiselect(
    "เลือกรายการแข่งขัน",
    options=list(options.keys()),
    default=safe_default,
    format_func=lambda x: options[x],
)

hours_limit = st.sidebar.selectbox(
    "กรอบเวลาการแข่งขัน",
    options=[36, 48, 72, 120],
    index=0,
    format_func=lambda x: (
        f"ภายใน {x} ชั่วโมง (แนะนำ)" if x == 36 else f"ภายใน {x} ชั่วโมง"
    ),
)

only_value = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่มีตลาดน่าลงทุน (+EV >= 2.0%)", value=False
)
budget_input = st.sidebar.number_input(
    "งบประมาณลงทุนรวมวันนี้ (บาท)", value=1000, step=100
)

scan_btn = st.sidebar.button(
    "🚀 เริ่มสแกนบอล + จัดสเต็ปบาลานซ์", type="primary"
)

if scan_btn:
  if not selected:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
  else:
    with st.spinner(
        f"กำลังสแกนรายการแข่งขันเตะภายใน {hours_limit} ชั่วโมง..."
    ):
      results = []
      target_leagues = (
          list(active_leagues.keys()) if "all" in selected else selected
      )
      quota_hit = False

      for leg_key in target_leagues:
        if leg_key in active_leagues:
          res = scan_league(active_leagues[leg_key], hours_limit)
          if res == "QUOTA_EXCEEDED":
            quota_hit = True
            break
          elif isinstance(res, list):
            results.extend(res)

      if quota_hit:
        st.error(
            "⚠️ โควตา API ของคุณหมดแล้ว (429 Too Many Requests)"
            " กรุณารอรีเซ็ตโควตาในเดือนถัดไป"
        )
      else:
        if results:
          df = pd.DataFrame(results)
          if only_value:
            df = df[df["is_value"] == True]

          st.success(
              f"ประมวลผลเสร็จสิ้นพบทั้งหมด {len(df)} คู่ที่เตะภายใน {hours_limit}"
              " ชม."
          )

          tab1, tab2 = st.tabs(
              ["📱 การ์ดวิเคราะห์รายแมตช์", "🎫 จัดบิลสเต็ปบาลานซ์ 4 บิล"]
          )

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

                with col2:
                  st.markdown("#### 💰 ตารางราคาและค่าน้ำ")
                  st.write(
                      f"⚖️ **ต่อรอง:** {match['hdc_label']} @"
                      f" {match['hdc_odds']} (EV: {match['ev_hdc']}%)"
                  )
                  st.write(
                      f"⚽ **สูง/ต่ำ:** {match['tot_label']} @"
                      f" {match['tot_odds']} (EV: {match['ev_tot']}%)"
                  )

                st.markdown("---")

          with tab2:
            st.subheader(
                "🎯 บิลสเต็ปจัดบาลานซ์ (ผสม แฮนดิแคป 50% + สูง/ต่ำ 50%)"
            )

            hdc_cand = df[df["is_hdc_v"] == True].sort_values(
                by="ev_hdc", ascending=False
            )
            tot_cand = df[df["is_tot_v"] == True].sort_values(
                by="ev_tot", ascending=False
            )

            combined_picks = []
            for _, r in hdc_cand.iterrows():
              combined_picks.append({
                  "time": r["time"],
                  "match": r["match"],
                  "pick": r["hdc_label"],
                  "odds": r["hdc_odds"],
                  "ev": r["ev_hdc"],
                  "type": "HDC",
              })
            for _, r in tot_cand.iterrows():
              combined_picks.append({
                  "time": r["time"],
                  "match": r["match"],
                  "pick": r["tot_label"],
                  "odds": r["tot_odds"],
                  "ev": r["ev_tot"],
                  "type": "Totals",
              })

            cdf = pd.DataFrame(combined_picks).sort_values(
                by="ev", ascending=False
            )

            if len(cdf) < 6:
              st.warning(
                  f"พบคู่ที่มี +EV เพียง {len(cdf)} ตัวเลือก (ต้องการอย่างน้อย"
                  " 6 ตัวเลือกเพื่อจัดบิล)"
              )
            else:
              top20 = cdf.head(20).reset_index(drop=True)

              slip_6 = top20.head(6)
              slip_9 = top20.head(min(9, len(top20)))
              slip_13 = top20.head(min(13, len(top20)))
              slip_20 = top20.head(min(20, len(top20)))

              st.markdown("### 💰 การแบ่งเงินลงทุน (Staking Plan)")
              stake_data = [
                  {
                      "บิล": "บิลที่ 1 (สเต็ป 6 - บิลหลัก)",
                      "สัดส่วน": "50%",
                      "เงินลงทุน (บาท)": round(budget_input * 0.50),
                      "เป้าหมาย": "บิลหลักเน้นทำกำไร/คืนทุน",
                  },
                  {
                      "บิล": "บิลที่ 2 (สเต็ป 9 - บิลต่อยอด)",
                      "สัดส่วน": "25%",
                      "เงินลงทุน (บาท)": round(budget_input * 0.25),
                      "เป้าหมาย": "บิลต่อยอดกำไร",
                  },
                  {
                      "บิล": "บิลที่ 3 (สเต็ป 13 - บิลโบนัส)",
                      "สัดส่วน": "15%",
                      "เงินลงทุน (บาท)": round(budget_input * 0.15),
                      "เป้าหมาย": "บิลลุ้นโบนัสค่าน้ำสูง",
                  },
                  {
                      "บิล": "บิลที่ 4 (สเต็ป 20 - บิลแจ็คพอต)",
                      "สัดส่วน": "10%",
                      "เงินลงทุน (บาท)": round(budget_input * 0.10),
                      "เป้าหมาย": "บิลแจ็คพอต (ขำๆ)",
                  },
              ]
              st.table(pd.DataFrame(stake_data))
              st.markdown("---")

              col_a, col_b = st.columns(2)
              with col_a:
                st.markdown("#### 🟢 บิลที่ 1: สเต็ป 6 คู่ (บิลหลัก)")
                st.dataframe(
                    slip_6.rename(
                        columns={
                            "time": "เวลาเตะ",
                            "match": "คู่แข่งขัน",
                            "pick": "ตัวเลือกแนะนำ",
                            "odds": "ค่าน้ำ",
                            "ev": "EV (%)",
                            "type": "ประเภทตลาด",
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

                st.markdown("#### 🔵 บิลที่ 2: สเต็ป 9 คู่ (บิลต่อยอด)")
                st.dataframe(
                    slip_9.rename(
                        columns={
                            "time": "เวลาเตะ",
                            "match": "คู่แข่งขัน",
                            "pick": "ตัวเลือกแนะนำ",
                            "odds": "ค่าน้ำ",
                            "ev": "EV (%)",
                            "type": "ประเภทตลาด",
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

              with col_b:
                st.markdown("#### 🟡 บิลที่ 3: สเต็ป 13 คู่ (บิลโบนัส)")
                st.dataframe(
                    slip_13.rename(
                        columns={
                            "time": "เวลาเตะ",
                            "match": "คู่แข่งขัน",
                            "pick": "ตัวเลือกแนะนำ",
                            "odds": "ค่าน้ำ",
                            "ev": "EV (%)",
                            "type": "ประเภทตลาด",
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

                st.markdown("#### 🔴 บิลที่ 4: สเต็ป 20 คู่ (บิลแจ็คพอต)")
                st.dataframe(
                    slip_20.rename(
                        columns={
                            "time": "เวลาเตะ",
                            "match": "คู่แข่งขัน",
                            "pick": "ตัวเลือกแนะนำ",
                            "odds": "ค่าน้ำ",
                            "ev": "EV (%)",
                            "type": "ประเภทตลาด",
                        }
                    ),
                    hide_index=True,
                    use_container_width=True,
                )

        else:
          st.warning(
              f"ไม่พบคู่แข่งขันที่เตะภายใน {hours_limit} ชั่วโมงในลีกที่เลือก"
          )
