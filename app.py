import math
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import streamlit as st

st.set_page_config(
    page_title="Value Bet Scanner Ultimate", page_icon="⚽", layout="wide"
)

st.title("⚽ ระบบสแกนบอล Value Bet Ultimate (ทุกลีก + บอลถ้วย)")
st.markdown(
    "ดึงรายการแข่งขันฟุตบอลและบอลถ้วยทุกลีกทั่วโลกจาก API แบบ Real-time พร้อมวิเคราะห์ **xG / Fair Odds + Kelly Criterion**"
)

API_KEY = "95a50f0403619f536aa4c3fb35dccc41"

# แผนผังรหัส CSV สถิติย้อนหลังสำหรับลีกหลัก (รายการถ้วย/ลีกอื่นจะใช้ Fair Odds Fallback)
CSV_MAPPING = {
    "soccer_epl": "E0",
    "soccer_efl_champ": "E1",
    "soccer_england_league1": "E2",
    "soccer_england_league2": "E3",
    "soccer_spain_la_liga": "SP1",
    "soccer_spain_segunda_division": "SP2",
    "soccer_italy_serie_a": "I1",
    "soccer_italy_serie_b": "I2",
    "soccer_germany_bundesliga": "D1",
    "soccer_germany_bundesliga2": "D2",
    "soccer_france_league1": "F1",
    "soccer_france_league2": "F2",
    "soccer_netherlands_eredivisie": "N1",
    "soccer_belgium_first_div": "B1",
    "soccer_portugal_primeira_liga": "P1",
    "soccer_turkey_super_league": "T1",
    "soccer_greece_super_league": "G1",
    "soccer_spl": "SC0",
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
    """ดึงรายชื่อลีกและบอลถ้วยฟุตบอลทั้งหมดที่กำลังมีการแข่งขันจาก API"""
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
                df = df[["HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
                if len(df) > 0:
                    return df
        except Exception:
            pass

    extra_url = (
        f"https://www.football-data.co.uk/new_league_data/{csv_code}.csv"
    )
    try:
        df = pd.read_csv(extra_url)
        if "Home" in df.columns:
            df = df.rename(
                columns={
                    "Home": "HomeTeam",
                    "Away": "AwayTeam",
                    "HG": "FTHG",
                    "AG": "FTAG",
                }
            )
        df = df[["HomeTeam", "AwayTeam", "FTHG", "FTAG"]].dropna()
        if len(df) > 0:
            return df
    except Exception:
        pass
    return None


def calculate_xg(df, home_team, away_team, last_n=10):
    if df is None or len(df) == 0:
        return None, None
    league_avg = (df["FTHG"].mean() + df["FTAG"].mean()) / 2
    if league_avg == 0:
        return None, None

    h_df = df[df["HomeTeam"] == home_team].tail(last_n)
    a_df = df[df["AwayTeam"] == away_team].tail(last_n)

    if len(h_df) == 0 or len(a_df) == 0:
        return None, None

    h_att = h_df["FTHG"].mean()
    h_def = h_df["FTAG"].mean()
    a_att = a_df["FTAG"].mean()
    a_def = a_df["FTHG"].mean()

    h_xg = round((h_att / league_avg) * (a_def / league_avg) * league_avg, 2)
    a_xg = round((a_att / league_avg) * (h_def / league_avg) * league_avg, 2)
    return h_xg, a_xg


def poisson_prob(lmbda, k):
    return (math.pow(lmbda, k) * math.exp(-lmbda)) / math.factorial(k)


def get_win_probabilities(home_xg, away_xg):
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
        "markets": "h2h",
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
        if not (
            now_utc - timedelta(hours=2) <= commence_time <= next_36h_utc
        ):
            continue

        match_time_th = commence_time.astimezone(tz_th).strftime(
            "%d/%m %H:%M น."
        )
        home = m["home_team"]
        away = m["away_team"]

        if not m.get("bookmakers"):
            continue
        bm = m["bookmakers"][0]
        odds_list = bm["markets"][0]["outcomes"]

        h_odds = next((o["price"] for o in odds_list if o["name"] == home), None)
        d_odds = next(
            (o["price"] for o in odds_list if o["name"] == "Draw"), None
        )
        a_odds = next((o["price"] for o in odds_list if o["name"] == away), None)
        if not h_odds or not a_odds:
            continue

        h_xg, a_xg = calculate_xg(stats_df, home, away)

        if h_xg is not None and a_xg is not None:
            p_home, p_draw, p_away = get_win_probabilities(h_xg, a_xg)
        else:
            if d_odds:
                total_prob = (1 / h_odds) + (1 / d_odds) + (1 / a_odds)
                p_home = (1 / h_odds) / total_prob
                p_away = (1 / a_odds) / total_prob
            else:
                total_prob = (1 / h_odds) + (1 / a_odds)
                p_home = (1 / h_odds) / total_prob
                p_away = (1 / a_odds) / total_prob

        ev_home = round(((p_home * h_odds) - 1) * 100, 2)
        ev_away = round(((p_away * a_odds) - 1) * 100, 2)

        if ev_home >= ev_away:
            best_side, best_odds, best_prob, best_ev = (
                f"เจ้าบ้าน ({home})",
                h_odds,
                round(p_home * 100, 1),
                ev_home,
            )
            kelly_pct = calculate_kelly(p_home, h_odds)
        else:
            best_side, best_odds, best_prob, best_ev = (
                f"ทีมเยือน ({away})",
                a_odds,
                round(p_away * 100, 1),
                ev_away,
            )
            kelly_pct = calculate_kelly(p_away, a_odds)

        recommendation = (
            "🔥 น่าลงทุน (+EV)" if best_ev > 2.0 else "➖ สูสี/ไม่คุ้ม"
        )
        kelly_display = (
            f"{kelly_pct}% ของทุน" if best_ev > 2.0 and kelly_pct > 0 else "0%"
        )

        results.append(
            {
                "เวลาเตะ (ไทย)": match_time_th,
                "รายการ/ลีก": league_name,
                "คู่แข่งขัน": f"{home} vs {away}",
                "ฝั่งที่น่าเล่น": best_side,
                "ค่าน้ำ": best_odds,
                "โอกาสชนะ": f"{best_prob}%",
                "ค่า EV": f"{'+' if best_ev > 0 else ''}{best_ev}%",
                "ทุนแนะนำ (Kelly)": kelly_display,
                "สถานะ": recommendation,
            }
        )
    return results


# โหลดรายการลีกและบอลถ้วยทั้งหมดที่เปิดอยู่ในขณะนั้น
active_leagues = get_active_soccer_leagues()

st.sidebar.header("🔍 ตัวเลือกการสแกน")

options_dict = {"all": f"🔥 ทุกลีก/บอลถ้วยทั้งหมด ({len(active_leagues)} รายการ)"}
for k, v in active_leagues.items():
    options_dict[k] = v["name"]

selected_league_id = st.sidebar.selectbox(
    "เลือกรายการแข่งขัน/บอลถ้วย",
    options=list(options_dict.keys()),
    format_func=lambda x: options_dict[x],
)

only_value_bets = st.sidebar.checkbox(
    "แสดงเฉพาะคู่ที่น่าลงทุน (+EV > 2%)", value=False
)
scan_btn = st.sidebar.button("🚀 เริ่มสแกนบอล", type="primary")

if scan_btn:
    with st.spinner("กำลังดึงข้อมูลการแข่งขันและคำนวณอัตราต่อรอง..."):
        all_results = []
        if selected_league_id == "all":
            for k, league in active_leagues.items():
                all_results.extend(scan_league(league))
        else:
            if selected_league_id in active_leagues:
                all_results = scan_league(active_leagues[selected_league_id])

        if all_results:
            df = pd.DataFrame(all_results)
            if only_value_bets:
                df = df[df["สถานะ"].str.contains("🔥")]

            st.success(f"พบรายการแข่งขันทั้งหมด {len(df)} รายการ")
            st.dataframe(df, use_container_width=True, hide_index=True)
        else:
            st.warning(
                "ไม่พบคู่แข่งขันในช่วง 36 ชั่วโมงข้างหน้า ในรายการที่เลือก"
            )
