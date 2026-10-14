
import os
import math
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st


# =========================================================
# VALUE BET PRO
# Football odds + Asian Handicap + Over/Under + EV
# The Odds API + Thai time filter + SQLite
# =========================================================

APP_NAME = "Value Bet Pro"
API_BASE = "https://api.the-odds-api.com/v4"
DB_FILE = "value_bet_pro.db"
THAI_TZ = ZoneInfo("Asia/Bangkok")
UTC_TZ = timezone.utc

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚽",
    layout="wide"
)

st.title("⚽ Value Bet Pro")
st.caption(
    "ระบบวิเคราะห์ราคาฟุตบอล | Asian Handicap | Over/Under | EV | "
    "กรองคู่แข่งขันภายใน 24 ชั่วโมงตามเวลาไทย"
)

# ---------------------------------------------------------
# LEAGUES
# ---------------------------------------------------------

LEAGUES = {
    "พรีเมียร์ลีก อังกฤษ": "soccer_epl",
    "แชมเปียนชิพ อังกฤษ": "soccer_efl_champ",
    "ลีกวัน อังกฤษ": "soccer_england_league1",
    "ลีกทู อังกฤษ": "soccer_england_league2",
    "ลาลีกา สเปน": "soccer_spain_la_liga",
    "บุนเดสลีกา เยอรมนี": "soccer_germany_bundesliga",
    "เซเรียอา อิตาลี": "soccer_italy_serie_a",
    "ลีกเอิง ฝรั่งเศส": "soccer_france_ligue_one",
    "เอเรดิวิซี เนเธอร์แลนด์": "soccer_netherlands_eredivisie",
    "พรีเมราลีกา โปรตุเกส": "soccer_portugal_primeira_liga",
    "สกอตติช พรีเมียร์ชิพ": "soccer_spl",
    "เจลีก ญี่ปุ่น": "soccer_japan_j_league",
    "เคลีก เกาหลีใต้": "soccer_korea_k_league_1",
    "ไทยลีก": "soccer_thailand_thai_league",
    "ออสเตรีย บุนเดสลีกา": "soccer_austria_bundesliga",
    "สวิส ซูเปอร์ลีก": "soccer_switzerland_superleague",
    "กรีซ ซูเปอร์ลีก": "soccer_greece_super_league",
    "MLS สหรัฐฯ": "soccer_usa_mls",
    "บราซิล ซีรีเอ": "soccer_brazil_campeonato",
    "อาร์เจนตินา": "soccer_argentina_primera_division",
    "ยูฟ่า แชมเปียนส์ลีก": "soccer_uefa_champs_league",
    "ยูฟ่า ยูโรปาลีก": "soccer_uefa_europa_league",
    "ยูฟ่า คอนเฟอเรนซ์ลีก": "soccer_uefa_europa_conference_league",
}


# ---------------------------------------------------------
# DATABASE
# ---------------------------------------------------------

def get_connection():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS odds_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_at TEXT NOT NULL,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                league TEXT,
                kickoff TEXT,
                market TEXT,
                selection TEXT,
                line REAL,
                odds REAL,
                source TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                match_name TEXT NOT NULL,
                market TEXT NOT NULL,
                selection TEXT NOT NULL,
                line REAL,
                odds REAL NOT NULL,
                stake REAL NOT NULL,
                result TEXT DEFAULT 'Pending',
                profit REAL DEFAULT 0,
                notes TEXT
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS manual_prices (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                saved_at TEXT NOT NULL,
                match_date TEXT,
                league TEXT,
                home_team TEXT NOT NULL,
                away_team TEXT NOT NULL,
                opening_ah_line REAL,
                opening_ah_odds REAL,
                current_ah_line REAL,
                current_ah_odds REAL,
                opening_total_line REAL,
                opening_over_odds REAL,
                opening_under_odds REAL,
                current_total_line REAL,
                current_over_odds REAL,
                current_under_odds REAL,
                notes TEXT
            )
        """)

        conn.commit()


init_db()


# ---------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------

def thai_now():
    return datetime.now(THAI_TZ)


def parse_datetime(value):
    if not value:
        return None

    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC_TZ)

        return dt.astimezone(UTC_TZ)
    except (ValueError, TypeError, OverflowError):
        return None


def format_thai_time(value):
    dt = parse_datetime(value)

    if dt is None:
        return "-"

    return dt.astimezone(THAI_TZ).strftime("%d/%m/%Y %H:%M")


def filter_events_next_24h(events):
    """
    Keep only events scheduled from now through the next 24 hours.
    Comparisons are performed in UTC to avoid timezone errors.
    Display times are converted to Thailand time.
    """
    now_utc = datetime.now(UTC_TZ)
    end_utc = now_utc + timedelta(hours=24)

    result = []

    for event in events:
        kickoff = parse_datetime(event.get("commence_time"))

        if kickoff is None:
            continue

        if now_utc <= kickoff <= end_utc:
            item = dict(event)
            item["kickoff_th"] = kickoff.astimezone(
                THAI_TZ
            ).strftime("%d/%m/%Y %H:%M")
            result.append(item)

    return sorted(
        result,
        key=lambda x: x.get("commence_time", "")
    )


def safe_float(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default
        number = float(value)
        if not math.isfinite(number):
            return default
        return number
    except (ValueError, TypeError):
        return default


def valid_odds(value):
    number = safe_float(value)
    return number is not None and number > 1.0


# ---------------------------------------------------------
# API KEY AND ODDS API
# ---------------------------------------------------------

def get_api_key():
    key = os.getenv("ODDS_API_KEY", "").strip()

    try:
        secret_key = st.secrets.get("ODDS_API_KEY", "")
        if secret_key:
            key = str(secret_key).strip()
    except Exception:
        pass

    # Session input is useful for local testing.
    # It is not written to the database.
    session_key = st.session_state.get("runtime_api_key", "")
    if session_key:
        key = str(session_key).strip()

    return key


def api_get(path, params=None, timeout=25):
    api_key = get_api_key()

    if not api_key:
        raise RuntimeError(
            "ยังไม่ได้ตั้งค่า API Key กรุณาใส่ในแถบด้านข้าง "
            "หรือกำหนด ODDS_API_KEY ใน Streamlit secrets"
        )

    query = dict(params or {})
    query["apiKey"] = api_key

    response = requests.get(
        f"{API_BASE}{path}",
        params=query,
        timeout=timeout
    )

    if response.status_code == 401:
        raise RuntimeError("API Key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้งาน")

    if response.status_code == 429:
        raise RuntimeError(
            "เรียก API เกินโควตาหรือเกินอัตราที่อนุญาต "
            "กรุณาตรวจสอบโควตา The Odds API"
        )

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]

        raise RuntimeError(
            f"The Odds API ตอบกลับ HTTP {response.status_code}: {detail}"
        )

    return response.json(), response.headers


@st.cache_data(ttl=60, show_spinner=False)
def fetch_events(sport_key, api_key, _refresh_token=0):
    """
    Fetch spreads and totals odds.
    The API key is passed to this function so cache entries are
    separated by key. The key is never displayed in the UI.
    """
    url = f"{API_BASE}/sports/{sport_key}/odds"

    params = {
        "apiKey": api_key,
        "regions": "uk,eu,us",
        "markets": "spreads,totals",
        "oddsFormat": "decimal",
        "dateFormat": "iso",
    }

    response = requests.get(url, params=params, timeout=30)

    if response.status_code == 401:
        raise RuntimeError("API Key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้งาน")

    if response.status_code == 429:
        raise RuntimeError("API โควตาเต็มหรือเรียกถี่เกินไป")

    if response.status_code >= 400:
        try:
            detail = response.json()
        except Exception:
            detail = response.text[:500]

        raise RuntimeError(
            f"API error {response.status_code}: {detail}"
        )

    return response.json(), dict(response.headers)


def get_quota_info(headers):
    return {
        "เหลือ": headers.get("x-requests-remaining", "-"),
        "ใช้ไป": headers.get("x-requests-used", "-"),
        "ราคาโดยประมาณ": headers.get("x-requests-last", "-"),
    }


# ---------------------------------------------------------
# PARSE ODDS
# ---------------------------------------------------------

def find_best_market(event, market_key):
    """
    Find the highest decimal odds for each outcome/line.
    For analysis, AH and totals selections are grouped by line.
    """
    best = {}

    for bookmaker in event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != market_key:
                continue

            for outcome in market.get("outcomes", []):
                name = outcome.get("name")
                price = safe_float(outcome.get("price"))
                point = safe_float(outcome.get("point"))

                if not valid_odds(price):
                    continue

                key = (name, point)

                if key not in best or price > best[key]["odds"]:
                    best[key] = {
                        "name": name,
                        "point": point,
                        "odds": price,
                        "bookmaker": bookmaker.get("title", ""),
                    }

    return best


def parse_event_odds(event, league_name):
    home = event.get("home_team", "")
    away = event.get("away_team", "")

    spreads = find_best_market(event, "spreads")
    totals = find_best_market(event, "totals")

    home_ah = None
    away_ah = None
    total_over = None
    total_under = None

    # Use the best price for each team's available handicap line.
    for (name, point), data in spreads.items():
        if name == home:
            if home_ah is None or data["odds"] > home_ah["odds"]:
                home_ah = data
        elif name == away:
            if away_ah is None or data["odds"] > away_ah["odds"]:
                away_ah = data

    # Over and Under must use the same total line for a fair comparison.
    total_lines = {}
    for (name, point), data in totals.items():
        if point is None:
            continue
        total_lines.setdefault(point, {})
        total_lines[point][name.lower()] = data

    complete_total_lines = [
        (line, values)
        for line, values in total_lines.items()
        if "over" in values and "under" in values
    ]

    if complete_total_lines:
        # Select the total line with the best combined availability.
        line, values = sorted(
            complete_total_lines,
            key=lambda item: (
                item[1]["over"]["odds"] + item[1]["under"]["odds"]
            ),
            reverse=True
        )[0]

        total_over = values["over"]
        total_under = values["under"]

    return {
        "event_id": event.get("id", ""),
        "league": league_name,
        "home": home,
        "away": away,
        "commence_time": event.get("commence_time"),
        "kickoff_th": event.get("kickoff_th", format_thai_time(
            event.get("commence_time")
        )),
        "home_ah_line": home_ah.get("point") if home_ah else None,
        "home_ah_odds": home_ah.get("odds") if home_ah else None,
        "home_ah_book": home_ah.get("bookmaker") if home_ah else "",
        "away_ah_line": away_ah.get("point") if away_ah else None,
        "away_ah_odds": away_ah.get("odds") if away_ah else None,
        "away_ah_book": away_ah.get("bookmaker") if away_ah else "",
        "total_line": (
            total_over.get("point") if total_over else None
        ),
        "over_odds": total_over.get("odds") if total_over else None,
        "under_odds": total_under.get("odds") if total_under else None,
        "over_book": total_over.get("bookmaker") if total_over else "",
        "under_book": total_under.get("bookmaker") if total_under else "",
    }


# ---------------------------------------------------------
# HISTORICAL RESULTS / EXPECTED GOALS
# ---------------------------------------------------------

def normalize_history_columns(df):
    """
    Accept common Football-Data CSV columns:
    HomeTeam, AwayTeam, FTHG, FTAG
    Also accepts common alternative column names.
    """
    df = df.copy()
    df.columns = [
        str(c).strip().lower().replace(" ", "").replace("_", "")
        for c in df.columns
    ]

    aliases = {
        "hometeam": ["hometeam", "home", "teamhome"],
        "awayteam": ["awayteam", "away", "teamaway"],
        "fthg": ["fthg", "homegoals", "hg", "goalsforhome"],
        "ftag": ["ftag", "awaygoals", "ag", "goalsforaway"],
    }

    rename = {}
    for target, options in aliases.items():
        for option in options:
            if option in df.columns:
                rename[option] = target
                break

    df = df.rename(columns=rename)

    required = ["hometeam", "awayteam", "fthg", "ftag"]
    if not all(c in df.columns for c in required):
        raise ValueError(
            "ไฟล์ผลย้อนหลังต้องมีคอลัมน์ทีมเหย้า ทีมเยือน "
            "ประตูทีมเหย้า และประตูทีมเยือน เช่น HomeTeam, AwayTeam, FTHG, FTAG"
        )

    df["hometeam"] = df["hometeam"].astype(str).str.strip()
    df["awayteam"] = df["awayteam"].astype(str).str.strip()
    df["fthg"] = pd.to_numeric(df["fthg"], errors="coerce")
    df["ftag"] = pd.to_numeric(df["ftag"], errors="coerce")

    df = df.dropna(subset=["fthg", "ftag"])
    df = df[
        (df["fthg"] >= 0)
        & (df["ftag"] >= 0)
        & (df["fthg"] <= 20)
        & (df["ftag"] <= 20)
    ]

    return df[required].reset_index(drop=True)


def team_history_stats(history, team):
    if history is None or history.empty:
        return None

    home_games = history[history["hometeam"].str.casefold() == team.casefold()]
    away_games = history[history["awayteam"].str.casefold() == team.casefold()]

    scored = []
    conceded = []

    if not home_games.empty:
        scored.extend(home_games["fthg"].tolist())
        conceded.extend(home_games["ftag"].tolist())

    if not away_games.empty:
        scored.extend(away_games["ftag"].tolist())
        conceded.extend(away_games["fthg"].tolist())

    if not scored:
        return None

    return {
        "matches": len(scored),
        "scored_avg": sum(scored) / len(scored),
        "conceded_avg": sum(conceded) / len(conceded),
    }


def estimate_lambdas(history, home, away):
    """
    Estimate expected goals from uploaded historical results.
    This is a basic Poisson model, not a guaranteed prediction.
    """
    if history is None or history.empty:
        return None, None, "ไม่มีข้อมูลย้อนหลัง"

    home_hist = history[history["hometeam"].str.casefold() == home.casefold()]
    away_hist = history[history["awayteam"].str.casefold() == away.casefold()]

    home_any = history[
        (history["hometeam"].str.casefold() == home.casefold())
        | (history["awayteam"].str.casefold() == home.casefold())
    ]
    away_any = history[
        (history["hometeam"].str.casefold() == away.casefold())
        | (history["awayteam"].str.casefold() == away.casefold())
    ]

    if home_any.empty or away_any.empty:
        return None, None, "ไม่มีข้อมูลทีมครบ"

    # Prefer home/away splits, falling back to all matches for each team.
    h_home_scored = home_hist["fthg"].mean() if not home_hist.empty else None
    h_home_conceded = home_hist["ftag"].mean() if not home_hist.empty else None

    a_away_scored = away_hist["ftag"].mean() if not away_hist.empty else None
    a_away_conceded = away_hist["fthg"].mean() if not away_hist.empty else None

    home_stats = team_history_stats(history, home)
    away_stats = team_history_stats(history, away)

    if h_home_scored is None:
        h_home_scored = home_stats["scored_avg"]
    if h_home_conceded is None:
        h_home_conceded = home_stats["conceded_avg"]
    if a_away_scored is None:
        a_away_scored = away_stats["scored_avg"]
    if a_away_conceded is None:
        a_away_conceded = away_stats["conceded_avg"]

    league_home_avg = max(float(history["fthg"].mean()), 0.2)
    league_away_avg = max(float(history["ftag"].mean()), 0.2)

    # Attack/defence strength relative to league averages.
    home_attack = max(h_home_scored, 0.05) / league_home_avg
    home_defence = max(h_home_conceded, 0.05) / league_away_avg
    away_attack = max(a_away_scored, 0.05) / league_away_avg
    away_defence = max(a_away_conceded, 0.05) / league_home_avg

    lambda_home = league_home_avg * home_attack * away_defence
    lambda_away = league_away_avg * away_attack * home_defence

    # Keep unrealistic inputs from exploding the Poisson distribution.
    lambda_home = min(max(lambda_home, 0.15), 4.5)
    lambda_away = min(max(lambda_away, 0.15), 4.5)

    if len(home_any) < 5 or len(away_any) < 5:
        status = "ข้อมูลจำกัด: ทีมใดทีมหนึ่งมีผลย้อนหลังน้อยกว่า 5 นัด"
    else:
        status = "มีข้อมูลย้อนหลัง"

    return lambda_home, lambda_away, status


def poisson_pmf(k, lam):
    return math.exp(-lam) * (lam ** k) / math.factorial(k)


def score_probabilities(lambda_home, lambda_away, max_goals=10):
    probs = {}

    home_p = [poisson_pmf(i, lambda_home) for i in range(max_goals + 1)]
    away_p = [poisson_pmf(i, lambda_away) for i in range(max_goals + 1)]

    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            probs[(h, a)] = home_p[h] * away_p[a]

    total_prob = sum(probs.values())

    if total_prob > 0:
        probs = {score: p / total_prob for score, p in probs.items()}

    return probs


# ---------------------------------------------------------
# ASIAN LINE SETTLEMENT AND EV
# ---------------------------------------------------------

def split_asian_line(line):
    """
    Split quarter lines into two adjacent lines:
    0.25 -> 0.0 and 0.5
    0.75 -> 0.5 and 1.0
    Whole and half lines remain single.
    """
    if line is None:
        return []

    line = float(line)
    scaled = line * 4

    if abs(scaled - round(scaled)) > 1e-7:
        return [line]

    quarter_remainder = round(abs(line) * 4) % 2

    if quarter_remainder == 1:
        if line >= 0:
            return [line - 0.25, line + 0.25]
        return [line - 0.25, line + 0.25]

    return [line]


def single_bet_net(outcome, odds):
    if outcome > 0:
        return odds - 1.0
    if outcome == 0:
        return 0.0
    return -1.0


def asian_handicap_outcome(goal_difference, line):
    adjusted = goal_difference + line

    if adjusted > 1e-9:
        return 1
    if adjusted < -1e-9:
        return -1
    return 0


def total_outcome(total_goals, line, side):
    if side == "Over":
        diff = total_goals - line
    else:
        diff = line - total_goals

    if diff > 1e-9:
        return 1
    if diff < -1e-9:
        return -1
    return 0


def asian_ev(probs, odds, line, market, side):
    """
    Expected net return per unit staked.
    Returns EV as a decimal: 0.05 means +5%.
    Quarter lines are split into two half-stakes.
    """
    if not valid_odds(odds) or line is None:
        return None

    lines = split_asian_line(float(line))
    if not lines:
        return None

    split_weight = 1.0 / len(lines)
    expected_net = 0.0

    for (home_goals, away_goals), probability in probs.items():
        score_net = 0.0

        for sub_line in lines:
            if market == "AH":
                if side == "Home":
                    outcome = asian_handicap_outcome(
                        home_goals - away_goals, sub_line
                    )
                else:
                    outcome = asian_handicap_outcome(
                        away_goals - home_goals, sub_line
                    )
            else:
                outcome = total_outcome(
                    home_goals + away_goals,
                    sub_line,
                    side
                )

            score_net += split_weight * single_bet_net(outcome, odds)

        expected_net += probability * score_net

    return expected_net


def ev_label(ev):
    if ev is None:
        return "ยังคำนวณไม่ได้"
    if ev >= 0.05:
        return "EV บวกสูง"
    if ev > 0:
        return "EV บวก"
    return "EV ไม่บวก"


# ---------------------------------------------------------
# SNAPSHOTS AND BET LEDGER
# ---------------------------------------------------------

def save_odds_snapshot(rows):
    now = thai_now().isoformat()

    with get_connection() as conn:
        for row in rows:
            for market, selection, line, odds, source in [
                ("AH", row["home"], row["home_ah_line"],
                 row["home_ah_odds"], row["home_ah_book"]),
                ("AH", row["away"], row["away_ah_line"],
                 row["away_ah_odds"], row["away_ah_book"]),
                ("OU", "Over", row["total_line"],
                 row["over_odds"], row["over_book"]),
                ("OU", "Under", row["total_line"],
                 row["under_odds"], row["under_book"]),
            ]:
                if odds is None:
                    continue

                conn.execute("""
                    INSERT INTO odds_snapshots (
                        saved_at, home_team, away_team, league,
                        kickoff, market, selection, line, odds, source
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    now, row["home"], row["away"], row["league"],
                    row["commence_time"], market, selection,
                    line, odds, source
                ))

        conn.commit()


def get_bets():
    with get_connection() as conn:
        return pd.read_sql_query(
            "SELECT * FROM bets ORDER BY id DESC", conn
        )


def add_bet(match_name, market, selection, line, odds, stake, notes):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO bets (
                created_at, match_name, market, selection,
                line, odds, stake, result, profit, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Pending', 0, ?)
        """, (
            thai_now().isoformat(),
            match_name,
            market,
            selection,
            line,
            odds,
            stake,
            notes
        ))
        conn.commit()


def settle_bet(bet_id, result):
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM bets WHERE id = ?", (bet_id,)
        ).fetchone()

        if row is None:
            return

        stake = float(row["stake"])
        odds = float(row["odds"])

        if result == "Win":
            profit = stake * (odds - 1)
        elif result == "Half Win":
            profit = stake * (odds - 1) / 2
        elif result == "Push":
            profit = 0.0
        elif result == "Half Loss":
            profit = -stake / 2
        elif result == "Loss":
            profit = -stake
        else:
            profit = 0.0

        conn.execute("""
            UPDATE bets
            SET result = ?, profit = ?
            WHERE id = ?
        """, (result, profit, int(bet_id)))

        conn.commit()


# ---------------------------------------------------------
# SIDEBAR SETTINGS
# ---------------------------------------------------------

with st.sidebar:
    st.header("⚙️ ตั้งค่าระบบ")

    key_input = st.text_input(
        "The Odds API Key",
        type="password",
        help="คีย์จะใช้ใน session นี้เท่านั้น หากต้องการตั้งค่าถาวรให้ใช้ Streamlit secrets"
    )

    if key_input.strip():
        st.session_state["runtime_api_key"] = key_input.strip()

    st.caption("ระบบเปรียบเทียบเวลาแข่งขันด้วย UTC และแสดงผลเป็นเวลาไทย UTC+7")

    selected_leagues = st.multiselect(
        "เลือกลีก",
        options=list(LEAGUES.keys()),
        default=["พรีเมียร์ลีก อังกฤษ", "ลาลีกา สเปน", "บุนเดสลีกา เยอรมนี"]
    )

    st.divider()
    st.subheader("ข้อมูลย้อนหลัง")

    uploaded_history = st.file_uploader(
        "อัปโหลดผลการแข่งขัน CSV",
        type=["csv"],
        help="ตัวอย่างคอลัมน์: HomeTeam, AwayTeam, FTHG, FTAG"
    )

    history_df = None

    if uploaded_history is not None:
        try:
            raw_history = pd.read_csv(uploaded_history)
            history_df = normalize_history_columns(raw_history)
            st.success(f"อ่านข้อมูลย้อนหลังได้ {len(history_df)} นัด")
        except Exception as exc:
            st.error(f"อ่านไฟล์ย้อนหลังไม่สำเร็จ: {exc}")

    st.caption(
        "หมายเหตุ: หากไม่มีไฟล์ผลย้อนหลัง ระบบจะแสดงราคาได้ "
        "แต่ไม่สามารถสร้าง EV จากแบบจำลองทีมได้อย่างน่าเชื่อถือ"
    )


# ---------------------------------------------------------
# MAIN TABS
# ---------------------------------------------------------

tab_odds, tab_manual, tab_history, tab_ledger, tab_settings = st.tabs([
    "📊 วิเคราะห์คู่ 24 ชั่วโมง",
    "✍️ กรอกราคาเอง",
    "📚 ผลย้อนหลัง",
    "🧾 บันทึกเดิมพัน",
    "ℹ️ วิธีใช้ / สถานะ"
])


# ---------------------------------------------------------
# TAB 1: NEXT 24 HOURS
# ---------------------------------------------------------

with tab_odds:
    now_th = thai_now()
    end_th = now_th + timedelta(hours=24)

    st.subheader("คู่ที่กำหนดแข่งขันภายใน 24 ชั่วโมง")

    st.write(
        f"เวลาปัจจุบัน: **{now_th.strftime('%d/%m/%Y %H:%M')}** "
        f"ถึง **{end_th.strftime('%d/%m/%Y %H:%M')}** (เวลาไทย)"
    )

    col_refresh, col_status = st.columns([1, 3])

    with col_refresh:
        refresh_clicked = st.button(
            "🔄 ดึงราคาล่าสุด",
            type="primary",
            use_container_width=True
        )

    if refresh_clicked:
        fetch_events.clear()

    all_rows = []
    errors = []
    quota_rows = []

    api_key = get_api_key()

    if not api_key:
        st.info("ใส่ The Odds API Key ที่แถบด้านซ้ายก่อนดึงราคา")
    elif not selected_leagues:
        st.warning("กรุณาเลือกลีกอย่างน้อย 1 ลีก")
    else:
        progress = st.progress(0)

        for idx, league_name in enumerate(selected_leagues):
            sport_key = LEAGUES[league_name]

            try:
                events, headers = fetch_events(
                    sport_key,
                    api_key,
                    _refresh_token=int(refresh_clicked)
                )

                quota_rows.append({
                    "ลีก": league_name,
                    **get_quota_info(headers)
                })

                # Filter before parsing or calculating EV.
                filtered_events = filter_events_next_24h(events)

                for event in filtered_events:
                    row = parse_event_odds(event, league_name)
                    all_rows.append(row)

            except Exception as exc:
                errors.append(f"{league_name}: {exc}")

            progress.progress((idx + 1) / len(selected_leagues))

        progress.empty()

        if errors:
            with st.expander("รายละเอียดข้อผิดพลาด", expanded=True):
                for error in errors:
                    st.error(error)

        if quota_rows:
            with st.expander("สถานะโควตา API"):
                st.dataframe(
                    pd.DataFrame(quota_rows),
                    use_container_width=True,
                    hide_index=True
                )

    if api_key and selected_leagues:
        if not all_rows:
            st.warning(
                "ไม่พบคู่แข่งขันที่เข้าเงื่อนไขใน 24 ชั่วโมงข้างหน้า "
                "หรือ API ไม่มีราคาให้สำหรับลีกที่เลือก"
            )
        else:
            # Calculate historical model and EV for each event.
            analysis_rows = []

            for row in all_rows:
                lh, la, hist_status = estimate_lambdas(
                    history_df, row["home"], row["away"]
                )

                ev_home = None
                ev_away = None
                ev_over = None
                ev_under = None
                lambda_total = None

                if lh is not None and la is not None:
                    probs = score_probabilities(lh, la)
                    lambda_total = lh + la

                    if row["home_ah_line"] is not None:
                        ev_home = asian_ev(
                            probs,
                            row["home_ah_odds"],
                            row["home_ah_line"],
                            "AH",
                            "Home"
                        )

                    if row["away_ah_line"] is not None:
                        ev_away = asian_ev(
                            probs,
                            row["away_ah_odds"],
                            row["away_ah_line"],
                            "AH",
                            "Away"
                        )

                    if row["total_line"] is not None:
                        ev_over = asian_ev(
                            probs,
                            row["over_odds"],
                            row["total_line"],
                            "OU",
                            "Over"
                        )
                        ev_under = asian_ev(
                            probs,
                            row["under_odds"],
                            row["total_line"],
                            "OU",
                            "Under"
                        )

                row["history_status"] = hist_status
                row["lambda_home"] = lh
                row["lambda_away"] = la
                row["lambda_total"] = lambda_total
                row["ev_home"] = ev_home
                row["ev_away"] = ev_away
                row["ev_over"] = ev_over
                row["ev_under"] = ev_under

                analysis_rows.append(row)

            # Save current odds snapshot for tracking changes over time.
            if st.button("💾 บันทึกราคาชุดนี้ลงฐานข้อมูล"):
                save_odds_snapshot(analysis_rows)
                st.success("บันทึกราคาชุดนี้แล้ว")

            # Summary metrics
            m1, m2, m3 = st.columns(3)
            m1.metric("คู่ใน 24 ชั่วโมง", len(analysis_rows))
            m2.metric(
                "คู่ที่มีข้อมูลย้อนหลัง",
                sum(
                    1 for r in analysis_rows
                    if r["lambda_home"] is not None
                )
            )
            m3.metric(
                "คู่ที่มีราคา AH",
                sum(
                    1 for r in analysis_rows
                    if r["home_ah_odds"] is not None
                    and r["away_ah_odds"] is not None
                )
            )

            st.caption(
                "ราคาเป็นราคาที่ API ส่งกลับ ณ เวลาดึงข้อมูล "
                "ไม่ใช่การรับประกันว่าราคายังอยู่ที่เจ้ามือ"
            )

            # One row per event, with all relevant markets.
            display_rows = []

            for r in analysis_rows:
                display_rows.append({
                    "วันเวลาไทย": r["kickoff_th"],
                    "ลีก": r["league"],
                    "คู่แข่งขัน": f'{r["home"]} vs {r["away"]}',
                    "AH ทีมเหย้า": (
                        f'{r["home_ah_line"]:+g} @ {r["home_ah_odds"]:.2f}'
                        if r["home_ah_line"] is not None
                        and r["home_ah_odds"] is not None else "-"
                    ),
                    "AH ทีมเยือน": (
                        f'{r["away_ah_line"]:+g} @ {r["away_ah_odds"]:.2f}'
                        if r["away_ah_line"] is not None
                        and r["away_ah_odds"] is not None else "-"
                    ),
                    "สูง": (
                        f'{r["total_line"]:g} @ {r["over_odds"]:.2f}'
                        if r["total_line"] is not None
                        and r["over_odds"] is not None else "-"
                    ),
                    "ต่ำ": (
                        f'{r["total_line"]:g} @ {r["under_odds"]:.2f}'
                        if r["total_line"] is not None
                        and r["under_odds"] is not None else "-"
                    ),
                    "คาดการณ์ประตู": (
                        f'{r["lambda_home"]:.2f} - {r["lambda_away"]:.2f}'
                        if r["lambda_home"] is not None else "-"
                    ),
                    "สถานะข้อมูล": r["history_status"],
                })

            st.dataframe(
                pd.DataFrame(display_rows),
                use_container_width=True,
                hide_index=True
            )

            st.subheader("ผลวิเคราะห์ EV")

            ev_rows = []

            for r in analysis_rows:
                match = f'{r["home"]} vs {r["away"]}'

                selections = [
                    ("AH", r["home"], r["home_ah_line"],
                     r["home_ah_odds"], r["ev_home"]),
                    ("AH", r["away"], r["away_ah_line"],
                     r["away_ah_odds"], r["ev_away"]),
                    ("สูง/ต่ำ", "สูง", r["total_line"],
                     r["over_odds"], r["ev_over"]),
                    ("สูง/ต่ำ", "ต่ำ", r["total_line"],
                     r["under_odds"], r["ev_under"]),
                ]

                for market, selection, line, odds, ev in selections:
                    if odds is None:
                        continue

                    ev_rows.append({
                        "วันเวลาไทย": r["kickoff_th"],
                        "ลีก": r["league"],
                        "คู่": match,
                        "ตลาด": market,
                        "ตัวเลือก": selection,
                        "ไลน์": line if line is not None else "-",
                        "ราคา": odds,
                        "EV (%)": round(ev * 100, 2) if ev is not None else None,
                        "ประเมิน": ev_label(ev),
                        "ข้อมูล": r["history_status"],
                    })

            if ev_rows:
                ev_df = pd.DataFrame(ev_rows)
                ev_df = ev_df.sort_values(
                    "EV (%)",
                    ascending=False,
                    na_position="last"
                )

                st.dataframe(
                    ev_df,
                    use_container_width=True,
                    hide_index=True
                )

                positive_ev = ev_df[
                    pd.to_numeric(ev_df["EV (%)"], errors="coerce") > 0
                ]

                if positive_ev.empty:
                    st.info(
                        "ยังไม่พบตัวเลือก EV บวกจากแบบจำลองและข้อมูลที่มี "
                        "ไม่จำเป็นต้องเดิมพันทุกคู่"
                    )
                else:
                    st.success(
                        f"พบตัวเลือก EV บวก {len(positive_ev)} รายการ "
                        "ควรตรวจสอบราคาและข้อมูลทีมก่อนตัดสินใจ"
                    )

                csv_data = ev_df.to_csv(
                    index=False
                ).encode("utf-8-sig")

                st.download_button(
                    "ดาวน์โหลดผลวิเคราะห์ CSV",
                    data=csv_data,
                    file_name="value_bet_analysis.csv",
                    mime="text/csv"
                )
            else:
                st.info("ยังไม่มีราคาเพียงพอสำหรับสร้างตาราง EV")


# ---------------------------------------------------------
# TAB 2: MANUAL OPENING / CURRENT PRICES
# ---------------------------------------------------------

with tab_manual:
    st.subheader("กรอกราคาเปิดและราคาปัจจุบันด้วยตนเอง")
    st.caption(
        "ใช้บันทึกการไหลของราคา เช่น ราคาเปิดเทียบราคาช่วงเย็น "
        "ระบบยังไม่สามารถยืนยันได้เองว่าราคาที่กรอกเป็นราคาจริงจากเจ้ามือ"
    )

    with st.form("manual_price_form"):
        c1, c2, c3 = st.columns(3)

        with c1:
            manual_league = st.selectbox(
                "ลีก",
                ["ไม่ระบุ"] + list(LEAGUES.keys())
            )
            match_date = st.text_input(
                "วันเวลาแข่งขัน (เวลาไทย)",
                placeholder="เช่น 14/10/2026 20:00"
            )
            manual_home = st.text_input("ทีมเหย้า")
            manual_away = st.text_input("ทีมเยือน")

        with c2:
            st.markdown("**แฮนดิแคป**")
            opening_ah_line = st.number_input(
                "ไลน์เปิด AH (มุมทีมเหย้า)",
                value=0.0, step=0.25, format="%.2f"
            )
            opening_ah_odds = st.number_input(
                "ราคาเปิด AH",
                min_value=1.01, value=1.90, step=0.01
            )
            current_ah_line = st.number_input(
                "ไลน์ปัจจุบัน AH (มุมทีมเหย้า)",
                value=0.0, step=0.25, format="%.2f"
            )
            current_ah_odds = st.number_input(
                "ราคาปัจจุบัน AH",
                min_value=1.01, value=1.90, step=0.01
            )

        with c3:
            st.markdown("**สูง / ต่ำ**")
            opening_total_line = st.number_input(
                "ไลน์เปิด สูง/ต่ำ",
                min_value=0.0, value=2.5, step=0.25, format="%.2f"
            )
            opening_over_odds = st.number_input(
                "ราคาเปิด สูง",
                min_value=1.01, value=1.90, step=0.01
            )
            opening_under_odds = st.number_input(
                "ราคาเปิด ต่ำ",
                min_value=1.01, value=1.90, step=0.01
            )
            current_total_line = st.number_input(
                "ไลน์ปัจจุบัน สูง/ต่ำ",
                min_value=0.0, value=2.5, step=0.25, format="%.2f"
            )
            current_over_odds = st.number_input(
                "ราคาปัจจุบัน สูง",
                min_value=1.01, value=1.90, step=0.01
            )
            current_under_odds = st.number_input(
                "ราคาปัจจุบัน ต่ำ",
                min_value=1.01, value=1.90, step=0.01
            )

        manual_notes = st.text_area(
            "หมายเหตุ",
            placeholder="เช่น ราคาเปิดเช้า ราคาช่วงเย็น ข่าวทีม หรือเหตุผลที่สนใจ"
        )

        submitted_manual = st.form_submit_button(
            "บันทึกราคา",
            type="primary"
        )

        if submitted_manual:
            if not manual_home.strip() or not manual_away.strip():
                st.error("กรุณากรอกชื่อทีมเหย้าและทีมเยือน")
            else:
                with get_connection() as conn:
                    conn.execute("""
                        INSERT INTO manual_prices (
                            saved_at, match_date, league,
                            home_team, away_team,
                            opening_ah_line, opening_ah_odds,
                            current_ah_line, current_ah_odds,
                            opening_total_line, opening_over_odds,
                            opening_under_odds, current_total_line,
                            current_over_odds, current_under_odds, notes
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        thai_now().isoformat(),
                        match_date,
                        manual_league,
                        manual_home.strip(),
                        manual_away.strip(),
                        opening_ah_line,
                        opening_ah_odds,
                        current_ah_line,
                        current_ah_odds,
                        opening_total_line,
                        opening_over_odds,
                        opening_under_odds,
                        current_total_line,
                        current_over_odds,
                        current_under_odds,
                        manual_notes
                    ))
                    conn.commit()

                st.success("บันทึกราคาเรียบร้อย")

    with get_connection() as conn:
        manual_df = pd.read_sql_query(
            "SELECT * FROM manual_prices ORDER BY id DESC",
            conn
        )

    if not manual_df.empty:
        st.markdown("**ประวัติราคาเปิดเทียบราคาปัจจุบัน**")
        st.dataframe(manual_df, use_container_width=True, hide_index=True)

        st.download_button(
            "ดาวน์โหลดประวัติราคา CSV",
            data=manual_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="manual_prices.csv",
            mime="text/csv"
        )
    else:
        st.info("ยังไม่มีรายการราคาที่บันทึก")


# ---------------------------------------------------------
# TAB 3: HISTORY
# ---------------------------------------------------------

with tab_history:
    st.subheader("ผลการแข่งขันย้อนหลัง")

    if history_df is not None and not history_df.empty:
        st.success(f"มีข้อมูลที่อ่านได้ {len(history_df)} นัด")
        st.dataframe(
            history_df.tail(100),
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "ดาวน์โหลดข้อมูลย้อนหลังที่อ่านแล้ว",
            data=history_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="normalized_history.csv",
            mime="text/csv"
        )
    else:
        st.info(
            "ยังไม่มีข้อมูลย้อนหลังใน session นี้ "
            "อัปโหลดไฟล์ CSV ที่มีผลการแข่งขันเพื่อให้ระบบประมาณประตูและคำนวณ EV"
        )

    st.markdown("**รูปแบบไฟล์ที่แนะนำ**")
    st.code(
        "HomeTeam,AwayTeam,FTHG,FTAG\n"
        "Arsenal,Chelsea,2,1\n"
        "Liverpool,Everton,3,0",
        language="csv"
    )


# ---------------------------------------------------------
# TAB 4: BET LEDGER AND BACKTEST
# ---------------------------------------------------------

with tab_ledger:
    st.subheader("บันทึกเดิมพันและตรวจผลย้อนหลัง")

    with st.form("bet_form"):
        b1, b2, b3 = st.columns(3)

        with b1:
            bet_home = st.text_input("คู่แข่งขัน", placeholder="ทีม A vs ทีม B")
            bet_market = st.selectbox("ตลาด", ["AH", "สูง/ต่ำ"])
            bet_selection = st.text_input(
                "ตัวเลือก",
                placeholder="เช่น ทีมเหย้า / ทีมเยือน / สูง / ต่ำ"
            )

        with b2:
            bet_line = st.number_input(
                "ไลน์เดิมพัน",
                value=0.0,
                step=0.25,
                format="%.2f"
            )
            bet_odds = st.number_input(
                "ราคาที่เดิมพัน",
                min_value=1.01,
                value=1.90,
                step=0.01
            )

        with b3:
            bet_stake = st.number_input(
                "เงินเดิมพัน",
                min_value=0.01,
                value=100.0,
                step=10.0
            )
            bet_notes = st.text_input("หมายเหตุ")

        save_bet_clicked = st.form_submit_button(
            "บันทึกเดิมพัน",
            type="primary"
        )

        if save_bet_clicked:
            if not bet_home.strip() or not bet_selection.strip():
                st.error("กรุณากรอกคู่แข่งขันและตัวเลือก")
            else:
                add_bet(
                    bet_home.strip(),
                    bet_market,
                    bet_selection.strip(),
                    bet_line,
                    bet_odds,
                    bet_stake,
                    bet_notes
                )
                st.success("บันทึกเดิมพันแล้ว")

    bets_df = get_bets()

    if not bets_df.empty:
        st.markdown("**รายการเดิมพัน**")

        pending_df = bets_df[bets_df["result"] == "Pending"]

        if not pending_df.empty:
            st.markdown("**บันทึกผลเดิมพันที่ยัง Pending**")

            for _, bet in pending_df.iterrows():
                with st.expander(
                    f'#{int(bet["id"])} | {bet["match_name"]} | '
                    f'{bet["market"]} | {bet["selection"]}'
                ):
                    st.write(
                        f'ไลน์ {bet["line"]} | ราคา {bet["odds"]} | '
                        f'เงินเดิมพัน {bet["stake"]}'
                    )

                    result = st.selectbox(
                        "ผลเดิมพัน",
                        [
                            "Pending", "Win", "Half Win",
                            "Push", "Half Loss", "Loss"
                        ],
                        key=f'result_{int(bet["id"])}'
                    )

                    if st.button(
                        "บันทึกผล",
                        key=f'settle_{int(bet["id"])}'
                    ):
                        settle_bet(int(bet["id"]), result)
                        st.rerun()

        total_stake = pd.to_numeric(
            bets_df["stake"], errors="coerce"
        ).fillna(0).sum()

        total_profit = pd.to_numeric(
            bets_df["profit"], errors="coerce"
        ).fillna(0).sum()

        settled = bets_df[bets_df["result"] != "Pending"]
        wins = bets_df[
            bets_df["result"].isin(["Win", "Half Win"])
        ]

        settled_count = len(settled)
        win_rate = (
            len(wins) / settled_count * 100
            if settled_count > 0 else 0
        )

        c1, c2, c3 = st.columns(3)
        c1.metric("เงินเดิมพันรวม", f"{total_stake:,.2f}")
        c2.metric("กำไร/ขาดทุนที่บันทึก", f"{total_profit:,.2f}")
        c3.metric("Win rate โดยประมาณ", f"{win_rate:.1f}%")

        st.dataframe(
            bets_df,
            use_container_width=True,
            hide_index=True
        )

        st.download_button(
            "ดาวน์โหลดประวัติเดิมพัน CSV",
            data=bets_df.to_csv(index=False).encode("utf-8-sig"),
            file_name="bet_ledger.csv",
            mime="text/csv"
        )

        st.caption(
            "Win rate ในตารางนี้นับ Win และ Half Win เป็นรายการชนะ "
            "ไม่ใช่ Win rate แบบถ่วงน้ำหนักตามเงินเดิมพัน"
        )
    else:
        st.info("ยังไม่มีรายการเดิมพัน")


# ---------------------------------------------------------
# TAB 5: STATUS / HOW TO USE
# ---------------------------------------------------------

with tab_settings:
    st.subheader("สถานะระบบและข้อจำกัด")

    st.markdown("""
    **ระบบนี้ทำอะไรได้บ้าง**
    
    1. ดึงราคาจาก The Odds API ในตลาด Asian Handicap (`spreads`)
       และสูง/ต่ำ (`totals`)
    2. กรองเฉพาะคู่ที่กำหนดเริ่มแข่งขันตั้งแต่เวลาปัจจุบัน
       จนถึง 24 ชั่วโมงข้างหน้า
    3. ตัดคู่ที่เวลาเริ่มแข่งขันผ่านไปแล้วออกจากการวิเคราะห์
    4. แสดงเวลาแข่งขันเป็นเวลาไทย (UTC+7)
    5. ใช้ไฟล์ผลย้อนหลังที่ผู้ใช้อัปโหลดเพื่อประมาณจำนวนประตู
    6. คำนวณ EV จากแบบจำลอง Poisson พร้อมรองรับไลน์เอเชียนแบบควอเตอร์
    7. บันทึกราคาที่ดึงมา ราคาเปิด/ปัจจุบันที่กรอกเอง และผลเดิมพันลง SQLite
    8. ส่งออกตารางเป็น CSV ได้
    """)

    st.warning("""
    **ข้อจำกัดที่ควรทราบ**

    - EV เป็นค่าประมาณจากแบบจำลองและข้อมูลที่ป้อน ไม่ใช่ความน่าจะเป็นจริงที่รับประกันได้
    - ถ้าไม่มีผลย้อนหลังที่เหมาะสม ระบบจะไม่คำนวณ EV จากข้อมูลทีม
    - ชื่อทีมในไฟล์ย้อนหลังต้องตรงกับชื่อทีมจาก API จึงจะจับคู่ได้
    - การใช้สถิติรวมแบบง่ายยังไม่รวมตัวจริง อาการบาดเจ็บ ข่าวทีม สภาพอากาศ
      ความแข็งแกร่งของคู่แข่ง หรือการเปลี่ยนแปลงแท็กติก
    - ราคาใน API อาจไม่ตรงกับราคาเจ้ามือที่ผู้ใช้จะเดิมพันจริง
    - การกรอง 24 ชั่วโมงอาศัยเวลาเริ่มแข่งที่ API ส่งมา
      ไม่ได้ตรวจสถานะสดจากสนามหรือผลการแข่งขันโดยตรง
    - การบันทึกผลเดิมพันในระบบนี้ต้องกรอกผลเอง
    """)

    st.markdown("**การตั้งค่า API Key แบบถาวร**")
    st.write(
        "ใน Streamlit Cloud ให้เปิด App → Settings → Secrets "
        "แล้วใส่ค่าในรูปแบบนี้:"
    )
    st.code(
        'ODDS_API_KEY = "ใส่_API_KEY_ของนายท่านที่นี่"',
        language="toml"
    )

    st.write(
        "หากรันบนเครื่องตัวเอง สามารถใส่คีย์ในแถบด้านซ้าย "
        "หรือกำหนด environment variable ชื่อ ODDS_API_KEY ได้"
    )

    st.caption(
        f"เวลาระบบตอนนี้: {thai_now().strftime('%d/%m/%Y %H:%M:%S')} เวลาไทย"
    )

st.divider()
st.caption(
    "Value Bet Pro | ใช้เพื่อวิเคราะห์ข้อมูลและบันทึกผลเท่านั้น "
    "ไม่มีระบบใดรับประกันกำไรจากการเดิมพัน"
)
