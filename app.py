
import os
import math
import sqlite3
from pathlib import Path
from datetime import datetime, date

import pandas as pd
import requests
import streamlit as st

# ============================================================
# VALUE BET PRO — INTEGRATED DUAL ENGINE
# Engine 1: Value Bet / Poisson / Asian Handicap / Totals / EV
# Engine 2: Opening vs Current Odds / Form / H2H
# Storage: SQLite
# ============================================================

APP_NAME = "Value Bet Pro"
ODDS_BASE = "https://api.the-odds-api.com/v4"
FOOTBALL_DATA_BASE = "https://www.football-data.co.uk/mmz4281"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "value_bet_pro.db"

st.set_page_config(
    page_title=APP_NAME,
    page_icon="⚽",
    layout="wide",
)

# --------------------------- CONFIG ---------------------------

SEASONS = ["2627", "2526", "2425", "2324", "2223"]

LEAGUES = {
    "Premier League": {"fd": "E0", "odds": "soccer_epl"},
    "Championship": {"fd": "E1", "odds": "soccer_efl_champ"},
    "League One": {"fd": "E2", "odds": None},
    "League Two": {"fd": "E3", "odds": None},
    "La Liga": {"fd": "SP1", "odds": "soccer_spain_la_liga"},
    "Bundesliga": {"fd": "D1", "odds": "soccer_germany_bundesliga"},
    "Serie A": {"fd": "I1", "odds": "soccer_italy_serie_a"},
    "Ligue 1": {"fd": "F1", "odds": "soccer_france_ligue_one"},
    "Eredivisie": {"fd": "N1", "odds": "soccer_netherlands_eredivisie"},
    "Primeira Liga": {"fd": "P1", "odds": "soccer_portugal_primeira_liga"},
    "Scottish Premiership": {"fd": "SC0", "odds": "soccer_spl"},
}

ODDS_API_KEY = ""
try:
    ODDS_API_KEY = str(st.secrets.get("ODDS_API_KEY", "") or "")
except Exception:
    ODDS_API_KEY = ""

ODDS_API_KEY = ODDS_API_KEY or os.getenv("ODDS_API_KEY", "")

REQUEST_HEADERS = {"User-Agent": "ValueBetPro/2.0"}

# ------------------------ DATABASE ----------------------------

def db_connect():
    conn = sqlite3.connect(DB_PATH, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db_connect() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            saved_at TEXT NOT NULL,
            match_date TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            bookmaker TEXT,
            market TEXT,
            selection TEXT,
            line REAL,
            odds REAL,
            source TEXT,
            snapshot_type TEXT,
            notes TEXT
        )
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            match_date TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            engine TEXT,
            market TEXT,
            selection TEXT,
            line REAL,
            odds REAL,
            stake REAL,
            status TEXT DEFAULT 'Pending',
            result TEXT,
            profit REAL,
            notes TEXT
        )
        """)
        conn.commit()


init_db()


def db_read(query, params=()):
    with db_connect() as conn:
        return pd.read_sql_query(query, conn, params=params)


def db_write(query, params=()):
    with db_connect() as conn:
        conn.execute(query, params)
        conn.commit()


def save_snapshot(record):
    db_write("""
        INSERT INTO odds_snapshots
        (saved_at, match_date, league, home_team, away_team,
         bookmaker, market, selection, line, odds, source,
         snapshot_type, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        record.get("match_date"),
        record.get("league"),
        record.get("home_team"),
        record.get("away_team"),
        record.get("bookmaker"),
        record.get("market"),
        record.get("selection"),
        record.get("line"),
        record.get("odds"),
        record.get("source", "manual"),
        record.get("snapshot_type", "current"),
        record.get("notes", ""),
    ))


def save_bet(record):
    db_write("""
        INSERT INTO bets
        (created_at, match_date, league, home_team, away_team,
         engine, market, selection, line, odds, stake,
         status, result, profit, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().isoformat(timespec="seconds"),
        record.get("match_date"),
        record.get("league"),
        record.get("home_team"),
        record.get("away_team"),
        record.get("engine"),
        record.get("market"),
        record.get("selection"),
        record.get("line"),
        record.get("odds"),
        record.get("stake"),
        record.get("status", "Pending"),
        record.get("result"),
        record.get("profit"),
        record.get("notes", ""),
    ))


def update_bet_result(bet_id, result, profit):
    db_write(
        "UPDATE bets SET status=?, result=?, profit=? WHERE id=?",
        ("Settled", result, profit, int(bet_id)),
    )


# ------------------------- HELPERS ----------------------------

def safe_float(value, default=None):
    try:
        if value is None or str(value).strip() == "":
            return default
        number = float(value)
        return number if math.isfinite(number) else default
    except (TypeError, ValueError):
        return default


def norm(value):
    return " ".join(str(value or "").lower().strip().split())


def api_get(url, params=None, timeout=25):
    response = requests.get(
        url,
        params=params or {},
        headers=REQUEST_HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    return response.json()


def closest_team(name, candidates):
    name_norm = norm(name)
    if not name_norm:
        return None

    normalized = {norm(x): x for x in candidates}
    if name_norm in normalized:
        return normalized[name_norm]

    # Conservative alias checks; avoid forcing uncertain matches.
    aliases = {
        "man utd": "manchester united",
        "man united": "manchester united",
        "spurs": "tottenham",
        "wolves": "wolverhampton",
        "west ham": "west ham",
        "brighton": "brighton",
    }
    name_norm = aliases.get(name_norm, name_norm)

    if name_norm in normalized:
        return normalized[name_norm]

    return None


# -------------------- HISTORICAL FOOTBALL DATA ----------------

@st.cache_data(ttl=3600, show_spinner=False)
def load_history(league_code, season_codes):
    frames = []
    errors = []

    for season in season_codes:
        url = f"{FOOTBALL_DATA_BASE}/{season}/{league_code}.csv"
        try:
            frame = pd.read_csv(url, on_bad_lines="skip")
            needed = ["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"]
            if not all(col in frame.columns for col in needed):
                continue
            frame = frame[needed].copy()
            frame["Season"] = season
            frames.append(frame)
        except Exception as exc:
            errors.append(f"{season}: {exc}")

    if not frames:
        return pd.DataFrame(), errors

    df = pd.concat(frames, ignore_index=True)
    df["Date"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
    for col in ["FTHG", "FTAG"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(
        subset=["Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"]
    )
    df = df.sort_values("Date")
    df = df.drop_duplicates(
        subset=["Date", "HomeTeam", "AwayTeam"], keep="last"
    )
    return df.reset_index(drop=True), errors


def team_matches(df, team, venue="all", limit=5):
    if df.empty or not team:
        return df.iloc[0:0].copy()

    if venue == "home":
        out = df[df["HomeTeam"] == team].copy()
    elif venue == "away":
        out = df[df["AwayTeam"] == team].copy()
    else:
        out = df[
            (df["HomeTeam"] == team) | (df["AwayTeam"] == team)
        ].copy()

    return out.sort_values("Date").tail(limit)


def form_stats(df, team, venue="all", limit=5):
    games = team_matches(df, team, venue, limit)

    result = {
        "n": len(games), "gf": None, "ga": None, "points": None,
        "wins": 0, "draws": 0, "losses": 0,
    }
    if games.empty:
        return result

    gf, ga, pts = [], [], []
    for _, row in games.iterrows():
        home = row["HomeTeam"] == team
        scored = float(row["FTHG"] if home else row["FTAG"])
        conceded = float(row["FTAG"] if home else row["FTHG"])
        gf.append(scored)
        ga.append(conceded)

        if scored > conceded:
            pts.append(3)
            result["wins"] += 1
        elif scored == conceded:
            pts.append(1)
            result["draws"] += 1
        else:
            pts.append(0)
            result["losses"] += 1

    result["gf"] = sum(gf) / len(gf)
    result["ga"] = sum(ga) / len(ga)
    result["points"] = sum(pts)
    return result


def get_h2h(df, home, away, limit=5):
    if df.empty:
        return df.iloc[0:0].copy()

    return df[
        ((df["HomeTeam"] == home) & (df["AwayTeam"] == away))
        | ((df["HomeTeam"] == away) & (df["AwayTeam"] == home))
    ].sort_values("Date").tail(limit)


def league_averages(df):
    if df.empty:
        return 1.45, 1.15

    h = pd.to_numeric(df["FTHG"], errors="coerce").mean()
    a = pd.to_numeric(df["FTAG"], errors="coerce").mean()

    return (
        float(h) if pd.notna(h) and h > 0 else 1.45,
        float(a) if pd.notna(a) and a > 0 else 1.15,
    )


def expected_goals(df, home, away):
    league_home, league_away = league_averages(df)
    hs = form_stats(df, home, "home", 5)
    aws = form_stats(df, away, "away", 5)

    # Simple baseline with shrinkage toward league averages.
    h_attack = (
        0.65 * hs["gf"] + 0.35 * league_home
        if hs["n"] >= 2 else league_home
    )
    h_defence = (
        0.65 * hs["ga"] + 0.35 * league_away
        if hs["n"] >= 2 else league_away
    )
    a_attack = (
        0.65 * aws["gf"] + 0.35 * league_away
        if aws["n"] >= 2 else league_away
    )
    a_defence = (
        0.65 * aws["ga"] + 0.35 * league_home
        if aws["n"] >= 2 else league_home
    )

    xh = max(0.15, min(4.0, (h_attack + a_defence) / 2))
    xa = max(0.15, min(4.0, (a_attack + h_defence) / 2))
    return xh, xa, hs, aws


# ------------------------ POISSON ------------------------------

def poisson_pmf(k, lam):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam) * lam ** k / math.factorial(k)


def score_matrix(xh, xa, max_goals=10):
    rows = []
    total = 0.0
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            p = poisson_pmf(h, xh) * poisson_pmf(a, xa)
            rows.append((h, a, p))
            total += p

    if total <= 0:
        return []

    return [(h, a, p / total) for h, a, p in rows]


# ---------------------- ASIAN SETTLEMENT -----------------------

def split_asian_line(line):
    """
    Quarter lines split into two equal components:
    -0.75 => [-1.0, -0.5]
    -0.25 => [-0.5, 0.0]
     0.25 => [0.0, 0.5]
     0.75 => [0.5, 1.0]
    """
    q = round(float(line) * 4)
    if q % 2 == 0:
        return [q / 4]
    low = math.floor(q / 2) / 2
    return [low, low + 0.5]


def component_profit(selection, odds, hg, ag, line, market):
    if market == "AH":
        gd = hg - ag if selection == "Home" else ag - hg
        margin = gd + line
    else:
        total = hg + ag
        margin = total - line if selection == "Over" else line - total

    if margin > 1e-9:
        return odds - 1.0
    if margin < -1e-9:
        return -1.0
    return 0.0


def asian_profit(selection, odds, hg, ag, line, market):
    components = split_asian_line(line)
    return sum(
        component_profit(selection, odds, hg, ag, part, market)
        for part in components
    ) / len(components)


def evaluate_market(matrix, selection, odds, line, market):
    if not matrix or odds is None or odds <= 1.0:
        return None

    ev = 0.0
    categories = {
        "win": 0.0, "half_win": 0.0, "push": 0.0,
        "half_loss": 0.0, "loss": 0.0,
    }

    for hg, ag, probability in matrix:
        profit = asian_profit(selection, odds, hg, ag, line, market)
        ev += probability * profit

        if profit > 1e-8:
            # A half-win has profit between zero and a full win.
            if profit >= (odds - 1.0) - 1e-8:
                categories["win"] += probability
            else:
                categories["half_win"] += probability
        elif profit < -1e-8:
            if profit <= -1.0 + 1e-8:
                categories["loss"] += probability
            else:
                categories["half_loss"] += probability
        else:
            categories["push"] += probability

    return {"ev": ev, **categories}


# ------------------------- ODDS API ----------------------------

@st.cache_data(ttl=180, show_spinner=False)
def fetch_odds(api_key, sport_key, regions):
    if not api_key:
        raise ValueError("ยังไม่ได้ตั้งค่า ODDS_API_KEY")

    return api_get(
        f"{ODDS_BASE}/sports/{sport_key}/odds/",
        params={
            "apiKey": api_key,
            "regions": regions,
            "markets": "spreads,totals",
            "oddsFormat": "decimal",
            "dateFormat": "iso",
        },
    )


def parse_odds(events, league_name):
    rows = []

    for event in events:
        home = event.get("home_team", "")
        away = event.get("away_team", "")
        kickoff = event.get("commence_time", "")

        for bookmaker in event.get("bookmakers", []):
            book = bookmaker.get("title", "Unknown")

            for market in bookmaker.get("markets", []):
                key = market.get("key")
                market_name = "AH" if key == "spreads" else "OU" if key == "totals" else None
                if market_name is None:
                    continue

                for outcome in market.get("outcomes", []):
                    odds = safe_float(outcome.get("price"))
                    line = safe_float(outcome.get("point"))
                    if odds is None or line is None:
                        continue

                    selection = outcome.get("name", "")
                    if market_name == "AH":
                        if selection == home:
                            side = "Home"
                        elif selection == away:
                            side = "Away"
                        else:
                            side = selection
                    else:
                        side = selection

                    rows.append({
                        "Kickoff": kickoff,
                        "League": league_name,
                        "Home": home,
                        "Away": away,
                        "Bookmaker": book,
                        "Market": market_name,
                        "Selection": side,
                        "Line": line,
                        "Odds": odds,
                        "Updated": market.get("last_update", ""),
                    })

    return pd.DataFrame(rows)


# ------------------------ ENGINE 1 -----------------------------

def scan_value_bets(odds_df, history_df, min_ev_pct=2.0):
    rows = []
    if odds_df.empty or history_df.empty:
        return pd.DataFrame()

    home_names = history_df["HomeTeam"].dropna().unique().tolist()
    away_names = history_df["AwayTeam"].dropna().unique().tolist()
    all_names = list(set(home_names + away_names))

    for _, odd in odds_df.iterrows():
        home = closest_team(odd["Home"], all_names)
        away = closest_team(odd["Away"], all_names)

        if not home or not away or home == away:
            continue

        xh, xa, hs, aws = expected_goals(history_df, home, away)
        matrix = score_matrix(xh, xa)

        market = odd["Market"]
        selection = odd["Selection"]
        line = float(odd["Line"])
        odds = float(odd["Odds"])

        if market == "AH" and selection not in ("Home", "Away"):
            continue
        if market == "OU":
            if selection not in ("Over", "Under"):
                continue

        result = evaluate_market(matrix, selection, odds, line, market)
        if not result:
            continue

        ev_pct = result["ev"] * 100
        if ev_pct < min_ev_pct:
            continue

        rows.append({
            "Kickoff": odd["Kickoff"],
            "League": odd["League"],
            "Home": odd["Home"],
            "Away": odd["Away"],
            "Bookmaker": odd["Bookmaker"],
            "Market": market,
            "Selection": selection,
            "Line": line,
            "Odds": odds,
            "EV (%)": ev_pct,
            "Expected Goals": f"{xh:.2f} - {xa:.2f}",
            "Model Home Form N": hs["n"],
            "Model Away Form N": aws["n"],
            "Updated": odd["Updated"],
        })

    if not rows:
        return pd.DataFrame()

    return pd.DataFrame(rows).sort_values("EV (%)", ascending=False)


# --------------------- ENGINE 2: PRICE READING -----------------

def movement_summary(open_line, current_line, open_odds, current_odds, market):
    line_delta = current_line - open_line
    odds_delta = current_odds - open_odds

    if abs(line_delta) < 1e-9:
        line_text = "เส้นคงเดิม"
    elif market == "AH":
        line_text = (
            "เส้นเปลี่ยนไปทางบวก"
            if line_delta > 0 else "เส้นเปลี่ยนไปทางลบ"
        )
    else:
        line_text = "เส้นสูง/ต่ำขยับขึ้น" if line_delta > 0 else "เส้นสูง/ต่ำขยับลง"

    if abs(odds_delta) < 1e-9:
        odds_text = "ค่าน้ำคงเดิม"
    elif odds_delta < 0:
        odds_text = "ค่าน้ำลดลง"
    else:
        odds_text = "ค่าน้ำสูงขึ้น"

    return line_text, odds_text, line_delta, odds_delta


# -------------------------- UI ---------------------------------

st.title("⚽ Value Bet Pro")
st.caption(
    "ระบบ 1: Value Bet / EV | ระบบ 2: อ่านราคาเปิด–ราคาปัจจุบัน "
    "| ใช้เพื่อวิเคราะห์ ไม่รับประกันกำไร"
)

with st.sidebar:
    st.header("ตั้งค่าการวิเคราะห์")
    selected_league = st.selectbox("ลีก", list(LEAGUES.keys()))
    seasons_n = st.selectbox("ฤดูกาลย้อนหลัง", [2, 3, 4, 5], index=2)
    min_ev_pct = st.slider("EV ขั้นต่ำ (%)", -5.0, 15.0, 2.0, 0.5)
    regions = st.multiselect(
        "ภูมิภาคราคาจาก API",
        ["uk", "eu", "us", "au"],
        default=["uk", "eu"],
    )
    st.caption("ราคาเปิดต้องมาจากข้อมูลจริงหรือกรอกเอง ไม่อนุมานจากราคาปัจจุบัน")

history, history_errors = load_history(
    LEAGUES[selected_league]["fd"],
    SEASONS[:seasons_n],
)

if history.empty:
    st.warning(
        "ไม่พบข้อมูล Football-Data สำหรับลีก/ฤดูกาลนี้ "
        "ระบบ EV จะยังไม่สามารถสแกนคู่ได้"
    )
else:
    st.success(
        f"ข้อมูลย้อนหลัง {len(history)} นัด | "
        f"{history['Date'].min().date()} ถึง {history['Date'].max().date()}"
    )

tabs = st.tabs([
    "1. Value Bet Engine",
    "2. Match Reading Engine",
    "3. Bet Ledger / Backtest",
    "4. ตั้งค่าและข้อมูล",
])


# ========================= TAB 1: VALUE BET ====================

with tabs[0]:
    st.subheader("Value Bet Engine")
    st.write(
        "สแกนราคา AH และสูง/ต่ำจาก The Odds API "
        "แล้วคำนวณ EV ด้วยโมเดล Poisson พื้นฐาน"
    )

    sport_key = LEAGUES[selected_league]["odds"]

    if not ODDS_API_KEY:
        st.error(
            "ยังไม่ได้ตั้งค่า ODDS_API_KEY ใน Streamlit Secrets "
            "หรือ Environment Variables"
        )
    elif not sport_key:
        st.info("ลีกนี้ยังไม่มี sport key ที่ตั้งค่าไว้สำหรับ The Odds API")
    elif st.button("ดึงราคาและสแกน Value Bet", type="primary"):
        try:
            with st.spinner("กำลังดึงราคาและคำนวณ EV..."):
                events = fetch_odds(
                    ODDS_API_KEY,
                    sport_key,
                    ",".join(regions),
                )
                odds_df = parse_odds(events, selected_league)

            st.session_state["last_odds_df"] = odds_df

            if odds_df.empty:
                st.warning("API ไม่พบราคาตลาด AH/สูง-ต่ำสำหรับลีกนี้")
            elif history.empty:
                st.warning("มีราคาแล้ว แต่ไม่มีข้อมูลสถิติย้อนหลังเพียงพอ")
                st.dataframe(odds_df, use_container_width=True)
            else:
                value_df = scan_value_bets(
                    odds_df, history, min_ev_pct
                )
                st.session_state["last_value_df"] = value_df

                if value_df.empty:
                    st.info(
                        "ไม่พบคู่ที่ผ่านเกณฑ์ EV ในข้อมูลที่ดึงได้ "
                        "ไม่ได้หมายความว่าไม่มีทีมชนะ"
                    )
                else:
                    st.success(f"พบ {len(value_df)} รายการที่ผ่านเกณฑ์ EV")
                    st.dataframe(
                        value_df,
                        use_container_width=True,
                        hide_index=True,
                    )
                    st.download_button(
                        "ดาวน์โหลดรายการ Value Bet CSV",
                        data=value_df.to_csv(index=False).encode("utf-8-sig"),
                        file_name="value_bet_candidates.csv",
                        mime="text/csv",
                    )

        except Exception as exc:
            st.error(f"ดึงหรือประมวลผลราคาไม่สำเร็จ: {exc}")

    if "last_value_df" in st.session_state:
        value_df = st.session_state["last_value_df"]

        if not value_df.empty:
            st.markdown("#### บันทึกรายการเป็นเดิมพัน")
            options = [
                f"{i} | {r['Home']} vs {r['Away']} | "
                f"{r['Market']} {r['Selection']} {r['Line']} @ {r['Odds']}"
                for i, (_, r) in enumerate(value_df.iterrows())
            ]
            chosen = st.selectbox("เลือกรายการ", options, key="bet_pick")
            stake = st.number_input(
                "เงินเดิมพัน", min_value=0.0, value=100.0, step=10.0
            )
            notes = st.text_input("หมายเหตุ", key="ev_bet_notes")

            if st.button("บันทึกเดิมพันที่เลือก"):
                idx = int(chosen.split("|")[0].strip())
                row = value_df.iloc[idx]
                save_bet({
                    "match_date": row["Kickoff"],
                    "league": row["League"],
                    "home_team": row["Home"],
                    "away_team": row["Away"],
                    "engine": "Value Bet Engine",
                    "market": row["Market"],
                    "selection": row["Selection"],
                    "line": row["Line"],
                    "odds": row["Odds"],
                    "stake": stake,
                    "status": "Pending",
                    "notes": notes,
                })
                st.success("บันทึกเดิมพันแล้ว")


# ======================== TAB 2: MATCH READING =================

with tabs[1]:
    st.subheader("Match Reading Engine")
    st.write(
        "แยกจาก EV Engine: อ่านราคาเปิดเทียบราคาปัจจุบัน "
        "และใช้ฟอร์มเหย้า–เยือน/H2H เป็นหลักฐานประกอบ"
    )

    c1, c2 = st.columns(2)
    with c1:
        read_home = st.text_input("ทีมเหย้า", key="read_home")
        open_line = st.number_input(
            "ราคาเปิด — เส้น", value=-0.25, step=0.25, key="read_open_line"
        )
        open_odds = st.number_input(
            "ค่าน้ำราคาเปิด", min_value=1.01, value=1.90, step=0.01,
            key="read_open_odds",
        )
    with c2:
        read_away = st.text_input("ทีมเยือน", key="read_away")
        current_line = st.number_input(
            "ราคาปัจจุบัน — เส้น", value=-0.25, step=0.25,
            key="read_current_line",
        )
        current_odds = st.number_input(
            "ค่าน้ำปัจจุบัน", min_value=1.01, value=1.90, step=0.01,
            key="read_current_odds",
        )

    read_market = st.radio(
        "ตลาดที่กำลังอ่าน",
        ["AH", "สูง/ต่ำ"],
        horizontal=True,
        key="read_market",
    )
    if read_market == "AH":
        read_selection = st.radio(
            "ทีมที่เลือก",
            ["Home", "Away"],
            format_func=lambda x: "ทีมเหย้า" if x == "Home" else "ทีมเยือน",
            horizontal=True,
        )
        st.caption(
            "สำหรับ AH ให้กรอกเส้นจากมุมมองทีมที่เลือก "
            "เช่น ทีมต่อ 0.25 = -0.25, ทีมรอง 0.25 = +0.25"
        )
    else:
        read_selection = st.radio(
            "สูงหรือต่ำ",
            ["Over", "Under"],
            horizontal=True,
            format_func=lambda x: "สูง" if x == "Over" else "ต่ำ",
        )

    st.markdown("#### ดึงราคาปัจจุบันจาก The Odds API (ถ้ามี)")
    if not ODDS_API_KEY:
        st.caption("ไม่มี API Key — กรอกราคาเองได้")
    elif not LEAGUES[selected_league]["odds"]:
        st.caption("ลีกนี้ยังไม่มี sport key สำหรับดึงราคา")
    elif st.button("ค้นหาราคาปัจจุบันของคู่ที่กรอก"):
        try:
            events = fetch_odds(
                ODDS_API_KEY,
                LEAGUES[selected_league]["odds"],
                ",".join(regions),
            )
            current_df = parse_odds(events, selected_league)
            if current_df.empty:
                st.info("ไม่พบราคาปัจจุบัน")
            else:
                matched = current_df[
                    current_df["Home"].map(norm).eq(norm(read_home))
                    & current_df["Away"].map(norm).eq(norm(read_away))
                ]
                if matched.empty:
                    st.warning(
                        "ไม่พบคู่ที่ชื่อทีมตรงกันพอดี "
                        "ตรวจชื่อทีมและลีก หรือกรอกราคาเอง"
                    )
                else:
                    st.dataframe(matched, use_container_width=True, hide_index=True)
        except Exception as exc:
            st.error(f"ดึงราคาไม่สำเร็จ: {exc}")

    if st.button("วิเคราะห์ราคาและสถิติ", type="primary", key="run_reading"):
        if not read_home.strip() or not read_away.strip():
            st.error("กรุณากรอกชื่อทีมเหย้าและทีมเยือน")
        else:
            line_text, odds_text, line_delta, odds_delta = movement_summary(
                open_line, current_line, open_odds, current_odds, read_market
            )

            c1, c2, c3 = st.columns(3)
            c1.metric("การเปลี่ยนแปลงเส้น", line_text, f"{line_delta:+.2f}")
            c2.metric("การเปลี่ยนแปลงค่าน้ำ", odds_text, f"{odds_delta:+.2f}")
            c3.metric(
                "ค่าน้ำเปิด → ปัจจุบัน",
                f"{open_odds:.2f} → {current_odds:.2f}",
            )

            st.warning(
                "เส้นที่ขยับขึ้นหรือลงไม่ได้แปลว่าเป็นสัญญาณซื้อ/ขายโดยตัวมันเอง "
                "ต้องดูว่ากำลังดูฝั่งใด เส้นใด และราคาใด"
            )

            if history.empty:
                st.warning("ไม่มีข้อมูลย้อนหลัง จึงแสดงได้เฉพาะการเปลี่ยนแปลงราคา")
            else:
                candidates = list(
                    set(history["HomeTeam"].dropna())
                    | set(history["AwayTeam"].dropna())
                )
                home_match = closest_team(read_home, candidates)
                away_match = closest_team(read_away, candidates)

                if not home_match or not away_match:
                    st.warning(
                        "จับคู่ชื่อทีมกับข้อมูลย้อนหลังไม่ได้อย่างมั่นใจ "
                        "ตรวจการสะกดชื่อทีมหรือเลือกลีกที่ถูกต้อง"
                    )
                else:
                    hs = form_stats(history, home_match, "home", 5)
                    aws = form_stats(history, away_match, "away", 5)
                    h2h = get_h2h(history, home_match, away_match, 5)

                    st.markdown("#### ฟอร์มเหย้า–เยือน 5 นัดล่าสุด")
                    form_df = pd.DataFrame([
                        {
                            "ทีม": home_match,
                            "บริบท": "เหย้า",
                            "จำนวนเกม": hs["n"],
                            "ชนะ": hs["wins"],
                            "เสมอ": hs["draws"],
                            "แพ้": hs["losses"],
                            "ยิงเฉลี่ย": hs["gf"],
                            "เสียเฉลี่ย": hs["ga"],
                            "คะแนนรวม": hs["points"],
                        },
                        {
                            "ทีม": away_match,
                            "บริบท": "เยือน",
                            "จำนวนเกม": aws["n"],
                            "ชนะ": aws["wins"],
                            "เสมอ": aws["draws"],
                            "แพ้": aws["losses"],
                            "ยิงเฉลี่ย": aws["gf"],
                            "เสียเฉลี่ย": aws["ga"],
                            "คะแนนรวม": aws["points"],
                        },
                    ])
                    st.dataframe(
                        form_df,
                        use_container_width=True,
                        hide_index=True,
                    )

                    st.markdown("#### H2H — การพบกันล่าสุด")
                    if h2h.empty:
                        st.info("ไม่พบข้อมูลการพบกันในชุดข้อมูลที่โหลดได้")
                    else:
                        st.dataframe(
                            h2h[[
                                "Date", "HomeTeam", "FTHG", "FTAG", "AwayTeam"
                            ]].sort_values("Date", ascending=False),
                            use_container_width=True,
                            hide_index=True,
                        )

                    st.markdown("#### สรุปหลักฐาน")
                    if hs["n"] < 3 or aws["n"] < 3:
                        st.warning(
                            "ข้อมูลเหย้าหรือเยือนน้อยกว่า 3 นัด "
                            "ไม่ควรสรุปจากฟอร์มอย่างเดียว"
                        )
                    elif hs["points"] > aws["points"]:
                        st.info(
                            "คะแนนจากฟอร์มเหย้าของเจ้าบ้านสูงกว่าคะแนนจากฟอร์มเยือน "
                            "ของทีมเยือนในตัวอย่างล่าสุด แต่ยังต้องพิจารณาราคาและคุณภาพคู่แข่ง"
                        )
                    elif hs["points"] < aws["points"]:
                        st.info(
                            "คะแนนจากฟอร์มเหย้าของเจ้าบ้านต่ำกว่าคะแนนจากฟอร์มเยือน "
                            "ของทีมเยือน ควรตรวจปัจจัยอื่นก่อนเลือกฝั่ง"
                        )
                    else:
                        st.info("คะแนนฟอร์มเหย้าและเยือนใกล้เคียงกัน")

                    st.caption(
                        "อันดับลีกยังไม่ได้ถูกใช้เป็นตัวตัดสินอัตโนมัติ "
                        "และ H2H เป็นข้อมูลประกอบ ไม่ใช่หลักฐานยืนยันผล"
                    )

            st.markdown("#### บันทึกราคาเพื่อเปรียบเทียบภายหลัง")
            snapshot_notes = st.text_input(
                "หมายเหตุราคา (เช่น บ้านผลบอลช่วงเย็น)", key="snap_notes"
            )
            snapshot_book = st.text_input(
                "เจ้ามือ/แหล่งราคา", value="บ้านผลบอล / กรอกเอง",
                key="snap_book",
            )

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("บันทึกเป็นราคาเปิด"):
                    save_snapshot({
                        "match_date": None,
                        "league": selected_league,
                        "home_team": read_home,
                        "away_team": read_away,
                        "bookmaker": snapshot_book,
                        "market": read_market,
                        "selection": read_selection,
                        "line": open_line,
                        "odds": open_odds,
                        "source": "manual",
                        "snapshot_type": "opening",
                        "notes": snapshot_notes,
                    })
                    st.success("บันทึกราคาเปิดแล้ว")

            with col_b:
                if st.button("บันทึกเป็นราคาปัจจุบัน"):
                    save_snapshot({
                        "match_date": None,
                        "league": selected_league,
                        "home_team": read_home,
                        "away_team": read_away,
                        "bookmaker": snapshot_book,
                        "market": read_market,
                        "selection": read_selection,
                        "line": current_line,
                        "odds": current_odds,
                        "source": "manual",
                        "snapshot_type": "current",
                        "notes": snapshot_notes,
                    })
                    st.success("บันทึกราคาปัจจุบันแล้ว")

            if st.button("บันทึกการอ่านนี้เป็นเดิมพัน"):
                save_bet({
                    "match_date": None,
                    "league": selected_league,
                    "home_team": read_home,
                    "away_team": read_away,
                    "engine": "Match Reading Engine",
                    "market": read_market,
                    "selection": read_selection,
                    "line": current_line,
                    "odds": current_odds,
                    "stake": 100.0,
                    "status": "Pending",
                    "notes": "บันทึกจาก Match Reading Engine",
                })
                st.success("บันทึกรายการเดิมพันแล้ว (เงินเดิมพันเริ่มต้น 100)")


# ===================== TAB 3: LEDGER / BACKTEST ================

with tabs[2]:
    st.subheader("Bet Ledger / Backtest")
    st.caption("บันทึกผลจริงเองเพื่อให้ ROI อิงจากผลที่ตรวจสอบแล้ว")

    bets = db_read("SELECT * FROM bets ORDER BY id DESC")

    if bets.empty:
        st.info("ยังไม่มีรายการเดิมพัน")
    else:
        settled = bets[bets["status"] == "Settled"].copy()
        if not settled.empty:
            settled["stake"] = pd.to_numeric(settled["stake"], errors="coerce").fillna(0)
            settled["profit"] = pd.to_numeric(settled["profit"], errors="coerce").fillna(0)
            total_stake = settled["stake"].sum()
            total_profit = settled["profit"].sum()
            roi = total_profit / total_stake * 100 if total_stake > 0 else 0
            win_rate = (
                (settled["profit"] > 0).mean() * 100 if len(settled) else 0
            )

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("เดิมพันที่ปิดผลแล้ว", len(settled))
            c2.metric("กำไร/ขาดทุน", f"{total_profit:+.2f}")
            c3.metric("ROI", f"{roi:+.2f}%")
            c4.metric("อัตรารายการที่กำไร", f"{win_rate:.1f}%")
        else:
            st.info("ยังไม่มีรายการที่ปิดผลแล้ว")

        st.dataframe(bets, use_container_width=True, hide_index=True)
        st.download_button(
            "ดาวน์โหลด Bet Ledger CSV",
            data=bets.to_csv(index=False).encode("utf-8-sig"),
            file_name="value_bet_pro_ledger.csv",
            mime="text/csv",
        )

        pending = bets[bets["status"] == "Pending"]
        if not pending.empty:
            st.markdown("#### บันทึกผลเดิมพัน")
            bet_ids = pending["id"].astype(int).tolist()
            selected_id = st.selectbox("เลือกรายการ ID", bet_ids)
            result_choice = st.selectbox(
                "ผลการแข่งขัน/เดิมพัน",
                ["Win", "Half Win", "Push", "Half Loss", "Loss"],
            )
            selected_row = pending[pending["id"] == selected_id].iloc[0]
            stake_value = safe_float(selected_row["stake"], 0.0) or 0.0
            odds_value = safe_float(selected_row["odds"], 1.0) or 1.0

            profit_values = {
                "Win": stake_value * (odds_value - 1),
                "Half Win": stake_value * (odds_value - 1) / 2,
                "Push": 0.0,
                "Half Loss": -stake_value / 2,
                "Loss": -stake_value,
            }
            preview_profit = profit_values[result_choice]
            st.write(f"กำไร/ขาดทุนที่จะบันทึก: **{preview_profit:+.2f}**")

            if st.button("ยืนยันผลเดิมพัน"):
                update_bet_result(
                    selected_id, result_choice, preview_profit
                )
                st.success("อัปเดตผลแล้ว")
                st.rerun()

    st.markdown("#### ประวัติราคาเปิดและราคาปัจจุบัน")
    snapshots = db_read(
        "SELECT * FROM odds_snapshots ORDER BY id DESC LIMIT 500"
    )
    if snapshots.empty:
        st.info("ยังไม่มีประวัติราคาที่บันทึก")
    else:
        st.dataframe(snapshots, use_container_width=True, hide_index=True)
        st.download_button(
            "ดาวน์โหลดประวัติราคา CSV",
            data=snapshots.to_csv(index=False).encode("utf-8-sig"),
            file_name="value_bet_pro_odds_snapshots.csv",
            mime="text/csv",
        )


# ======================= TAB 4: SETTINGS =======================

with tabs[3]:
    st.subheader("การตั้งค่าและข้อจำกัด")

    st.write(
        "สถานะ The Odds API:",
        "ตั้งค่าแล้ว" if ODDS_API_KEY else "ยังไม่ได้ตั้งค่า",
    )

    st.markdown("#### ตั้งค่า API Key อย่างปลอดภัย")
    st.write("สร้างไฟล์ `.streamlit/secrets.toml` แล้วใส่:")
    st.code('ODDS_API_KEY = "ใส่_API_KEY_ของคุณ"', language="toml")

    st.markdown("#### แหล่งข้อมูล")
    st.markdown(
        "- Football-Data.co.uk: https://www.football-data.co.uk/\n"
        "- The Odds API: https://the-odds-api.com/liveapi/guides/v4/"
    )

    st.markdown("#### ข้อจำกัดของโมเดล")
    st.markdown("""
- EV ขึ้นอยู่กับความแม่นยำของโมเดล Poisson ซึ่งเวอร์ชันนี้ยังเป็นโมเดลพื้นฐาน
- ข้อมูลฟอร์มใช้ผลล่าสุด 5 นัดแยกเหย้าและเยือน ไม่ได้ปรับคุณภาพคู่แข่งครบถ้วน
- ไม่มีการรับประกันกำไร อัตราชนะ หรือผลตอบแทนในอนาคต
- The Odds API ให้ราคาปัจจุบันตามตลาดและเจ้ามือที่มีข้อมูล ไม่ใช่ราคาเปิดย้อนหลังทุกคู่
- การกรอกราคาเปิดเองต้องบันทึกให้ตรงกับตลาด ฝั่ง เส้น และเจ้ามือเดียวกันจึงจะเปรียบเทียบได้ดี
- ผลเดิมพันที่บันทึกเป็น Win/Half Win/Push/Half Loss/Loss ต้องตรวจให้ตรงกับกติกา AH ของเจ้ามือ
- ฐานข้อมูล SQLite อยู่ในโฟลเดอร์ `data/` ควรสำรองไฟล์ `value_bet_pro.db` เป็นประจำ
""")

st.divider()
st.caption(
    "Value Bet Pro — ใช้เพื่อวิเคราะห์และทดสอบระบบเท่านั้น "
    "ตรวจสอบราคาและข้อมูลก่อนตัดสินใจเดิมพันทุกครั้ง"
)
