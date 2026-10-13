import math
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

# ==============================================================================
# 1. SETUP & CONFIGURATION (ALL-MARKET DUAL-ENGINE)
# ==============================================================================
st.set_page_config(
    page_title="Value Bet Pro - All-Market VIP", page_icon="⚽", layout="centered"
)

st.markdown(
    "<h3 style='text-align: center;'>⚽ Value Bet Pro (All-Market EV+ & Trap)</h3>",
    unsafe_allow_html=True,
)
st.caption(
    "ระบบ 2 สมองฉบับไร้ขีดจำกัด: แนะนำตัวชัวร์ครบทุกตลาด (ต่อ / รอง / สูง / ต่ำ)"
)

API_KEY = "9b01dce091987a5fc57447a84e05badc"


# ==============================================================================
# 2. DATA FETCHING & STATS ENGINE
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
# 3. ALL-MARKET EV+ & TRAP SCANNER ENGINE
# ==============================================================================
def scan_league(leg_info, hours_limit):
  csv_df = fetch_csv_stats(leg_info["csv"])
  url = f"https://api.the-odds-api.com/v4/sports/{leg_info['key']}/odds/"
  params = {
      "apiKey": API_KEY,
      "regions": "eu",
      "markets": "spreads,totals",  # ดึงทั้งแฮนดิแคป และสูง/ต่ำ
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

    # ดึงราคาตลาด Spreads และ Totals
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

    standings = get_temporal_standings(csv_df, m_dt) if csv_df is not None else {}
    h_data = standings.get(home, {})
    a_data = standings.get(away, {})
    hrank, arank = h_data.get("rank"), a_data.get("rank")
    rank_str = f" [อันดับ {hrank} vs {arank}]" if (hrank and arank) else ""

    # --------------------------------------------------------------------------
    # 1. สมอง EV+ (เปรียบเทียบทุกตลาดเพื่อคัดตัวได้เปรียบสูงสุด)
    # --------------------------------------------------------------------------
    ev_pick, ev_odds, ev_score, ev_note = (
        format_handicap_pick(home, 0),
        1.90,
        70.0,
        "ราคาปกติ",
    )

    candidates = []  # เก็บตัวเลือกจากทุกตลาดเพื่อหาค่า EV สูงสุด

    # 1.1 วิเคราะห์ตลาด Spreads
    if sp_mkt:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]

        if hrank and arank:
          rank_diff = arank - hrank
          true_win_prob = min(max(0.50 + (rank_diff * 0.03), 0.25), 0.85)
          ev_h = (true_win_prob * hp) - 1.0
          ev_a = ((1.0 - true_win_prob) * ap) - 1.0

          if ev_h >= ev_a:
            candidates.append({
                "pick": format_handicap_pick(home, line),
                "odds": hp,
                "score": round(70.0 + (ev_h * 100), 1),
                "note": (
                    f"📈 EV+ (แฮนดิแคป): ได้เปรียบค่าน้ำต่อ"
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
                "score": round(70.0 + (ev_a * 100), 1),
                "note": (
                    f"📈 EV+ (แฮนดิแคป): ได้เปรียบค่าน้ำรอง"
                    f" (+{round(ev_a*100, 1)}%)"
                ),
            })

    # 1.2 วิเคราะห์ตลาด Totals (สูง/ต่ำ 2.5)
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

        if h_data and a_data:
          # ประเมินประตูรวมคาดหวัง (Expected Goals)
          exp_goals = (h_data.get("avg_gf", 1.2) + a_data.get("avg_ga", 1.2)) / 2 + (
              a_data.get("avg_gf", 1.2) + h_data.get("avg_ga", 1.2)
          ) / 2
          prob_over = min(
              max(0.45 + ((exp_goals - t_line) * 0.20), 0.20), 0.80
          )
          prob_under = 1.0 - prob_over

          ev_over = (prob_over * op) - 1.0
          ev_under = (prob_under * up) - 1.0

          if ev_over > ev_under and exp_goals > 2.7:
            candidates.append({
                "pick": f"🔥 สกอร์สูง (Over {t_line})",
                "odds": op,
                "score": round(72.0 + (ev_over * 100), 1),
                "note": (
                    f"⚽ EV+ (สูง/ต่ำ): เกมบุกจัดจ้าน คาดยิงรวม {round(exp_goals,1)} ลูก"
                ),
            })
          elif ev_under > ev_over and exp_goals < 2.3:
            candidates.append({
                "pick": f"🛡️ สกอร์ต่ำ (Under {t_line})",
                "odds": up,
                "score": round(72.0 + (ev_under * 100), 1),
                "note": (
                    f"🛡️ EV+ (สูง/ต่ำ): เกมรับเหนียว คาดยิงรวม {round(exp_goals,1)} ลูก"
                ),
            })

    # คัดเลือกตลาดที่มีค่า EV Score สูงสุดขึ้นเป็นตัวแนะนำชัวร์
    if candidates:
      best = max(candidates, key=lambda x: x["score"])
      ev_pick, ev_odds, ev_score, ev_note = (
          best["pick"],
          best["odds"],
          best["score"],
          best["note"],
      )

    # --------------------------------------------------------------------------
    # 2. สมอง Trap Hunter (ตรวจจับกับดักราคาแฮนดิแคป & สูง/ต่ำ)
    # --------------------------------------------------------------------------
    trap_pick, trap_odds, trap_score, trap_note = (
        format_handicap_pick(home, 0),
        1.90,
        60.0,
        "ราคาปกติ",
    )
    public_teams = [
        "Real Madrid",
        "Barcelona",
        "Manchester United",
        "Liverpool",
        "Arsenal",
        "Bayern Munich",
        "Paris Saint-Germain",
    ]
    is_public = any(pt in home or pt in away for pt in public_teams)

    if sp_mkt and is_public:
      outcomes = sp_mkt.get("outcomes", [])
      h_obj = next((o for o in outcomes if o["name"] == home), None)
      a_obj = next((o for o in outcomes if o["name"] == away), None)
      if h_obj and a_obj:
        line = h_obj.get("point", 0.0)
        hp, ap = h_obj["price"], a_obj["price"]
        if line <= -1.25:
          trap_pick = (
              f"รอง {away} (+{-line})"
              if home in str(public_teams)
              else f"รอง {home} (+{line})"
          )
          trap_odds = ap if home in str(public_teams) else hp
          trap_score = 92.0
          trap_note = "🔥 Public Bias Trap: โต๊ะเปิดต่อแพงบีบทีมมหาชน -> สวนรองได้เปรียบ"
    elif tot_mkt:
      t_outcomes = tot_mkt.get("outcomes", [])
      over_obj = next(
          (o for o in t_outcomes if o["name"].lower() == "over"), None
      )
      if over_obj and over_obj["price"] <= 1.55:
        # ราคาสูงไหลต่ำเกินไป ให้ระวังราคากั๊ก
        trap_pick = f"🛡️ สกอร์ต่ำ (Under {over_obj.get('point', 2.5)})"
        trap_odds = next(
            (o["price"] for o in t_outcomes if o["name"].lower() == "under"),
            1.90,
        )
        trap_score = 85.0
        trap_note = "🔥 Total Trap: โต๊ะเปิดค่าน้ำสูงล่อตา -> สวนต่ำได้เปรียบ"
      else:
        trap_pick = ev_pick
        trap_odds = ev_odds
        trap_score = 65.0
        trap_note = "🛡️ Standard Play: เลือกตลาดตามค่าความคุ้มค่า"

    match_res = check_match_result(csv_df, home, away, m_dt)

    results.append({
        "time": m_time,
        "match_date": m_dt,
        "league": leg_info["name"],
        "match": f"{home} vs {away}{rank_str}",
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
# 4. STREAMLIT UI DASHBOARD
# ==============================================================================
active_leagues = get_active_leagues()

st.sidebar.header("📌 เมนูจัดโพย 2 สมอง (ทุกตลาด)")
menu_choice = st.sidebar.radio(
    "เลือกหน้าการทำงาน",
    ["🔥 จัดบิลสเต็ป VIP (ทุกตลาด)", "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)"],
)

st.sidebar.markdown("---")

if menu_choice == "🔥 จัดบิลสเต็ป VIP (ทุกตลาด)":
  st.sidebar.header("ตั้งค่าการสแกน")
  if "quota_remaining" in st.session_state:
    st.sidebar.metric(
        label="โควตา API คงเหลือ",
        value=f"{st.session_state['quota_remaining']} ครั้ง",
    )

  options = {k: v["name"] for k, v in active_leagues.items()}
  selected = st.sidebar.multiselect(
      "เลือกรายการลีกคืนนี้",
      options=list(options.keys()),
      default=[
          "soccer_uefa_champs_league",
          "soccer_england_championship",
          "soccer_epl",
      ],
      format_func=lambda x: options[x],
  )

  hours_limit = st.sidebar.selectbox("กรอบเวลาการแข่งขัน", options=[24, 48], index=0)
  budget = st.sidebar.number_input(
      "งบลงทุนรวมวันนี้ (บาท)", value=1000, step=100
  )
  st.session_state["budget"] = budget

  scan_btn = st.sidebar.button(
      "🚀 สแกนและจัดบิล (คัดตัวชัวร์ทุกตลาด: ต่อ/รอง/สูง/ต่ำ)", type="primary"
  )

  if scan_btn:
    if not selected:
      st.sidebar.warning("กรุณาเลือกอย่างน้อย 1 ลีก")
    else:
      with st.spinner(
          "กำลังวิเคราะห์ทุกตลาด (ต่อ/รอง/สูง/ต่ำ) เพื่อหาตัวได้เปรียบสูงสุด..."
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
          df = df.drop_duplicates(subset=["match"]).reset_index(drop=True)
          st.session_state["saved_scan_df"] = df
          st.success(f"สแกนสำเร็จ! ประมวลผลสำเร็จ {len(df)} คู่")

  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]

    tab_ev, tab_trap = st.tabs([
        "📈 สมองที่ 1: Best Value Signal (คัดตัวคุ้มสุดจากทุกตลาด)",
        "🎯 สมองที่ 2: Trap Hunter (ดักกับดักราคาต่อ/สูงต่ำ)",
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
                f"**{idx+1}. {row['match']}** (คะแนนความคุ้มค่า:"
                f" `{row['ev_score']}%`)<br>👉 แนะนำ: **`{row['ev_pick']}`** |"
                f" ค่าน้ำ: `{row['ev_odds']}`<br><small"
                f" style='color:lightgreen;'>{row['ev_note']}</small> |"
                f" สถานะ: **{row['result']}**",
                unsafe_allow_html=True,
            )
          else:
            st.markdown(
                f"**{idx+1}. {row['match']}** (คะแนนจับกับดัก:"
                f" `{row['trap_score']}%`)<br>👉 แนะนำ: **`{row['trap_pick']}`** |"
                f" ค่าน้ำ: `{row['trap_odds']}`<br><small"
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
      st.subheader("🎯 โพยสเต็ปสาย Trap Hunter")
      df_trap_sorted = df.sort_values(
          by="trap_score", ascending=False
      ).reset_index(drop=True)
      display_slips(df_trap_sorted, is_trap=True)

  else:
    st.info("💡 กรุณากดปุ่มสแกนที่เมนูด้านข้างเพื่อเริ่มต้นสร้างโพย")

elif menu_choice == "🏆 ตรวจผลบิลย้อนหลัง (ฟรี)":
  st.subheader("🏆 ตรวจผลบิลอัตโนมัติ")
  if (
      "saved_scan_df" in st.session_state
      and not st.session_state["saved_scan_df"].empty
  ):
    df = st.session_state["saved_scan_df"]
    for _, r in df.iterrows():
      st.write(
          f"⚽ **{r['match']}** ➔ แนะนำ: `{r['ev_pick']}` | สรุปผล:"
          f" **{r['result']}**"
      )
  else:
    st.warning("⚠️ ยังไม่มีโพยในระบบ กรุณาสร้างโพยก่อน")
