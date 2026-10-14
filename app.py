# app.py
# Value Bet Pro
# Streamlit + The Odds API + Football-Data.co.uk
# Markets: Asian Handicap / Over-Under
# Historical results are downloaded automatically where supported.

import os
import io
import math
import sqlite3
from datetime import datetime, timedelta, timezone
from itertools import combinations

import pandas as pd
import requests
import streamlit as st

try:
    from zoneinfo import ZoneInfo
    TH_TZ = ZoneInfo("Asia/Bangkok")
except Exception:
    TH_TZ = timezone(timedelta(hours=7))


# =========================================================
# CONFIG
# =========================================================

st.set_page_config(
    page_title="Value Bet Pro",
    page_icon="⚽",
    layout="wide"
)

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, "value_bet_pro.db")
HISTORY_DIR = os.path.join(APP_DIR, "history")
os.makedirs(HISTORY_DIR, exist_ok=True)

API_BASE = "https://api.the-odds-api.com/v4"
HISTORY_BASE = "https://www.football-data.co.uk/mmz4281"

# The Odds API sport keys
LEAGUES = {
    "พรีเมียร์ลีก อังกฤษ": {
        "sport": "soccer_epl",
        "fd": "E0",
    },
    "แชมเปียนชิพ อังกฤษ": {
        "sport": "soccer_efl_champ",
        "fd": "E1",
    },
    "ลีกวัน อังกฤษ": {
        "sport": "soccer_england_league1",
        "fd": "E2",
    },
    "ลีกทู อังกฤษ": {
        "sport": "soccer_england_league2",
        "fd": "E3",
    },
    "ลาลีกา สเปน": {
        "sport": "soccer_spain_la_liga",
        "fd": "SP1",
    },
    "บุนเดสลีกา เยอรมนี": {
        "sport": "soccer_germany_bundesliga",
        "fd": "D1",
    },
    "เซเรียอา อิตาลี": {
        "sport": "soccer_italy_serie_a",
        "fd": "I1",
    },
    "ลีกเอิง ฝรั่งเศส": {
        "sport": "soccer_france_ligue_one",
        "fd": "F1",
    },
    "เอเรดิวิซี เนเธอร์แลนด์": {
        "sport": "soccer_netherlands_eredivisie",
        "fd": "N1",
    },
    "พรีเมียร์ชิพ สกอตแลนด์": {
        "sport": "soccer_scotland_premiership",
        "fd": "SC0",
    },
    "พรีเมียร์ลีก โปรตุเกส": {
        "sport": "soccer_portugal_primeira_liga",
        "fd": "P1",
    },
}

# Asian odds commonly use decimal odds.
DEFAULT_REGIONS = "uk,eu"
DEFAULT_MARKETS = "spreads,totals"


# =========================================================
# DATABASE
# =========================================================

def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                captured_at TEXT NOT NULL,
                event_id TEXT NOT NULL,
                home_team TEXT,
                away_team TEXT,
                commence_time TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                bookmaker TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                event_id TEXT,
                match_name TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                estimated_probability REAL,
                estimated_ev REAL,
                stake REAL DEFAULT 0,
                result TEXT DEFAULT 'Pending',
                actual_profit REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                updated_at TEXT NOT NULL,
                event_id TEXT,
                match_name TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                opening_odds REAL,
                current_odds REAL,
                notes TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS history_cache (
                league TEXT NOT NULL,
                season TEXT NOT NULL,
                downloaded_at TEXT NOT NULL,
                PRIMARY KEY (league, season)
            )
        """)


init_db()


# =========================================================
# GENERAL HELPERS
# =========================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_thai():
    return datetime.now(TH_TZ)


def parse_utc(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def fmt_thai(value):
    dt = parse_utc(value)
    if dt is None:
        return "-"
    return dt.astimezone(TH_TZ).strftime("%d/%m/%Y %H:%M")


def safe_float(value, default=None):
    try:
        if value is None or pd.isna(value):
            return default
        return float(value)
    except (ValueError, TypeError):
        return default


def api_key_from_config():
    # Preferred: Streamlit secrets or environment variable.
    try:
        value = st.secrets.get("ODDS_API_KEY", "")
        if value:
            return str(value).strip()
    except Exception:
        pass

    return os.getenv("ODDS_API_KEY", "").strip()


def api_get(url, params=None, timeout=20):
    response = requests.get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response.json(), response.headers


def season_codes():
    """
    Return current and previous Football-Data season codes.
    Example: 2627 = 2026/27.
    """
    year = now_thai().year
    month = now_thai().month

    start_year = year if month >= 7 else year - 1
    current = f"{str(start_year)[-2:]}{str(start_year + 1)[-2:]}"
    previous_year = start_year - 1
    previous = (
        f"{str(previous_year)[-2:]}"
        f"{str(previous_year + 1)[-2:]}"
    )
    older_year = previous_year - 1
    older = (
        f"{str(older_year)[-2:]}"
        f"{str(older_year + 1)[-2:]}"
    )
    return [current, previous, older]


# =========================================================
# THE ODDS API
# =========================================================

@st.cache_data(ttl=180, show_spinner=False)
def fetch_events(sport_key, api_key):
    url = f"{API_BASE}/sports/{sport_key}/odds"

    params = {
        "apiKey": api_key,
        "regions": DEFAULT_REGIONS,
        "markets": DEFAULT_MARKETS,
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    data, headers = api_get(url, params=params)

    # Do not print the API key or full request URL.
    remaining = headers.get("x-requests-remaining", "?")
    used = headers.get("x-requests-used", "?")

    return data, str(remaining), str(used)


def event_in_next_24h(event):
    kickoff = parse_utc(event.get("commence_time"))
    if kickoff is None:
        return False

    now = now_utc()
    return now <= kickoff <= now + timedelta(hours=24)


def american_or_decimal(value):
    odds = safe_float(value)
    if odds is None or odds <= 1.0:
        return None
    return odds


def collect_market_quotes(event):
    """
    Build quotes for the exact same line.
    Home and away prices are paired only when their line values
    are opposing sides of the same handicap magnitude.
    """
    rows = []

    for bookmaker in event.get("bookmakers", []):
        book_name = bookmaker.get("title", "Unknown")

        for market in bookmaker.get("markets", []):
            market_key = market.get("key")

            outcomes = market.get("outcomes", [])
            if market_key == "spreads":
                parsed = []

                for outcome in outcomes:
                    team = outcome.get("name")
                    odds = american_or_decimal(outcome.get("price"))
                    line = safe_float(outcome.get("point"))

                    if team and odds and line is not None:
                        parsed.append({
                            "team": team,
                            "odds": odds,
                            "line": line,
                            "bookmaker": book_name,
                        })

                # Match opposing lines of the same absolute handicap.
                for i in range(len(parsed)):
                    for j in range(i + 1, len(parsed)):
                        a, b = parsed[i], parsed[j]

                        if a["team"] == b["team"]:
                            continue

                        if abs(a["line"] + b["line"]) > 0.001:
                            continue

                        rows.append({
                            "market": "AH",
                            "selection": a["team"],
                            "line": a["line"],
                            "odds": a["odds"],
                            "opponent": b["team"],
                            "opponent_line": b["line"],
                            "opponent_odds": b["odds"],
                            "bookmaker": book_name,
                        })

            elif market_key == "totals":
                by_name = {}
                for outcome in outcomes:
                    name = outcome.get("name")
                    odds = american_or_decimal(outcome.get("price"))
                    line = safe_float(outcome.get("point"))

                    if name and odds and line is not None:
                        by_name.setdefault(round(line, 3), {})[name] = {
                            "odds": odds,
                            "line": line,
                        }

                for line, sides in by_name.items():
                    over = sides.get("Over")
                    under = sides.get("Under")

                    if over and under:
                        rows.append({
                            "market": "OU",
                            "selection": "Over",
                            "line": line,
                            "odds": over["odds"],
                            "opponent": "Under",
                            "opponent_line": line,
                            "opponent_odds": under["odds"],
                            "bookmaker": book_name,
                        })

    return rows


def best_market_quotes(event):
    """
    Select the best available odds for each exact selection and line.
    For Over/Under, Over and Under must share the same total line.
    """
    quotes = collect_market_quotes(event)
    grouped = {}

    for q in quotes:
        key = (
            q["market"],
            q["selection"],
            round(q["line"], 3),
        )
        grouped.setdefault(key, []).append(q)

    result = []

    for key, options in grouped.items():
        options.sort(key=lambda x: x["odds"], reverse=True)
        result.append(options[0])

    return result


# =========================================================
# AUTOMATIC HISTORICAL RESULTS
# =========================================================

@st.cache_data(ttl=21600, show_spinner=False)
def download_history_csv(division_code, season_code):
    """
    Download Football-Data CSVs for supported divisions.
    Cache results for six hours. A season can be incomplete.
    """
    url = (
        f"{HISTORY_BASE}/{season_code}/"
        f"{division_code}.csv"
    )

    response = requests.get(
        url,
        timeout=20,
        headers={"User-Agent": "ValueBetPro/1.0"},
    )
    response.raise_for_status()

    if not response.content:
        return pd.DataFrame()

    try:
        df = pd.read_csv(io.BytesIO(response.content), encoding="utf-8-sig")
    except Exception:
        df = pd.read_csv(io.BytesIO(response.content), encoding="latin-1")

    required = {"HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not required.issubset(set(df.columns)):
        return pd.DataFrame()

    df = df.copy()
    df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
    df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")

    if "Date" in df.columns:
        df["ParsedDate"] = pd.to_datetime(
            df["Date"],
            dayfirst=True,
            errors="coerce",
        )
    else:
        df["ParsedDate"] = pd.NaT

    df = df.dropna(subset=["HomeTeam", "AwayTeam", "FTHG", "FTAG"])
    df["HomeTeam"] = df["HomeTeam"].astype(str).str.strip()
    df["AwayTeam"] = df["AwayTeam"].astype(str).str.strip()

    return df


@st.cache_data(ttl=21600, show_spinner=False)
def load_league_history(division_code):
    frames = []
    errors = []

    for season in season_codes():
        try:
            frame = download_history_csv(division_code, season)
            if not frame.empty:
                frame["SeasonCode"] = season
                frames.append(frame)
        except Exception as exc:
            errors.append(f"{season}: {type(exc).__name__}")

    if not frames:
        return pd.DataFrame(), errors

    history = pd.concat(frames, ignore_index=True)
    history = history.drop_duplicates(
        subset=["SeasonCode", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    )

    return history, errors


def team_name_match(name, candidates):
    """
    Conservative exact/normalized team-name match.
    Avoid guessing a different team when names differ substantially.
    """
    target = str(name).strip().lower()
    normalized = {
        str(x).strip().lower(): x
        for x in candidates
    }

    if target in normalized:
        return normalized[target]

    aliases = {
        "manchester united": ["man united", "man utd"],
        "manchester city": ["man city"],
        "tottenham hotspur": ["tottenham"],
        "wolverhampton wanderers": ["wolves"],
        "west ham united": ["west ham"],
        "newcastle united": ["newcastle"],
        "brighton and hove albion": ["brighton"],
        "nottingham forest": ["nott'm forest", "nottingham forest"],
    }

    for canonical, variants in aliases.items():
        all_names = [canonical] + variants
        if target in all_names:
            for candidate in candidates:
                if str(candidate).strip().lower() in all_names:
                    return candidate

    return None


def history_before_kickoff(history, kickoff):
    """
    Avoid future-data leakage: only include matches dated before kickoff.
    If the historical file has no usable dates, refuse to model.
    """
    if history.empty or "ParsedDate" not in history.columns:
        return pd.DataFrame()

    df = history.dropna(subset=["ParsedDate"]).copy()
    if df.empty:
        return df

    cutoff = kickoff.astimezone(TH_TZ).replace(tzinfo=None)
    df = df[df["ParsedDate"] < cutoff]

    return df


def estimate_goal_rates(history, home_team, away_team, kickoff):
    """
    Simple league-average adjusted team goal-rate estimate.
    This is a baseline Poisson model, not a fully calibrated predictor.
    """
    past = history_before_kickoff(history, kickoff)

    if len(past) < 80:
        return None

    home_names = past["HomeTeam"].dropna().unique().tolist()
    away_names = past["AwayTeam"].dropna().unique().tolist()
    all_names = list(set(home_names + away_names))

    home_match = team_name_match(home_team, all_names)
    away_match = team_name_match(away_team, all_names)

    if home_match is None or away_match is None:
        return None

    league_home_avg = past["FTHG"].mean()
    league_away_avg = past["FTAG"].mean()

    if league_home_avg <= 0 or league_away_avg <= 0:
        return None

    home_games = past[
        (past["HomeTeam"] == home_match)
        | (past["AwayTeam"] == home_match)
    ]
    away_games = past[
        (past["HomeTeam"] == away_match)
        | (past["AwayTeam"] == away_match)
    ]

    if len(home_games) < 5 or len(away_games) < 5:
        return None

    home_scored = (
        home_games.loc[home_games["HomeTeam"] == home_match, "FTHG"].sum()
        + home_games.loc[home_games["AwayTeam"] == home_match, "FTAG"].sum()
    )
    home_conceded = (
        home_games.loc[home_games["HomeTeam"] == home_match, "FTAG"].sum()
        + home_games.loc[home_games["AwayTeam"] == home_match, "FTHG"].sum()
    )

    away_scored = (
        away_games.loc[away_games["HomeTeam"] == away_match, "FTHG"].sum()
        + away_games.loc[away_games["AwayTeam"] == away_match, "FTAG"].sum()
    )
    away_conceded = (
        away_games.loc[away_games["HomeTeam"] == away_match, "FTAG"].sum()
        + away_games.loc[away_games["AwayTeam"] == away_match, "FTHG"].sum()
    )

    home_n = max(len(home_games), 1)
    away_n = max(len(away_games), 1)

    # Blend team rates toward league average to reduce small-sample extremes.
    shrink = 8.0

    home_attack = (
        home_scored / home_n + shrink * league_home_avg
    ) / (1 + shrink) / league_home_avg

    home_defence = (
        home_conceded / home_n + shrink * league_away_avg
    ) / (1 + shrink) / league_away_avg

    away_attack = (
        away_scored / away_n + shrink * league_away_avg
    ) / (1 + shrink) / league_away_avg

    away_defence = (
        away_conceded / away_n + shrink * league_home_avg
    ) / (1 + shrink) / league_home_avg

    lambda_home = league_home_avg * home_attack * away_defence
    lambda_away = league_away_avg * away_attack * home_defence

    # Prevent implausible extremes in this baseline model.
    lambda_home = min(max(lambda_home, 0.15), 4.5)
    lambda_away = min(max(lambda_away, 0.15), 4.5)

    return {
        "home": lambda_home,
        "away": lambda_away,
        "matches": len(past),
        "home_team_matches": len(home_games),
        "away_team_matches": len(away_games),
    }


# =========================================================
# POISSON PROBABILITIES / ASIAN SETTLEMENT
# =========================================================

def poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def score_matrix(lambda_home, lambda_away, max_goals=10):
    matrix = []
    for h in range(max_goals + 1):
        ph = poisson_pmf(h, lambda_home)
        for a in range(max_goals + 1):
            pa = poisson_pmf(a, lambda_away)
            matrix.append((h, a, ph * pa))
    return matrix


def split_asian_line(line):
    """
    Split quarter lines:
      -0.25 -> [0, -0.5]
      +0.75 -> [+0.5, +1.0]
    """
    line = float(line)
    doubled = round(line * 2)

    if abs(line * 2 - doubled) < 1e-8 and doubled % 2 != 0:
        return [line]

    quarter = round(line * 4)
    if abs(line * 4 - quarter) < 1e-8 and quarter % 2 != 0:
        return [(quarter // 2) / 2, (quarter // 2 + 1) / 2]

    return [line]


def asian_profit_factor(selection, line, home_goals, away_goals):
    """
    Return profit factor per 1 unit stake:
      win = odds - 1, push = 0, loss = -1
    Half wins/losses arise from quarter-line split settlement.
    """
    is_home = selection == "HOME"
    goal_diff = home_goals - away_goals if is_home else away_goals - home_goals

    lines = split_asian_line(line)
    factors = []

    for part in lines:
        result = goal_diff + part

        if result > 0:
            factors.append(1.0)
        elif abs(result) < 1e-9:
            factors.append(0.0)
        else:
            factors.append(-1.0)

    return sum(factors) / len(factors)


def asian_probability_and_ev(matrix, market, selection, line, odds):
    """
    Estimate expected profit per unit stake.
    For AH, selection is HOME/AWAY and line is that team's handicap.
    For OU, selection is Over/Under.
    """
    if odds is None or odds <= 1.0:
        return None, None

    expected_profit = 0.0
    win_weight = 0.0
    half_win_weight = 0.0
    push_weight = 0.0
    loss_weight = 0.0
    half_loss_weight = 0.0

    for hg, ag, prob in matrix:
        if market == "AH":
            profit_factor = asian_profit_factor(
                selection, line, hg, ag
            )
        else:
            total = hg + ag
            lines = split_asian_line(line)
            part_factors = []

            for part in lines:
                if selection == "Over":
                    diff = total - part
                else:
                    diff = part - total

                if diff > 0:
                    part_factors.append(1.0)
                elif abs(diff) < 1e-9:
                    part_factors.append(0.0)
                else:
                    part_factors.append(-1.0)

            profit_factor = sum(part_factors) / len(part_factors)

        # For decimal odds: win returns odds - 1; loss is -1.
        # For quarter lines, average split factors need the odds applied
        # only to winning portions. Compute each component separately.
        if market == "AH":
            if selection == "HOME":
                goal_diff = hg - ag
            else:
                goal_diff = ag - hg

            factors = []
            for part in split_asian_line(line):
                diff = goal_diff + part
                if diff > 0:
                    factors.append(1.0)
                elif abs(diff) < 1e-9:
                    factors.append(0.0)
                else:
                    factors.append(-1.0)
        else:
            total = hg + ag
            factors = []
            for part in split_asian_line(line):
                diff = total - part if selection == "Over" else part - total
                if diff > 0:
                    factors.append(1.0)
                elif abs(diff) < 1e-9:
                    factors.append(0.0)
                else:
                    factors.append(-1.0)

        # Evaluate each half of a quarter-line independently.
        match_profit = 0.0
        for factor in factors:
            if factor > 0:
                match_profit += odds - 1
            elif factor < 0:
                match_profit -= 1
            # Push contributes zero.
        match_profit /= len(factors)

        expected_profit += prob * match_profit

        if profit_factor >= 1:
            win_weight += prob
        elif profit_factor > 0:
            half_win_weight += prob
        elif profit_factor == 0:
            push_weight += prob
        elif profit_factor <= -1:
            loss_weight += prob
        else:
            half_loss_weight += prob

    return {
        "win": win_weight,
        "half_win": half_win_weight,
        "push": push_weight,
        "half_loss": half_loss_weight,
        "loss": loss_weight,
    }, expected_profit * 100


def remove_vig_probability(odds_a, odds_b):
    if not odds_a or not odds_b or odds_a <= 1 or odds_b <= 1:
        return None, None

    p_a = 1 / odds_a
    p_b = 1 / odds_b
    total = p_a + p_b

    if total <= 0:
        return None, None

    return p_a / total, p_b / total


# =========================================================
# SNAPSHOT STORAGE / PRICE MOVEMENT
# =========================================================

def save_snapshot(event, quote):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO odds_snapshots (
                captured_at, event_id, home_team, away_team,
                commence_time, market, selection, line, odds, bookmaker
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_utc().isoformat(),
            event.get("id"),
            event.get("home_team"),
            event.get("away_team"),
            event.get("commence_time"),
            quote.get("market"),
            quote.get("selection"),
            quote.get("line"),
            quote.get("odds"),
            quote.get("bookmaker"),
        ))


def get_snapshot_movement(event_id, market, selection, line, current_odds):
    with get_db() as conn:
        rows = conn.execute("""
            SELECT odds, captured_at
            FROM odds_snapshots
            WHERE event_id = ?
              AND market = ?
              AND selection = ?
              AND ABS(line - ?) < 0.001
            ORDER BY captured_at ASC
        """, (event_id, market, selection, line)).fetchall()

    if not rows:
        return None, None

    opening = safe_float(rows[0]["odds"])
    if opening is None:
        return None, None

    movement = current_odds - opening
    return opening, movement


# =========================================================
# BET LEDGER
# =========================================================

def add_bet(row, stake):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO bets (
                created_at, event_id, match_name, market,
                selection, line, odds, estimated_probability,
                estimated_ev, stake, result
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Pending')
        """, (
            now_utc().isoformat(),
            row.get("event_id"),
            row.get("match_name"),
            row.get("market"),
            row.get("selection"),
            row.get("line"),
            row.get("odds"),
            row.get("estimated_probability"),
            row.get("estimated_ev"),
            stake,
        ))


def load_bets():
    with get_db() as conn:
        return pd.read_sql_query(
            "SELECT * FROM bets ORDER BY id DESC",
            conn
        )


def settle_bet(bet_id, result, actual_profit=None):
    with get_db() as conn:
        conn.execute("""
            UPDATE bets
            SET result = ?, actual_profit = ?
            WHERE id = ?
        """, (result, actual_profit, int(bet_id)))


# =========================================================
# MANUAL PRICE TRACKER
# =========================================================

def save_manual_price(
    event_id, match_name, market, selection,
    line, opening_odds, current_odds, notes
):
    with get_db() as conn:
        conn.execute("""
            INSERT INTO manual_prices (
                updated_at, event_id, match_name, market, selection,
                line, opening_odds, current_odds, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now_utc().isoformat(),
            event_id,
            match_name,
            market,
            selection,
            line,
            opening_odds,
            current_odds,
            notes,
        ))


def load_manual_prices():
    with get_db() as conn:
        return pd.read_sql_query(
            "SELECT * FROM manual_prices ORDER BY id DESC",
            conn
        )


# =========================================================
# SIDEBAR
# =========================================================

st.title("⚽ Value Bet Pro")
st.caption(
    "วิเคราะห์ Asian Handicap / สูง-ต่ำ | ราคาเปิด-ราคาไหล | EV | "
    "ประวัติเดิมพัน"
)

with st.sidebar:
    st.header("ตั้งค่าระบบ")

    api_key = api_key_from_config()

    if not api_key:
        api_key = st.text_input(
            "The Odds API Key",
            type="password",
            help="ใส่เฉพาะในเซสชันนี้ หรือใช้ Streamlit Secrets",
        ).strip()

    league_names = list(LEAGUES.keys())
    selected_leagues = st.multiselect(
        "ลีกที่ต้องการวิเคราะห์",
        league_names,
        default=["พรีเมียร์ลีก อังกฤษ", "ลาลีกา สเปน"],
    )

    min_ev = st.number_input(
        "EV ขั้นต่ำ (%)",
        min_value=-100.0,
        max_value=100.0,
        value=2.0,
        step=0.5,
    )

    min_odds = st.number_input(
        "ราคา decimal ต่ำสุด",
        min_value=1.01,
        max_value=10.0,
        value=1.50,
        step=0.05,
    )

    max_odds = st.number_input(
        "ราคา decimal สูงสุด",
        min_value=1.01,
        max_value=100.0,
        value=3.50,
        step=0.10,
    )

    min_history = st.number_input(
        "จำนวนแมตช์ย้อนหลังขั้นต่ำ",
        min_value=20,
        max_value=1000,
        value=80,
        step=10,
    )

    refresh = st.button("🔄 โหลดข้อมูลใหม่", use_container_width=True)

if refresh:
    st.cache_data.clear()

if not api_key:
    st.warning(
        "ยังไม่ได้ตั้งค่า API Key กรุณาใส่ในแถบด้านซ้าย "
        "หรือกำหนด ODDS_API_KEY ใน Streamlit Secrets"
    )
    st.stop()

if not selected_leagues:
    st.info("เลือกลีกอย่างน้อย 1 ลีก")
    st.stop()


# =========================================================
# LOAD FIXTURES AND ANALYSE
# =========================================================

all_rows = []
api_errors = []
history_status = {}

progress = st.progress(0)
status = st.empty()

for idx, league_name in enumerate(selected_leagues):
    league_cfg = LEAGUES[league_name]
    status.write(f"กำลังโหลดราคา: {league_name}")

    try:
        events, remaining, used = fetch_events(
            league_cfg["sport"], api_key
        )
    except Exception as exc:
        api_errors.append(
            f"{league_name}: {type(exc).__name__}: "
            "ดึงราคาจาก API ไม่สำเร็จ"
        )
        progress.progress((idx + 1) / len(selected_leagues))
        continue

    # Fetch history automatically; data may not exist for every league.
    try:
        history, hist_errors = load_league_history(league_cfg["fd"])
        history_status[league_name] = {
            "matches": len(history),
            "error_count": len(hist_errors),
        }
    except Exception:
        history = pd.DataFrame()
        history_status[league_name] = {
            "matches": 0,
            "error_count": 1,
        }

    for event in events:
        if not event_in_next_24h(event):
            continue

        kickoff = parse_utc(event.get("commence_time"))
        if kickoff is None:
            continue

        home = event.get("home_team", "")
        away = event.get("away_team", "")
        match_name = f"{home} vs {away}"

        rates = estimate_goal_rates(
            history, home, away, kickoff
        ) if not history.empty else None

        matrix = (
            score_matrix(rates["home"], rates["away"])
            if rates else None
        )

        quotes = best_market_quotes(event)

        # Save each quote as a snapshot for future price comparisons.
        for quote in quotes:
            save_snapshot(event, quote)

        # For each selection, only calculate EV if history/model exists.
        for quote in quotes:
            market = quote["market"]
            selection = quote["selection"]
            line = quote["line"]
            odds = quote["odds"]

            if odds < min_odds or odds > max_odds:
                continue

            probability = None
            ev = None
            confidence_label = "ข้อมูลไม่พอ"

            if matrix:
                if market == "AH":
                    model_side = (
                        "HOME" if selection == home else "AWAY"
                    )

                    probabilities, ev = asian_probability_and_ev(
                        matrix, "AH", model_side, line, odds
                    )

                    if probabilities:
                        probability = (
                            probabilities["win"]
                            + 0.5 * probabilities["half_win"]
                        )

                elif market == "OU":
                    probabilities, ev = asian_probability_and_ev(
                        matrix, "OU", selection, line, odds
                    )

                    if probabilities:
                        probability = (
                            probabilities["win"]
                            + 0.5 * probabilities["half_win"]
                        )

                if rates and rates["matches"] >= min_history:
                    confidence_label = "มีข้อมูลย้อนหลัง"
                else:
                    confidence_label = "ตัวอย่างน้อย"

            opening, movement = get_snapshot_movement(
                event.get("id"),
                market,
                selection,
                line,
                odds,
            )

            # For the first observation, opening/current movement is unknown.
            if opening is None:
                opening = odds
                movement = 0.0

            all_rows.append({
                "ลีก": league_name,
                "เวลาไทย": fmt_thai(event.get("commence_time")),
                "คู่แข่งขัน": match_name,
                "event_id": event.get("id"),
                "ตลาด": market,
                "ตัวเลือก": selection,
                "เส้น": line,
                "ราคา": odds,
                "ราคาแรกที่ระบบบันทึก": opening,
                "ราคาเปลี่ยน": movement,
                "ความน่าจะเป็นประมาณการ (%)": (
                    round(probability * 100, 2)
                    if probability is not None else None
                ),
                "EV (%)": round(ev, 2) if ev is not None else None,
                "สถานะข้อมูล": confidence_label,
                "จำนวนเกมย้อนหลัง": rates["matches"] if rates else 0,
                "estimated_probability": probability,
                "estimated_ev": ev,
                "market_internal": market,
                "selection_internal": selection,
                "line_internal": line,
                "odds_internal": odds,
                "home_internal": home,
                "away_internal": away,
                "kickoff_internal": event.get("commence_time"),
            })

    progress.progress((idx + 1) / len(selected_leagues))

status.empty()
progress.empty()


# =========================================================
# DASHBOARD
# =========================================================

if api_errors:
    with st.expander("⚠️ รายการที่โหลดข้อมูลไม่สำเร็จ"):
        for error in api_errors:
            st.write(error)

with st.expander("ข้อมูลผลย้อนหลังอัตโนมัติ"):
    for league, info in history_status.items():
        st.write(
            f"**{league}:** โหลดได้ {info['matches']} แมตช์ "
            f"(ข้อผิดพลาด {info['error_count']} รายการ)"
        )
    st.caption(
        "ผลย้อนหลังมาจาก Football-Data.co.uk เฉพาะลีก/ฤดูกาลที่มีไฟล์ "
        "และอาจมีข้อมูลไม่ครบ ระบบไม่สร้างข้อมูลที่ไม่มีอยู่ขึ้นมาเอง"
    )

if not all_rows:
    st.warning(
        "ไม่พบคู่แข่งขันที่เริ่มภายใน 24 ชั่วโมงข้างหน้า "
        "หรือไม่สามารถโหลดราคาได้ในขณะนี้"
    )
else:
    df = pd.DataFrame(all_rows)

    # Strict EV display: hide rows with no valid model estimate.
    valid_model = df[df["EV (%)"].notna()].copy()

    tab1, tab2, tab3, tab4 = st.tabs([
        "📊 วิเคราะห์ราคา",
        "🎟️ จัดบิล",
        "📉 ราคาเปิด/ราคาไหล",
        "📒 บันทึกเดิมพัน",
    ])

    with tab1:
        st.subheader("คู่แข่งขันใน 24 ชั่วโมงข้างหน้า")

        c1, c2, c3 = st.columns(3)
        c1.metric("จำนวนตัวเลือก", len(df))
        c2.metric("มี EV คำนวณได้", len(valid_model))
        c3.metric(
            "EV ถึงเกณฑ์",
            int((valid_model["EV (%)"] >= min_ev).sum())
        )

        show_cols = [
            "ลีก", "เวลาไทย", "คู่แข่งขัน", "ตลาด", "ตัวเลือก",
            "เส้น", "ราคา", "ราคาแรกที่ระบบบันทึก", "ราคาเปลี่ยน",
            "ความน่าจะเป็นประมาณการ (%)", "EV (%)",
            "สถานะข้อมูล", "จำนวนเกมย้อนหลัง",
        ]

        st.dataframe(
            df[show_cols].sort_values(
                by="EV (%)",
                ascending=False,
                na_position="last",
            ),
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "EV เป็นค่าประมาณจากโมเดล Poisson พื้นฐาน "
            "ไม่ใช่ความน่าจะเป็นที่ผ่านการสอบเทียบหรือรับประกันกำไร "
            "ราคาแรกที่ระบบบันทึกคือราคาที่ระบบพบครั้งแรก "
            "ไม่ใช่ราคาเปิดตลาดจากเจ้ามือโดยอัตโนมัติ"
        )

        if not valid_model.empty:
            qualified = valid_model[
                (valid_model["EV (%)"] >= min_ev)
                & (valid_model["สถานะข้อมูล"] == "มีข้อมูลย้อนหลัง")
            ].copy()

            st.subheader("ตัวเลือกผ่านเกณฑ์")
            if qualified.empty:
                st.info(
                    "ยังไม่มีตัวเลือกที่ผ่านทั้งเกณฑ์ EV "
                    "และจำนวนข้อมูลย้อนหลัง ไม่ควรฝืนเลือกเดิมพัน"
                )
            else:
                st.dataframe(
                    qualified[show_cols].sort_values(
                        "EV (%)", ascending=False
                    ),
                    use_container_width=True,
                    hide_index=True,
                )

                options = qualified.index.tolist()
                selected_index = st.selectbox(
                    "เลือกตัวเลือกเพื่อบันทึกบิล",
                    options,
                    format_func=lambda i: (
                        f"{df.loc[i, 'คู่แข่งขัน']} | "
                        f"{df.loc[i, 'ตลาด']} "
                        f"{df.loc[i, 'ตัวเลือก']} "
                        f"{df.loc[i, 'เส้น']} | "
                        f"ราคา {df.loc[i, 'ราคา']}"
                    ),
                )

                stake = st.number_input(
                    "เงินเดิมพัน (หน่วยเงินที่ใช้)",
                    min_value=0.0,
                    value=100.0,
                    step=50.0,
                )

                if st.button("บันทึกตัวเลือกนี้"):
                    row = df.loc[selected_index]
                    add_bet({
                        "event_id": row["event_id"],
                        "match_name": row["คู่แข่งขัน"],
                        "market": row["ตลาด"],
                        "selection": row["ตัวเลือก"],
                        "line": row["เส้น"],
                        "odds": row["ราคา"],
                        "estimated_probability":
                            row["estimated_probability"],
                        "estimated_ev": row["estimated_ev"],
                    }, stake)
                    st.success("บันทึกตัวเลือกแล้ว")

    with tab2:
        st.subheader("จัดชุดตัวเลือกสำหรับบิล")

        if valid_model.empty:
            st.warning(
                "ยังไม่มีตัวเลือกที่คำนวณ EV ได้ "
                "จึงไม่สามารถจัดอันดับด้วย EV ได้"
            )
        else:
            eligible = valid_model[
                (valid_model["EV (%)"] >= min_ev)
                & (valid_model["สถานะข้อมูล"] == "มีข้อมูลย้อนหลัง")
            ].copy()

            # One selection per fixture in the pool to avoid duplicate matches.
            eligible = eligible.sort_values("EV (%)", ascending=False)
            eligible = eligible.drop_duplicates(
                subset=["event_id"], keep="first"
            )

            requested_legs = st.selectbox(
                "จำนวนคู่ต่อบิล",
                [5, 9, 13, 20],
                index=0,
            )

            st.write(f"ตัวเลือกที่ผ่านเกณฑ์: **{len(eligible)} คู่**")

            if len(eligible) < requested_legs:
                st.warning(
                    f"มีตัวเลือกผ่านเกณฑ์เพียง {len(eligible)} คู่ "
                    f"แต่ต้องการ {requested_legs} คู่ "
                    "ระบบจะไม่เติมคู่ที่ไม่ผ่านเกณฑ์ให้ครบ"
                )
            else:
                # Highest estimated EV selections, one per match.
                ticket = eligible.head(requested_legs).copy()

                st.dataframe(
                    ticket[[
                        "เวลาไทย", "คู่แข่งขัน", "ตลาด", "ตัวเลือก",
                        "เส้น", "ราคา", "EV (%)",
                        "ความน่าจะเป็นประมาณการ (%)",
                    ]],
                    use_container_width=True,
                    hide_index=True,
                )

                combined_odds = float(ticket["ราคา"].prod())
                probabilities = ticket[
                    "estimated_probability"
                ].fillna(0).clip(0, 1)

                # Independence assumption is unrealistic; show only as a
                # rough illustration, not as a reliable parlay probability.
                rough_joint = float(probabilities.prod()) * 100

                st.metric(
                    "ราคาคูณรวมโดยประมาณ",
                    f"{combined_odds:.2f}"
                )
                st.metric(
                    "โอกาสเข้าทุกคู่โดยประมาณ*",
                    f"{rough_joint:.4f}%"
                )
                st.caption(
                    "*คำนวณจากความน่าจะเป็นรายคู่แบบคูณกัน "
                    "โดยสมมติว่าผลแต่ละคู่เป็นอิสระต่อกัน "
                    "และใช้ค่าประมาณจากโมเดลพื้นฐาน "
                    "ค่าจริงอาจต่างมาก จึงห้ามใช้เป็นคำรับประกัน"
                )

                if st.button("บันทึกทุกคู่ในชุดนี้"):
                    for _, row in ticket.iterrows():
                        add_bet({
                            "event_id": row["event_id"],
                            "match_name": row["คู่แข่งขัน"],
                            "market": row["ตลาด"],
                            "selection": row["ตัวเลือก"],
                            "line": row["เส้น"],
                            "odds": row["ราคา"],
                            "estimated_probability":
                                row["estimated_probability"],
                            "estimated_ev": row["estimated_ev"],
                        }, 0.0)
                    st.success(
                        "บันทึกตัวเลือกทั้งหมดแล้ว "
                        "เงินเดิมพันต่อคู่ถูกตั้งไว้ที่ 0"
                    )

    with tab3:
        st.subheader("บันทึกราคาเปิดและราคาปัจจุบันด้วยตนเอง")

        with st.form("manual_price_form"):
            match_name = st.text_input("คู่แข่งขัน")
            event_id = st.text_input("Event ID (ถ้ามี)")
            market = st.selectbox("ตลาด", ["AH", "OU"])
            selection = st.text_input(
                "ตัวเลือก เช่น ทีมต่อ, ทีมรอง, Over, Under"
            )
            line = st.number_input(
                "เส้นแฮนดิแคป/สูงต่ำ",
                value=0.0,
                step=0.25,
            )
            opening_odds = st.number_input(
                "ราคาเปิด",
                min_value=1.01,
                value=1.90,
                step=0.01,
            )
            current_odds = st.number_input(
                "ราคาปัจจุบัน",
                min_value=1.01,
                value=1.90,
                step=0.01,
            )
            notes = st.text_input("หมายเหตุ")
            submitted = st.form_submit_button("บันทึกราคา")

            if submitted:
                if not match_name.strip() or not selection.strip():
                    st.error("กรอกคู่แข่งขันและตัวเลือกก่อน")
                else:
                    save_manual_price(
                        event_id, match_name, market, selection,
                        line, opening_odds, current_odds, notes
                    )
                    st.success("บันทึกราคาแล้ว")

        prices = load_manual_prices()

        if not prices.empty:
            prices["movement"] = (
                prices["current_odds"] - prices["opening_odds"]
            )
            prices["movement_pct"] = (
                prices["movement"] / prices["opening_odds"] * 100
            ).round(2)

            st.dataframe(
                prices,
                use_container_width=True,
                hide_index=True,
            )

            st.caption(
                "ราคาไหลลงไม่ได้แปลว่าฝั่งนั้นมีโอกาสชนะมากขึ้นเสมอ "
                "ต้องดูว่าเป็นราคาเส้นเดียวกัน เจ้ามือเดียวกันหรือไม่ "
                "และตรวจการเปลี่ยนแปลงของเส้นควบคู่กัน"
            )

    with tab4:
        st.subheader("บันทึกและตรวจผลเดิมพัน")

        bets = load_bets()

        if bets.empty:
            st.info("ยังไม่มีรายการเดิมพัน")
        else:
            st.dataframe(
                bets,
                use_container_width=True,
                hide_index=True,
            )

            bet_ids = bets["id"].tolist()
            selected_bet = st.selectbox(
                "เลือกรายการที่ต้องการตัดสินผล",
                bet_ids,
            )

            result = st.selectbox(
                "ผลเดิมพัน",
                [
                    "Pending",
                    "Win",
                    "Half Win",
                    "Push",
                    "Half Loss",
                    "Loss",
                    "Void",
                ],
            )

            actual_profit = st.number_input(
                "กำไร/ขาดทุนจริง (กรอกเอง)",
                value=0.0,
                step=10.0,
            )

            if st.button("บันทึกผลเดิมพัน"):
                settle_bet(
                    selected_bet,
                    result,
                    actual_profit if result != "Pending" else None,
                )
                st.success("บันทึกผลแล้ว")
                st.rerun()

            settled = bets[bets["result"] != "Pending"].copy()

            if not settled.empty:
                settled["actual_profit"] = pd.to_numeric(
                    settled["actual_profit"],
                    errors="coerce",
                ).fillna(0)

                total_profit = settled["actual_profit"].sum()
                total_bets = len(settled)
                wins = settled["result"].isin(["Win", "Half Win"]).sum()
                win_rate = wins / total_bets * 100 if total_bets else 0

                c1, c2, c3 = st.columns(3)
                c1.metric("กำไร/ขาดทุนจริง", f"{total_profit:.2f}")
                c2.metric("รายการที่ตัดสินผลแล้ว", total_bets)
                c3.metric("อัตราชนะเบื้องต้น", f"{win_rate:.2f}%")

                st.caption(
                    "อัตราชนะเบื้องต้นนับ Half Win เป็นหนึ่งรายการที่ชนะ "
                    "และยังไม่ใช่ ROI; Push/Void มีผลต่อการตีความสถิติ"
                )

        # Export ledger
        if not bets.empty:
            csv_data = bets.to_csv(index=False).encode("utf-8-sig")
            st.download_button(
                "ดาวน์โหลดบันทึกเดิมพัน CSV",
                data=csv_data,
                file_name="value_bet_pro_ledger.csv",
                mime="text/csv",
            )


# =========================================================
# FOOTER / DISCLAIMER
# =========================================================

st.divider()
st.caption(
    "Value Bet Pro เป็นเครื่องมือช่วยวิเคราะห์ข้อมูล ไม่ใช่คำแนะนำ "
    "หรือการรับประกันผลเดิมพัน ผลย้อนหลังไม่รับประกันผลอนาคต "
    "ควรตรวจสอบราคา เส้น และข้อมูลทีมก่อนตัดสินใจทุกครั้ง"
)
