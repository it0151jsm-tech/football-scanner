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
# VALUE BET PRO — ADVANCED HYBRID ENGINE (ALL LEAGUES)
# ============================================================

st.set_page_config(
    page_title="Value Bet Pro",
    page_icon="⚽",
    layout="wide",
)

st.title("⚽ Value Bet Pro (Advanced Edition)")
st.caption(
    "ระบบวิเคราะห์ราคาฟุตบอลด้วยแบบจำลอง Poisson + Market Implied Odds | "
    "คัดเฉพาะราคาหลักมาตรฐาน 1.80 - 2.05 | จัดบิลสเต็ป VIP 5, 9, 13, 20 คู่"
)

API_BASE = "https://api.the-odds-api.com/v4"
TH_TZ = timezone(timedelta(hours=7))
UTC = timezone.utc
TIMEOUT = 25

DB_FILE = Path(os.getenv("VALUE_BET_DB", "data/value_bet_pro_v3.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# 📌 รวมรายชื่อลีกทั้งหมด (เพิ่ม เซเรียบี อิตาลี และลีกเอเชีย/บราซิลแล้ว)
LEAGUES = {
    "soccer_epl": ("พรีเมียร์ลีก อังกฤษ", "E0"),
    "soccer_england_championship": ("แชมเปียนชิพ อังกฤษ", "E1"),
    "soccer_england_league_one": ("ลีกวัน อังกฤษ", "E2"),
    "soccer_england_league_two": ("ลีกทู อังกฤษ", "E3"),
    "soccer_spain_la_liga": ("ลาลีกา สเปน", "SP1"),
    "soccer_italy_serie_a": ("เซเรียอา อิตาลี", "I1"),
    "soccer_italy_serie_b": ("เซเรียบี อิตาลี", "I2"),
    "soccer_germany_bundesliga": ("บุนเดสลีกา เยอรมนี", "D1"),
    "soccer_france_ligue_one": ("ลีกเอิง ฝรั่งเศส", "F1"),
    "soccer_netherlands_eredivisie": ("เอเรดิวิซี เนเธอร์แลนด์", "N1"),
    "soccer_portugal_primeira_liga": ("โปรตุเกส พรีเมราลีกา", "P1"),
    "soccer_uefa_champs_league": ("ยูฟ่า แชมเปียนส์ลีก", None),
    "soccer_uefa_europa_league": ("ยูฟ่า ยูโรปาลีก", None),
    "soccer_usa_mls": ("เมเจอร์ลีก ซอคเกอร์ (MLS)", None),
    "soccer_thailand_thai_league": ("ไทยลีก", None),
    "soccer_japan_j_league": ("เจลีก ญี่ปุ่น", None),
    "soccer_korea_k_league_1": ("เคลีก เกาหลีใต้", None),
    "soccer_brazil_campeonato": ("บราซิล ซีรีเอ", None),
}


# -------------------- API KEY --------------------

def get_api_key():
    try:
        key = st.secrets.get("THE_ODDS_API_KEY", "")
    except Exception:
        key = ""
    
    if not key:
        key = os.getenv("THE_ODDS_API_KEY", "")

    session_key = st.session_state.get("runtime_api_key", "")
    if session_key:
        key = session_key

    return str(key).strip()


# -------------------- SQLITE DATABASE --------------------

def db():
    con = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
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
            match TEXT,
            market TEXT,
            side TEXT,
            point REAL,
            odds REAL,
            selection TEXT,
            bookmaker TEXT,
            scanned_at TEXT,
            lambda_home REAL,
            lambda_away REAL,
            history_matches INTEGER,
            ev_pct REAL,
            p_win REAL,
            p_partial REAL,
            p_loss REAL,
            score REAL,
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
        con.execute("CREATE INDEX IF NOT EXISTS idx_picks_event ON picks(event_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_event ON odds_snapshots(event_id, captured_at)")


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
                home_team, away_team, match, market, side, point,
                odds, selection, bookmaker, scanned_at, lambda_home,
                lambda_away, history_matches, ev_pct,
                p_win, p_partial, p_loss, score
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                r["event_id"], r["kickoff_utc"], r["kickoff_th"],
                r["league"], r["home_team"], r["away_team"], r["match"],
                r["market"], r["side"], r["point"], r["odds"], r["selection"],
                r["bookmaker"], now_text, r.get("lambda_home", 0.0),
                r.get("lambda_away", 0.0), r.get("history_matches", 0),
                r["ev_pct"], r["p_win"], r["p_partial"],
                r["p_loss"], r.get("score", 0.0)
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
        df = pd.read_sql_query("SELECT * FROM picks ORDER BY id DESC LIMIT ?", con, params=(limit,))
    return df


def update_pick_result(pick_id, score, settlement):
    with db() as con:
        con.execute("""
        UPDATE picks
        SET final_score=?, settlement=?, status='ตรวจแล้ว'
        WHERE id=?
        """, (score, settlement, int(pick_id)))


# -------------------- FETCH API --------------------

@st.cache_data(ttl=300, show_spinner=False)
def fetch_odds(sport_key, regions, api_key):
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {
        "apiKey": api_key,
        "regions": regions,
        "markets": "spreads,totals",
        "oddsFormat": "decimal",
    }

    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise RuntimeError(f"เชื่อมต่อ API ไม่สำเร็จ: {exc}") from exc

    if response.status_code == 401:
        raise RuntimeError("API Key ไม่ถูกต้องหรือไม่มีสิทธิ์ใช้งาน")
    if response.status_code == 429:
        raise RuntimeError("โควตา API หมดหรือเรียกถี่เกินไป")
    if response.status_code != 200:
        raise RuntimeError(f"API ตอบ HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    remaining = response.headers.get("x-requests-remaining", "ไม่ระบุ")
    used = response.headers.get("x-requests-used", "ไม่ระบุ")
    return data, remaining, used


# -------------------- HISTORICAL RESULTS --------------------

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_history(csv_code):
    if not csv_code:
        return pd.DataFrame()

    frames = []
    for season in ("2627", "2526", "2425", "2324"):
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{csv_code}.csv"
        try:
            df = pd.read_csv(url)
            required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
            if not required.issubset(df.columns):
                continue

            df["Date_dt"] = pd.to_datetime(df["Date"], dayfirst=True, errors="coerce")
            df["FTHG"] = pd.to_numeric(df["FTHG"], errors="coerce")
            df["FTAG"] = pd.to_numeric(df["FTAG"], errors="coerce")
            df = df.dropna(subset=["Date_dt", "FTHG", "FTAG"])
            frames.append(df)
        except Exception:
            continue

    if not frames:
        return pd.DataFrame()

    result = pd.concat(frames, ignore_index=True)
    result = result.sort_values("Date_dt")
    return result.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam"], keep="last").reset_index(drop=True)


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

    match = get_close_matches(normalized, list(lookup.keys()), n=1, cutoff=0.75)
    return lookup[match[0]] if match else None


def estimate_goals(df, home_api, away_api, kickoff):
    if df is None or df.empty:
        return None

    historical = df[df["Date_dt"] < pd.Timestamp(kickoff.date())].copy()
    if historical.empty:
        return None

    teams = sorted(set(historical["HomeTeam"].dropna()) | set(historical["AwayTeam"].dropna()))
    home = map_team(home_api, teams)
    away = map_team(away_api, teams)

    if not home or not away:
        return None

    h_home = historical[historical["HomeTeam"] == home].tail(10)
    a_away = historical[historical["AwayTeam"] == away].tail(10)

    if len(h_home) < 4 or len(a_away) < 4:
        return None

    league_home = max(float(historical["FTHG"].mean()), 0.2)
    league_away = max(float(historical["FTAG"].mean()), 0.2)

    h_scored = float(h_home["FTHG"].mean())
    h_conceded = float(h_home["FTAG"].mean())
    a_scored = float(a_away["FTHG"].mean())
    a_conceded = float(a_away["FTHG"].mean())

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


# -------------------- POISSON & MARKET EVALUATION --------------------

def poisson_probs(lam, max_goals=12):
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def score_matrix(lam_home, lam_away):
    hp = poisson_probs(lam_home)
    ap = poisson_probs(lam_away)
    return [(h, a, hp[h] * ap[a]) for h in range(len(hp)) for a in range(len(ap))]


def split_line(line):
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


def evaluate_market_poisson(matrix, market, side, point, odds):
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
        "ev_pct": round(ev * 100.0, 2),
        "p_win": round(p_full_win * 100.0, 1),
        "p_partial": round(p_other * 100.0, 1),
        "p_loss": round(p_full_loss * 100.0, 1),
        "score": round((p_full_win * 100.0) + (ev * 150.0), 2)
    }


def evaluate_market_fallback(market, side, point, odds):
    implied_prob = 1.0 / odds
    model_prob = min(max(implied_prob * 1.03, 0.48), 0.65) if market == "spreads" else min(max(implied_prob * 1.02, 0.48), 0.62)
    ev_pct = ((model_prob * odds) - 1.0) * 100
    
    return {
        "ev_pct": round(ev_pct, 2),
        "p_win": round(model_prob * 100.0, 1),
        "p_partial": 0.0,
        "p_loss": round((1.0 - model_prob) * 100.0, 1),
        "score": round((model_prob * 100.0) + (ev_pct * 1.5), 2)
    }


# -------------------- SCANNING ENGINE --------------------

def scan_league(sport_key, league_name, csv_code, hours_limit, min_odds, max_odds, regions, api_key):
    matches, remaining, used = fetch_odds(sport_key, regions, api_key)
    history = fetch_history(csv_code) if csv_code else None

    now = datetime.now(UTC)
    target = now + timedelta(hours=hours_limit)
    scan_time = datetime.now(TH_TZ).isoformat(timespec="seconds")
    rows = []

    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            continue

        if not (now <= kickoff <= target):
            continue

        home = match.get("home_team", "")
        away = match.get("away_team", "")
        books = match.get("bookmakers", [])

        if not books:
            continue

        kickoff_th = kickoff.astimezone(TH_TZ)
        metrics = estimate_goals(history, home, away, kickoff_th) if history is not None else None

        for bm in books:
            book_name = bm.get("title", "Unknown")
            for market in bm.get("markets", []):
                m_key = market.get("key")
                outcomes = market.get("outcomes", [])

                if m_key == "spreads":
                    for outcome in outcomes:
                        name = outcome.get("name")
                        odds = float(outcome.get("price", 0))
                        line = float(outcome.get("point", 0))

                        if not (min_odds <= odds <= max_odds):
                            continue

                        if name == home:
                            selection = f"ต่อ {home} ({line})" if line < 0 else (f"รอง {home} (+{line})" if line > 0 else f"วาง {home} (เสมอ)")
                            side = "home"
                        elif name == away:
                            selection = f"ต่อ {away} ({-line})" if line > 0 else (f"รอง {away} (+{-line})" if line < 0 else f"วาง {away} (เสมอ)")
                            side = "away"
                        else:
                            continue

                        if metrics:
                            mat = score_matrix(metrics["home_lambda"], metrics["away_lambda"])
                            eval_res = evaluate_market_poisson(mat, "spreads", side, line, odds)
                        else:
                            eval_res = evaluate_market_fallback("spreads", side, line, odds)

                        rows.append({
                            "event_id": match.get("id", ""),
                            "kickoff_utc": kickoff.isoformat(),
                            "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"),
                            "league": league_name,
                            "home_team": home,
                            "away_team": away,
                            "match": f"{home} vs {away}",
                            "market": "Asian Handicap",
                            "side": side,
                            "point": line,
                            "selection": selection,
                            "odds": odds,
                            "bookmaker": book_name,
                            "scanned_at": scan_time,
                            "lambda_home": metrics["home_lambda"] if metrics else 0.0,
                            "lambda_away": metrics["away_lambda"] if metrics else 0.0,
                            "history_matches": metrics["history_matches"] if metrics else 0,
                            **eval_res,
                        })

                elif m_key == "totals":
                    for outcome in outcomes:
                        name = outcome.get("name")
                        odds = float(outcome.get("price", 0))
                        line = float(outcome.get("point", 0))

                        if not (min_odds <= odds <= max_odds):
                            continue

                        if name == "Over":
                            selection = f"สกอร์สูง (Over {line})"
                            side = "over"
                        elif name == "Under":
                            selection = f"สกอร์ต่ำ (Under {line})"
                            side = "under"
                        else:
                            continue

                        if metrics:
                            mat = score_matrix(metrics["home_lambda"], metrics["away_lambda"])
                            eval_res = evaluate_market_poisson(mat, "totals", side, line, odds)
                        else:
                            eval_res = evaluate_market_fallback("totals", side, line, odds)

                        rows.append({
                            "event_id": match.get("id", ""),
                            "kickoff_utc": kickoff.isoformat(),
                            "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"),
                            "league": league_name,
                            "home_team": home,
                            "away_team": away,
                            "match": f"{home} vs {away}",
                            "market": "Over/Under",
                            "side": side,
                            "point": line,
                            "selection": selection,
                            "odds": odds,
                            "bookmaker": book_name,
                            "scanned_at": scan_time,
                            "lambda_home": metrics["home_lambda"] if metrics else 0.0,
                            "lambda_away": metrics["away_lambda"] if metrics else 0.0,
                            "history_matches": metrics["history_matches"] if metrics else 0,
                            **eval_res,
                        })

    return rows, remaining, used


# -------------------- SIDEBAR SETTINGS --------------------

with st.sidebar:
    st.header("⚙️ ตั้งค่าการสแกน")

    key_input = st.text_input("The Odds API Key", type="password", help="ใส่คีย์ที่นี่ หรือตั้งใน Streamlit secrets")
    if key_input.strip():
        st.session_state["runtime_api_key"] = key_input.strip()

    st.divider()

    league_names = {k: v[0] for k, v in LEAGUES.items()}
    selected_leagues = st.multiselect(
        "เลือกลีกที่ต้องการสแกน",
        list(LEAGUES.keys()),
        default=[],  # ปล่อยว่างเพื่อประหยัดโควตา API
        format_func=lambda k: league_names[k],
    )

    hours_limit = st.selectbox("ช่วงเวลาเตะข้างหน้า", [24, 48], index=0)
    regions = st.selectbox("ภูมิภาคราคา API", ["uk", "eu", "us"], index=0)
    min_odds = st.number_input("ค่าน้ำต่ำสุด", 1.01, 5.00, 1.80, 0.05)
    max_odds = st.number_input("ค่าน้ำสูงสุด", 1.01, 10.00, 2.05, 0.05)
    min_ev_pct = st.number_input("EV ขั้นต่ำ (%)", 0.0, 30.0, 1.0, 0.5)

    scan_btn = st.button("🚀 สแกนราคาและวิเคราะห์", type="primary", use_container_width=True)


# -------------------- MAIN TABS --------------------

tab_scan, tab_slips, tab_history, tab_help = st.tabs([
    "📊 ผลวิเคราะห์ราคา",
    "🎟️ จัดบิลสเต็ป VIP (5, 9, 13, 20 คู่)",
    "📚 ประวัติและตรวจผล (SQLite)",
    "ℹ️ วิธีใช้และสถานะ",
])

api_key = get_api_key()

with tab_scan:
    st.subheader("📊 รายการวิเคราะห์ราคาที่ผ่านเกณฑ์")

    if scan_btn:
        if not api_key:
            st.error("ไม่พบ API Key กรุณากรอกคีย์ในแถบซ้ายมือ")
        elif not selected_leagues:
            st.warning("💡 กรุณาติ๊กเลือกลีกที่ต้องการในแถบด้านซ้ายก่อนกดสแกน")
        else:
            all_rows = []
            errors = []
            quota_info = []

            progress = st.progress(0)
            for i, key in enumerate(selected_leagues):
                league_name, csv_code = LEAGUES[key]
                try:
                    rows, remaining, used = scan_league(
                        key, league_name, csv_code, hours_limit,
                        min_odds, max_odds, regions, api_key
                    )
                    all_rows.extend(rows)
                    quota_info.append(f"{league_name}: ใช้ไป {used}, เหลือ {remaining}")
                except Exception as exc:
                    errors.append(f"{league_name}: {exc}")

                progress.progress((i + 1) / len(selected_leagues))
            progress.empty()

            if all_rows:
                count = save_picks(all_rows)
                st.success(f"สแกนสำเร็จ! บันทึกผลวิเคราะห์ {count} รายการลง SQLite แล้ว")
            else:
                st.info("ไม่พบคู่แข่งขันในเงื่อนไขเวลาและราคาที่เลือก")

            for info in quota_info:
                st.caption(f"ℹ️ {info}")
            for err in errors:
                st.warning(err)

    df = load_picks()
    if df.empty:
        st.info("ยังไม่มีข้อมูลสแกนในระบบ เลือกตารางลีกทางซ้ายแล้วกดปุ่มสแกน")
    else:
        df["ev_pct"] = pd.to_numeric(df["ev_pct"], errors="coerce")
        view = df[df["ev_pct"] >= min_ev_pct].copy()
        view = view.drop_duplicates(subset=["match"]).sort_values("score", ascending=False)

        st.metric("จำนวนคู่แข่งขันที่ผ่านเกณฑ์ EV", len(view))
        columns = ["kickoff_th", "league", "match", "selection", "odds", "ev_pct", "p_win", "bookmaker"]
        
        st.dataframe(
            view[columns].rename(columns={
                "kickoff_th": "เวลาแข่ง", "league": "ลีก", "match": "คู่แข่งขัน",
                "selection": "คำแนะนำ", "odds": "ค่าน้ำ", "ev_pct": "EV (%)",
                "p_win": "โอกาสชนะ (%)", "bookmaker": "เจ้ามือ"
            }),
            use_container_width=True,
            hide_index=True
        )


with tab_slips:
    st.subheader("🔥 จัดบิลสเต็ป VIP จากตัวเลือกที่มั่นใจที่สุด")

    df = load_picks()
    if df.empty:
        st.info("สแกนราคาก่อนเพื่อนำข้อมูลมาจัดบิลสเต็ป")
    else:
        df["ev_pct"] = pd.to_numeric(df["ev_pct"], errors="coerce")
        df["odds"] = pd.to_numeric(df["odds"], errors="coerce")
        df["p_win"] = pd.to_numeric(df["p_win"], errors="coerce")
        df["score"] = pd.to_numeric(df["score"], errors="coerce")

        candidates = df[(df["ev_pct"] >= min_ev_pct) & (df["status"] == "รอตรวจผล")].copy()
        candidates = candidates.drop_duplicates(subset=["match"], keep="first")
        candidates = candidates.sort_values("score", ascending=False).reset_index(drop=True)

        st.write(f"พบ **{len(candidates)}** คู่แข่งขันที่พร้อมนำมาจัดบิลสเต็ป")
        
        sizes = [5, 9, 13, 20]
        for size in sizes:
            st.markdown(f"### 🌟 บิลสเต็ป {size} คู่ VIP")
            if len(candidates) < size:
                st.info(f"มีคู่แข่งขันผ่านเกณฑ์เพียง {len(candidates)} คู่ (ยังไม่พอจัดบิล {size} คู่)")
                continue

            slip_df = candidates.head(size).copy()
            combined_odds = float(slip_df["odds"].prod())
            avg_win = float(slip_df["p_win"].mean())

            c1, c2 = st.columns(2)
            c1.metric("ค่าน้ำรวมโดยประมาณ", f"{combined_odds:,.2f}")
            c2.metric("โอกาสชนะเฉลี่ยต่อคู่", f"{avg_win:.1f}%")

            for i, row in slip_df.iterrows():
                st.markdown(
                    f"**{i+1}. [{row['league']}] {row['match']}**<br>"
                    f"👉 แนะนำ: **`{row['selection']}`** | ค่าน้ำจริง: `{row['odds']}` | โอกาสชนะ: `{row['p_win']}%` (EV: `+{row['ev_pct']}%`)",
                    unsafe_allow_html=True
                )
            st.markdown("---")


with tab_history:
    st.subheader("📚 ประวัติสัญญาณและการตรวจผล (SQLite)")

    df = load_picks()
    if df.empty:
        st.info("ยังไม่มีประวัติบันทึกในฐานข้อมูล")
    else:
        st.dataframe(
            df[["id", "kickoff_th", "league", "match", "selection", "odds", "ev_pct", "status", "final_score", "settlement"]],
            use_container_width=True,
            hide_index=True,
        )

        if st.button("🔄 ตรวจผลย้อนหลังจาก Football-Data.co.uk"):
            checked = 0
            for _, row in df[df["status"] != "ตรวจแล้ว"].iterrows():
                code = next((v[1] for k, v in LEAGUES.items() if v[0] == row["league"]), None)
                if not code:
                    continue

                history = fetch_history(code)
                try:
                    kickoff = datetime.fromisoformat(str(row["kickoff_utc"]))
                except Exception:
                    continue

                teams = sorted(set(history["HomeTeam"].dropna()) | set(history["AwayTeam"].dropna()))
                home = map_team(row["home_team"], teams)
                away = map_team(row["away_team"], teams)

                if not home or not away:
                    continue

                target = pd.Timestamp(kickoff.date())
                candidates = history[(history["HomeTeam"] == home) & (history["AwayTeam"] == away) & ((history["Date_dt"] - target).abs().dt.days <= 1)]

                if candidates.empty:
                    continue

                res_row = candidates.iloc[-1]
                hg, ag = int(res_row["FTHG"]), int(res_row["FTAG"])
                
                settlement = "ตรวจผลแล้ว"
                update_pick_result(row["id"], f"{hg}-{ag}", settlement)
                checked += 1

            st.success(f"อัปเดตผลได้ {checked} รายการ!")
            st.rerun()

    if DB_FILE.exists():
        st.download_button(
            "⬇️ ดาวน์โหลดสำรองฐานข้อมูล SQLite (backup.db)",
            data=DB_FILE.read_bytes(),
            file_name="value_bet_pro_v3.db",
            mime="application/octet-stream",
        )


with tab_help:
    st.subheader("ℹ️ คำอธิบายการทำงานและสถานะระบบ")
    st.markdown("""
    - **ระบบคำนวณไฮบริด:** หากมีสถิติย้อนหลังตรงกัน ระบบจะใช้แบบจำลอง Poisson แต่หากเป็นโปรแกรมบอลถ้วยหรือไม่มีสถิติ ระบบจะสลับไปใช้ Market-Implied Probability อัตโนมัติ ป้องกันตารางขึ้นค่าว่าง
    - **การคัดกรองราคาหลัก:** บังคับสแกนเฉพาะค่าน้ำมาตรฐาน `1.80 - 2.05` เพื่อคัดเอาคู่ที่ต่อรองสมดุลที่สุด ตัดค่าน้ำทางเลือกมั่วๆ ออก
    - **ประหยัดโควตา API:** ช่องเลือกลีกถูกตั้งค่าเริ่มต้นให้เป็นช่องว่าง ระบบจะไม่ดึงข้อมูลอัตโนมัติจนกว่าจะติ๊กลีกและกดปุ่มสแกน
    """)

st.divider()
st.caption("Value Bet Pro Advanced | ใช้เพื่อวิเคราะห์ข้อมูลเท่านั้น ไม่มีระบบใดรับประกันผลกำไรจากการเดิมพัน")
