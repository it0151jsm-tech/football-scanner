import math
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (MOBILE OPTIMIZED)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - Mobile Master", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (Mobile Edition)</h3>",
    unsafe_allow_html=True,
)
st.caption(
    "💡 คำแนะนำ: เลือกเฉพาะลีกที่ต้องการแทงครั้งละ 1-2 ลีกเพื่อประหยัดโควตา API"
)

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DYNAMIC LEAGUES & STATS FETCHING (UPDATED FOR 2026/27)
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
  }
  leagues = {}
  try:
    res = requests.get(
        f"https://api.the-odds-api.com/v4/sports/?apiKey={API_KEY}"
    )
    if res.status_code == 200:
      for item in res.json():
        if item.get("group") == "Soccer" and item.get("active"):
          k, title = item["key"], item["title"]
          leagues[k] = {"name": title, "key": k, "csv": csv_map.get(k)}
  except Exception:
    pass
  if not leagues:
    leagues = {
        "soccer_epl": {
            "name": "Premier League - England",
            "key": "soccer_epl",
            "csv": "E0",
        }
    }
  return leagues


@st.cache_data(ttl=3600)
def fetch_csv_stats(csv_code):
  if not csv_code:
    return None
  # อัปเดตปีปัจจุบัน 2026 รองรับฤดูกาล 2627 และ 2526
  for season in ["2627", "2526"]:
    try:
      df = pd.read_csv(
          f"https://www.football-data.co.uk/mmz4281/{season}/{csv_code}.csv"
      )
      if "HomeTeam" in df.columns:
        df["Date_dt"] = pd.to_datetime(
            df["Date"], format="%d/%m/%Y", errors="coerce"
        )
        return df[["Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
    except Exception:
      pass
  return None


# ==============================================================================
# 3. MATH & BOOKMAKER ENGINE FUNCTIONS
# ==============================================================================
def norm_cdf(x):
  return (1.0 + math.erf(x / math.sqrt(2.0))) / 2.0


def devig_odds(o1, o2):
  i1, i2 = 1.0 / o1, 1.0 / o2
  tot = i1 + i2
  return i1 / tot, i2 / tot


def calculate_ev(prob, odds):
  if odds <= 1.0 or prob <= 0:
    return -100.0
  return round(((prob * odds) - 1.0) * 100, 2)


def get_team_stats(df_team, team):
  if df_team is None or df_team.empty:
    return "ไม่มีข้อมูลสถิติ", 0, 0, 0, 0, 0
  w, d, l, gf, ga = 0, 0, 0, 0, 0
  for _, r in df_team.iterrows():
    hg, ag = (
        (r["FTHG"], r["FTAG"])
        if r["HomeTeam"] == team
        else (r["FTAG"], r["FTHG"])
    )
    gf, ga = gf + hg, ga + ag
    if hg > ag:
      w += 1
    elif hg == ag:
      d += 1
    else:
      l += 1
  return f"ชนะ {w} เสมอ {d} แพ้ {l} (ยิง {gf} / เสีย {ga})", w, d, l, gf, ga


def get_h2h(df, home, away):
  if df is None or df.empty:
    return "ไม่มีข้อมูล H2H ย้อนหลัง"
  sub = df[
      ((df["HomeTeam"] == home) & (df["AwayTeam"] == away))
      | ((df["HomeTeam"] == away) & (df["AwayTeam"] == home))
  ].tail(5)
  if sub.empty:
    return "ไม่พบประวัติพบกันล่าสุด"
  hw, dr, aw = 0, 0, 0
  for _, r in sub.iterrows():
    if r["FTHG"] == r["FTAG"]:
      dr += 1
    elif (r["HomeTeam"] == home and r["FTHG"] > r["FTAG"]) or (
        r["HomeTeam"] == away and r["FTAG"] > r["FTHG"]
    ):
      hw += 1
    else:
      aw += 1
  return (
      f"H2H ({len(sub)} นัดล่าสุด): {home} ชนะ {hw} | เสมอ {dr} | {away}"
      f" ชนะ {aw}"
  )


def analyze_match(df, h_api, a_api, m_date):
  if df is None or df.empty:
    return (
        None,
        None,
        None,
        None,
        "พัก 7 วัน",
        "พัก 7 วัน",
        "ไม่มีข้อมูล",
        "ไม่มีข้อมูล",
        "ไม่มีข้อมูล",
        30,
    )
  teams = list(set(df["HomeTeam"]).union(set(df["AwayTeam"])))
  h_t = get_close_matches(h_api, teams, n=1, cutoff=0.35)
  a_t = get_close_matches(a_api, teams, n=1, cutoff=0.35)
  home_t = h_t[0] if h_t else h_api
  away_t = a_t[0] if a_t else a_api

  def rest_days(t):
    past = df[(df["HomeTeam"] == t) | (df["AwayTeam"] == t)]
    past = past[past["Date_dt"] < m_date].sort_values("Date_dt")
    if past.empty:
      return 7
    d = (m_date - past.iloc[-1]["Date_dt"]).days
    return d if 1 <= d <= 14 else 7

  hr, ar = rest_days(home_t), rest_days(away_t)
  h_df = df[(df["HomeTeam"] == home_t) | (df["AwayTeam"] == home_t)].tail(7)
  a_df = df[(df["HomeTeam"] == away_t) | (df["AwayTeam"] == away_t)].tail(7)

  h_str, _, _, _, _, _ = get_team_stats(h_df, home_t)
  a_str, _, _, _, _, _ = get_team_stats(a_df, away_t)
  h2h_str = get_h2h(df, home_t, away_t)

  if len(h_df) < 3 or len(a_df) < 3:
    return (
        None,
        None,
        None,
        None,
        f"พัก {hr} วัน",
        f"พัก {ar} วัน",
        h_str,
        a_str,
        h2h_str,
        40,
    )

  h_att = pd.Series([
      r["FTHG"] if r["HomeTeam"] == home_t else r["FTAG"]
      for _, r in h_df.iterrows()
  ]).mean()
  h_def = pd.Series([
      r["FTAG"] if r["HomeTeam"] == home_t else r["FTHG"]
      for _, r in h_df.iterrows()
  ]).mean()
  a_att = pd.Series([
      r["FTHG"] if r["HomeTeam"] == away_t else r["FTAG"]
      for _, r in a_df.iterrows()
  ]).mean()
  a_def = pd.Series([
      r["FTAG"] if r["HomeTeam"] == away_t else r["FTHG"]
      for _, r in a_df.iterrows()
  ]).mean()

  fat_h = 0.9 if hr <= 3 else (1.04 if hr >= 6 else 1.0)
  fat_a = 0.9 if ar <= 3 else (1.04 if ar >= 6 else 1.0)

  h_net = round((h_att - h_def) * fat_h, 2)
  a_net = round((a_att - a_def) * fat_a, 2)
  h_xg = round(h_att * fat_h, 2)
  a_xg = round(a_att * fat_a, 2)

  return (
      h_net,
      a_net,
      h_xg,
      a_xg,
      f"พัก {hr} วัน {'⚠️เตะถี่' if hr <= 3 else '✅ฟิต'}",
      f"พัก {ar} วัน {'⚠️เตะถี่' if ar <= 3 else '✅ฟิต'}",
      h_str,
      a_str,
      h2h_str,
      75,
  )


# ==============================================================================
# 4. SCANNER CORE
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
    h2h_mkt = next(
        (
            k
            for bm in bm_list
            for k in bm.get("markets", [])
            if k["key"] == "h2h"
        ),
        None,
    )
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

    h_net, a_net, h_xg, a_xg, h_inf, a_inf, h_7s, a_7s, h2hs, b_score = (
        analyze_match(csv_df, home, away, m_dt)
    )

    o1, ox, o2 = "N/A", "N/A", "N/A"
    if h2h_mkt:
      for o in h2h_mkt.get("outcomes", []):
        if o["name"] == home:
          o1 = o["price"]
        elif o["name"] == away:
          o2 = o["price"]
        elif o["name"] == "Draw":
          ox = o["price"]

    # Handicap EV
    hdc_label, hdc_odds, ev_hdc = "รอเปิด", 1.0, -999.0
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]
        mp_h, mp_a = devig_odds(hp, ap)
        if h_net is not None and a_net is not None:
          edge = (h_net - a_net) + line
          p_shift = (norm_cdf(edge / 1.1) - 0.5) * 0.35
          fp_h = max(min(mp_h + p_shift, 0.85), 0.15)
          fp_a = 1.0 - fp_h
        else:
          fp_h, fp_a = mp_h, mp_a
        ev_h, ev_a = calculate_ev(fp_h, hp), calculate_ev(fp_a, ap)
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

    # Totals EV
    tot_label, tot_odds, ev_tot = "รอเปิด", 1.0, -999.0
    if tot_mkt:
      outcomes = tot_mkt.get("outcomes", [])
      over = next((o for o in outcomes if o["name"] == "Over"), None)
      under = next((o for o in outcomes if o["name"] == "Under"), None)
      if over and under:
        t_line = over.get("point", 2.5)
        op, up = over["price"], under["price"]
        mp_o, mp_u = devig_odds(op, up)
        if h_xg is not None and a_xg is not None:
          t_edge = (h_xg + a_xg) - t_line
          p_shift = (norm_cdf(t_edge / 1.0) - 0.5) * 0.35
          fp_o = max(min(mp_o + p_shift, 0.85), 0.15)
          fp_u = 1.0 - fp_o
        else:
          fp_o, fp_u = mp_o, mp_u
        ev_o, ev_u = calculate_ev(fp_o, op), calculate_ev(fp_u, up)
        if ev_o >= ev_u:
          ev_tot, tot_odds, tot_label = ev_o, op, f"สูงกว่า {t_line}"
        else:
          ev_tot, tot_odds, tot_label = ev_u, up, f"ต่ำกว่า {t_line}"

    results.append({
        "time": m_time,
        "league": leg_info["name"],
        "match": f"{home} vs {away}",
        "h_7m": h_7s,
        "a_7m": a_7s,
        "h2h": h2hs,
        "h_info": h_inf,
        "a_info": a_inf,
        "odds_1": o1,
        "odds_x": ox,
        "odds_2": o2,
        "hdc_label": hdc_label,
        "hdc_odds": hdc_odds,
        "ev_hdc": ev_hdc,
        "tot_label": tot_label,
        "tot_odds": tot_odds,
        "ev_tot": ev_tot,
        "is_hdc_v": ev_hdc >= 2.0,
        "is_tot_v": ev_tot >= 2.0,
        "is_value": (ev_hdc >= 2.0) or (ev_tot >= 2.0),
    })
  return results


# ==============================================================================
# 5. STREAMLIT UI (MOBILE RESPONSIVE DESIGN)
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("🔍 ตั้งค่าการสแกน")
if "quota_remaining" in st.session_state:
  st.sidebar.metric(
      label="🎫 โควตา API คงเหลือ",
      value=f"{st.session_state['quota_remaining']} ครั้ง",
  )
else:
  st.sidebar.info("🎫 โควตา API: กดสแกนเพื่ออัปเดต")

options = {}
for k, v in active_leagues.items():
  options[k] = v["name"]

selected = st.sidebar.multiselect(
    "เลือกรายการลีก (แนะนำเลือกทีละ 1-2 ลีก)",
    options=list(options.keys()),
    default=["soccer_epl"] if "soccer_epl" in options else list(options.keys())[:1],
    format_func=lambda x: options[x],
)
hours_limit = st.sidebar.selectbox(
    "กรอบเวลาการแข่งขัน", options=[24, 36, 48, 72], index=1
)
only_value = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่มี +EV >= 2.0% เท่านั้น", value=True
)
budget = st.sidebar.number_input(
    "งบลงทุนรวมวันนี้ (บาท)", value=1000, step=100
)

scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล", type="primary")

if scan_btn:
  if not selected:
    st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
  else:
    with st.spinner("กำลังวิเคราะห์ข้อมูลสถิติและค่าน้ำ..."):
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
        st.error("⚠️ โควตา API ของคุณหมดแล้ว (429 Too Many Requests)")
      elif res_all:
        df = pd.DataFrame(res_all)
        if only_value:
          df = df[df["is_value"] == True]

        st.success(
            f"🎯 วิเคราะห์สำเร็จ พบ {len(df)} คู่ (ภายใน {hours_limit} ชม.)"
        )

        tab1, tab2 = st.tabs(["📱 วิเคราะห์รายคู่", "🎫 บิลสเต็ป (มือถือ)"])

        with tab1:
          for _, m in df.iterrows():
            with st.container():
              st.markdown(f"**⚽ {m['match']}**")
              st.caption(f"🕒 {m['time']} | 🏆 {m['league']}")
              st.markdown(f"🏠 **เจ้าบ้าน:** {m['h_7m']} ({m['h_info']})")
              st.markdown(f"✈️ **ทีมเยือน:** {m['a_7m']} ({m['a_info']})")
              st.info(f"🤝 {m['h2h']}")
              st.markdown(
                  f"⚖️ **แฮนดิแคป:** {m['hdc_label']} (ค่าน้ำ: {m['hdc_odds']}"
                  f" | EV: **{m['ev_hdc']}%**)"
              )
              st.markdown(
                  f"⚽ **สูง/ต่ำ:** {m['tot_label']} (ค่าน้ำ: {m['tot_odds']} |"
                  f" EV: **{m['ev_tot']}%**)"
              )
              st.markdown("---")

        with tab2:
          st.subheader("🎯 จัดบิลสเต็ป (1 คู่เลือก 1 ตลาดที่ดีที่สุด)")
          h_c = df[df["is_hdc_v"]].sort_values(by="ev_hdc", ascending=False)
          t_c = df[df["is_tot_v"]].sort_values(by="ev_tot", ascending=False)

          picks = []
          for _, r in h_c.iterrows():
            picks.append({
                "time": r["time"],
                "match": r["match"],
                "pick": r["hdc_label"],
                "odds": r["hdc_odds"],
                "ev": r["ev_hdc"],
            })
          for _, r in t_c.iterrows():
            picks.append({
                "time": r["time"],
                "match": r["match"],
                "pick": r["tot_label"],
                "odds": r["tot_odds"],
                "ev": r["ev_tot"],
            })

          cdf = pd.DataFrame(picks).sort_values(by="ev", ascending=False)
          cdf = cdf.drop_duplicates(subset=["match"]).reset_index(drop=True)

          if len(cdf) < 3:
            st.warning(
                f"พบคู่ +EV เพียง {len(cdf)} คู่ (แนะนำเลือกเพิ่มลีกอื่นเพื่อจัดสเต็ป)"
            )
          else:
            st.markdown("### 💰 Staking Plan (แผนการลงทุน)")
            st.write(f"• **สเต็ป 6 คู่ (50%):** {round(budget * 0.5)} บาท")
            st.write(f"• **สเต็ป 9 คู่ (25%):** {round(budget * 0.25)} บาท")
            st.write(f"• **สเต็ป 13 คู่ (15%):** {round(budget * 0.15)} บาท")
            st.write(f"• **สเต็ป 20 คู่ (10%):** {round(budget * 0.1)} บาท")
            st.markdown("---")

            def render_slip(title, data_sub):
              st.markdown(f"#### {title}")
              if len(data_sub) == 0:
                st.write("- ไม่มีคู่แข่งขันเพียงพอสำหรับบิลนี้ -")
              for _, row in data_sub.iterrows():
                st.markdown(
                    f"✅ **{row['match']}**<br>👉 เลือก: `{row['pick']}` |"
                    f" ค่าน้ำ: `{row['odds']}` | EV: `{row['ev']}%`",
                    unsafe_allow_html=True,
                )
              st.markdown("---")

            render_slip("🟢 บิลหลัก (สเต็ป 6)", cdf.head(6))
            render_slip("🔵 บิลต่อยอด (สเต็ป 9)", cdf.head(9))
            render_slip("🟡 บิลโบนัส (สเต็ป 13)", cdf.head(13))
            render_slip("🔴 บิลแจ็คพอต (สเต็ป 20)", cdf.head(20))
      else:
        st.warning("ไม่พบคู่แข่งขันในกรอบเวลาหรือเงื่อนไขที่เลือก")
