
import os
import re
import math
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import pandas as pd
import requests
import streamlit as st

# ============================================================
# VALUE BET PRO
# Streamlit + The Odds API + Football-Data.co.uk + SQLite
# ============================================================

st.set_page_config(
    page_title="Value Bet Pro",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Value Bet Pro")
st.caption(
    "วิเคราะห์ราคาและสถิติฟุตบอลด้วยแบบจำลอง Poisson "
    "ไม่ใช่ระบบรับประกันกำไร"
)

API_BASE = "https://api.the-odds-api.com/v4"
TH_TZ = timezone(timedelta(hours=7))
UTC = timezone.utc
TIMEOUT = 20

# Streamlit Cloud อาจรีเซ็ตไฟล์ในเครื่องได้
DB_FILE = Path(os.getenv("VALUE_BET_DB", "data/value_bet_pro.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

LEAGUES = {
    "soccer_epl": ("Premier League", "E0"),
    "soccer_england_championship": ("Championship", "E1"),
    "soccer_england_league_one": ("League One", "E2"),
    "soccer_england_league_two": ("League Two", "E3"),
    "soccer_spain_la_liga": ("La Liga", "SP1"),
    "soccer_italy_serie_a": ("Serie A", "I1"),
    "soccer_germany_bundesliga": ("Bundesliga", "D1"),
    "soccer_france_ligue_one": ("Ligue 1", "F1"),
    "soccer_netherlands_eredivisie": ("Eredivisie", "N1"),
    "soccer_portugal_primeira_liga": ("Primeira Liga", "P1"),
    "soccer_uefa_champs_league": ("UEFA Champions League", None),
}


# -------------------- API KEY --------------------

def get_api_key():
    try:
        key = st.secrets.get("THE_ODDS_API_KEY", "")
    except Exception:
        key = ""
    return (key or os.getenv("THE_ODDS_API_KEY", "")).strip()


# -------------------- SQLITE --------------------

def db():
    con = sqlite3.connect(DB_FILE, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            kickoff_utc TEXT,
            kickoff_th TEXT,
            league TEXT,
            home_team TEXT,
            away_team TEXT,
            market TEXT,
            side TEXT,
            point REAL,
            odds REAL,
            bookmaker TEXT,
            scanned_at TEXT,
            lambda_home REAL,
            lambda_away REAL,
            history_matches INTEGER,
            ev_pct REAL,
            p_win REAL,
            p_partial REAL,
            p_loss REAL,
            status TEXT DEFAULT 'รอตรวจผล',
            final_score TEXT DEFAULT '',
            settlement TEXT DEFAULT ''
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT,
            match_name TEXT,
            market TEXT,
            side TEXT,
            point REAL,
            odds REAL,
            bookmaker TEXT,
            captured_at TEXT
        )
        """)
        con.execute("""
        CREATE TABLE IF NOT EXISTS slips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT,
            slip_size INTEGER,
            selections_json TEXT,
            combined_odds REAL,
            estimated_probability REAL,
            status TEXT DEFAULT 'รอตรวจผล'
        )
        """)
        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_picks_event
        ON picks(event_id)
        """)
        con.execute("""
        CREATE INDEX IF NOT EXISTS idx_snapshots_event
        ON odds_snapshots(event_id, captured_at)
        """)


init_db()


def save_picks(rows):
    if not rows:
        return 0

    now_text = datetime.now(TH_TZ).isoformat(timespec="seconds")
    count = 0

    with db() as con:
        for r in rows:
            con.execute("""
            INSERT INTO picks (
                event_id, kickoff_utc, kickoff_th, league,
                home_team, away_team, market, side, point,
                odds, bookmaker, scanned_at, lambda_home,
                lambda_away, history_matches, ev_pct,
                p_win, p_partial, p_loss
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["event_id"], r["kickoff_utc"], r["kickoff_th"],
                r["league"], r["home_team"], r["away_team"],
                r["market"], r["side"], r["point"], r["odds"],
                r["bookmaker"], now_text, r["lambda_home"],
                r["lambda_away"], r["history_matches"],
                r["ev_pct"], r["p_win"], r["p_partial"],
                r["p_loss"]
            ))

            con.execute("""
            INSERT INTO odds_snapshots (
                event_id, match_name, market, side, point,
                odds, bookmaker, captured_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """, (
                r["event_id"], r["match"], r["market"],
                r["side"], r["point"], r["odds"],
                r["bookmaker"], now_text
            ))
            count += 1

    return count


def load_picks(limit=5000):
    with db() as con:
        df = pd.read_sql_query(
            "SELECT * FROM picks ORDER BY id DESC LIMIT ?",
            con, params=(limit,)
        )
    return df


def update_pick_result(pick_id, score, settlement):
    with db() as con:
        con.execute("""
        UPDATE picks
        SET final_score=?, settlement=?, status='ตรวจแล้ว'
        WHERE id=?
        """, (score, settlement, int(pick_id)))


# -------------------- API --------------------

@st.cache_data(ttl=900, show_spinner=False)
def fetch_odds(sport_key, regions, api_key):
    """Cache 15 นาที เพื่อลดการเรียก API ซ้ำ"""
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "spreads,totals",
        "oddsFormat": "decimal",
    }

    try:
        response = requests.get(
            url, params=params, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"เชื่อมต่อ API ไม่สำเร็จ: {exc}") from exc

    if response.status_code == 401:
        raise RuntimeError("API Key ไม่ถูกต้องหรือไม่มีสิทธิ์")
    if response.status_code == 429:
        raise RuntimeError("โควตา API หมดหรือเรียกเกินขีดจำกัด")
    if response.status_code != 200:
        raise RuntimeError(
            f"API ตอบ HTTP {response.status_code}: "
            f"{response.text[:200]}"
        )

    data = response.json()
    remaining = response.headers.get(
        "x-requests-remaining", "ไม่ระบุ"
    )
    used = response.headers.get(
        "x-requests-used", "ไม่ระบุ"
    )
    return data, remaining, used


# -------------------- HISTORICAL RESULTS --------------------

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_history(csv_code):
    """โหลดสถิติประตูจาก Football-Data.co.uk"""
    if not csv_code:
        return pd.DataFrame()

    frames = []
    # ฤดูกาลปัจจุบันและย้อนหลัง
    for season in ("2627", "2526", "2425", "2324"):
        url = (
            "https://www.football-data.co.uk/mmz4281/"
            f"{season}/{csv_code}.csv"
        )
        try:
            df = pd.read_csv(url)
            required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
            if not required.issubset(df.columns):
                continue

            df["Date_dt"] = pd.to_datetime(
                df["Date"], dayfirst=True, errors="coerce"
            )
            df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
            df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
            df = df.dropna(
                subset=["Date_dt", "FTHG", "FTAG"]
            )
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("Date_dt")
    return result.drop_duplicates(
        subset=["Date_dt", "HomeTeam", "AwayTeam"],
        keep="last"
    ).reset_index(drop=True)


def norm_name(name):
    name = str(name or "").lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]", "", name)


def map_team(name, candidates):
    if not candidates:
        return None

    lookup = {norm_name(x): x for x in candidates}
    normalized = norm_name(name)

    if normalized in lookup:
        return lookup[normalized]

    match = get_close_matches(
        normalized, list(lookup.keys()), n=1, cutoff=0.78
    )
    return lookup[match[0]] if match else None


def estimate_goals(df, home_api, away_api, kickoff):
    """ประมาณประตูจากสถิติเหย้า/เยือนย้อนหลัง"""
    if df is None or df.empty:
        return None

    historical = df[
        df["Date_dt"] < pd.Timestamp(kickoff.date())
    ].copy()

    if historical.empty:
        return None

    teams = sorted(
        set(historical["HomeTeam"].dropna())
        | set(historical["AwayTeam"].dropna())
    )
    home = map_team(home_api, teams)
    away = map_team(away_api, teams)

    if not home or not away:
        return None

    h_home = historical[historical["HomeTeam"] == home].tail(10)
    a_away = historical[historical["AwayTeam"] == away].tail(10)

    if len(h_home) < 5 or len(a_away) < 5:
        return None

    league_home = max(float(historical["FTHG"].mean()), 0.2)
    league_away = max(float(historical["FTAG"].mean()), 0.2)

    h_scored = float(h_home["FTHG"].mean())
    h_conceded = float(h_home["FTAG"].mean())
    a_scored = float(a_away["FTAG"].mean())
    a_conceded = float(a_away["FTHG"].mean())

    # Shrink toward league averages to reduce small-sample extremes
    home_attack = (h_scored + league_home * 3) / 4 / league_home
    away_defence = (a_conceded + league_home * 3) / 4 / league_home
    away_attack = (a_scored + league_away * 3) / 4 / league_away
    home_defence = (h_conceded + league_away * 3) / 4 / league_away

    lam_home = league_home * (home_attack + away_defence) / 2
    lam_away = league_away * (away_attack + home_defence) / 2

    return {
        "home_lambda": min(max(lam_home, 0.15), 4.0),
        "away_lambda": min(max(lam_away, 0.15), 4.0),
        "history_matches": len(historical),
    }


# -------------------- POISSON MODEL --------------------

def poisson_probs(lam, max_goals=12):
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def score_matrix(lam_home, lam_away):
    hp = poisson_probs(lam_home)
    ap = poisson_probs(lam_away)
    return [
        (h, a, hp[h] * ap[a])
        for h in range(len(hp))
        for a in range(len(ap))
    ]


def split_line(line):
    """แยกเส้นควบ เช่น -0.25 เป็น 0 และ -0.5"""
    q = round(float(line) * 4)

    if q % 2 == 0:
        return [(q / 4.0, 1.0)]

    lower = math.floor(q / 2) / 2.0
    upper = lower + 0.5
    return [(lower, 0.5), (upper, 0.5)]


def settle_leg(adjusted):
    if adjusted > 1e-9:
        return "win"
    if adjusted < -1e-9:
        return "loss"
    return "push"


def evaluate_market(matrix, market, side, point, odds):
    """
    EV = expected net profit per 1 unit stake.
    Partial outcomes are calculated by splitting quarter-line stakes.
    """
    if odds <= 1:
        return None

    ev = p_full_win = p_full_loss = p_other = 0.0
    legs = split_line(point)

    for home, away, prob in matrix:
        net = 0.0
        outcomes = []

        for line, weight in legs:
            if market == "spreads":
                diff = home - away if side == "home" else away - home
                outcome = settle_leg(diff + line)
            else:
                goals = home + away
                adjusted = goals - line if side == "over" else line - goals
                outcome = settle_leg(adjusted)

            outcomes.append(outcome)

            if outcome == "win":
                net += weight * (odds - 1.0)
            elif outcome == "loss":
                net -= weight

        ev += prob * net

        if all(x == "win" for x in outcomes):
            p_full_win += prob
        elif all(x == "loss" for x in outcomes):
            p_full_loss += prob
        else:
            p_other += prob

    return {
        "ev_pct": ev * 100.0,
        "p_win": p_full_win * 100.0,
        "p_partial": p_other * 100.0,
        "p_loss": p_full_loss * 100.0,
    }


# -------------------- MARKET SELECTION --------------------

def collect_market_records(bookmakers, market_key, home, away):
    records = []

    for bm in bookmakers:
        for market in bm.get("markets", []):
            if market.get("key") != market_key:
                continue

            outcomes = market.get("outcomes", [])
            book_name = bm.get("title", bm.get("key", "Unknown"))
            updated = market.get(
                "last_update", bm.get("last_update", "")
            )

            if market_key == "spreads":
                h = next((
                    x for x in outcomes
                    if x.get("name") == home and x.get("point") is not None
                ), None)
                a = next((
                    x for x in outcomes
                    if x.get("name") == away and x.get("point") is not None
                ), None)

                if not h or not a:
                    continue

                hp, ap = float(h["point"]), float(a["point"])
                if abs(hp + ap) > 0.01:
                    continue

                records.append({
                    "market": market_key,
                    "point": hp,
                    "home": h,
                    "away": a,
                    "book": book_name,
                    "updated": updated,
                })

            elif market_key == "totals":
                over = next((
                    x for x in outcomes
                    if str(x.get("name", "")).lower() == "over"
                    and x.get("point") is not None
                ), None)
                under = next((
                    x for x in outcomes
                    if str(x.get("name", "")).lower() == "under"
                    and x.get("point") is not None
                ), None)

                if not over or not under:
                    continue
                if abs(float(over["point"]) - float(under["point"])) > 0.01:
                    continue

                records.append({
                    "market": market_key,
                    "point": float(over["point"]),
                    "over": over,
                    "under": under,
                    "book": book_name,
                    "updated": updated,
                })

    return records


def choose_main_market(records, min_odds, max_odds):
    if not records:
        return None

    counts = pd.Series([r["point"] for r in records]).value_counts()
    max_count = counts.max()
    common = set(counts[counts == max_count].index.tolist())
    records = [r for r in records if r["point"] in common]

    def in_band(r):
        keys = ("home", "away") if r["market"] == "spreads" else ("over", "under")
        return all(
            min_odds <= float(r[k]["price"]) <= max_odds for k in keys
        )

    preferred = [r for r in records if in_band(r)]
    if preferred:
        records = preferred

    records.sort(key=lambda r: str(r["updated"]), reverse=True)
    return records[0]


# -------------------- SCANNING --------------------

def scan_league(
    sport_key, league_name, csv_code, hours_limit,
    min_odds, max_odds, min_ev_pct, regions, api_key
):
    matches, remaining, used = fetch_odds(
        sport_key, regions, api_key
    )
    history = fetch_history(csv_code)

    now = datetime.now(UTC)
    target = now + timedelta(hours=hours_limit)
    scan_time = datetime.now(TH_TZ).isoformat(timespec="seconds")
    rows = []

    for match in matches:
        try:
            kickoff = datetime.fromisoformat(
                match["commence_time"].replace("Z", "+00:00")
            ).astimezone(UTC)
        except Exception:
            continue

        # Exclude started matches; only next 24 hours by default
        if not (now < kickoff <= target):
            continue

        home = match.get("home_team", "")
        away = match.get("away_team", "")
        books = match.get("bookmakers", [])

        if not books:
            continue

        kickoff_th = kickoff.astimezone(TH_TZ)
        metrics = estimate_goals(history, home, away, kickoff_th)

        # No independent model = no fabricated EV
        if not metrics:
            continue

        matrix = score_matrix(
            metrics["home_lambda"], metrics["away_lambda"]
        )

        for market_key in ("spreads", "totals"):
            records = collect_market_records(
                books, market_key, home, away
            )
            chosen = choose_main_market(
                records, min_odds, max_odds
            )
            if not chosen:
                continue

            if market_key == "spreads":
                selections = [
                    ("home", chosen["home"], float(chosen["home"]["point"])),
                    ("away", chosen["away"], float(chosen["away"]["point"])),
                ]
            else:
                selections = [
                    ("over", chosen["over"], float(chosen["point"])),
                    ("under", chosen["under"], float(chosen["point"])),
                ]

            for side, item, point in selections:
                odds = float(item["price"])
                if not (min_odds <= odds <= max_odds):
                    continue

                result = evaluate_market(
                    matrix, market_key, side, point, odds
                )
                if result is None:
                    continue

                # Keep all qualifying-odds candidates; display EV filter later
                label = (
                    f"{home if side == 'home' else away} "
                    f"({point:+g})"
                    if market_key == "spreads"
                    else f"{'สูง' if side == 'over' else 'ต่ำ'} {point:g}"
                )

                rows.append({
                    "event_id": match.get("id", ""),
                    "kickoff_utc": kickoff.isoformat(),
                    "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"),
                    "league": league_name,
                    "home_team": home,
                    "away_team": away,
                    "match": f"{home} vs {away}",
                    "market": market_key,
                    "side": side,
                    "point": point,
                    "selection": label,
                    "odds": odds,
                    "bookmaker": chosen["book"],
                    "scanned_at": scan_time,
                    "lambda_home": metrics["home_lambda"],
                    "lambda_away": metrics["away_lambda"],
                    "history_matches": metrics["history_matches"],
                    **result,
                })

    return rows, remaining, used


# -------------------- RESULTS SETTLEMENT --------------------

def result_for_match(history, home_api, away_api, kickoff_utc):
    if history is None or history.empty:
        return None

    teams = sorted(
        set(history["HomeTeam"].dropna())
        | set(history["AwayTeam"].dropna())
    )
    home = map_team(home_api, teams)
    away = map_team(away_api, teams)

    if not home or not away:
        return None

    target = pd.Timestamp(kickoff_utc.date())
    candidates = history[
        (history["HomeTeam"] == home)
        & (history["AwayTeam"] == away)
        & ((history["Date_dt"] - target).abs().dt.days <= 1)
    ]

    if candidates.empty:
        return None

    row = candidates.iloc[-1]
    return int(row["FTHG"]), int(row["FTAG"])


def grade_pick(market, side, point, home_goals, away_goals):
    legs = split_line(point)
    outcomes = []

    for line, _ in legs:
        if market == "spreads":
            diff = (
                home_goals - away_goals
                if side == "home"
                else away_goals - home_goals
            )
            outcome = settle_leg(diff + line)
        else:
            goals = home_goals + away_goals
            adjusted = (
                goals - line if side == "over" else line - goals
            )
            outcome = settle_leg(adjusted)

        outcomes.append(outcome)

    if all(x == "win" for x in outcomes):
        return "ชนะเต็ม"
    if all(x == "loss" for x in outcomes):
        return "แพ้เต็ม"
    if all(x == "push" for x in outcomes):
        return "คืนทุน"
    if "win" in outcomes and "push" in outcomes:
        return "ครึ่งชนะ"
    if "loss" in outcomes and "push" in outcomes:
        return "ครึ่งแพ้"
    if "win" in outcomes and "loss" in outcomes:
        return "ครึ่งชนะ/ครึ่งแพ้"
    return "ตรวจสอบ"


# -------------------- SIDEBAR --------------------

with st.sidebar:
    st.header("ตั้งค่าการวิเคราะห์")

    league_names = {k: v[0] for k, v in LEAGUES.items()}
    selected_leagues = st.multiselect(
        "เลือกลีก",
        list(LEAGUES.keys()),
        default=[
            "soccer_epl",
            "soccer_spain_la_liga",
            "soccer_italy_serie_a",
            "soccer_germany_bundesliga",
        ],
        format_func=lambda k: league_names[k],
    )

    hours_limit = st.selectbox(
        "ช่วงเวลาแข่ง", [24, 48], index=0
    )
    regions = st.selectbox(
        "ภูมิภาคราคา API (มีผลต่อโควตา)",
        ["eu", "uk", "us", "au"],
        index=0,
    )
    min_odds = st.number_input(
        "ค่าน้ำต่ำสุด", 1.01, 5.00, 1.60, 0.05
    )
    max_odds = st.number_input(
        "ค่าน้ำสูงสุด", 1.01, 10.00, 2.30, 0.05
    )
    min_ev_pct = st.number_input(
        "EV ขั้นต่ำ (%)", 0.0, 30.0, 2.0, 0.5
    )

    scan_btn = st.button(
        "🚀 สแกนราคา", type="primary", use_container_width=True
    )
    st.caption(
        "หนึ่งลีกต่อหนึ่งคำขอข้อมูล แต่โควตาที่คิดจริงขึ้นกับแพ็กเกจ "
        "ภูมิภาค และตลาดที่ร้องขอ"
    )


api_key = get_api_key()
if not api_key:
    st.warning(
        "ยังไม่ได้ตั้งค่า API Key: เพิ่ม THE_ODDS_API_KEY "
        "ใน Streamlit Secrets ก่อนใช้งาน"
    )

if scan_btn:
    if not api_key:
        st.error("ไม่พบ API Key")
    elif not selected_leagues:
        st.error("กรุณาเลือกอย่างน้อยหนึ่งลีก")
    elif min_odds > max_odds:
        st.error("ค่าน้ำต่ำสุดต้องไม่มากกว่าค่าน้ำสูงสุด")
    else:
        all_rows = []
        errors = []
        quota_info = []

        progress = st.progress(0)
        for i, key in enumerate(selected_leagues):
            league_name, csv_code = LEAGUES[key]
            try:
                with st.spinner(f"กำลังสแกน {league_name}..."):
                    rows, remaining, used = scan_league(
                        key, league_name, csv_code, hours_limit,
                        min_odds, max_odds, min_ev_pct,
                        regions, api_key
                    )
                    all_rows.extend(rows)
                    quota_info.append(
                        f"{league_name}: ใช้ {used}, เหลือ {remaining}"
                    )
            except Exception as exc:
                errors.append(f"{league_name}: {exc}")

            progress.progress((i + 1) / len(selected_leagues))

        if all_rows:
            count = save_picks(all_rows)
            st.session_state["last_scan"] = pd.DataFrame(all_rows)
            st.success(
                f"บันทึกผลวิเคราะห์ {count} รายการลง SQLite แล้ว"
            )
        else:
            st.info(
                "ไม่พบตัวเลือก หรือสถิติย้อนหลังไม่เพียงพอ "
                "ระบบจะไม่สร้าง EV แทนข้อมูลที่ขาด"
            )

        for info in quota_info:
            st.caption(info)
        for err in errors:
            st.warning(err)


# -------------------- MAIN TABS --------------------

tab_scan, tab_slips, tab_history, tab_help = st.tabs([
    "📊 วิเคราะห์",
    "🎟️ จัดบิล",
    "📚 ประวัติและผล",
    "ℹ️ วิธีใช้",
])

with tab_scan:
    st.subheader("ตัวเลือกจากการสแกน")

    df = load_picks()
    if df.empty:
        st.info("ยังไม่มีประวัติ กดสแกนจากเมนูด้านซ้ายก่อน")
    else:
        ev_values = pd.to_numeric(df["ev_pct"], errors="coerce")
        view = df[ev_values >= min_ev_pct].copy()
        view = view.sort_values("ev_pct", ascending=False)

        st.metric("จำนวนรายการที่ผ่าน EV", len(view))
        columns = [
            "kickoff_th", "league", "home_team", "away_team",
            "market", "side", "point", "odds", "bookmaker",
            "ev_pct", "p_win", "p_partial", "p_loss", "status"
        ]
        columns = [c for c in columns if c in view.columns]
        st.dataframe(
            view[columns], use_container_width=True, hide_index=True
        )

        st.caption(
            "EV เป็นค่าคาดหวังจากแบบจำลอง ไม่ใช่กำไรที่รับประกัน "
            "P(win) หมายถึงโอกาสชนะเต็ม ส่วน P(partial) รวมผลครึ่งชนะ "
            "ครึ่งแพ้ และคืนทุน"
        )


with tab_slips:
    st.subheader("จัดบิลจากตัวเลือกที่ผ่านเกณฑ์")

    df = load_picks()
    if df.empty:
        st.info("ต้องสแกนราคาและมีประวัติก่อน")
    else:
        df["ev_pct"] = pd.to_numeric(df["ev_pct"], errors="coerce")
        df["odds"] = pd.to_numeric(df["odds"], errors="coerce")
        df["p_win"] = pd.to_numeric(df["p_win"], errors="coerce")

        candidates = df[
            (df["ev_pct"] >= min_ev_pct)
            & (df["status"] == "รอตรวจผล")
        ].copy()

        # เลือกหนึ่งตัวเลือกต่อหนึ่งแมตช์ และใช้สัญญาณล่าสุดของแมตช์นั้น
        candidates = candidates.sort_values(
            ["scanned_at", "ev_pct"], ascending=[False, False]
        )
        candidates = candidates.drop_duplicates(
            subset=["event_id"], keep="first"
        )
        candidates = candidates.sort_values(
            "ev_pct", ascending=False
        )

        st.write(
            f"มี {len(candidates)} แมตช์ที่ผ่านเกณฑ์และยังไม่ตรวจผล"
        )
        slip_size = st.selectbox(
            "จำนวนคู่ในบิล", [5, 9, 13, 20], index=0
        )

        if st.button("สร้างบิลจาก EV สูงสุด"):
            if len(candidates) < slip_size:
                st.warning(
                    f"มีเพียง {len(candidates)} คู่ "
                    f"ยังไม่พอสำหรับบิล {slip_size} คู่"
                )
            else:
                chosen = candidates.head(slip_size).copy()
                combined_odds = float(chosen["odds"].prod())
                # ประมาณแบบง่าย โดยสมมติผลแต่ละคู่เป็นอิสระ
                p = (chosen["p_win"].clip(0, 100) / 100).prod()
                created = datetime.now(TH_TZ).isoformat(
                    timespec="seconds"
                )

                slip_json = chosen[[
                    "event_id", "match", "market", "side",
                    "point", "odds", "ev_pct"
                ]].to_dict(orient="records")

                with db() as con:
                    con.execute("""
                    INSERT INTO slips (
                        created_at, slip_size, selections_json,
                        combined_odds, estimated_probability
                    ) VALUES (?,?,?,?,?)
                    """, (
                        created, slip_size, json.dumps(
                            slip_json, ensure_ascii=False
                        ), combined_odds, float(p * 100)
                    ))

                st.session_state["current_slip"] = chosen
                st.success("บันทึกบิลแล้ว")

        if "current_slip" in st.session_state:
            slip = st.session_state["current_slip"]
            st.dataframe(
                slip[[
                    "kickoff_th", "match", "market", "side",
                    "point", "odds", "ev_pct"
                ]],
                use_container_width=True,
                hide_index=True,
            )
            st.metric(
                "ค่าน้ำรวมโดยประมาณ",
                f"{slip['odds'].prod():.2f}"
            )
            st.caption(
                "ความน่าจะเป็นบิลเป็นค่าประมาณจากโอกาสชนะเต็มของแต่ละคู่ "
                "ไม่ใช่ความน่าจะเป็นที่ผ่านการสอบเทียบ และไม่รวมความสัมพันธ์ "
                "ระหว่างคู่ จึงไม่ควรใช้เป็นหลักประกันการเดิมพัน"
            )


with tab_history:
    st.subheader("ประวัติสัญญาณและตรวจผล")

    df = load_picks()
    if df.empty:
        st.info("ยังไม่มีข้อมูล")
    else:
        st.dataframe(
            df[[
                "id", "kickoff_th", "league", "home_team",
                "away_team", "market", "side", "point",
                "odds", "ev_pct", "status", "final_score",
                "settlement"
            ]],
            use_container_width=True,
            hide_index=True,
        )

        if st.button("🔄 ตรวจผลย้อนหลังจาก Football-Data.co.uk"):
            checked = 0
            for _, row in df[df["status"] != "ตรวจแล้ว"].iterrows():
                code = next(
                    (
                        v[1] for k, v in LEAGUES.items()
                        if v[0] == row["league"]
                    ),
                    None,
                )
                if not code:
                    continue

                history = fetch_history(code)
                try:
                    kickoff = datetime.fromisoformat(
                        str(row["kickoff_utc"])
                    )
                except Exception:
                    continue

                result = result_for_match(
                    history, row["home_team"], row["away_team"], kickoff
                )
                if not result:
                    continue

                hg, ag = result
                settlement = grade_pick(
                    row["market"], row["side"], float(row["point"]),
                    hg, ag
                )
                update_pick_result(
                    row["id"], f"{hg}-{ag}", settlement
                )
                checked += 1

            st.success(
                f"อัปเดตผลได้ {checked} รายการ "
                "รายการที่หาไม่พบจะยังคงรอตรวจผล"
            )
            st.rerun()

        # Summary of resolved picks (not actual monetary ROI)
        settled = df[df["status"] == "ตรวจแล้ว"]
        st.metric("รายการตรวจผลแล้ว", len(settled))
        st.caption(
            "จำนวนชนะ/แพ้ยังไม่ใช่ ROI จริง เพราะต้องมีเงินเดิมพัน "
            "และผลตอบแทนจริงของแต่ละบิลประกอบด้วย"
        )

    with db() as con:
        snapshots = pd.read_sql_query(
            """
            SELECT event_id, match_name, market, side, point,
                   odds, bookmaker, captured_at
            FROM odds_snapshots
            ORDER BY id DESC LIMIT 1000
            """,
            con,
        )

    st.subheader("ประวัติราคาที่บันทึกไว้")
    if snapshots.empty:
        st.info(
            "ยังไม่มี Snapshot ราคา ระบบจะเริ่มเก็บตั้งแต่การสแกนครั้งแรก"
        )
    else:
        st.dataframe(
            snapshots, use_container_width=True, hide_index=True
        )
        st.download_button(
            "ดาวน์โหลดประวัติราคา CSV",
            snapshots.to_csv(index=False).encode("utf-8-sig"),
            file_name="value_bet_odds_snapshots.csv",
            mime="text/csv",
        )

    # Backup the SQLite file
    if DB_FILE.exists():
        st.download_button(
            "⬇️ สำรองฐานข้อมูล SQLite",
            data=DB_FILE.read_bytes(),
            file_name="value_bet_pro_backup.db",
            mime="application/octet-stream",
        )


with tab_help:
    st.subheader("คำอธิบายและข้อจำกัด")

    st.markdown("""
**EV (%)**
- เป็นผลตอบแทนสุทธิคาดหวังต่อเงินเดิมพัน 1 หน่วยจากแบบจำลอง Poisson
- EV บวกไม่ได้หมายความว่าคู่นั้นจะชนะ หรือระบบจะทำกำไรจริง

**Asian Handicap และสูง/ต่ำ**
- รองรับเส้นเต็ม เส้นครึ่ง และเส้นควบ เช่น -0.25, +0.25, 2.25 และ 2.75
- ผลครึ่งชนะ ครึ่งแพ้ และคืนทุนถูกนำมาคำนวณ EV ตามส่วนของเงินเดิมพัน

**ข้อมูลที่ใช้**
- The Odds API: ราคาตลาด spreads และ totals ที่ผู้ให้บริการส่งมา
- Football-Data.co.uk: ผลประตูย้อนหลังในลีกที่มีไฟล์ข้อมูล
- แบบจำลองใช้ประตูย้อนหลัง ไม่ใช่ xG จริง และไม่ได้รวมผู้เล่นเจ็บ โทษแบน
  สภาพอากาศ หรือรายชื่อ 11 ตัวจริง

**ราคาเปิดและราคาไหล**
- โปรแกรมเริ่มสะสมราคาเมื่อสแกนและบันทึก Snapshot
- จะไม่มีราคาเปิดย้อนหลังที่แท้จริง หากไม่ได้เก็บไว้หรือไม่มี Historical Odds API

**การทำงานอัตโนมัติ**
- โค้ดนี้ดึงข้อมูลเมื่อผู้ใช้สั่งสแกน ไม่ใช่ระบบตั้งเวลารันเบื้องหลัง
- แคช 15 นาทีช่วยลดการเรียก API ซ้ำ แต่ไม่ได้ทำให้แอปทำงานเองตลอดเวลา

**SQLite บน Streamlit Cloud**
- ไฟล์ฐานข้อมูลนี้อยู่ในพื้นที่ของแอป ซึ่งอาจไม่ถาวรเมื่อระบบถูกสร้างใหม่
- ดาวน์โหลดไฟล์สำรองเป็นระยะ หรือย้ายไปฐานข้อมูลภายนอกที่มีพื้นที่ถาวร

**ข้อมูลนักเตะ**
- รุ่นนี้ยังไม่ได้เชื่อม API สำหรับอาการบาดเจ็บและโทษแบน จึงไม่แสดงข้อมูลที่ไม่ได้ตรวจสอบ
""")

st.divider()
st.caption(
    "Value Bet Pro ใช้เพื่อวิเคราะห์ข้อมูลเท่านั้น "
    "การพนันมีความเสี่ยงและไม่มีระบบใดรับประกันกำไร"
)
