import os
import re
import math
import json
import codecs
import sqlite3
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from difflib import get_close_matches

import pandas as pd
import requests
import streamlit as st

# ============================================================
# VALUE BET PRO — safer baseline
# Streamlit + The Odds API + SQLite
# ============================================================
st.set_page_config(page_title="Value Bet Pro", page_icon="⚽", layout="centered", initial_sidebar_state="collapsed")
st.title("⚽ Value Bet Pro — xG Edition")
st.caption("ราคาไหล + xG/xGA + EV | ออกแบบให้อ่านง่ายบนมือถือ — EV เป็นค่าประมาณ ไม่รับประกันกำไร")
st.markdown("""<style>
.block-container {padding-top: 1rem; padding-bottom: 2rem; max-width: 1100px;}
[data-testid="stMetric"] {padding: .65rem; border: 1px solid rgba(128,128,128,.22); border-radius: .65rem;}
[data-testid="stDataFrame"] {font-size: .9rem;}
@media (max-width: 640px) { .block-container {padding-left: .75rem; padding-right: .75rem;} h1 {font-size: 1.55rem !important;} }
</style>""", unsafe_allow_html=True)

API_BASE = "https://api.the-odds-api.com/v4"
TH_TZ = timezone(timedelta(hours=7))
UTC = timezone.utc
TIMEOUT = 25
DB_FILE = Path(os.getenv("VALUE_BET_DB", "data/value_bet_pro_v4.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# CSV codes are only set where a matching Football-Data competition code is known.
# Do not reuse a code across unrelated leagues.
LEAGUES = {
    "soccer_epl": ("พรีเมียร์ลีก อังกฤษ", "E0"),
    "soccer_england_championship": ("แชมเปียนชิพ อังกฤษ", "E1"),
    "soccer_england_league_one": ("ลีกวัน อังกฤษ", "E2"),
    "soccer_england_league_two": ("ลีกทู อังกฤษ", "E3"),
    "soccer_spain_la_liga": ("ลาลีกา สเปน", "SP1"),
    "soccer_italy_serie_a": ("เซเรียอา อิตาลี", "I1"),
    "soccer_italy_serie_b": ("เซเรียบี อิตาลี", "I2"),
    "soccer_germany_bundesliga": ("บุนเดสลีกา เยอรมนี", "D1"),
    "soccer_germany_bundesliga2": ("บุนเดสลีกา 2 เยอรมนี", "D2"),
    "soccer_germany_3liga": ("ลีก 3 เยอรมนี", None),
    "soccer_france_ligue_one": ("ลีกเอิง ฝรั่งเศส", "F1"),
    "soccer_netherlands_eredivisie": ("เอเรดิวิซี เนเธอร์แลนด์", "N1"),
    "soccer_portugal_primeira_liga": ("โปรตุเกส พรีเมราลีกา", "P1"),
    "soccer_norway_eliteserien": ("นอร์เวย์ เอลิเตเซเรียน", None),
    "soccer_uefa_champs_league": ("ยูฟ่า แชมเปียนส์ลีก", None),
    "soccer_uefa_europa_league": ("ยูฟ่า ยูโรปาลีก", None),
    "soccer_uefa_europa_conference_league": ("ยูฟ่า คอนเฟอเรนซ์ลีก", None),
    "soccer_saudi_pro_league": ("ซาอุดีอาระเบีย โปรลีก", None),
    "soccer_usa_mls": ("เมเจอร์ลีก ซอคเกอร์ (MLS)", None),
    "soccer_thailand_thai_league": ("ไทยลีก", None),
    "soccer_japan_j_league": ("เจลีก ญี่ปุ่น", None),
    "soccer_korea_k_league_1": ("เคลีก เกาหลีใต้", None),
    "soccer_brazil_campeonato": ("บราซิล ซีรีเอ", None),
    "soccer_brazil_serie_b": ("บราซิล ซีรีบี", None),
}

# Understat public league pages provide historical match xG for selected competitions.
# Unsupported leagues intentionally return no xG; the model must not invent it.
UNDERSTAT_LEAGUES = {
    "soccer_epl": "EPL",
    "soccer_spain_la_liga": "La_liga",
    "soccer_germany_bundesliga": "Bundesliga",
    "soccer_italy_serie_a": "Serie_A",
    "soccer_france_ligue_one": "Ligue_1",
    "soccer_russia_premier_league": "RFPL",
}
UNDERSTAT_SEASONS = (2026, 2025, 2024, 2023)

# -------------------- API KEY --------------------
def get_api_key():
    try:
        key = st.secrets.get("THE_ODDS_API_KEY", "")
    except Exception:
        key = ""
    if not key:
        key = os.getenv("THE_ODDS_API_KEY", "")
    session_key = st.session_state.get("runtime_api_key", "")
    return str(session_key or key).strip()

# -------------------- DATABASE / MIGRATIONS --------------------
def db():
    con = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    with db() as con:
        con.execute("""CREATE TABLE IF NOT EXISTS picks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_id TEXT, sport_key TEXT, kickoff_utc TEXT, kickoff_th TEXT, league TEXT,
            home_team TEXT, away_team TEXT, match TEXT, market TEXT, side TEXT,
            point REAL, odds REAL, selection TEXT, bookmaker TEXT, scanned_at TEXT,
            lambda_home REAL, lambda_away REAL, history_matches INTEGER,
            xg_home_avg REAL, xga_home_avg REAL, xg_away_avg REAL, xga_away_avg REAL,
            ev_pct REAL, p_win REAL, p_partial REAL, p_loss REAL, score REAL,
            status TEXT DEFAULT 'รอตรวจผล', final_score TEXT DEFAULT '', settlement TEXT DEFAULT '',
            model_status TEXT DEFAULT 'unknown', profit_units REAL, outcome_binary INTEGER
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, match_name TEXT,
            market TEXT, side TEXT, point REAL, odds REAL, bookmaker TEXT, captured_at TEXT
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS slips (
            id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, slip_size INTEGER,
            selections_json TEXT, combined_odds REAL, estimated_probability REAL,
            status TEXT DEFAULT 'รอตรวจผล'
        )""")
        # Upgrade an existing v3 database without deleting the user's data.
        existing = {r[1] for r in con.execute("PRAGMA table_info(picks)").fetchall()}
        migrations = {
            "model_status": "TEXT DEFAULT 'unknown'",
            "sport_key": "TEXT",
            "profit_units": "REAL",
            "outcome_binary": "INTEGER",
            "xg_home_avg": "REAL",
            "xga_home_avg": "REAL",
            "xg_away_avg": "REAL",
            "xga_away_avg": "REAL",
        }
        for name, decl in migrations.items():
            if name not in existing:
                con.execute(f"ALTER TABLE picks ADD COLUMN {name} {decl}")
        con.execute("""CREATE TABLE IF NOT EXISTS scan_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT, scanned_at TEXT, leagues TEXT,
            rows_found INTEGER DEFAULT 0, rows_with_ev INTEGER DEFAULT 0,
            api_remaining TEXT, api_used TEXT, notes TEXT DEFAULT ''
        )""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_picks_event ON picks(event_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_event ON odds_snapshots(event_id, captured_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON odds_snapshots(event_id, market, side, bookmaker, captured_at)")

init_db()


def save_picks(rows):
    if not rows:
        return 0
    now_text = datetime.now(TH_TZ).isoformat(timespec="seconds")
    count = 0
    with db() as con:
        for r in rows:
            con.execute("""INSERT INTO picks (
                event_id,sport_key,kickoff_utc,kickoff_th,league,home_team,away_team,match,market,side,point,
                odds,selection,bookmaker,scanned_at,lambda_home,lambda_away,history_matches,
                xg_home_avg,xga_home_avg,xg_away_avg,xga_away_avg,
                ev_pct,p_win,p_partial,p_loss,score,model_status
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                r.get("event_id"), r.get("sport_key"), r.get("kickoff_utc"), r.get("kickoff_th"), r.get("league"),
                r.get("home_team"), r.get("away_team"), r.get("match"), r.get("market"), r.get("side"),
                r.get("point"), r.get("odds"), r.get("selection"), r.get("bookmaker"), now_text,
                r.get("lambda_home"), r.get("lambda_away"), r.get("history_matches"),
                r.get("xg_home_avg"), r.get("xga_home_avg"), r.get("xg_away_avg"), r.get("xga_away_avg"),
                r.get("ev_pct"), r.get("p_win"), r.get("p_partial"), r.get("p_loss"),
                r.get("score"), r.get("model_status", "ข้อมูลย้อนหลังไม่พอ")
            ))
            # Store every scan as a timestamped observation. Repeated same-price snapshots are useful
            # for seeing that a market was checked, but scan frequency should be controlled by API quota.
            con.execute("""INSERT INTO odds_snapshots
                (event_id,match_name,market,side,point,odds,bookmaker,captured_at)
                VALUES (?,?,?,?,?,?,?,?)""", (
                r.get("event_id"), r.get("match"), r.get("market"), r.get("side"),
                r.get("point"), r.get("odds"), r.get("bookmaker"), now_text
            ))
            count += 1
    return count



def expire_pending_picks(league_names):
    """Mark old recommendations for successfully scanned leagues as stale, retaining history."""
    names = [str(x) for x in league_names if x]
    if not names:
        return 0
    placeholders = ",".join("?" for _ in names)
    now_utc = datetime.now(UTC).isoformat()
    with db() as con:
        cur = con.execute(
            f"UPDATE picks SET status='ราคาเก่า/หมดอายุ' WHERE league IN ({placeholders}) "
            "AND status='รอตรวจผล' AND kickoff_utc >= ?",
            (*names, now_utc),
        )
        return cur.rowcount


def log_scan(leagues, rows, remaining_values, used_values, notes=""):
    now_text = datetime.now(TH_TZ).isoformat(timespec="seconds")
    remaining = "; ".join(str(x) for x in remaining_values if x is not None)
    used = "; ".join(str(x) for x in used_values if x is not None)
    with db() as con:
        con.execute("""INSERT INTO scan_runs
            (scanned_at,leagues,rows_found,rows_with_ev,api_remaining,api_used,notes)
            VALUES (?,?,?,?,?,?,?)""", (
                now_text, ", ".join(leagues), len(rows),
                sum(1 for r in rows if r.get("ev_pct") is not None), remaining, used, notes
            ))


def database_backup_bytes():
    """Use SQLite's backup API for a consistent backup, even if the DB is open."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        temp_path = tmp.name
    try:
        source = sqlite3.connect(str(DB_FILE), timeout=30)
        target = sqlite3.connect(temp_path)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return Path(temp_path).read_bytes()
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass


def restore_database_bytes(uploaded_bytes):
    """Validate an uploaded SQLite backup before replacing the active DB."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
        tmp.write(uploaded_bytes)
        temp_path = tmp.name
    try:
        test = sqlite3.connect(temp_path)
        try:
            integrity = test.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {r[0] for r in test.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if integrity != "ok" or "picks" not in tables or "odds_snapshots" not in tables:
                raise ValueError("ไฟล์ไม่ใช่ฐานข้อมูล Value Bet Pro ที่สมบูรณ์")
        finally:
            test.close()
        # Preserve current data as an in-memory backup file before replacement.
        old_bytes = database_backup_bytes() if DB_FILE.exists() else None
        if old_bytes:
            Path(str(DB_FILE) + ".before_restore.bak").write_bytes(old_bytes)
        DB_FILE.parent.mkdir(parents=True, exist_ok=True)
        replacement = Path(str(DB_FILE) + ".restore.tmp")
        replacement.write_bytes(Path(temp_path).read_bytes())
        replacement.replace(DB_FILE)
        init_db()
    finally:
        try:
            Path(temp_path).unlink(missing_ok=True)
        except Exception:
            pass

def load_picks(limit=5000):
    with db() as con:
        return pd.read_sql_query("SELECT * FROM picks ORDER BY id DESC LIMIT ?", con, params=(limit,))


def update_pick_result(pick_id, score, settlement, profit_units, outcome_binary=None):
    with db() as con:
        con.execute("""UPDATE picks SET final_score=?, settlement=?, status='ตรวจแล้ว',
            profit_units=?, outcome_binary=? WHERE id=?""",
            (score, settlement, profit_units, outcome_binary, int(pick_id)))

# -------------------- ODDS API --------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_odds(sport_key, regions, api_key):
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {"apiKey": api_key, "regions": regions, "markets": "spreads,totals", "oddsFormat": "decimal"}
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
    return response.json(), response.headers.get("x-requests-remaining", "ไม่ระบุ"), response.headers.get("x-requests-used", "ไม่ระบุ")


@st.cache_data(ttl=900, show_spinner=False)
def fetch_api_scores(sport_key, api_key, days_from=3):
    """Fetch recent completed scores from The Odds API (useful for UCL and unsupported leagues)."""
    url = f"{API_BASE}/sports/{sport_key}/scores/"
    params = {"apiKey": api_key, "daysFrom": int(days_from)}
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    try:
        data = response.json()
        return data if isinstance(data, list) else []
    except ValueError:
        return []


def scores_by_event(scores, event_id):
    """Return final home/away scores only when The Odds API marks the event completed."""
    for item in scores or []:
        if str(item.get("id", "")) != str(event_id) or not item.get("completed"):
            continue
        values = {str(x.get("name", "")): x.get("score") for x in (item.get("scores") or [])}
        home, away = item.get("home_team", ""), item.get("away_team", "")
        try:
            hg, ag = int(float(values[home])), int(float(values[away]))
        except (KeyError, TypeError, ValueError):
            continue
        return hg, ag
    return None

# -------------------- HISTORICAL RESULTS --------------------
@st.cache_data(ttl=21600, show_spinner=False)
def fetch_history(csv_code):
    if not csv_code:
        return pd.DataFrame()
    frames = []
    # Current and recent seasons. Missing/unpublished CSVs are skipped safely.
    for season in ("2627", "2526", "2425", "2324"):
        url = f"https://www.football-data.co.uk/mmz4281/{season}/{csv_code}.csv"
        try:
            response = requests.get(url, timeout=TIMEOUT)
            if response.status_code != 200 or not response.content:
                continue
            from io import BytesIO
            frame = pd.read_csv(BytesIO(response.content))
            required = {"Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
            if not required.issubset(frame.columns):
                continue
            frame["Date_dt"] = pd.to_datetime(frame["Date"], dayfirst=True, errors="coerce")
            frame["FTHG"] = pd.to_numeric(frame["FTHG"], errors="coerce")
            frame["FTAG"] = pd.to_numeric(frame["FTAG"], errors="coerce")
            frame = frame.dropna(subset=["Date_dt", "FTHG", "FTAG", "HomeTeam", "AwayTeam"])
            frames.append(frame)
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    result = pd.concat(frames, ignore_index=True).sort_values("Date_dt")
    return result.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam"], keep="last").reset_index(drop=True)


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_understat_history(sport_key):
    """Fetch actual Understat match xG/xGA. Returns empty data on unsupported/blocked pages."""
    league_code = UNDERSTAT_LEAGUES.get(sport_key)
    if not league_code:
        return pd.DataFrame()
    frames = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "X-Requested-With": "XMLHttpRequest",
    }
    for season in UNDERSTAT_SEASONS:
        url = f"https://understat.com/getLeagueData/{league_code}/{season}"
        try:
            response = requests.get(url, headers={**headers, "Referer": f"https://understat.com/league/{league_code}/{season}"}, timeout=TIMEOUT)
            if response.status_code != 200 or not response.content:
                continue
            payload = response.json()
            # Understat's league data endpoint returns match-level rows under `dates`.
            dates = payload.get("dates", []) if isinstance(payload, dict) else []
            rows = []
            for item in dates:
                if not item.get("isResult", True):
                    continue
                home_obj, away_obj = item.get("h") or {}, item.get("a") or {}
                try:
                    dt = pd.to_datetime(item.get("datetime"), utc=True, errors="coerce")
                    hg, ag = float(item.get("goals", {}).get("h")), float(item.get("goals", {}).get("a"))
                    xgh, xga = float(item.get("xG", {}).get("h")), float(item.get("xG", {}).get("a"))
                except (TypeError, ValueError):
                    continue
                if pd.isna(dt) or not home_obj.get("title") or not away_obj.get("title"):
                    continue
                if not all(math.isfinite(v) for v in (hg, ag, xgh, xga)):
                    continue
                rows.append({
                    "Date_dt": dt.tz_convert(None),
                    "HomeTeam": str(home_obj.get("title")),
                    "AwayTeam": str(away_obj.get("title")),
                    "FTHG": hg, "FTAG": ag, "xG_home": xgh, "xG_away": xga,
                })
            if rows:
                frames.append(pd.DataFrame(rows))
        except Exception:
            # A blocked or changed upstream page means no model, not synthetic values.
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam"], keep="last")
    return out.sort_values("Date_dt").reset_index(drop=True)

def norm_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower().replace("&", "and"))


def map_team(name, candidates):
    if not candidates:
        return None
    lookup = {norm_name(x): x for x in candidates}
    normalized = norm_name(name)
    if normalized in lookup:
        return lookup[normalized]
    match = get_close_matches(normalized, list(lookup.keys()), n=1, cutoff=0.82)
    return lookup[match[0]] if match else None


def estimate_goals(df, home_api, away_api, kickoff):
    """Estimate expected goals from actual historical xG/xGA only; no goals-only fallback."""
    if df is None or df.empty:
        return None
    cutoff = pd.Timestamp(kickoff.astimezone(UTC).replace(tzinfo=None))
    hist = df[df["Date_dt"] < cutoff].copy()
    if hist.empty or not {"xG_home", "xG_away"}.issubset(hist.columns):
        return None
    teams = sorted(set(hist["HomeTeam"].dropna()) | set(hist["AwayTeam"].dropna()))
    home = map_team(home_api, teams)
    away = map_team(away_api, teams)
    if not home or not away or home == away:
        return None
    h_home = hist[hist["HomeTeam"] == home].tail(10)
    a_away = hist[hist["AwayTeam"] == away].tail(10)
    if len(h_home) < 5 or len(a_away) < 5:
        return None
    # Require xG values for all sampled matches; don't silently substitute goals.
    h_home = h_home.dropna(subset=["xG_home", "xG_away"])
    a_away = a_away.dropna(subset=["xG_home", "xG_away"])
    if len(h_home) < 5 or len(a_away) < 5:
        return None
    league_home = float(hist["xG_home"].mean())
    league_away = float(hist["xG_away"].mean())
    if not all(math.isfinite(x) and x > 0 for x in (league_home, league_away)):
        return None
    prior_n = 4.0
    h_xgf = (float(h_home["xG_home"].sum()) + league_home * prior_n) / (len(h_home) + prior_n)
    h_xga = (float(h_home["xG_away"].sum()) + league_away * prior_n) / (len(h_home) + prior_n)
    a_xgf = (float(a_away["xG_away"].sum()) + league_away * prior_n) / (len(a_away) + prior_n)
    a_xga = (float(a_away["xG_home"].sum()) + league_home * prior_n) / (len(a_away) + prior_n)
    lam_home = league_home * (h_xgf / league_home) * (a_xga / league_home)
    lam_away = league_away * (a_xgf / league_away) * (h_xga / league_away)
    if not all(math.isfinite(x) and x > 0 for x in (lam_home, lam_away)):
        return None
    used = pd.concat([h_home.assign(_team_sample="home"), a_away.assign(_team_sample="away")])
    return {
        "home_lambda": min(max(lam_home, 0.10), 4.5),
        "away_lambda": min(max(lam_away, 0.10), 4.5),
        "history_matches": int(len(used.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam"]))),
        "league_matches": int(len(hist)),
        "xg_home_avg": round(float(h_home["xG_home"].mean()), 3),
        "xga_home_avg": round(float(h_home["xG_away"].mean()), 3),
        "xg_away_avg": round(float(a_away["xG_away"].mean()), 3),
        "xga_away_avg": round(float(a_away["xG_home"].mean()), 3),
    }

# -------------------- POISSON / ASIAN SETTLEMENT --------------------
def poisson_probs(lam, max_goals=12):
    probs = [math.exp(-lam)]
    for k in range(1, max_goals + 1):
        probs.append(probs[-1] * lam / k)
    probs[-1] += max(0.0, 1.0 - sum(probs))
    return probs


def score_matrix(lam_home, lam_away):
    hp, ap = poisson_probs(lam_home), poisson_probs(lam_away)
    return [(h, a, hp[h] * ap[a]) for h in range(len(hp)) for a in range(len(ap))]


def split_line(line):
    q = round(float(line) * 4)
    if q % 2 == 0:
        return [(q / 4.0, 1.0)]
    lower = math.floor(q / 2) / 2.0
    return [(lower, 0.5), (lower + 0.5, 0.5)]


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
        "p_win": round(p_full_win * 100.0, 2),
        "p_partial": round(p_other * 100.0, 2),
        "p_loss": round(p_full_loss * 100.0, 2),
        # Ranking is by EV directly; this is not a separate confidence score.
        "score": round(ev * 100.0, 2),
    }


def selection_spread(team, side, line):
    # The API point is signed for each outcome; preserve it for settlement math.
    if abs(line) < 1e-9:
        return f"วาง {team} (0)"
    if line < 0:
        return f"ต่อ {team} (-{abs(line):g})"
    return f"รอง {team} (+{abs(line):g})"

# -------------------- SCAN --------------------
def scan_league(sport_key, league_name, csv_code, hours_limit, min_odds, max_odds, regions, api_key):
    matches, remaining, used = fetch_odds(sport_key, regions, api_key)
    history = fetch_understat_history(sport_key)
    now = datetime.now(UTC)
    target = now + timedelta(hours=hours_limit)
    rows = []
    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            continue
        if not (now <= kickoff <= target):
            continue
        home, away = match.get("home_team", ""), match.get("away_team", "")
        kickoff_th = kickoff.astimezone(TH_TZ)
        metrics = estimate_goals(history, home, away, kickoff) if not history.empty else None
        matrix = score_matrix(metrics["home_lambda"], metrics["away_lambda"]) if metrics else None
        for bm in match.get("bookmakers", []):
            book_name = bm.get("title", "Unknown")
            for market in bm.get("markets", []):
                m_key = market.get("key")
                if m_key not in ("spreads", "totals"):
                    continue
                for outcome in market.get("outcomes", []):
                    try:
                        odds = float(outcome.get("price", 0))
                        line = float(outcome.get("point", 0))
                    except (TypeError, ValueError):
                        continue
                    if not (min_odds <= odds <= max_odds):
                        continue
                    name = outcome.get("name")
                    if m_key == "spreads":
                        if name == home:
                            side = "home"
                            selection = selection_spread(home, side, line)
                        elif name == away:
                            side = "away"
                            selection = selection_spread(away, side, line)
                        else:
                            continue
                        market_label = "Asian Handicap"
                    else:
                        if name == "Over":
                            side, selection = "over", f"สกอร์สูง (Over {line:g})"
                        elif name == "Under":
                            side, selection = "under", f"สกอร์ต่ำ (Under {line:g})"
                        else:
                            continue
                        market_label = "Over/Under"
                    eval_res = evaluate_market_poisson(matrix, m_key, side, line, odds) if matrix else None
                    rows.append({
                        "event_id": match.get("id", ""), "sport_key": sport_key,
                        "kickoff_utc": kickoff.isoformat(),
                        "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"),
                        "league": league_name, "home_team": home, "away_team": away,
                        "match": f"{home} vs {away}", "market": market_label,
                        "side": side, "point": line, "selection": selection,
                        "odds": odds, "bookmaker": book_name,
                        "lambda_home": metrics["home_lambda"] if metrics else None,
                        "lambda_away": metrics["away_lambda"] if metrics else None,
                        "history_matches": metrics["history_matches"] if metrics else 0,
                        "xg_home_avg": metrics.get("xg_home_avg") if metrics else None,
                        "xga_home_avg": metrics.get("xga_home_avg") if metrics else None,
                        "xg_away_avg": metrics.get("xg_away_avg") if metrics else None,
                        "xga_away_avg": metrics.get("xga_away_avg") if metrics else None,
                        "model_status": "Poisson จาก xG/xGA จริง (Understat)" if metrics else ("ลีกนี้ไม่มี xG จากแหล่งที่ตั้งไว้ — งดคำนวณ EV" if sport_key not in UNDERSTAT_LEAGUES else "xG ย้อนหลังไม่พอ/แหล่งข้อมูลไม่พร้อม — งดคำนวณ EV"),
                        "ev_pct": eval_res["ev_pct"] if eval_res else None,
                        "p_win": eval_res["p_win"] if eval_res else None,
                        "p_partial": eval_res["p_partial"] if eval_res else None,
                        "p_loss": eval_res["p_loss"] if eval_res else None,
                        "score": eval_res["score"] if eval_res else None,
                    })
    return rows, remaining, used

# -------------------- RESULT SETTLEMENT --------------------
def settle_pick(row, home_goals, away_goals):
    odds = float(row["odds"])
    point = float(row["point"] or 0)
    market = row["market"]
    side = row["side"]
    if market == "Asian Handicap":
        diff = (home_goals - away_goals) if side == "home" else (away_goals - home_goals)
        adjusted_base = diff
    elif market == "Over/Under":
        adjusted_base = home_goals + away_goals
    else:
        return None, None
    outcomes = []
    net_profit = 0.0
    for line, weight in split_line(point):
        if market == "Asian Handicap":
            result = settle_leg(adjusted_base + line)
        else:
            adjusted = adjusted_base - line if side == "over" else line - adjusted_base
            result = settle_leg(adjusted)
        outcomes.append(result)
        if result == "win":
            net_profit += weight * (odds - 1.0)
        elif result == "loss":
            net_profit -= weight
    if all(x == "win" for x in outcomes):
        label, binary = "ชนะเต็ม", 1
    elif all(x == "loss" for x in outcomes):
        label, binary = "แพ้เต็ม", 0
    elif all(x == "push" for x in outcomes):
        label, binary = "คืนทุน", None
    elif net_profit > 1e-9:
        label, binary = "ชนะครึ่ง", None
    elif net_profit < -1e-9:
        label, binary = "แพ้ครึ่ง", None
    else:
        label, binary = "คืนทุน", None
    return (label, round(net_profit, 4), binary)

# -------------------- SIDEBAR --------------------
with st.sidebar:
    st.header("⚙️ ตั้งค่าการสแกน")
    key_input = st.text_input("The Odds API Key", type="password", help="แนะนำให้ตั้งใน Streamlit secrets")
    if key_input.strip():
        st.session_state["runtime_api_key"] = key_input.strip()
    st.caption("อย่าใส่ API Key ลงในโค้ดหรือส่งให้ผู้อื่น")
    st.divider()
    league_names = {k: v[0] for k, v in LEAGUES.items()}
    selected_leagues = st.multiselect("เลือกลีกที่ต้องการสแกน", list(LEAGUES.keys()), default=[], format_func=lambda k: league_names[k])
    hours_limit = st.selectbox("ช่วงเวลาเตะข้างหน้า", [24, 48], index=0)
    regions = st.selectbox("ภูมิภาคราคา API", ["uk", "eu", "us"], index=0)
    min_odds = st.number_input("ค่าน้ำต่ำสุด", 1.01, 5.00, 1.80, 0.05)
    max_odds = st.number_input("ค่าน้ำสูงสุด", 1.01, 10.00, 2.05, 0.05)
    min_ev_pct = st.number_input("EV ขั้นต่ำ (%)", -20.0, 30.0, 1.0, 0.5)
    scan_btn = st.button("🚀 สแกนราคาและวิเคราะห์", type="primary", use_container_width=True)

api_key = get_api_key()
tab_scan, tab_slips, tab_history, tab_help = st.tabs(["📊 ผลวิเคราะห์ราคา", "🎟️ ตัวเลือกสเต็ป", "📚 ราคาไหลและตรวจผล", "ℹ️ วิธีใช้"])

with tab_scan:
    st.subheader("📊 รายการวิเคราะห์")
    if scan_btn:
        if not api_key:
            st.error("ไม่พบ API Key กรุณากรอกคีย์ในแถบซ้ายมือ")
        elif not selected_leagues:
            st.warning("เลือกลีกทางซ้ายก่อนกดสแกน")
        elif min_odds > max_odds:
            st.error("ค่าน้ำต่ำสุดต้องไม่มากกว่าค่าน้ำสูงสุด")
        else:
            all_rows, errors, quota_info = [], [], []
            successful_leagues, remaining_values, used_values = [], [], []
            progress = st.progress(0)
            for i, key in enumerate(selected_leagues):
                league_name, csv_code = LEAGUES[key]
                try:
                    rows, remaining, used = scan_league(key, league_name, csv_code, hours_limit, min_odds, max_odds, regions, api_key)
                    all_rows.extend(rows)
                    successful_leagues.append(league_name)
                    remaining_values.append(remaining)
                    used_values.append(used)
                    quota_info.append(f"{league_name}: ใช้ไป {used}, เหลือ {remaining}")
                except Exception as exc:
                    errors.append(f"{league_name}: {exc}")
                progress.progress((i + 1) / len(selected_leagues))
            progress.empty()
            if successful_leagues:
                expired_count = expire_pending_picks(successful_leagues)
                count = save_picks(all_rows)
                log_scan(successful_leagues, all_rows, remaining_values, used_values,
                         f"หมดอายุรายการราคาเก่า {expired_count} รายการ")
                if all_rows:
                    st.success(f"บันทึก snapshot ใหม่ {count} รายการแล้ว; ทำเครื่องหมายราคาเก่า {expired_count} รายการ")
                    reliable = sum(1 for r in all_rows if r.get("ev_pct") is not None)
                    st.info(f"มีข้อมูลพอคำนวณ EV: {reliable}/{len(all_rows)} รายการ — รายการที่ข้อมูลไม่พอจะไม่ถูกใช้จัดบิล")
                else:
                    st.info("สแกนสำเร็จ แต่ไม่มีคู่ผ่านช่วงเวลาและตัวกรองค่าน้ำที่ตั้งไว้")
                    st.caption(f"ทำเครื่องหมายรายการราคาเก่า/ไม่พบในผลสแกนล่าสุด {expired_count} รายการ โดยยังเก็บประวัติเดิมไว้")
            else:
                st.info("สแกนไม่สำเร็จทุกลีก จึงไม่เปลี่ยนสถานะราคาเดิม")
            for info in quota_info:
                st.caption("ℹ️ " + info)
            for err in errors:
                st.warning(err)
    df = load_picks()
    if df.empty:
        st.info("ยังไม่มีข้อมูล เลือกลีกและกดสแกน")
    else:
        df["kickoff_parsed"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
        now_ts = pd.Timestamp.now(tz="UTC")
        df["ev_pct"] = pd.to_numeric(df["ev_pct"], errors="coerce")
        upcoming = df[(df["kickoff_parsed"] >= now_ts) & (df["status"] == "รอตรวจผล")].copy()
        view = upcoming[upcoming["ev_pct"].notna() & (upcoming["ev_pct"] >= min_ev_pct)].copy()
        view = view.sort_values("ev_pct", ascending=False).drop_duplicates(subset=["event_id", "market", "side", "point", "bookmaker"])
        st.metric("ตัวเลือกที่ผ่าน EV และยังไม่เริ่มแข่ง", len(view))
        if not view.empty:
            cols = ["kickoff_th", "league", "match", "selection", "odds", "xg_home_avg", "xga_home_avg", "xg_away_avg", "xga_away_avg", "ev_pct", "p_win", "bookmaker", "model_status"]
            st.markdown("#### ตัวเลือกเรียงตาม EV สูงสุด")
            for _, item in view.head(20).iterrows():
                with st.container(border=True):
                    st.markdown(f"**{item['match']}**")
                    st.caption(f"{item['league']} · {item['kickoff_th']} · {item['market']}")
                    a, b, c = st.columns(3)
                    a.metric("ค่าน้ำ", f"{float(item['odds']):.2f}")
                    ev_value = pd.to_numeric(item.get("ev_pct"), errors="coerce")
                    b.metric("EV", f"{ev_value:+.2f}%" if pd.notna(ev_value) else "—")
                    win_value = pd.to_numeric(item.get("p_win"), errors="coerce")
                    c.metric("ชนะเต็มโดยแบบจำลอง", f"{win_value:.1f}%" if pd.notna(win_value) else "—")
                    st.write(f"**ตัวเลือก:** {item['selection']}  |  **เจ้ามือ:** {item['bookmaker']}")
                    st.caption(str(item.get("model_status") or "ไม่ระบุแหล่งประเมิน"))
            with st.expander("ดูตารางรายละเอียดทั้งหมด"):
                st.dataframe(view[cols].rename(columns={"kickoff_th":"เวลาแข่ง", "league":"ลีก", "match":"คู่แข่งขัน", "selection":"ตัวเลือก", "odds":"ค่าน้ำ", "xg_home_avg":"เหย้า xG เฉลี่ย", "xga_home_avg":"เหย้า xGA เฉลี่ย", "xg_away_avg":"เยือน xG เฉลี่ย", "xga_away_avg":"เยือน xGA เฉลี่ย", "ev_pct":"EV (%)", "p_win":"ชนะเต็ม (%)", "bookmaker":"เจ้ามือ", "model_status":"แหล่งประเมิน"}), use_container_width=True, hide_index=True)
        else:
            st.info("ยังไม่มีตัวเลือกที่ผ่านเกณฑ์จากแบบจำลองที่มีข้อมูลเพียงพอ")
        unsupported = upcoming[upcoming["ev_pct"].isna()]
        if not unsupported.empty:
            with st.expander(f"ดูราคาแต่ไม่มี EV เพราะข้อมูลย้อนหลังไม่พอ ({len(unsupported)} รายการ)"):
                st.dataframe(unsupported[["kickoff_th","league","match","selection","odds","bookmaker","model_status"]], use_container_width=True, hide_index=True)

with tab_slips:
    st.subheader("🎟️ คัดตัวเลือกสำหรับพิจารณา — ไม่ใช่การรับประกันบิลชนะ")
    df = load_picks()
    if df.empty:
        st.info("สแกนราคาก่อน")
    else:
        df["kickoff_parsed"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
        for col in ("ev_pct", "odds", "p_win", "score"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        now_ts = pd.Timestamp.now(tz="UTC")
        candidates = df[(df["kickoff_parsed"] >= now_ts) & df["ev_pct"].notna() & df["p_win"].notna() & (df["ev_pct"] >= min_ev_pct) & (df["status"] == "รอตรวจผล")].copy()
        candidates = candidates.sort_values("ev_pct", ascending=False).drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
        bookmakers = sorted(str(x) for x in candidates["bookmaker"].dropna().unique())
        if bookmakers:
            selected_bookmaker = st.selectbox("เจ้ามือสำหรับคำนวณบิล (ต้องเป็นเจ้ามือเดียวกัน)", bookmakers, key="slip_bookmaker")
            candidates = candidates[candidates["bookmaker"].astype(str) == selected_bookmaker].copy()
        st.write(f"มี {len(candidates)} คู่ที่มีแบบจำลองและยังไม่เริ่มแข่งจากเจ้ามือที่เลือก (เลือกสูงสุด 1 ตัวเลือกต่อคู่)")
        sizes = [5, 9, 13, 20]
        for size in sizes:
            st.markdown(f"#### บิล {size} คู่")
            if len(candidates) < size:
                st.caption(f"มีเพียง {len(candidates)} คู่ที่ผ่านเกณฑ์ จึงไม่จัดบิล {size} คู่")
                continue
            slip = candidates.head(size).copy()
            combined_odds = float(slip["odds"].prod())
            p_full = float((slip["p_win"].clip(0, 100) / 100).prod() * 100)
            c1, c2 = st.columns(2)
            c1.metric("ค่าน้ำรวม (กรณีชนะเต็มทุกคู่)", f"{combined_odds:,.2f}")
            c2.metric("โอกาสชนะเต็มทุกคู่โดยประมาณ", f"{p_full:.3f}%")
            st.caption("คำนวณโดยสมมติว่าผลแต่ละคู่เป็นอิสระต่อกัน และไม่รวมผลชนะครึ่ง/คืนทุน จึงไม่ใช่ความน่าจะเป็นที่แน่นอนของบิลเอเชียน")
            st.dataframe(slip[["league","match","selection","odds","ev_pct","p_win","bookmaker"]].rename(columns={"league":"ลีก","match":"คู่แข่งขัน","selection":"ตัวเลือก","odds":"ค่าน้ำ","ev_pct":"EV (%)","p_win":"ชนะเต็ม (%)","bookmaker":"เจ้ามือ"}), use_container_width=True, hide_index=True)

with tab_history:
    st.subheader("📈 ประวัติราคาและตรวจผล")
    with db() as con:
        keys = pd.read_sql_query("""SELECT DISTINCT event_id, match_name, market, side, bookmaker
            FROM odds_snapshots ORDER BY event_id DESC LIMIT 500""", con)
        movement_rows = []
        for _, keyrow in keys.iterrows():
            rows = con.execute("""SELECT point, odds, captured_at FROM odds_snapshots
                WHERE event_id=? AND market=? AND side=? AND bookmaker=?
                ORDER BY captured_at ASC, id ASC""", (keyrow["event_id"], keyrow["market"], keyrow["side"], keyrow["bookmaker"])).fetchall()
            if not rows:
                continue
            first, latest = rows[0], rows[-1]
            try:
                first_odds, latest_odds = float(first["odds"]), float(latest["odds"])
                first_point = float(first["point"] or 0)
                latest_point = float(latest["point"] or 0)
            except (TypeError, ValueError):
                continue
            odds_delta = round(latest_odds - first_odds, 3)
            point_changed = abs(latest_point - first_point) > 1e-9
            trend = "ราคาเพิ่ม" if odds_delta > 0 else ("ราคาลด" if odds_delta < 0 else "ราคาน้ำคงที่")
            if point_changed:
                trend += " / แต้มต่อเปลี่ยน"
            movement_rows.append({"คู่แข่งขัน":keyrow["match_name"], "ตลาด":keyrow["market"], "ฝั่ง":keyrow["side"], "เจ้ามือ":keyrow["bookmaker"], "ราคาแรก":first_odds, "ราคาล่าสุด":latest_odds, "เปลี่ยนแปลงน้ำ":odds_delta, "แต้มแรก":first_point, "แต้มล่าสุด":latest_point, "จำนวน snapshot":len(rows), "ตรวจครั้งแรก":first["captured_at"], "ตรวจล่าสุด":latest["captured_at"], "สัญญาณ":trend})
    if movement_rows:
        st.dataframe(pd.DataFrame(movement_rows), use_container_width=True, hide_index=True)
        st.caption("การเปลี่ยนแปลงราคาเป็นเพียงสัญญาณตลาด ไม่สามารถยืนยันได้ว่าเป็นราคาไหลหลอก")
    else:
        st.info("ยังไม่มีประวัติราคา กดสแกนมากกว่าหนึ่งครั้งในช่วงเวลาต่างกันเพื่อเริ่มเปรียบเทียบ")

    st.divider()
    st.markdown("#### ตรวจผลย้อนหลัง (The Odds API Scores ก่อน แล้วใช้ Football-Data เมื่อรองรับ)")
    df = load_picks()
    if not df.empty:
        st.dataframe(df[["id","kickoff_th","league","match","selection","odds","ev_pct","model_status","status","final_score","settlement","profit_units"]], use_container_width=True, hide_index=True)
        if st.button("🔄 ตรวจและคำนวณผลที่หาเจอ", type="secondary"):
            checked = 0
            skipped = 0
            # Avoid claiming settlement if the league has no matching results source.
            score_cache = {}
            for _, row in df[df["status"].isin(["รอตรวจผล", "ราคาเก่า/หมดอายุ"])].iterrows():
                # First use the event ID from The Odds API; this works for Champions League too.
                sport_key = str(row.get("sport_key") or next(
                    (key for key, value in LEAGUES.items() if value[0] == row["league"]), ""
                ))
                api_result = None
                if sport_key and row.get("event_id"):
                    if sport_key not in score_cache:
                        score_cache[sport_key] = fetch_api_scores(sport_key, api_key, 3) if api_key else []
                    api_result = scores_by_event(score_cache[sport_key], row["event_id"])
                if api_result is not None:
                    hg, ag = api_result
                else:
                    code = next((v[1] for v in LEAGUES.values() if v[0] == row["league"]), None)
                    if not code:
                        skipped += 1
                        continue
                    hist = fetch_history(code)
                    if hist.empty:
                        skipped += 1
                        continue
                    try:
                        kickoff = pd.to_datetime(row["kickoff_utc"], utc=True).tz_convert(None).normalize()
                    except Exception:
                        skipped += 1
                        continue
                    teams = sorted(set(hist["HomeTeam"].dropna()) | set(hist["AwayTeam"].dropna()))
                    home = map_team(row["home_team"], teams)
                    away = map_team(row["away_team"], teams)
                    if not home or not away:
                        skipped += 1
                        continue
                    candidates = hist[(hist["HomeTeam"] == home) & (hist["AwayTeam"] == away)].copy()
                    if candidates.empty:
                        skipped += 1
                        continue
                    candidates["date_diff"] = (candidates["Date_dt"].dt.normalize() - kickoff).abs().dt.days
                    candidates = candidates[candidates["date_diff"] <= 1].sort_values("date_diff")
                    if candidates.empty:
                        skipped += 1
                        continue
                    result = candidates.iloc[0]
                    hg, ag = int(result["FTHG"]), int(result["FTAG"])
                settled = settle_pick(row, hg, ag)
                if settled is None:
                    skipped += 1
                    continue
                label, profit, binary = settled
                update_pick_result(row["id"], f"{hg}-{ag}", label, profit, binary)
                checked += 1
            st.success(f"ตรวจผลและคำนวณการชำระราคาได้ {checked} รายการ; ข้ามเพราะไม่มีผลตรงกัน/ไม่มีแหล่งข้อมูล {skipped} รายการ")
            st.rerun()

        settled = df[df["status"] == "ตรวจแล้ว"].copy()
        if not settled.empty:
            settled["profit_units"] = pd.to_numeric(settled["profit_units"], errors="coerce")
            total_profit = settled["profit_units"].fillna(0).sum()
            n = len(settled)
            roi = total_profit / n * 100 if n else 0
            a, b, c = st.columns(3)
            a.metric("จำนวนเดิมพันที่ตรวจผลแล้ว", n)
            b.metric("กำไร/ขาดทุนสุทธิ (หน่วยเดิมพัน)", f"{total_profit:+.2f}")
            c.metric("ROI ต่อเดิมพันที่ตรวจแล้ว", f"{roi:+.2f}%")
            st.caption("ROI นี้นับหนึ่งหน่วยเดิมพันต่อรายการ และใช้เฉพาะรายการที่มีการชำระผลได้ ไม่รวมค่าคอมมิชชันหรือความแตกต่างของเงินเดิมพัน")
    st.divider()
    if DB_FILE.exists():
        st.download_button("⬇️ ดาวน์โหลดสำรองฐานข้อมูล SQLite", data=database_backup_bytes(), file_name=f"value_bet_backup_{datetime.now(TH_TZ).strftime('%Y%m%d_%H%M')}.db", mime="application/octet-stream", use_container_width=True)
    with st.expander("♻️ กู้คืนฐานข้อมูลจากไฟล์สำรอง"):
        st.warning("การกู้คืนจะแทนที่ฐานข้อมูลปัจจุบัน ไฟล์ปัจจุบันจะถูกเก็บเป็น .before_restore.bak ในพื้นที่แอป แต่บน Streamlit Cloud พื้นที่นี้อาจหายเมื่อรีสตาร์ต")
        uploaded_db = st.file_uploader("เลือกไฟล์สำรอง Value Bet Pro (.db)", type=["db", "sqlite", "sqlite3"], key="restore_db_upload")
        confirm_restore = st.checkbox("ยืนยันว่าต้องการแทนที่ฐานข้อมูลปัจจุบัน", key="confirm_restore_db")
        if st.button("กู้คืนฐานข้อมูล", disabled=not (uploaded_db and confirm_restore), use_container_width=True):
            try:
                restore_database_bytes(uploaded_db.getvalue())
                st.success("กู้คืนฐานข้อมูลแล้ว")
                st.rerun()
            except Exception as exc:
                st.error(f"กู้คืนไม่สำเร็จ: {exc}")

    with st.expander("🧾 ประวัติการสแกน"):
        with db() as con:
            runs = pd.read_sql_query("SELECT * FROM scan_runs ORDER BY id DESC LIMIT 100", con)
        if runs.empty:
            st.info("ยังไม่มีประวัติการสแกน")
        else:
            st.dataframe(runs, use_container_width=True, hide_index=True)

with tab_help:
    st.subheader("ℹ️ วิธีใช้และข้อจำกัด")
    st.markdown("""
- **ไม่มี fallback EV ปลอม:** ถ้าประวัติไม่พอ ระบบจะแสดงราคาแต่เว้น EV และไม่ใช้จัดบิล
- **แบบจำลอง:** ใช้ xG/xGA จริงจากหน้า Understat สำหรับลีกที่รองรับเท่านั้น และใช้ Poisson ประเมินสกอร์ ไม่ใช่คำรับประกันผลกำไร
- **ความครอบคลุม xG:** โค้ดตั้งค่า Understat สำหรับพรีเมียร์ลีก, ลาลีกา, บุนเดสลีกา, เซเรียอา, ลีกเอิง และรัสเซีย หากเว็บปิดกั้น/เปลี่ยนรูปแบบ หรือไม่มีข้อมูลก่อนเตะ ระบบจะเว้น EV
- **ลีกอื่น:** ยังดึงราคาได้ แต่จะไม่แสดง EV จนกว่าจะเชื่อมแหล่ง xG ที่เชื่อถือได้สำหรับลีกนั้น ห้ามตีความว่าไม่มี EV แปลว่าไม่มี value
- **ประวัติราคา:** เก็บ snapshot แต่ละครั้งแยกกัน รายการที่ไม่อยู่ในผลสแกนล่าสุดของลีกที่สแกนสำเร็จจะถูกทำเครื่องหมายราคาเก่า/หมดอายุ ไม่ลบประวัติเดิม
- **โควตา The Odds API:** เลือกสแกนเฉพาะลีกที่สนใจ; แต่ละลีกใช้คำขอราคา 1 ครั้ง โดยขอ 2 ตลาดในภูมิภาคเดียว (ต้นทุนโดยทั่วไป 2 credits ต่อลีกที่มีข้อมูล) ตรวจตัวเลขคงเหลือจาก response header
- **ข้อมูลบน Streamlit Cloud:** SQLite ในพื้นที่แอปอาจหายหลังรีสตาร์ต/redeploy ให้ดาวน์โหลดไฟล์ .db เก็บไว้นอกแอปเป็นประจำ และใช้ส่วนกู้คืนเมื่อจำเป็น; ปุ่มสำรองไม่ได้ทำให้พื้นที่ Cloud ถาวรเอง
- **การจัดอันดับ:** เรียง EV จากมากไปน้อยโดยตรง ไม่ใช้คะแนนผสม EV กับโอกาสชนะเต็ม
- **ผล Champions League:** ลองตรวจด้วย event ID จาก The Odds API Scores ก่อน; ถ้าข้อมูลหมดช่วง/ไม่มีผล ระบบจะข้าม ไม่เดาคะแนน
- **บิลสเต็ป:** ใช้ราคาเจ้ามือเดียวกันในการคำนวณค่าน้ำรวม เพื่อลดปัญหาราคาในบิลเดียวกันไม่สามารถเล่นกับเจ้ามือเดียวได้
- **ความเสี่ยง:** EV เป็นค่าประมาณตามแบบจำลอง ไม่ใช่คำรับประกันผลกำไร
""")

st.divider()
st.caption("Value Bet Pro xG Edition | xG coverage varies by league; no model is guaranteed profitable")
