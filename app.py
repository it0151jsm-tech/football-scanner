
import os
import re
import math
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests
import streamlit as st

# ============================================================
# VALUE BET PRO - INTEGRATED EDITION
# Streamlit + The Odds API v4 + Historical Data + SQLite
# ============================================================

APP_NAME = "Value Bet Pro"
DB_PATH = Path("value_bet_pro.db")
ODDS_BASE = "https://api.the-odds-api.com/v4"
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚽",
    layout="wide",
)

# ------------------------------------------------------------
# 1. LEAGUE CONFIGURATION
# Odds API keys are verified against /v4/sports at runtime.
# Historical codes are used only where a Football-Data code
# is configured. Other leagues can use an uploaded CSV.
# ------------------------------------------------------------

LEAGUES = {
    "Premier League": {
        "odds_key": "soccer_epl",
        "fd_code": "E0",
        "confidence_base": "สูง",
        "region": "uk",
    },
    "Championship": {
        "odds_key": "soccer_efl_champ",
        "fd_code": "E1",
        "confidence_base": "สูง",
        "region": "uk",
    },
    "League One": {
        "odds_key": "soccer_england_league1",
        "fd_code": "E2",
        "confidence_base": "สูง",
        "region": "uk",
    },
    "League Two": {
        "odds_key": "soccer_england_league2",
        "fd_code": "E3",
        "confidence_base": "สูง",
        "region": "uk",
    },
    "La Liga": {
        "odds_key": "soccer_spain_la_liga",
        "fd_code": "SP1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Bundesliga": {
        "odds_key": "soccer_germany_bundesliga",
        "fd_code": "D1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Serie A": {
        "odds_key": "soccer_italy_serie_a",
        "fd_code": "I1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Ligue 1": {
        "odds_key": "soccer_france_ligue_one",
        "fd_code": "F1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Eredivisie": {
        "odds_key": "soccer_netherlands_eredivisie",
        "fd_code": "N1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Primeira Liga": {
        "odds_key": "soccer_portugal_primeira_liga",
        "fd_code": "P1",
        "confidence_base": "สูง",
        "region": "eu",
    },
    "Scottish Premiership": {
        "odds_key": "soccer_spl",
        "fd_code": "SC0",
        "confidence_base": "สูง",
        "region": "uk",
    },
    "J1 League (Japan)": {
        "odds_key": "soccer_japan_j_league",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "asia",
    },
    "K League 1 (Korea)": {
        "odds_key": "soccer_korea_k_league_1",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "asia",
    },
    "Thai League 1": {
        "odds_key": "soccer_thailand_thai_league",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "asia",
    },
    "Austrian Bundesliga": {
        "odds_key": "soccer_austria_bundesliga",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
    "Swiss Super League": {
        "odds_key": "soccer_switzerland_superleague",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
    "Greek Super League": {
        "odds_key": "soccer_greece_super_league",
        "fd_code": "G1",
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
    "MLS (USA)": {
        "odds_key": "soccer_usa_mls",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "us",
    },
    "Brazil Série A": {
        "odds_key": "soccer_brazil_campeonato",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "sa",
    },
    "Argentina Primera División": {
        "odds_key": "soccer_argentina_primera_division",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "sa",
    },
    "UEFA Champions League": {
        "odds_key": "soccer_uefa_champs_league",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
    "UEFA Europa League": {
        "odds_key": "soccer_uefa_europa_league",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
    "UEFA Conference League": {
        "odds_key": "soccer_uefa_europa_conference_league",
        "fd_code": None,
        "confidence_base": "ต้องตรวจสอบ",
        "region": "eu",
    },
}

# ------------------------------------------------------------
# 2. API KEY AND DATABASE
# ------------------------------------------------------------

def get_api_key():
    try:
        key = st.secrets.get("ODDS_API_KEY", "")
        if key:
            return str(key).strip()
    except Exception:
        pass
    return os.getenv("ODDS_API_KEY", "").strip()


def connect_db():
    con = sqlite3.connect(DB_PATH, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with connect_db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                league TEXT,
                home_team TEXT,
                away_team TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                source TEXT,
                note TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                league TEXT,
                home_team TEXT,
                away_team TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                stake REAL,
                model_probability REAL,
                ev_percent REAL,
                data_status TEXT,
                result TEXT DEFAULT 'Pending',
                profit REAL DEFAULT 0,
                note TEXT
            )
        """)
        con.commit()


def query_db(sql, params=()):
    with connect_db() as con:
        return pd.read_sql_query(sql, con, params=params)


def save_snapshot(
    league, home, away, market, selection, line, odds, source, note=""
):
    with connect_db() as con:
        con.execute("""
            INSERT INTO odds_snapshots
            (captured_at, league, home_team, away_team, market,
             selection, line, odds, source, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            league, home, away, market, selection,
            line, odds, source, note,
        ))
        con.commit()


def save_bet(record):
    with connect_db() as con:
        con.execute("""
            INSERT INTO bets
            (created_at, league, home_team, away_team, market,
             selection, line, odds, stake, model_probability,
             ev_percent, data_status, result, profit, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending', 0, ?)
        """, (
            datetime.now(timezone.utc).isoformat(),
            record["league"],
            record["home"],
            record["away"],
            record["market"],
            record["selection"],
            record["line"],
            record["odds"],
            record["stake"],
            record.get("probability"),
            record.get("ev"),
            record.get("data_status", "ไม่ทราบ"),
            record.get("note", ""),
        ))
        con.commit()


def settle_bet(bet_id, result):
    with connect_db() as con:
        row = con.execute(
            "SELECT stake, odds FROM bets WHERE id = ?", (bet_id,)
        ).fetchone()

        if row is None:
            return

        stake = float(row["stake"])
        odds = float(row["odds"])

        profit_map = {
            "Win": stake * (odds - 1),
            "Half Win": stake * (odds - 1) / 2,
            "Push": 0.0,
            "Half Loss": -stake / 2,
            "Loss": -stake,
        }
        profit = profit_map.get(result, 0.0)

        con.execute(
            "UPDATE bets SET result=?, profit=? WHERE id=?",
            (result, profit, int(bet_id)),
        )
        con.commit()


init_db()

# ------------------------------------------------------------
# 3. GENERAL HELPERS
# ------------------------------------------------------------

def safe_float(value, default=None):
    try:
        result = float(value)
        if math.isfinite(result):
            return result
    except (TypeError, ValueError):
        pass
    return default


def normalize_team(name):
    text = str(name or "").lower().strip()
    text = re.sub(r"\b(fc|cf|sc|afc|club|football club)\b", "", text)
    text = re.sub(r"[^a-z0-9ก-๙]+", "", text)
    return text


def team_matches(df, team):
    if df is None or df.empty or not team:
        return pd.DataFrame()

    target = normalize_team(team)
    if not target:
        return pd.DataFrame()

    mask = (
        df["HomeTeam"].astype(str).map(normalize_team).eq(target)
        | df["AwayTeam"].astype(str).map(normalize_team).eq(target)
    )
    return df.loc[mask].sort_values("Date", ascending=False)


def api_get(path, params=None, timeout=20):
    key = get_api_key()
    if not key:
        raise ValueError(
            "ยังไม่ได้ตั้ง ODDS_API_KEY ใน Streamlit Secrets หรือ Environment"
        )

    request_params = dict(params or {})
    request_params["apiKey"] = key

    response = requests.get(
        f"{ODDS_BASE}/{path.lstrip('/')}",
        params=request_params,
        timeout=timeout,
    )

    if response.status_code == 401:
        raise ValueError("API Key ไม่ถูกต้องหรือไม่ได้รับอนุญาต")
    if response.status_code == 429:
        raise ValueError("เรียก API เกินโควตาหรือเกินอัตราที่กำหนด")
    response.raise_for_status()
    return response.json(), response.headers


@st.cache_data(ttl=300, show_spinner=False)
def get_available_sports():
    data, _ = api_get("sports", {"all": "true"})
    return data


def find_sport_key(config):
    """ตรวจสอบว่าลีกมีอยู่ในรายการกีฬาของ API จริงหรือไม่."""
    try:
        sports = get_available_sports()
    except Exception as exc:
        return None, f"ตรวจ API ไม่สำเร็จ: {exc}"

    expected = config["odds_key"]
    active = {
        item.get("key"): item
        for item in sports
        if item.get("active", True)
    }

    if expected in active:
        return expected, "พบลีกใน API"

    # ถ้า key ไม่ตรง ให้แสดงตัวเลือกที่ใกล้เคียงเพื่อให้ตรวจเอง
    base = expected.replace("soccer_", "").replace("_", " ")
    candidates = [
        item.get("key", "")
        for item in sports
        if base in item.get("key", "").replace("soccer_", "").replace("_", " ")
        or any(
            token in item.get("key", "").lower()
            for token in base.split() if len(token) > 3
        )
    ]

    if candidates:
        return None, "ไม่พบ key ที่ตั้งไว้; ตัวเลือกใกล้เคียง: " + ", ".join(candidates[:5])

    return None, "ไม่พบลีกนี้ในรายการ API ที่บัญชีเข้าถึงได้"


@st.cache_data(ttl=180, show_spinner=False)
def fetch_odds(sport_key, regions="uk,eu,us", markets="spreads,totals"):
    data, headers = api_get(
        f"sports/{sport_key}/odds",
        {
            "regions": regions,
            "markets": markets,
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
    )
    remaining = headers.get("x-requests-remaining", "ไม่ทราบ")
    return data, remaining


# ------------------------------------------------------------
# 4. HISTORICAL RESULTS
# ------------------------------------------------------------

def normalize_history(df):
    if df is None or df.empty:
        return pd.DataFrame()

    # รองรับชื่อคอลัมน์แบบ Football-Data และ CSV ทั่วไป
    rename = {}
    aliases = {
        "date": "Date",
        "hometeam": "HomeTeam",
        "home_team": "HomeTeam",
        "awayteam": "AwayTeam",
        "away_team": "AwayTeam",
        "fthg": "FTHG",
        "homegoals": "FTHG",
        "home_goals": "FTHG",
        "ftag": "FTAG",
        "awaygoals": "FTAG",
        "away_goals": "FTAG",
    }

    for col in df.columns:
        key = str(col).strip().lower().replace(" ", "")
        if key in aliases:
            rename[col] = aliases[key]

    df = df.rename(columns=rename)

    required = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    if not all(col in df.columns for col in required):
        return pd.DataFrame()

    df = df[required].copy()
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
    df = df.dropna(subset=required)
    df = df.sort_values("Date").reset_index(drop=True)
    return df


@st.cache_data(ttl=3600, show_spinner=False)
def load_football_data(fd_code, seasons):
    frames = []
    errors = []

    if not fd_code:
        return pd.DataFrame(), ["ลีกนี้ไม่มีแหล่ง Football-Data ที่กำหนดไว้"]

    for season in seasons:
        url = f"{FOOTBALL_DATA_BASE}/{season}/{fd_code}.csv"
        try:
            response = requests.get(url, timeout=20)
            if response.status_code != 200:
                errors.append(f"{season}: HTTP {response.status_code}")
                continue

            from io import StringIO
            frame = pd.read_csv(StringIO(response.text), on_bad_lines="skip")
            frame = normalize_history(frame)

            if not frame.empty:
                frames.append(frame)
            else:
                errors.append(f"{season}: ไม่มีคอลัมน์ผลการแข่งขันที่ใช้ได้")
        except Exception as exc:
            errors.append(f"{season}: {exc}")

    if not frames:
        return pd.DataFrame(), errors

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["Date", "HomeTeam", "AwayTeam"]
    )
    return combined.sort_values("Date").reset_index(drop=True), errors


def recent_form(df, team, n=5):
    games = team_matches(df, team).tail(n)
    if games.empty:
        return {
            "played": 0, "wins": 0, "draws": 0, "losses": 0,
            "gf": None, "ga": None, "points": None,
        }

    wins = draws = losses = gf = ga = points = 0

    for _, row in games.iterrows():
        is_home = normalize_team(row["HomeTeam"]) == normalize_team(team)
        scored = int(row["FTHG"] if is_home else row["FTAG"])
        conceded = int(row["FTAG"] if is_home else row["FTHG"])
        gf += scored
        ga += conceded

        if scored > conceded:
            wins += 1
            points += 3
        elif scored == conceded:
            draws += 1
            points += 1
        else:
            losses += 1

    return {
        "played": len(games), "wins": wins, "draws": draws,
        "losses": losses, "gf": gf, "ga": ga, "points": points,
    }


def head_to_head(df, home, away, n=10):
    if df is None or df.empty:
        return pd.DataFrame()

    home_norm = normalize_team(home)
    away_norm = normalize_team(away)

    mask = (
        (
            df["HomeTeam"].map(normalize_team).eq(home_norm)
            & df["AwayTeam"].map(normalize_team).eq(away_norm)
        )
        | (
            df["HomeTeam"].map(normalize_team).eq(away_norm)
            & df["AwayTeam"].map(normalize_team).eq(home_norm)
        )
    )

    return df.loc[mask].sort_values("Date", ascending=False).head(n)


def estimate_expected_goals(df, home, away):
    """Poisson baseline; not a calibrated betting model."""
    if df is None or len(df) < 30:
        return None, None, "ข้อมูลย้อนหลังน้อยกว่า 30 นัด"

    home_games = df[df["HomeTeam"].map(normalize_team).eq(normalize_team(home))]
    away_games = df[df["AwayTeam"].map(normalize_team).eq(normalize_team(away))]

    if len(home_games) < 3 or len(away_games) < 3:
        return None, None, "ทีมใดทีมหนึ่งมีข้อมูลเหย้า/เยือนไม่พอ"

    league_home_avg = df["FTHG"].mean()
    league_away_avg = df["FTAG"].mean()

    if pd.isna(league_home_avg) or pd.isna(league_away_avg):
        return None, None, "คำนวณค่าเฉลี่ยลีกไม่ได้"

    home_scored = home_games["FTHG"].mean()
    home_conceded = home_games["FTAG"].mean()
    away_scored = away_games["FTAG"].mean()
    away_conceded = away_games["FTHG"].mean()

    # Shrink team estimates toward league averages to reduce small-sample noise.
    home_attack = (home_scored * len(home_games) + league_home_avg * 5) / (len(home_games) + 5)
    away_defence = (away_conceded * len(away_games) + league_home_avg * 5) / (len(away_games) + 5)
    away_attack = (away_scored * len(away_games) + league_away_avg * 5) / (len(away_games) + 5)
    home_defence = (home_conceded * len(home_games) + league_away_avg * 5) / (len(home_games) + 5)

    lam_home = max(0.05, (home_attack / max(league_home_avg, 0.1)) * away_defence)
    lam_away = max(0.05, (away_attack / max(league_away_avg, 0.1)) * home_defence)

    return min(lam_home, 4.5), min(lam_away, 4.5), "คำนวณจากผลย้อนหลัง"


# ------------------------------------------------------------
# 5. POISSON AND ASIAN SETTLEMENT
# ------------------------------------------------------------

def poisson_pmf(k, lam):
    if lam < 0:
        return 0.0
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def make_score_matrix(lam_home, lam_away, max_goals=10):
    matrix = []
    for hg in range(max_goals + 1):
        for ag in range(max_goals + 1):
            p = poisson_pmf(hg, lam_home) * poisson_pmf(ag, lam_away)
            matrix.append((hg, ag, p))

    total = sum(item[2] for item in matrix)
    if total > 0:
        matrix = [(h, a, p / total) for h, a, p in matrix]
    return matrix


def split_asian_line(line):
    """Split quarter lines into two half-stake components."""
    line = round(float(line) * 4) / 4
    quarter = round(line * 4) % 2 != 0

    if not quarter:
        return [(line, 1.0)]

    lower = math.floor(line * 2) / 2
    upper = math.ceil(line * 2) / 2
    return [(lower, 0.5), (upper, 0.5)]


def component_profit(market, selection, line, hg, ag, odds):
    if market == "spreads":
        if selection == "Home":
            margin = hg - ag + line
        else:
            margin = ag - hg + line
    else:
        total = hg + ag
        if selection == "Over":
            margin = total - line
        else:
            margin = line - total

    if margin > 1e-9:
        return odds - 1
    if margin < -1e-9:
        return -1
    return 0.0


def expected_profit(matrix, market, selection, line, odds):
    components = split_asian_line(line)
    ev = 0.0

    for hg, ag, probability in matrix:
        profit = 0.0
        for component_line, weight in components:
            profit += weight * component_profit(
                market, selection, component_line, hg, ag, odds
            )
        ev += probability * profit

    return ev


def win_probability(matrix, market, selection, line):
    """Probability of a strictly positive settlement, excluding pushes."""
    p = 0.0
    for hg, ag, probability in matrix:
        profit = 0.0
        for component_line, weight in split_asian_line(line):
            profit += weight * component_profit(
                market, selection, component_line, hg, ag, 2.0
            )
        if profit > 0:
            p += probability
    return p


def analyse_market(matrix, market, selection, line, odds):
    ev = expected_profit(matrix, market, selection, line, odds)
    p_win = win_probability(matrix, market, selection, line)
    return {
        "probability": p_win,
        "ev": ev * 100,
        "expected_profit_per_unit": ev,
        "odds": odds,
    }


# ------------------------------------------------------------
# 6. ODDS PARSING AND DATA QUALITY
# ------------------------------------------------------------

def parse_odds_events(events):
    rows = []

    for event in events or []:
        for bookmaker in event.get("bookmakers", []):
            for market in bookmaker.get("markets", []):
                market_key = market.get("key")
                if market_key not in ("spreads", "totals"):
                    continue

                outcomes = market.get("outcomes", [])
                for outcome in outcomes:
                    name = outcome.get("name")
                    price = safe_float(outcome.get("price"))
                    point = safe_float(outcome.get("point"))

                    if price is None or price <= 1.0:
                        continue

                    rows.append({
                        "commence_time": event.get("commence_time"),
                        "home_team": event.get("home_team"),
                        "away_team": event.get("away_team"),
                        "market": market_key,
                        "selection": name,
                        "line": point,
                        "odds": price,
                        "bookmaker": bookmaker.get("title"),
                        "last_update": market.get("last_update"),
                    })

    return pd.DataFrame(rows)


def data_quality(odds_available, history_count, team_match_ok):
    if not odds_available:
        return "งดวิเคราะห์", "ไม่พบราคาตลาดที่ใช้ได้"

    if history_count < 30:
        return "ต่ำ", "ผลย้อนหลังน้อยกว่า 30 นัด"

    if not team_match_ok:
        return "ต่ำ", "จับคู่ชื่อทีมกับข้อมูลย้อนหลังไม่ได้"

    if history_count < 100:
        return "ปานกลาง", "มีข้อมูล แต่จำนวนตัวอย่างยังจำกัด"

    return "ข้อมูลพร้อม", "พบราคาและมีผลย้อนหลังเพียงพอเบื้องต้น"


def movement_summary(open_line, open_odds, current_line, current_odds):
    messages = []

    if open_line is not None and current_line is not None:
        if abs(open_line - current_line) < 1e-9:
            messages.append("แต้มต่อ/เส้นสูงต่ำไม่เปลี่ยน")
        elif current_line > open_line:
            messages.append("ตัวเลขเส้นปัจจุบันสูงขึ้น")
        else:
            messages.append("ตัวเลขเส้นปัจจุบันลดลง")

    if open_odds is not None and current_odds is not None:
        if current_odds > open_odds:
            messages.append("ราคาจ่ายปัจจุบันสูงขึ้น")
        elif current_odds < open_odds:
            messages.append("ราคาจ่ายปัจจุบันลดลง")
        else:
            messages.append("ราคาจ่ายไม่เปลี่ยน")

    if not messages:
        return "ข้อมูลราคาเปิด/ปัจจุบันไม่ครบ"

    return " | ".join(messages)


# ------------------------------------------------------------
# 7. APP UI
# ------------------------------------------------------------

st.title("⚽ Value Bet Pro")
st.caption(
    "Asian Handicap • Over/Under • Expected Value • Match Reading • "
    "Historical Data Quality • Bet Ledger"
)

tab_value, tab_read, tab_history, tab_ledger, tab_settings = st.tabs([
    "Value Bet Engine",
    "Match Reading Engine",
    "Historical Data",
    "Bet Ledger / Backtest",
    "Settings & League Status",
])

league_names = list(LEAGUES.keys())

# ============================================================
# TAB 1: VALUE BET ENGINE
# ============================================================

with tab_value:
    st.subheader("ค้นหาราคาและวิเคราะห์ EV")

    league = st.selectbox("เลือกลีก", league_names, key="value_league")
    config = LEAGUES[league]

    col_a, col_b = st.columns(2)
    with col_a:
        regions = st.multiselect(
            "ภูมิภาคราคาจาก API",
            ["uk", "eu", "us", "au", "asia"],
            default=["uk", "eu", "us"],
            key="value_regions",
        )
    with col_b:
        min_ev = st.number_input(
            "EV ขั้นต่ำ (%) สำหรับกรองผล",
            min_value=-100.0,
            max_value=100.0,
            value=2.0,
            step=1.0,
        )

    if st.button("ดึงราคาและวิเคราะห์ EV", type="primary"):
        sport_key, sport_status = find_sport_key(config)
        st.session_state["value_sport_status"] = sport_status

        if not sport_key:
            st.error(sport_status)
        else:
            try:
                events, remaining = fetch_odds(
                    sport_key,
                    regions=",".join(regions) if regions else "eu",
                )
                st.session_state["value_events"] = events
                st.session_state["value_league_loaded"] = league
                st.session_state["value_remaining"] = remaining
                st.success(f"ดึงราคาได้ {len(events)} แมตช์")
            except Exception as exc:
                st.error(f"ดึงราคาไม่สำเร็จ: {exc}")

    if "value_events" in st.session_state:
        events = st.session_state["value_events"]
        loaded_league = st.session_state.get("value_league_loaded", league)

        if loaded_league != league:
            st.warning("ราคาที่แสดงมาจากลีกก่อนหน้า กรุณากดดึงราคาใหม่")
        else:
            st.caption(
                "API requests remaining: "
                + str(st.session_state.get("value_remaining", "ไม่ทราบ"))
            )

            rows = parse_odds_events(events)
            if rows.empty:
                st.warning(
                    "ไม่พบตลาด spreads/totals จากราคาที่บัญชีเข้าถึงได้ "
                    "ลองตรวจลีก ภูมิภาค แพ็กเกจ API และตลาดที่เปิดให้ใช้"
                )
            else:
                st.dataframe(rows, use_container_width=True, hide_index=True)

            st.markdown("#### ประเมิน EV ด้วยผลย้อนหลัง")
            st.info(
                "ต้องโหลดผลย้อนหลังของลีกจากแท็บ Historical Data ก่อน "
                "จึงจะใช้ Poisson baseline คำนวณ EV ได้"
            )

            if st.button("คำนวณ EV จากข้อมูลย้อนหลังที่โหลดไว้"):
                history = st.session_state.get(
                    f"history_{league}", pd.DataFrame()
                )

                if history.empty:
                    st.error("ยังไม่มีข้อมูลย้อนหลังของลีกนี้")
                else:
                    output = []
                    event_map = {
                        (
                            normalize_team(e.get("home_team")),
                            normalize_team(e.get("away_team")),
                        ): e
                        for e in events
                    }

                    for _, odds_row in rows.iterrows():
                        home = odds_row["home_team"]
                        away = odds_row["away_team"]
                        lam_h, lam_a, model_msg = estimate_expected_goals(
                            history, home, away
                        )

                        if lam_h is None:
                            continue

                        hform = recent_form(history, home, 5)
                        aform = recent_form(history, away, 5)
                        h_found = hform["played"] > 0
                        a_found = aform["played"] > 0
                        status, status_reason = data_quality(
                            True, len(history), h_found and a_found
                        )

                        # A team-specific line must be interpreted from
                        # that team's own perspective in the spreads market.
                        if odds_row["market"] == "spreads":
                            if normalize_team(odds_row["selection"]) == normalize_team(home):
                                selection = "Home"
                                line = odds_row["line"]
                            elif normalize_team(odds_row["selection"]) == normalize_team(away):
                                selection = "Away"
                                line = odds_row["line"]
                            else:
                                continue
                        else:
                            if odds_row["selection"] not in ("Over", "Under"):
                                continue
                            selection = odds_row["selection"]
                            line = odds_row["line"]

                        if line is None:
                            continue

                        matrix = make_score_matrix(lam_h, lam_a)
                        result = analyse_market(
                            matrix, odds_row["market"], selection,
                            line, odds_row["odds"]
                        )

                        output.append({
                            "League": league,
                            "Home": home,
                            "Away": away,
                            "Market": odds_row["market"],
                            "Selection": odds_row["selection"],
                            "Line": line,
                            "Odds": odds_row["odds"],
                            "Model P(positive)": round(result["probability"] * 100, 2),
                            "EV %": round(result["ev"], 2),
                            "Data status": status,
                            "Data note": status_reason,
                            "Model note": model_msg,
                            "Bookmaker": odds_row["bookmaker"],
                        })

                    result_df = pd.DataFrame(output)
                    if result_df.empty:
                        st.warning(
                            "ยังไม่มีคู่ที่จับคู่กับข้อมูลย้อนหลังได้ "
                            "ตรวจสอบชื่อทีมและไฟล์ผลย้อนหลัง"
                        )
                    else:
                        # Do not label low-quality estimates as recommendations.
                        result_df["Eligible"] = (
                            (result_df["EV %"] >= min_ev)
                            & result_df["Data status"].isin(
                                ["ข้อมูลพร้อม", "ปานกลาง"]
                            )
                        )
                        st.dataframe(
                            result_df.sort_values("EV %", ascending=False),
                            use_container_width=True,
                            hide_index=True,
                        )
                        st.download_button(
                            "ดาวน์โหลดผลวิเคราะห์ CSV",
                            result_df.to_csv(index=False).encode("utf-8-sig"),
                            file_name="value_bet_analysis.csv",
                            mime="text/csv",
                        )

                        st.caption(
                            "EV เป็นค่าประมาณจากโมเดล Poisson พื้นฐาน "
                            "ไม่ใช่การรับประกันกำไร และยังไม่รวม margin/ข้อจำกัด "
                            "ของราคาแต่ละเจ้าทั้งหมด"
                        )

# ============================================================
# TAB 2: MATCH READING ENGINE
# ============================================================

with tab_read:
    st.subheader("อ่านราคาเปิดเทียบราคาปัจจุบัน")

    r1, r2 = st.columns(2)
    with r1:
        read_league = st.selectbox(
            "ลีก", league_names, key="read_league"
        )
        home = st.text_input("ทีมเหย้า", key="read_home")
        away = st.text_input("ทีมเยือน", key="read_away")
    with r2:
        market_label = st.selectbox(
            "ตลาด", ["Asian Handicap", "สูง/ต่ำ"], key="read_market"
        )
        bookmaker = st.text_input(
            "เจ้ามือ/แหล่งราคา (ระบุให้ตรงกัน)", key="read_bookmaker"
        )

    market_key = "spreads" if market_label == "Asian Handicap" else "totals"

    st.markdown("#### ราคาเปิด")
    o1, o2 = st.columns(2)
    with o1:
        opening_line = st.number_input(
            "เส้นเปิด (เช่น -0.25, 2.5)",
            value=0.0, step=0.25, key="opening_line"
        )
    with o2:
        opening_odds = st.number_input(
            "ราคาจ่ายเปิด", min_value=1.01, value=1.90,
            step=0.01, key="opening_odds"
        )

    st.markdown("#### ราคาเย็น / ราคาปัจจุบัน")
    c1, c2 = st.columns(2)
    with c1:
        current_line = st.number_input(
            "เส้นปัจจุบัน", value=0.0, step=0.25, key="current_line"
        )
    with c2:
        current_odds = st.number_input(
            "ราคาจ่ายปัจจุบัน", min_value=1.01, value=1.90,
            step=0.01, key="current_odds"
        )

    note = st.text_area(
        "บันทึกเพิ่มเติม เช่น เวลาเก็บราคา/ข่าว/สภาพทีม",
        key="reading_note"
    )

    if st.button("อ่านราคาและบันทึก Snapshot", type="primary"):
        if not home.strip() or not away.strip():
            st.error("กรอกชื่อทีมเหย้าและทีมเยือนก่อน")
        else:
            movement = movement_summary(
                opening_line, opening_odds, current_line, current_odds
            )
            st.markdown("**ผลเปรียบเทียบราคา:** " + movement)

            save_snapshot(
                read_league, home, away, market_key,
                "Opening", opening_line, opening_odds,
                bookmaker or "Manual", note,
            )
            save_snapshot(
                read_league, home, away, market_key,
                "Current", current_line, current_odds,
                bookmaker or "Manual", note,
            )
            st.success("บันทึกราคาเปิดและราคาปัจจุบันแล้ว")

    st.markdown("#### ฟอร์ม 5 นัดและ H2H")
    history = st.session_state.get(
        f"history_{read_league}", pd.DataFrame()
    )

    if home.strip() and away.strip():
        if history.empty:
            st.info(
                "ยังไม่มีผลย้อนหลังของลีกนี้ ให้โหลดไฟล์จากแท็บ "
                "Historical Data ก่อน"
            )
        else:
            form_h = recent_form(history, home, 5)
            form_a = recent_form(history, away, 5)
            h2h = head_to_head(history, home, away)

            f1, f2 = st.columns(2)
            with f1:
                st.write(f"**{home} — 5 นัดล่าสุด**")
                st.json(form_h)
            with f2:
                st.write(f"**{away} — 5 นัดล่าสุด**")
                st.json(form_a)

            st.write("**H2H ล่าสุดที่พบในไฟล์**")
            if h2h.empty:
                st.info("ไม่พบ H2H ในข้อมูลที่โหลด")
            else:
                st.dataframe(h2h, use_container_width=True, hide_index=True)

    st.caption(
        "ระบบไม่ตีความว่าเส้นขึ้น/ลงหมายถึงทีมใดจะชนะโดยอัตโนมัติ "
        "ต้องพิจารณาทีมที่ถือราคา ทิศทางเส้น ราคาเจ้ามือ และเวลาเก็บข้อมูล"
    )

    st.markdown("#### บันทึก Match Reading เป็นรายการเดิมพัน")
    read_selection = st.selectbox(
        "ฝั่งที่เลือก",
        ["Home", "Away", "Over", "Under"],
        key="read_selection",
    )
    read_stake = st.number_input(
        "เงินเดิมพัน (หน่วยเงินของบัญชี)",
        min_value=0.0, value=100.0, step=10.0,
        key="read_stake",
    )
    read_odds = st.number_input(
        "ราคาที่จะเข้าเดิมพัน",
        min_value=1.01, value=1.90, step=0.01,
        key="read_bet_odds",
    )
    read_line = st.number_input(
        "เส้นที่เดิมพัน", value=0.0, step=0.25,
        key="read_bet_line",
    )

    if st.button("บันทึกเดิมพัน"):
        if not home.strip() or not away.strip():
            st.error("กรอกชื่อทีมก่อน")
        else:
            save_bet({
                "league": read_league,
                "home": home,
                "away": away,
                "market": market_key,
                "selection": read_selection,
                "line": read_line,
                "odds": read_odds,
                "stake": read_stake,
                "probability": None,
                "ev": None,
                "data_status": "Manual Reading — ยังไม่ยืนยัน EV",
                "note": note,
            })
            st.success("บันทึกรายการเดิมพันแล้ว")

# ============================================================
# TAB 3: HISTORICAL DATA
# ============================================================

with tab_history:
    st.subheader("โหลดผลย้อนหลังและตรวจสอบคุณภาพข้อมูล")

    history_league = st.selectbox(
        "เลือกลีกที่จะโหลดผลย้อนหลัง",
        league_names,
        key="history_league",
    )
    hist_config = LEAGUES[history_league]

    st.write("**แหล่งข้อมูลอัตโนมัติ:** Football-Data.co.uk")
    st.caption(
        "รองรับเฉพาะลีก/ฤดูกาลที่แหล่งข้อมูลมีจริง "
        "หากลีกไม่รองรับ ให้ใช้ CSV ที่มี Date, HomeTeam, AwayTeam, FTHG, FTAG"
    )

    default_seasons = ["2627", "2526", "2425", "2324", "2223"]
    seasons = st.multiselect(
        "ฤดูกาล Football-Data",
        ["2627", "2526", "2425", "2324", "2223", "2122", "2021", "1920"],
        default=default_seasons,
        key="history_seasons",
    )

    if st.button("โหลด Football-Data"):
        history, errors = load_football_data(
            hist_config.get("fd_code"), seasons
        )
        st.session_state[f"history_{history_league}"] = history
        st.session_state[f"errors_{history_league}"] = errors

        if history.empty:
            st.error("ไม่พบข้อมูลย้อนหลังที่ใช้ได้")
        else:
            st.success(f"โหลดผลย้อนหลังได้ {len(history)} นัด")

        if errors:
            with st.expander("รายละเอียดคำเตือนการโหลด"):
                st.write(errors)

    uploaded = st.file_uploader(
        "หรืออัปโหลด CSV ผลย้อนหลังของลีกนี้",
        type=["csv"],
        key=f"upload_{history_league}",
    )

    if uploaded is not None:
        try:
            raw = pd.read_csv(uploaded)
            normalized = normalize_history(raw)
            if normalized.empty:
                st.error(
                    "รูปแบบ CSV ไม่ถูกต้อง ต้องมีคอลัมน์ Date, HomeTeam, "
                    "AwayTeam, FTHG, FTAG (หรือชื่อที่เทียบเท่า)"
                )
            else:
                st.session_state[f"history_{history_league}"] = normalized
                st.success(f"นำเข้าข้อมูลแล้ว {len(normalized)} นัด")
        except Exception as exc:
            st.error(f"อ่านไฟล์ CSV ไม่สำเร็จ: {exc}")

    loaded_history = st.session_state.get(
        f"history_{history_league}", pd.DataFrame()
    )

    st.markdown("#### สถานะข้อมูลของลีกที่เลือก")
    sport_key, sport_status = find_sport_key(hist_config) if get_api_key() else (
        None, "ยังไม่ได้ตั้ง API Key"
    )

    history_count = len(loaded_history)
    if history_count:
        status = "ข้อมูลพร้อม" if history_count >= 100 else (
            "ปานกลาง" if history_count >= 30 else "ต่ำ"
        )
    else:
        status = "งดวิเคราะห์"

    status_table = pd.DataFrame([{
        "League": history_league,
        "Configured Odds API Key": hist_config["odds_key"],
        "API status": sport_status,
        "History source": (
            "Loaded CSV / Football-Data" if history_count else "ไม่มีข้อมูล محمّل"
        ),
        "Historical matches": history_count,
        "Data status": status,
        "Confidence meaning": (
            "คุณภาพข้อมูลเท่านั้น ไม่ใช่โอกาสชนะ"
        ),
    }])
    st.dataframe(status_table, use_container_width=True, hide_index=True)

    if not loaded_history.empty:
        st.dataframe(
            loaded_history.tail(100).sort_values("Date", ascending=False),
            use_container_width=True,
            hide_index=True,
        )
        st.download_button(
            "ดาวน์โหลดข้อมูลย้อนหลัง CSV",
            loaded_history.to_csv(index=False).encode("utf-8-sig"),
            file_name=f"history_{history_league.replace(' ', '_')}.csv",
            mime="text/csv",
        )

    st.markdown("#### สรุปสถานะลีกทั้งหมด")
    all_status = []

    for name, cfg in LEAGUES.items():
        hist = st.session_state.get(f"history_{name}", pd.DataFrame())
        n = len(hist)
        if n >= 100:
            data_status = "ข้อมูลพร้อม"
        elif n >= 30:
            data_status = "ปานกลาง"
        elif n > 0:
            data_status = "ต่ำ"
        else:
            data_status = "ยังไม่มีผลย้อนหลัง"

        all_status.append({
            "League": name,
            "Odds API Key (configured)": cfg["odds_key"],
            "Historical source code": cfg.get("fd_code") or "ต้องอัปโหลด CSV",
            "Historical matches loaded": n,
            "Data status": data_status,
        })

    st.dataframe(
        pd.DataFrame(all_status),
        use_container_width=True,
        hide_index=True,
    )

# ============================================================
# TAB 4: BET LEDGER / BACKTEST
# ============================================================

with tab_ledger:
    st.subheader("รายการเดิมพันและผลย้อนหลัง")

    bets = query_db("SELECT * FROM bets ORDER BY id DESC")
    if bets.empty:
        st.info("ยังไม่มีรายการเดิมพัน")
    else:
        settled = bets[bets["result"] != "Pending"].copy()
        total_stake = settled["stake"].sum() if not settled.empty else 0.0
        net_profit = settled["profit"].sum() if not settled.empty else 0.0
        roi = (net_profit / total_stake * 100) if total_stake else 0.0
        decided = len(settled)
        wins = int(settled["result"].isin(["Win", "Half Win"]).sum()) if decided else 0
        win_rate = wins / decided * 100 if decided else 0.0

        m1, m2, m3, m4 = st.columns(4)
        m1.metric("เดิมพันที่ตัดสินแล้ว", decided)
        m2.metric("กำไร/ขาดทุนสุทธิ", f"{net_profit:.2f}")
        m3.metric("ROI", f"{roi:.2f}%")
        m4.metric("Win / Half Win rate", f"{win_rate:.2f}%")

        st.dataframe(bets, use_container_width=True, hide_index=True)

        st.download_button(
            "ดาวน์โหลด Bet Ledger CSV",
            bets.to_csv(index=False).encode("utf-8-sig"),
            file_name="value_bet_ledger.csv",
            mime="text/csv",
        )

        st.markdown("#### ตัดสินผลเดิมพัน")
        pending = bets[bets["result"] == "Pending"]

        if pending.empty:
            st.info("ไม่มีเดิมพันที่รอผล")
        else:
            bet_options = {
                f'#{int(row["id"])} | {row["league"]} | '
                f'{row["home_team"]} vs {row["away_team"]} | '
                f'{row["selection"]} {row["line"]} @ {row["odds"]}':
                    int(row["id"])
                for _, row in pending.iterrows()
            }

            chosen_label = st.selectbox(
                "เลือกรายการเดิมพัน", list(bet_options.keys())
            )
            chosen_result = st.selectbox(
                "ผลเดิมพัน",
                ["Win", "Half Win", "Push", "Half Loss", "Loss"],
            )

            if st.button("บันทึกผลเดิมพัน"):
                settle_bet(bet_options[chosen_label], chosen_result)
                st.success("บันทึกผลแล้ว กรุณารีเฟรช/เปิดแท็บใหม่เพื่อตรวจสอบ")
                st.rerun()

    st.markdown("#### ราคา Snapshot")
    snapshots = query_db(
        "SELECT * FROM odds_snapshots ORDER BY id DESC LIMIT 1000"
    )
    if snapshots.empty:
        st.info("ยังไม่มี Snapshot ราคา")
    else:
        st.dataframe(snapshots, use_container_width=True, hide_index=True)
        st.download_button(
            "ดาวน์โหลด Snapshots CSV",
            snapshots.to_csv(index=False).encode("utf-8-sig"),
            file_name="odds_snapshots.csv",
            mime="text/csv",
        )

# ============================================================
# TAB 5: SETTINGS
# ============================================================

with tab_settings:
    st.subheader("Settings & League Status")

    if get_api_key():
        st.success("พบ API Key ใน Secrets/Environment")
    else:
        st.warning(
            "ยังไม่พบ API Key ให้เพิ่ม ODDS_API_KEY ใน Streamlit Secrets"
        )

    st.markdown("#### วิธีตั้งค่า Secrets")
    st.code('ODDS_API_KEY = "YOUR_THE_ODDS_API_KEY"', language="toml")

    st.markdown("#### แหล่งข้อมูล")
    st.markdown(
        "- The Odds API: https://the-odds-api.com/\n"
        "- Football-Data.co.uk: https://www.football-data.co.uk/\n"
        "- Streamlit secrets: "
        "https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/secrets-management"
    )

    st.warning(
        "ข้อจำกัดสำคัญ: key ของลีกอาจแตกต่างจากที่กำหนดไว้ "
        "ระบบตรวจสอบกับ /v4/sports แต่ไม่ได้เปลี่ยน key ให้อัตโนมัติ "
        "ถ้าพบ key อื่น ให้ตรวจสอบว่าเป็นลีกเดียวกันก่อนแก้ config"
    )

    st.info(
        "ระดับข้อมูลในแอปวัดจากจำนวนแมตช์ที่โหลดและการจับคู่ข้อมูลเบื้องต้น "
        "ไม่ใช่ผลทดสอบย้อนหลังแบบ walk-forward และไม่ใช่ความน่าจะเป็นที่เดิมพันจะชนะ"
    )

st.divider()
st.caption(
    "Value Bet Pro — ใช้เพื่อวิเคราะห์ข้อมูลเท่านั้น "
    "ผล EV ขึ้นอยู่กับคุณภาพข้อมูล สมมติฐานโมเดล ราคาเดิมพัน และตลาดจริง"
)
