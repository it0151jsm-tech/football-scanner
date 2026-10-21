import os
import re
import math
import json
import codecs
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
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
st.set_page_config(page_title="Value Bet Pro", page_icon="⚽", layout="wide")
st.title("⚽ Value Bet Pro — xG Edition")
st.caption("ระบบ C: อ่านหลักฐาน A และ Poisson/EV ของ B แยกกัน | ฟอร์ม + xG จริง | รองรับการ์ดมือถือ")

API_BASE = "https://api.the-odds-api.com/v4"
TH_TZ = timezone(timedelta(hours=7))
UTC = timezone.utc
TIMEOUT = 25
HISTORY_TIMEOUT = (3, 8)
def runtime_setting(name, default):
    # Read Cloud secrets before deriving paths; do not assume secrets were loaded earlier.
    try:
        return os.environ.get(name) or st.secrets.get(name, default)
    except Exception:
        return os.environ.get(name, default)


DB_FILE = Path(runtime_setting("VALUE_BET_DB", "data/value_bet_pro_v4.db"))
DB_FILE.parent.mkdir(parents=True, exist_ok=True)

# CSV codes are only set where a matching Football-Data competition code is known.
# Do not reuse a code across unrelated leagues.
LEAGUES = {
    "soccer_epl": ("พรีเมียร์ลีก อังกฤษ", "E0"),
    "soccer_efl_champ": ("แชมเปียนชิพ อังกฤษ", "E1"),
    "soccer_england_league1": ("ลีกวัน อังกฤษ", "E2"),
    "soccer_england_league2": ("ลีกทู อังกฤษ", "E3"),
    "soccer_spain_la_liga": ("ลาลีกา สเปน", "SP1"),
    "soccer_italy_serie_a": ("เซเรียอา อิตาลี", "I1"),
    "soccer_italy_serie_b": ("เซเรียบี อิตาลี", "I2"),
    "soccer_germany_bundesliga": ("บุนเดสลีกา เยอรมนี", "D1"),
    "soccer_germany_bundesliga2": ("บุนเดสลีกา 2 เยอรมนี", "D2"),
    "soccer_germany_liga3": ("ลีก 3 เยอรมนี", None),
    "soccer_france_ligue_one": ("ลีกเอิง ฝรั่งเศส", "F1"),
    "soccer_netherlands_eredivisie": ("เอเรดิวิซี เนเธอร์แลนด์", "N1"),
    "soccer_portugal_primeira_liga": ("โปรตุเกส พรีเมราลีกา", "P1"),
    "soccer_norway_eliteserien": ("นอร์เวย์ เอลิเตเซเรียน", None),
    "soccer_uefa_champs_league": ("ยูฟ่า แชมเปียนส์ลีก", None),
    "soccer_uefa_europa_league": ("ยูฟ่า ยูโรปาลีก", None),
    "soccer_uefa_europa_conference_league": ("ยูฟ่า คอนเฟอเรนซ์ลีก", None),
    "soccer_saudi_arabia_pro_league": ("ซาอุดีอาระเบีย โปรลีก", None),
    "soccer_usa_mls": ("เมเจอร์ลีก ซอคเกอร์ (MLS)", None),
    "soccer_thailand_thai_league": ("ไทยลีก", None),
    "soccer_japan_j_league": ("เจลีก ญี่ปุ่น", None),
    "soccer_korea_kleague1": ("เคลีก เกาหลีใต้", None),
    "soccer_brazil_campeonato": ("บราซิล ซีรีเอ", None),
    "soccer_brazil_serie_b": ("บราซิล ซีรีบี", None),
    # Added leagues: API keys confirmed in The Odds API's published sport-key list.
    "soccer_turkey_super_league": ("ตุรกี ซูเปอร์ลีก", "T1"),
    "soccer_belgium_first_div": ("เบลเยียม โปรลีก", "B1"),
    "soccer_denmark_superliga": ("เดนมาร์ก ซูเปอร์ลีกา", "DNK"),
    "soccer_sweden_allsvenskan": ("สวีเดน ออลสเวนส์คาน", "SWE"),
    "soccer_switzerland_superleague": ("สวิตเซอร์แลนด์ ซูเปอร์ลีก", "SWZ"),
    "soccer_poland_ekstraklasa": ("โปแลนด์ เอ็กสตราคลาซา", "POL"),
    "soccer_mexico_ligamx": ("เม็กซิโก ลีกา เอ็มเอ็กซ์", "MEX"),
    "soccer_china_superleague": ("จีน ซูเปอร์ลีก", "CHN"),
    "soccer_finland_veikkausliiga": ("ฟินแลนด์ เวกเคาส์ลีกา", "FIN"),
    "soccer_australia_aleague": ("ออสเตรเลีย เอ-ลีก", "AUS"),
    "soccer_spl": ("สกอตแลนด์ พรีเมียร์ชิป", "SC0"),
    "soccer_austria_bundesliga": ("ออสเตรีย บุนเดสลีกา", "AUT"),
    "soccer_greece_super_league": ("กรีซ ซูเปอร์ลีก", "G1"),
    "soccer_france_ligue_two": ("ลีกเดอซ์ ฝรั่งเศส", "F2"),
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
UNDERSTAT_SEASONS = (2026, 2025, 2024, 2023, 2022)
# Allow an off-season, but do not price fixtures from abandoned/stale feeds.
MAX_XG_AGE_DAYS = 120

EUROPEAN_CUPS = {
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
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
            event_id TEXT, kickoff_utc TEXT, kickoff_th TEXT, league TEXT,
            home_team TEXT, away_team TEXT, match TEXT, market TEXT, side TEXT,
            point REAL, odds REAL, selection TEXT, bookmaker TEXT, scanned_at TEXT,
            lambda_home REAL, lambda_away REAL, history_matches INTEGER,
            xg_home_avg REAL, xga_home_avg REAL, xg_away_avg REAL, xga_away_avg REAL,
            ev_pct REAL, p_win REAL, p_partial REAL, p_loss REAL, score REAL,
            status TEXT DEFAULT 'รอตรวจผล', final_score TEXT DEFAULT '', settlement TEXT DEFAULT '',
            model_status TEXT DEFAULT 'unknown', profit_units REAL, outcome_binary INTEGER,
            home_context_xg REAL, home_context_xga REAL, away_context_xg REAL, away_context_xga REAL,
            home_context_matches INTEGER, away_context_matches INTEGER, context_status TEXT,
            form_rate REAL, form_sample INTEGER, form_signal TEXT, form_summary TEXT, form_method TEXT,
            h2h_summary TEXT, h2h_detail TEXT, h2h_sample INTEGER,
            form_rate_10 REAL, form_rate_season REAL, confidence_score REAL, recommendation TEXT,
            analysis_reason TEXT, odds_in_range INTEGER DEFAULT 1, home_form_summary TEXT, away_form_summary TEXT
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
        con.execute("""CREATE TABLE IF NOT EXISTS manual_ou_quotes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL, bookmaker TEXT NOT NULL,
            point REAL NOT NULL, odds_over REAL NOT NULL, odds_under REAL NOT NULL,
            created_at TEXT NOT NULL, source TEXT NOT NULL DEFAULT 'MANUAL', model_result_json TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS league_health (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sport_key TEXT NOT NULL, checked_at TEXT NOT NULL, payload_json TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS result_reviews (
            id INTEGER PRIMARY KEY AUTOINCREMENT, pick_id INTEGER NOT NULL,
            received_at TEXT NOT NULL, reason TEXT NOT NULL, evidence_json TEXT NOT NULL
        )""")
        con.execute("""CREATE TABLE IF NOT EXISTS api_receipts (
            sport_key TEXT PRIMARY KEY, received_at TEXT NOT NULL
        )""")
        # Upgrade an existing v3 database without deleting the user's data.
        existing = {r[1] for r in con.execute("PRAGMA table_info(picks)").fetchall()}
        migrations = {
            "model_status": "TEXT DEFAULT 'unknown'",
            "profit_units": "REAL",
            "outcome_binary": "INTEGER",
            "xg_home_avg": "REAL",
            "xga_home_avg": "REAL",
            "xg_away_avg": "REAL",
            "xga_away_avg": "REAL",
            "home_context_xg": "REAL",
            "home_context_xga": "REAL",
            "away_context_xg": "REAL",
            "away_context_xga": "REAL",
            "home_context_matches": "INTEGER",
            "away_context_matches": "INTEGER",
            "context_status": "TEXT",
            "form_rate": "REAL",
            "form_sample": "INTEGER",
            "form_signal": "TEXT",
            "form_summary": "TEXT",
            "form_method": "TEXT",
            "h2h_summary": "TEXT",
            "h2h_detail": "TEXT",
            "h2h_sample": "INTEGER",
            "form_rate_10": "REAL",
            "form_rate_season": "REAL",
            "confidence_score": "REAL",
            "recommendation": "TEXT",
            "analysis_reason": "TEXT",
            "odds_in_range": "INTEGER DEFAULT 1",
            "home_form_summary": "TEXT",
            "away_form_summary": "TEXT",
            "analysis_a_json": "TEXT",
            "analysis_b_json": "TEXT",
            "received_at": "TEXT",
            "source_updated_at": "TEXT",
            "bookmaker_key": "TEXT",
        }
        for name, decl in migrations.items():
            if name not in existing:
                con.execute(f"ALTER TABLE picks ADD COLUMN {name} {decl}")
        slip_columns = {r[1] for r in con.execute("PRAGMA table_info(slips)").fetchall()}
        slip_migrations = {
            "profit_units": "REAL",
            "final_summary": "TEXT DEFAULT ''",
        }
        for name, decl in slip_migrations.items():
            if name not in slip_columns:
                con.execute(f"ALTER TABLE slips ADD COLUMN {name} {decl}")
        for name in ('received_at', 'source_updated_at', 'bookmaker_key'):
            if name not in {r[1] for r in con.execute("PRAGMA table_info(odds_snapshots)")}:
                con.execute(f"ALTER TABLE odds_snapshots ADD COLUMN {name} TEXT")
        con.execute("""CREATE TRIGGER IF NOT EXISTS protect_settled_pick
            BEFORE UPDATE OF status,final_score,settlement,profit_units,outcome_binary ON picks
            WHEN OLD.status='ตรวจแล้ว' AND (NEW.status IS NOT OLD.status OR
                NEW.final_score IS NOT OLD.final_score OR NEW.settlement IS NOT OLD.settlement OR
                NEW.profit_units IS NOT OLD.profit_units OR NEW.outcome_binary IS NOT OLD.outcome_binary)
            BEGIN SELECT RAISE(ABORT,'settled pick is immutable'); END""")
        con.execute("""CREATE TRIGGER IF NOT EXISTS protect_settled_slip
            BEFORE UPDATE OF status,profit_units,final_summary,selections_json ON slips
            WHEN OLD.status!='รอตรวจผล' AND (NEW.status IS NOT OLD.status OR
                NEW.profit_units IS NOT OLD.profit_units OR NEW.final_summary IS NOT OLD.final_summary OR
                NEW.selections_json IS NOT OLD.selections_json)
            BEGIN SELECT RAISE(ABORT,'settled slip is immutable'); END""")
        con.execute("CREATE INDEX IF NOT EXISTS idx_picks_event ON picks(event_id)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_event ON odds_snapshots(event_id, captured_at)")
        con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_lookup ON odds_snapshots(event_id, market, side, bookmaker, captured_at)")

init_db()


def read_slip_selections(raw):
    """Read legacy JSON without repairing, rewriting, or guessing its contents."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return []
    try:
        selections = json.loads(raw)
    except (ValueError, TypeError, UnicodeDecodeError):
        return None
    return selections if isinstance(selections, list) else None


def slip_pick_id(leg):
    """Accept only positive SQLite integer IDs, including legacy numeric strings."""
    if not isinstance(leg, dict):
        return None
    value = leg.get("pick_id")
    if type(value) is not int:
        if not isinstance(value, str) or not re.fullmatch(r"[0-9]+", value.strip()):
            return None
        try:
            value = int(value)
        except ValueError:
            return None
    return value if 0 < value <= 9223372036854775807 else None


def save_picks(rows, snapshot_rows=None):
    """Save the latest pending recommendation per event/market/side/bookmaker.
    Store all observed prices separately, regardless of the user's odds filter.
    """
    now_text = datetime.now(TH_TZ).isoformat(timespec="seconds")
    pick_columns = [
        "event_id", "kickoff_utc", "kickoff_th", "league", "home_team", "away_team", "match",
        "market", "side", "point", "odds", "selection", "bookmaker", "scanned_at", "lambda_home",
        "lambda_away", "history_matches", "xg_home_avg", "xga_home_avg", "xg_away_avg", "xga_away_avg",
        "ev_pct", "p_win", "p_partial", "p_loss", "score", "model_status", "home_context_xg",
        "home_context_xga", "away_context_xg", "away_context_xga", "home_context_matches",
        "away_context_matches", "context_status", "form_rate", "form_sample", "form_signal",
        "form_summary", "form_method", "h2h_summary", "h2h_detail", "h2h_sample", "form_rate_10", "form_rate_season",
        "confidence_score", "recommendation", "analysis_reason", "odds_in_range",
        "home_form_summary", "away_form_summary", "analysis_a_json", "analysis_b_json",
        "received_at", "source_updated_at", "bookmaker_key"
    ]
    saved = 0
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        # Picks referenced by a saved slip are immutable, including legacy slips.
        locked_ids = set()
        uncertain_references = False
        for saved_slip in con.execute("SELECT selections_json FROM slips"):
            selections = read_slip_selections(saved_slip["selections_json"])
            if selections is None:
                uncertain_references = True
                continue
            for leg in selections:
                pick_id = slip_pick_id(leg)
                if pick_id is None:
                    uncertain_references = True
                else:
                    locked_ids.add(pick_id)
        for r in rows or []:
            values = {col: r.get(col) for col in pick_columns}
            values["scanned_at"] = now_text
            values["model_status"] = values.get("model_status") or "ข้อมูลย้อนหลังไม่พอ"
            values["form_signal"] = values.get("form_signal") or "ข้อมูลย้อนหลังไม่พอ"
            # For recommendations (not actual placed bets), update the latest pending quote
            # instead of counting every rescan as a separate bet. Keep settled records unchanged.
            existing = con.execute("""SELECT id FROM picks
                WHERE event_id=? AND market=? AND side=? AND bookmaker=? AND point IS ? AND status!='ตรวจแล้ว'
                ORDER BY id DESC LIMIT 1""",
                (values["event_id"], values["market"], values["side"], values["bookmaker"], values["point"])).fetchone()
            # If damaged JSON hides references, append quotes rather than risk
            # overwriting an existing pick. The damaged bill itself stays intact.
            if existing and not uncertain_references and existing["id"] not in locked_ids:
                update_cols = [c for c in pick_columns if c != "event_id"]
                sql = "UPDATE picks SET " + ",".join(f"{c}=?" for c in update_cols) + " WHERE id=?"
                con.execute(sql, [values[c] for c in update_cols] + [existing["id"]])
            else:
                cols_sql = ",".join(pick_columns)
                marks = ",".join("?" for _ in pick_columns)
                con.execute(f"INSERT INTO picks ({cols_sql}) VALUES ({marks})", [values[c] for c in pick_columns])
            saved += 1
        # Price history is deliberately independent of the odds range used to select bets.
        inserted_snapshots = 0
        recent_cutoff = (datetime.now(TH_TZ) - timedelta(minutes=10)).isoformat(timespec="seconds")
        for r in snapshot_rows or []:
            # Avoid presenting repeated clicks on the same cached API response as new observations.
            if r.get('received_at'):
                duplicate = con.execute("""SELECT 1 FROM odds_snapshots
                    WHERE event_id=? AND market=? AND side=? AND bookmaker=? AND point=? AND odds=?
                    AND received_at=? AND source_updated_at IS ? LIMIT 1""", (
                    r.get('event_id'),r.get('market'),r.get('side'),r.get('bookmaker'),r.get('point'),r.get('odds'),
                    r.get('received_at'),r.get('source_updated_at'))).fetchone()
            else:
                duplicate = con.execute("""SELECT 1 FROM odds_snapshots
                    WHERE event_id=? AND market=? AND side=? AND bookmaker=? AND point=? AND odds=?
                    AND julianday(captured_at)>=julianday(?) LIMIT 1""", (
                    r.get('event_id'),r.get('market'),r.get('side'),r.get('bookmaker'),r.get('point'),r.get('odds'),recent_cutoff)).fetchone()
            if duplicate:
                continue
            con.execute("""INSERT INTO odds_snapshots
                (event_id,match_name,market,side,point,odds,bookmaker,captured_at,received_at,source_updated_at,bookmaker_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (
                r.get("event_id"), r.get("match_name"), r.get("market"), r.get("side"),
                r.get("point"), r.get("odds"), r.get("bookmaker"), r.get("received_at") or now_text,
                r.get("received_at"), r.get("source_updated_at"), r.get("bookmaker_key")
            ))
            inserted_snapshots += 1
    return saved, inserted_snapshots


def has_result_source(league):
    return any(name == league and code for name, code in LEAGUES.values())


def save_slip_record(slip_df, combined_odds, estimated_probability, selected_league_names=None):
    """Recheck canonical SQLite quotes at click time, under the same write lock."""
    from readiness import analysis_scope
    from market_recommendations import recommended_pick_ids
    if slip_df.empty:
        raise ValueError("บิลต้องมีอย่างน้อยหนึ่งคู่")
    ids = [slip_pick_id({'pick_id': value}) for value in slip_df['id']]
    if None in ids or len(set(ids)) != len(ids):
        raise ValueError("รหัสรายการในบิลไม่ถูกต้อง")
    with db() as con:
        con.execute('BEGIN IMMEDIATE')
        canonical = pd.read_sql_query("SELECT * FROM picks ORDER BY id DESC", con)
        leagues = selected_league_names if selected_league_names is not None else list(slip_df['league'])
        scoped = analysis_scope(canonical, leagues, latest_receipts=latest_api_receipts(con))
        eligible = recommended_pick_ids(scoped.to_dict('records'), choose_recommendation)
        if not set(ids).issubset(eligible):
            raise ValueError("ยังไม่แนะนำ: ราคา/ข้อมูล/EV ไม่ผ่านเกณฑ์ หรืออยู่นอกลีกและช่วง 24 ชั่วโมง")
        selections = []
        events = set()
        actual_odds = 1.0
        for _, supplied in slip_df.iterrows():
            row = canonical[canonical['id'] == supplied['id']].iloc[0]
            if row['status'] != 'รอตรวจผล' or not has_result_source(row.get('league')) or row['event_id'] in events:
                raise ValueError("รายการตรวจผลแล้ว ซ้ำคู่ หรือไม่มีแหล่งผลรองรับ")
            for key in ('event_id','market','side','point','odds','bookmaker'):
                if row[key] != supplied[key]:
                    raise ValueError("ราคา/ตัวเลือกเปลี่ยนแล้ว กรุณาตรวจสอบบิลอีกครั้ง")
            events.add(row['event_id'])
            actual_odds *= float(row['odds'])
            selections.append({
                'pick_id': int(row['id']), 'league': str(row['league']), 'match': str(row['match']),
                'market': str(row['market']), 'selection': str(row['selection']), 'odds': float(row['odds']),
                'point': float(row['point']), 'side': str(row['side']), 'event_id': str(row['event_id']),
                'bookmaker': str(row['bookmaker']),
            })
        if not math.isfinite(float(combined_odds)) or not math.isclose(actual_odds, float(combined_odds), rel_tol=1e-9):
            raise ValueError("ราคาบิลรวมไม่ตรงรายการที่บันทึก")
        cur = con.execute(
            "INSERT INTO slips (created_at, slip_size, selections_json, combined_odds, estimated_probability, status) VALUES (?,?,?,?,?,?)",
            (datetime.now(TH_TZ).isoformat(timespec="seconds"), len(selections),
             json.dumps(selections, ensure_ascii=False), actual_odds, estimated_probability, "รอตรวจผล"))
        return int(cur.lastrowid)


def load_slips(limit=200):
    with db() as con:
        return pd.read_sql_query("SELECT * FROM slips ORDER BY id DESC LIMIT ?", con, params=(limit,))


def refresh_slip_settlements():
    """Settle saved slips only after every stored pick has a final result; repeated runs are idempotent."""
    updated = 0
    with db() as con:
        slips = con.execute("SELECT id, selections_json FROM slips WHERE status='รอตรวจผล'").fetchall()
        for slip in slips:
            try:
                selections = read_slip_selections(slip["selections_json"])
                if not selections or any(slip_pick_id(leg) is None for leg in selections):
                    continue
                factors = []
                labels = []
                complete = True
                for leg in selections:
                    pick = con.execute("SELECT * FROM picks WHERE id=?", (slip_pick_id(leg),)).fetchone()
                    if not pick or pick["status"] != "ตรวจแล้ว":
                        complete = False
                        break
                    # Recompute using the saved quote, never a mutable pick settlement.
                    quote = dict(pick)
                    quote.update(leg)
                    if "point" not in leg:
                        # Older bills saved the original line in their selection text.
                        line_match = re.search(r"\((?:Over |Under )?([+-]?\d+(?:\.\d+)?)\)$", leg.get("selection", ""))
                        if not line_match:
                            complete = False
                            break
                        quote["point"] = float(line_match.group(1))
                    hg, ag = map(int, str(pick["final_score"]).split("-"))
                    result = settle_pick(quote, hg, ag)
                    if result is None:
                        complete = False
                        break
                    label = result[0]
                    odds = float(leg["odds"])
                    if label == "ชนะเต็ม": factor = odds
                    elif label == "ชนะครึ่ง": factor = (odds + 1.0) / 2.0
                    elif label == "คืนทุน": factor = 1.0
                    elif label == "แพ้ครึ่ง": factor = 0.5
                    elif label == "แพ้เต็ม": factor = 0.0
                    else:
                        complete = False
                        break
                    factors.append(factor)
                    labels.append(label)
                if not complete:
                    continue
                return_multiple = math.prod(factors)
                profit = return_multiple - 1.0
                if all(x == "ชนะเต็ม" for x in labels):
                    status = "ชนะเต็มทุกคู่"
                elif return_multiple <= 1e-12:
                    status = "แพ้บิล"
                else:
                    status = "ตัดสินแล้ว (ผลผสม/คืนทุน)"
                summary = f"ผลตอบแทนรวม {return_multiple:.4f} หน่วย; กำไร/ขาดทุน {profit:+.4f} หน่วย"
                con.execute("UPDATE slips SET status=?, profit_units=?, final_summary=? WHERE id=?",
                            (status, round(profit, 4), summary, int(slip["id"])))
                updated += 1
            except (ValueError, TypeError, KeyError, json.JSONDecodeError):
                continue
    return updated


def load_picks(limit=5000):
    with db() as con:
        if limit is None:
            return pd.read_sql_query("SELECT * FROM picks ORDER BY id DESC", con)
        return pd.read_sql_query("SELECT * FROM picks ORDER BY id DESC LIMIT ?", con, params=(limit,))


def record_api_receipt(sport_key, received_at):
    from readiness import instant
    if instant(received_at) is None:
        return
    with db() as con:
        con.execute("""INSERT INTO api_receipts(sport_key,received_at) VALUES(?,?)
            ON CONFLICT(sport_key) DO UPDATE SET received_at=excluded.received_at
            WHERE julianday(excluded.received_at)>julianday(api_receipts.received_at)""",(sport_key,received_at))


def latest_api_receipts(con=None):
    if con is None:
        with db() as connection:
            return latest_api_receipts(connection)
    return {LEAGUES[r['sport_key']][0]:r['received_at'] for r in con.execute('SELECT * FROM api_receipts') if r['sport_key'] in LEAGUES}


def record_result_review(pick_id, reason, evidence):
    with db() as con:
        con.execute('INSERT INTO result_reviews(pick_id,received_at,reason,evidence_json) VALUES(?,?,?,?)',
                    (int(pick_id),datetime.now(UTC).isoformat(),reason,json.dumps(evidence,ensure_ascii=False,default=str)))


def manual_review_ids():
    with db() as con:
        return {int(r[0]) for r in con.execute('SELECT DISTINCT pick_id FROM result_reviews')}


def update_pick_result(pick_id, score, settlement, profit_units, outcome_binary=None):
    with db() as con:
        con.execute("""UPDATE picks SET final_score=?, settlement=?, status='ตรวจแล้ว',
            profit_units=?, outcome_binary=? WHERE id=? AND status!='ตรวจแล้ว'""",
            (score, settlement, profit_units, outcome_binary, int(pick_id)))

# -------------------- ODDS API --------------------
@st.cache_data(ttl=300, show_spinner=False)
def fetch_odds(sport_key, regions, api_key):
    from odds_budget import Budget, ScanStopped, OddsBatch
    from credential_redaction import protect
    protect(api_key)
    budget = Budget(bill_path=DB_FILE)
    reservation = budget.reserve(api_key, sport_key, regions)
    url = f"{API_BASE}/sports/{sport_key}/odds/"
    params = {"apiKey": api_key, "regions": regions, "markets": "spreads,totals", "oddsFormat": "decimal"}
    try:
        response = requests.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException:
        budget.complete(reservation, 'network_error', {})
        # Never interpolate request exceptions, URLs or provider bodies containing credentials.
        raise ScanStopped("หยุดสแกน: เชื่อมต่อ API ไม่สำเร็จ") from None
    budget.complete(reservation, response.status_code, response.headers)
    if response.status_code != 200:
        raise ScanStopped(f"หยุดสแกน: API ตอบ HTTP {response.status_code}")
    try:
        matches = response.json()
        if not isinstance(matches, list) or not all(isinstance(m, dict) for m in matches):
            raise ValueError()
    except (ValueError, TypeError):
        raise ScanStopped("หยุดสแกน: รูปแบบข้อมูล API ไม่ถูกต้อง") from None
    received_at = datetime.now(UTC).isoformat()
    for match in matches:
        match['_received_at'] = received_at
    return OddsBatch(matches, received_at), response.headers.get("x-requests-remaining", "ไม่ระบุ"), response.headers.get("x-requests-used", "ไม่ระบุ")

# -------------------- HISTORICAL RESULTS --------------------
@st.cache_data(ttl=900, show_spinner=False)
def fetch_history(csv_code):
    from history_data import load_history
    return load_history(csv_code, requests.get, timeout=HISTORY_TIMEOUT)


def get_h2h_history(score_history, home_api, away_api, kickoff, limit=5):
    """Display-only H2H from the selected league's Football-Data results.
    Never feeds H2H into xG, Poisson, EV, or recommendation ranking.
    Only completed matches strictly before the fixture date are considered.
    """
    unavailable = {
        "h2h_summary": "ไม่มีข้อมูล H2H จากแหล่งข้อมูลลีกนี้",
        "h2h_detail": "",
        "h2h_sample": 0,
    }
    if score_history is None or score_history.empty:
        return unavailable
    required = {"Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG"}
    if not required.issubset(score_history.columns):
        return unavailable
    try:
        candidates = set(score_history["HomeTeam"].dropna().astype(str)) | set(score_history["AwayTeam"].dropna().astype(str))
        mapped_home = map_team(home_api, list(candidates))
        mapped_away = map_team(away_api, list(candidates))
        if not mapped_home or not mapped_away or mapped_home == mapped_away:
            return unavailable
        cutoff = pd.Timestamp(kickoff.date())
        hist = score_history.copy()
        hist = hist[hist["Date_dt"] < cutoff]
        meetings = hist[
            ((hist["HomeTeam"] == mapped_home) & (hist["AwayTeam"] == mapped_away)) |
            ((hist["HomeTeam"] == mapped_away) & (hist["AwayTeam"] == mapped_home))
        ].copy().sort_values("Date_dt", ascending=False).head(limit)
        if meetings.empty:
            return {"h2h_summary": "ไม่พบ H2H ในข้อมูลย้อนหลัง", "h2h_detail": "", "h2h_sample": 0}
        home_wins = draws = away_wins = total_goals = 0
        details = []
        for _, row in meetings.iterrows():
            hg, ag = int(row["FTHG"]), int(row["FTAG"])
            historical_home = str(row["HomeTeam"])
            if historical_home == mapped_home:
                fixture_hg, fixture_ag = hg, ag
                venue = "เหย้า"
            else:
                fixture_hg, fixture_ag = ag, hg
                venue = "เยือน"
            total_goals += hg + ag
            if fixture_hg > fixture_ag:
                home_wins += 1
                result_text = "ทีมเหย้าของคู่นี้ชนะ"
            elif fixture_hg < fixture_ag:
                away_wins += 1
                result_text = "ทีมเยือนของคู่นี้ชนะ"
            else:
                draws += 1
                result_text = "เสมอ"
            date_text = pd.Timestamp(row["Date_dt"]).strftime("%d/%m/%Y")
            details.append(f"{date_text} | {row['HomeTeam']} {hg}-{ag} {row['AwayTeam']} | มุมมองทีมปัจจุบัน: {result_text} (ทีมเหย้าปัจจุบันเล่นเป็น {venue} ในเกมนั้น)")
        n = len(meetings)
        avg_goals = total_goals / n
        summary = f"{n} นัด: ทีมเหย้าชนะ {home_wins} | เสมอ {draws} | ทีมเยือนชนะ {away_wins} | ประตูรวมเฉลี่ย {avg_goals:.2f}/นัด"
        return {"h2h_summary": summary, "h2h_detail": "\n".join(details), "h2h_sample": n}
    except Exception:
        return unavailable


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
                season_frame = pd.DataFrame(rows)
                season_frame["source_league"] = league_code
                frames.append(season_frame)
        except Exception:
            # A blocked or changed upstream page means no model, not synthetic values.
            continue
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    out = out.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam"], keep="last")
    return out.sort_values("Date_dt").reset_index(drop=True)

@st.cache_data(ttl=21600, show_spinner=False)
def fetch_understat_all_history():
    """Collect only real Understat data for supported domestic leagues, for context display."""
    frames = []
    for sport_key in UNDERSTAT_LEAGUES:
        frame = fetch_understat_history(sport_key)
        if frame is not None and not frame.empty:
            frame = frame.copy()
            if "source_league" not in frame.columns:
                frame["source_league"] = UNDERSTAT_LEAGUES[sport_key]
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=["Date_dt", "HomeTeam", "AwayTeam", "source_league"]).sort_values("Date_dt").reset_index(drop=True)


def estimate_domestic_context(df, home_api, away_api, kickoff):
    """Recent domestic-league xG form for cup matches. Context only; never used to price cup EV."""
    empty = {
        "home_context_xg": None, "home_context_xga": None,
        "away_context_xg": None, "away_context_xga": None,
        "home_context_matches": 0, "away_context_matches": 0,
        "context_status": "ไม่มีสถิติลีกในประเทศที่จับคู่ทีมได้",
    }
    if df is None or df.empty:
        return empty
    # Football-Data/Understat dates may be date-only; exclude the entire kickoff day
    # so a later match on the same date cannot leak into a pre-match estimate.
    cutoff_day = pd.Timestamp(kickoff.astimezone(UTC).date())
    hist = df[df["Date_dt"].dt.normalize() < cutoff_day].copy()
    if hist.empty:
        return empty
    teams = sorted(set(hist["HomeTeam"].dropna()) | set(hist["AwayTeam"].dropna()))
    home = map_team(home_api, teams)
    away = map_team(away_api, teams)
    if not home or not away or home == away:
        return empty

    def team_sample(team):
        home_rows = hist[hist["HomeTeam"] == team].assign(_xgf=lambda x: x["xG_home"], _xga=lambda x: x["xG_away"])
        away_rows = hist[hist["AwayTeam"] == team].assign(_xgf=lambda x: x["xG_away"], _xga=lambda x: x["xG_home"])
        sample = pd.concat([home_rows, away_rows], ignore_index=True).sort_values("Date_dt").tail(5)
        sample = sample.dropna(subset=["_xgf", "_xga"])
        return sample

    hs, aw = team_sample(home), team_sample(away)
    result = dict(empty)
    result.update({
        "home_context_xg": round(float(hs["_xgf"].mean()), 2) if len(hs) >= 3 else None,
        "home_context_xga": round(float(hs["_xga"].mean()), 2) if len(hs) >= 3 else None,
        "away_context_xg": round(float(aw["_xgf"].mean()), 2) if len(aw) >= 3 else None,
        "away_context_xga": round(float(aw["_xga"].mean()), 2) if len(aw) >= 3 else None,
        "home_context_matches": int(len(hs)),
        "away_context_matches": int(len(aw)),
    })
    if len(hs) >= 3 or len(aw) >= 3:
        result["context_status"] = "สถิติ xG ลีกในประเทศ 5 นัดล่าสุดโดยประมาณ — ใช้ประกอบเท่านั้น ไม่ใช่สถิติถ้วย"
    return result


def norm_name(name):
    return re.sub(r"[^a-z0-9]", "", str(name or "").lower().replace("&", "and"))


def map_team(name, candidates):
    from history_data import match_team
    return match_team(name, candidates)[0]


def estimate_goals(df, home_api, away_api, kickoff):
    """Estimate expected goals from actual historical xG/xGA only; no goals-only fallback."""
    if df is None or df.empty:
        return None
    # Exclude the entire kickoff date because source match timestamps may be date-only
    # or may not reliably encode local kickoff times.
    cutoff_day = pd.Timestamp(kickoff.astimezone(UTC).date())
    hist = df[df["Date_dt"].dt.normalize() < cutoff_day].copy()
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
    # Both venue samples must be current; old seasons alone are insufficient.
    if any((cutoff_day - sample["Date_dt"].max().normalize()).days > MAX_XG_AGE_DAYS
           for sample in (h_home, a_away)):
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

# -------------------- FORM-BASED FALLBACK (NEVER EV) --------------------
def evaluate_historical_line(goals_for, goals_against, market, side, point):
    """Score a historical result against today's line as a rough fit indicator.
    This is NOT historical handicap performance because past prices/lines are unavailable.
    Returns +1 for profit, 0 for push, -1 for loss, including Asian quarter-line splits.
    """
    net = 0.0
    for line, weight in split_line(point):
        if market == "spreads":
            diff = goals_for - goals_against
            adjusted = diff + line
        else:
            total = goals_for + goals_against
            adjusted = total - line if side == "over" else line - total
        outcome = settle_leg(adjusted)
        if outcome == "win":
            net += weight
        elif outcome == "loss":
            net -= weight
    if net > 1e-9:
        return 1
    if net < -1e-9:
        return -1
    return 0


def estimate_form_signal(df, home_api, away_api, kickoff, market, side, point, window=5):
    """Fallback from historical scores only. Never returns EV or model win probability.
    Uses up to 5 previous home matches for home side and 5 previous away matches for away side.
    Excludes the kickoff date entirely to reduce date-only historical data leakage.
    """
    result = {
        "form_rate": None, "form_sample": 0,
        "form_signal": "ข้อมูลย้อนหลังไม่พอ",
        "form_summary": "ไม่มีผลย้อนหลังที่จับคู่ทีมได้เพียงพอ",
        "form_method": "Football-Data scores only; not EV",
    }
    from history_data import team_history
    home_games, home_diagnostic = team_history(df, home_api, kickoff, "home")
    away_games, away_diagnostic = team_history(df, away_api, kickoff, "away")
    result["diagnostics"] = {"home": home_diagnostic, "away": away_diagnostic}
    if not home_diagnostic.get("matched_name") or not away_diagnostic.get("matched_name"):
        result["form_summary"] = " | ".join(d["message"] for d in (home_diagnostic, away_diagnostic))
        return result
    if window is not None:
        home_games = home_games.tail(window)
        away_games = away_games.tail(window)
    if market == "spreads":
        if side == "home":
            samples = [(float(r.FTHG), float(r.FTAG)) for r in home_games.itertuples()]
        else:
            # Reverse score order so the selected away team is the team being assessed.
            samples = [(float(r.FTAG), float(r.FTHG)) for r in away_games.itertuples()]
    else:
        # Each historical match appears only once; these are venue-specific samples.
        unique_games = pd.concat([home_games, away_games]).drop_duplicates(
            subset=["Date_dt", "HomeTeam", "AwayTeam"])
        samples = [(float(r.FTHG), float(r.FTAG)) for r in unique_games.itertuples()]
    if not samples:
        result["form_summary"] = "ไม่มีเกมเหย้า/เยือนย้อนหลังสำหรับทีมนี้"
        return result
    outcomes = [evaluate_historical_line(hg, ag, market, side, float(point)) for hg, ag in samples]
    decisive = [x for x in outcomes if x != 0]
    n = len(samples)
    result["form_sample"] = n
    if not decisive:
        result["form_summary"] = f"ตัวอย่าง {n} นัด แต่ผลเทียบเส้นนี้เป็นคืนทุนทั้งหมด"
        return result
    rate = sum(1 for x in decisive if x > 0) / len(decisive) * 100.0
    result["form_rate"] = round(rate, 1)
    if n < 4 or len(decisive) < 3:
        signal = "ข้อมูลน้อย — ใช้ประกอบเท่านั้น"
    elif rate >= 60:
        signal = "ฟอร์มย้อนหลังสนับสนุน"
    elif rate <= 40:
        signal = "ฟอร์มย้อนหลังสวนทาง"
    else:
        signal = "ฟอร์มย้อนหลังกลาง ๆ"
    result["form_signal"] = signal
    result["form_summary"] = f"ผลย้อนหลัง {n} นัด; เทียบเส้นปัจจุบันแล้วได้ผลบวก {rate:.1f}% ของ {len(decisive)} นัดที่ไม่คืนทุน — ไม่ใช่สถิติการกินราคาในอดีต"
    return result


# -------------------- TEAM FORM / CONFIDENCE --------------------
def team_form_profile(score_history, team_api, kickoff, venue=None):
    """Validated historical score form with explicit source/name/date diagnostics."""
    from history_data import team_history
    games, diagnostic = team_history(score_history, team_api, kickoff, venue)
    empty = {"summary": diagnostic["message"], "last5_gf": None, "last5_ga": None,
             "last10_gf": None, "last10_ga": None, "season_gf": None, "season_ga": None,
             "sample5": 0, "sample10": 0, "season_n": 0, "diagnostics": diagnostic}
    if games.empty:
        return empty
    team = diagnostic["matched_name"]
    games = games.copy()
    games["GF"] = games.apply(lambda r: float(r["FTHG"]) if str(r["HomeTeam"]) == team else float(r["FTAG"]), axis=1)
    games["GA"] = games.apply(lambda r: float(r["FTAG"]) if str(r["HomeTeam"]) == team else float(r["FTHG"]), axis=1)
    g5, g10 = games.tail(5), games.tail(10)
    gf5, ga5 = float(g5["GF"].mean()), float(g5["GA"].mean())
    gf10, ga10 = float(g10["GF"].mean()), float(g10["GA"].mean())
    gfs, gas = float(games["GF"].mean()), float(games["GA"].mean())
    points = sum(3 if gf > ga else 1 if gf == ga else 0 for gf, ga in zip(g5["GF"], g5["GA"]))
    summary = (f"ย้อนหลัง {len(games)} นัด | 5 นัด: ยิง {gf5:.2f}/เสีย {ga5:.2f}, {points}/15 แต้ม | "
               f"10 นัด: ยิง {gf10:.2f}/เสีย {ga10:.2f} | รวม: ยิง {gfs:.2f}/เสีย {gas:.2f} ต่อเกม")
    return {"summary": summary, "last5_gf": round(gf5, 3), "last5_ga": round(ga5, 3),
            "last10_gf": round(gf10, 3), "last10_ga": round(ga10, 3),
            "season_gf": round(gfs, 3), "season_ga": round(gas, 3),
            "sample5": len(g5), "sample10": len(g10), "season_n": len(games), "diagnostics": diagnostic}


def blended_form_rate(rate5, rate10, rate_season, n5=0, n10=0, nseason=0):
    """Weighted historical line-fit indicator; explicitly not a calibrated probability."""
    pairs = []
    if rate5 is not None and n5 >= 3:
        pairs.append((float(rate5), 0.50))
    if rate10 is not None and n10 >= 5:
        pairs.append((float(rate10), 0.30))
    if rate_season is not None and nseason >= 8:
        pairs.append((float(rate_season), 0.20))
    if not pairs:
        available = [(float(v), w) for v, w in [(rate5, .50), (rate10, .30), (rate_season, .20)] if v is not None]
        if not available:
            return None
        return sum(v*w for v,w in available) / sum(w for _,w in available)
    return sum(v*w for v,w in pairs) / sum(w for _,w in pairs)


def choose_recommendation(score, ev_pct, odds_in_range, has_data=True):
    if score is None or not has_data:
        return "ข้อมูลไม่พอ — ยังไม่แนะนำ"
    if ev_pct is None or not math.isfinite(float(ev_pct)):
        result = "ฟอร์มสนับสนุน — ยังประเมินความคุ้มค่าราคาไม่ได้" if score >= 55 else "หลักฐานฟอร์มยังไม่พอ — ยังประเมินความคุ้มค่าราคาไม่ได้"
    elif score >= 65 and ev_pct > 0:
        result = "ตัวเลือกเด่น — พิจารณา"
    elif score >= 55:
        result = "พิจารณาอย่างระวัง"
    else:
        result = "ผ่าน — หลักฐานยังไม่พอ"
    if ev_pct is not None and ev_pct < 0:
        result += " | EV ติดลบ: ราคาอาจไม่คุ้ม"
    if not odds_in_range:
        result += " | ค่าน้ำนอกช่วงที่ตั้งไว้"
    return result


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
        "score": round((p_full_win * 100.0) + (ev * 100.0), 2),
    }


def selection_spread(team, side, line):
    # The API point is signed for each outcome; preserve it for settlement math.
    if abs(line) < 1e-9:
        return f"วาง {team} (0)"
    if line < 0:
        return f"ต่อ {team} (-{abs(line):g})"
    return f"รอง {team} (+{abs(line):g})"

def opponent_quality(df, team_api, kickoff, venue):
    """Opponent PPG before each sampled game; descriptive, not a win probability."""
    empty = {"mean_ppg": None, "sample": 0, "status": "ข้อมูลคุณภาพคู่แข่งไม่พอ"}
    if df is None or df.empty:
        return empty
    cutoff = pd.Timestamp(kickoff.astimezone(UTC).date())
    hist = df[df["Date_dt"].dt.normalize() < cutoff].sort_values("Date_dt")
    names = sorted(set(hist["HomeTeam"]) | set(hist["AwayTeam"]))
    team = map_team(team_api, names)
    if not team:
        return empty
    column = "HomeTeam" if venue == "home" else "AwayTeam"
    other = "AwayTeam" if venue == "home" else "HomeTeam"
    games = hist[hist[column] == team].tail(10)
    strengths = []
    for _, game in games.iterrows():
        opponent = game[other]
        prior = hist[(hist["Date_dt"].dt.normalize() < game["Date_dt"].normalize()) &
                     ((hist["HomeTeam"] == opponent) | (hist["AwayTeam"] == opponent))].tail(10)
        if len(prior) < 3:
            continue
        points = []
        for _, result in prior.iterrows():
            gf, ga = (result["FTHG"], result["FTAG"]) if result["HomeTeam"] == opponent else (result["FTAG"], result["FTHG"])
            points.append(3 if gf > ga else 1 if gf == ga else 0)
        strengths.append(sum(points) / len(points))
    if not strengths:
        return empty
    return {"mean_ppg": round(sum(strengths) / len(strengths), 3), "sample": len(strengths),
            "status": "PPG คู่แข่งก่อนเกม จากไม่เกิน 10 นัด; ข้อมูลประกอบ ไม่ใช่การจัดอันดับที่ปรับเทียบแล้ว"}


def actual_xg_evidence(df, home_api, away_api, kickoff):
    """System A reads observed xG directly, independently of Poisson lambdas."""
    result = {}
    if df is None or df.empty or not {"xG_home", "xG_away"}.issubset(df.columns):
        return {"status": "ไม่มี xG จริงที่รองรับ", "home": None, "away": None}
    cutoff = pd.Timestamp(kickoff.astimezone(UTC).date())
    hist = df[df["Date_dt"].dt.normalize() < cutoff].sort_values("Date_dt")
    names = sorted(set(hist["HomeTeam"]) | set(hist["AwayTeam"]))
    for side, api, venue, gf, ga in [("home", home_api, "HomeTeam", "xG_home", "xG_away"),
                                    ("away", away_api, "AwayTeam", "xG_away", "xG_home")]:
        team = map_team(api, names)
        sample = hist[hist[venue] == team].tail(10).dropna(subset=[gf, ga])
        if sample.empty or (cutoff - sample["Date_dt"].max().normalize()).days > MAX_XG_AGE_DAYS:
            result[side] = None
        else:
            result[side] = {"xg": round(float(sample[gf].mean()), 3), "xga": round(float(sample[ga].mean()), 3), "sample": len(sample), "last_date": str(sample["Date_dt"].max().date())}
    result["status"] = "ค่า xG/xGA จริงตามสนาม; ไม่มีการสร้าง xG ทดแทน" if any(result.values()) else "xG เก่า/จับคู่ไม่ได้"
    return result


def first_observed_quotes(event_ids):
    """One local SQLite read per scan. Never requests bookmaker opening odds."""
    event_ids = list(dict.fromkeys(event_ids))
    if not event_ids:
        return {}
    # A bounded IN clause also works on SQLite builds with a 999-variable limit.
    out = {}
    with db() as con:
        for start in range(0, len(event_ids), 500):
            batch = event_ids[start:start + 500]
            marks = ",".join("?" for _ in batch)
            rows = con.execute(f"""SELECT * FROM odds_snapshots WHERE id IN (
                SELECT MIN(id) FROM odds_snapshots WHERE event_id IN ({marks})
                GROUP BY event_id,market,side,bookmaker)""", batch).fetchall()
            for row in rows:
                out[(row["event_id"], row["market"], row["side"], row["bookmaker"])] = dict(row)
    return out


def price_evidence(first, point, odds, observed_at):
    if first is None:
        first = {"point": point, "odds": odds, "captured_at": observed_at}
    same_line = abs(float(first["point"]) - point) < 1e-9
    return {"label": "First Observed Price", "first_odds": float(first["odds"]),
            "first_point": float(first["point"]), "first_at": first["captured_at"],
            "current_odds": odds, "current_point": point, "current_at": observed_at,
            "odds_delta": round(odds - float(first["odds"]), 3) if same_line else None,
            "status": "เส้นเดิม: เทียบค่าน้ำได้" if same_line else "เส้นเปลี่ยน: ห้ามเทียบค่าน้ำตรง ๆ",
            "opening_odds_available": False}


def system_a_analysis(form5, form10, form_all, home_profile, away_profile, opponents, xg, price):
    """Evidence ranking only. Receives no Poisson probability or EV."""
    score = blended_form_rate(form5.get("form_rate"), form10.get("form_rate"), form_all.get("form_rate"),
                              form5.get("form_sample", 0), form10.get("form_sample", 0), form_all.get("form_sample", 0))
    if score is not None:
        if form5.get("form_rate") is not None and form10.get("form_rate") is not None:
            score -= min(12, abs(form5["form_rate"] - form10["form_rate"]) * .18)
        if max(form5.get("form_sample", 0), form10.get("form_sample", 0)) < 5:
            score = min(52, score)
        score = round(max(0, min(100, score)), 1)
    return {"score": score, "result": "ข้อมูลหลักฐานไม่พอ" if score is None else
            ("หลักฐานฟอร์มสนับสนุน" if score >= 55 else "หลักฐานฟอร์มยังไม่สนับสนุน"),
            "form5": form5, "form10": form10, "form_all": form_all,
            "home_form": home_profile, "away_form": away_profile,
            "opponents": opponents, "xg": xg, "price": price,
            "method": "คะแนนจากฟอร์มเทียบเส้น 5/10/ทั้งหมดและความสอดคล้อง; ประตูได้เสีย คุณภาพคู่แข่ง xG และราคาเป็นหลักฐานประกอบ ไม่บวกคะแนนสมมติ",
            "status": "คะแนนหลักฐาน ไม่ใช่ความน่าจะเป็น และไม่ยืนยันความคุ้มค่าราคา"}


def system_b_analysis(metrics, matrix, evaluation, lines, ou_line):
    """Model probabilities are calculated solely from the existing xG Poisson model."""
    if metrics is None or matrix is None or evaluation is None:
        return {"available": False, "status": "ไม่มี Poisson/EV: xG ไม่รองรับ เก่า ไม่พอ หรือราคาใช้ไม่ได้", "calibrated": False}
    hda = {"home": 0., "draw": 0., "away": 0.}
    over = under = 0.
    for h, a, probability in matrix:
        hda["home" if h > a else "away" if h < a else "draw"] += probability * 100
        if all(h + a > line for line, _ in lines):
            over += probability * 100
        if all(h + a < line for line, _ in lines):
            under += probability * 100
    return {"available": True, "expected_goals": [metrics["home_lambda"], metrics["away_lambda"]],
            "hda": {k: round(v, 2) for k, v in hda.items()}, "selection": evaluation,
            "ou_line": ou_line, "ou": {"over_full": round(over, 2), "under_full": round(under, 2),
                   "partial_or_push": round(max(0, 100 - over - under), 2)},
            "calibrated": False, "status": "Poisson จาก xG จริง; ยังไม่ผ่าน Calibration; สมมติประตูสองทีมเป็นอิสระ"}


def saved_analysis(raw):
    if not isinstance(raw, str):
        return {}
    try:
        value = json.loads(raw)
        return value if isinstance(value, dict) else {}
    except (ValueError, TypeError):
        return {}


def display_number(value, digits=1, suffix=""):
    if value is None or not isinstance(value, (int, float)) or not math.isfinite(value):
        return "ไม่มีข้อมูล"
    return f"{value:.{digits}f}{suffix}"


def mobile_card_lines(row):
    """Plain text rows wrap on narrow screens; no horizontal data grid is required."""
    a, b = saved_analysis(row.get("analysis_a_json")), saved_analysis(row.get("analysis_b_json"))
    text = [str(row.get("match") or "ไม่ทราบคู่"),
            f"{row.get('league', '')} | {row.get('kickoff_th', '')} (ไทย)",
            f"{row.get('selection', '')} @ {display_number(row.get('odds'), 2)} | {row.get('bookmaker', '')}"]
    if not a and not b:
        return text + ["ข้อมูลเดิม/ยังไม่มีราคา: สแกนเมื่อพร้อมเพื่อสร้างผล A/B ไม่สร้างเปอร์เซ็นต์ทดแทน", str(row.get("model_status") or "ไม่มีข้อมูล")]
    text += [f"ระบบ A: {a.get('result', 'ข้อมูลไม่พอ')} | คะแนนหลักฐาน {display_number(a.get('score'))}/100",
             str(a.get("status", ""))]
    for side, label in [("home", "เหย้า"), ("away", "เยือน")]:
        text.append(f"ฟอร์ม{label}: {a.get(side + '_form', {}).get('summary', 'ไม่มีข้อมูล')}")
        opponent = a.get("opponents", {}).get(side, {})
        text.append(f"คู่แข่งที่{label}พบ: PPG {display_number(opponent.get('mean_ppg'), 2)} | ตัวอย่าง {opponent.get('sample', 0)} นัด")
        xg = a.get("xg", {}).get(side) or {}
        text.append(f"xG/xGA จริง{label}: {display_number(xg.get('xg'), 2)} / {display_number(xg.get('xga'), 2)} | {xg.get('sample', 0)} นัด")
    text.append(f"ฟอร์มเทียบเส้น (อัตราในอดีต ไม่ใช่โอกาสชนะ): 5 นัด {display_number(a.get('form5', {}).get('form_rate'), suffix='%')} | 10 นัด {display_number(a.get('form10', {}).get('form_rate'), suffix='%')}")
    price = a.get("price") or {}
    if price:
        text += [f"First Observed Price: {display_number(price.get('first_odds'), 2)} เส้น {display_number(price.get('first_point'), 2)} ({price.get('first_at', '')})",
                 f"ราคาปัจจุบัน ณ เวลาสแกน: {display_number(price.get('current_odds'), 2)} เส้น {display_number(price.get('current_point'), 2)} ({price.get('current_at', '')})",
                 f"ราคาไหล: {price.get('status', '')} | เปลี่ยนค่าน้ำ {display_number(price.get('odds_delta'), 3)} — ไม่มีข้อมูลราคาเปิดจริง"]
    text.append(f"ระบบ B: {b.get('status', 'ไม่มีข้อมูล')}")
    if b.get("available"):
        goals, hda, ou, selection = b["expected_goals"], b["hda"], b["ou"], b["selection"]
        text += [f"ประตูคาดการณ์ (λ ไม่ใช่สกอร์ฟันธง): เหย้า {goals[0]:.2f} / เยือน {goals[1]:.2f}",
                 f"โอกาสจากโมเดล: เหย้าชนะ {hda['home']:.2f}% | เสมอ {hda['draw']:.2f}% | เยือนชนะ {hda['away']:.2f}%",
                 f"ตัวเลือกนี้: ชนะเต็ม {selection['p_win']:.2f}% | ผลครึ่ง/คืนทุน {selection['p_partial']:.2f}% | แพ้เต็ม {selection['p_loss']:.2f}% | EV {selection['ev_pct']:+.2f}%"]
        ou_note = "เส้นตลาดนี้" if row.get("market") == "Over/Under" else "เส้นอ้างอิงโมเดล ไม่ใช่ราคาที่เสนอ"
        text.append(f"สูง/ต่ำ {b['ou_line']} ({ou_note}): สูงชนะเต็ม {ou['over_full']:.2f}% | ต่ำชนะเต็ม {ou['under_full']:.2f}% | ผลครึ่ง/คืนทุน {ou['partial_or_push']:.2f}%")
    else:
        text.append("ประตูคาดการณ์ / โอกาสชนะ–เสมอ–แพ้ / โอกาสสูง–ต่ำ / EV: ไม่มีข้อมูลโมเดลที่พร้อมใช้")
    ev = b.get("selection", {}).get("ev_pct") if b.get("available") else None
    text += ["ระบบ C (อ่าน A/B ร่วมกัน): " + choose_recommendation(a.get("score"), ev, bool(row.get("odds_in_range", 1))),
             "สถานะข้อมูล: " + str(row.get("model_status") or "ไม่มีข้อมูล"),
             "ยังไม่ผ่าน Calibration; First Observed Price ไม่ใช่ราคาเปิดจริง"]
    return text


def lookup_quote_snapshot(row):
    """Read a matching stored observation; never borrow another market/bookmaker."""
    with db() as con:
        snapshot = con.execute("""SELECT id,captured_at,received_at,source_updated_at FROM odds_snapshots
            WHERE event_id=? AND market=? AND side=? AND bookmaker=? AND point=? AND odds=?
              AND julianday(captured_at)<=julianday(?)
              AND (? IS NULL OR (received_at=? AND source_updated_at IS ?))
            ORDER BY julianday(captured_at) DESC,id DESC LIMIT 1""",
            (row.get("event_id"), row.get("market"), row.get("side"), row.get("bookmaker"),
             row.get("point"), row.get("odds"), row.get("received_at") or row.get("scanned_at") or "",
             row.get("received_at"),row.get("received_at"),row.get("source_updated_at"))).fetchone()
    return dict(snapshot) if snapshot else None


def record_data_health(sport_key, payload):
    with db() as con:
        con.execute("INSERT INTO league_health(sport_key,checked_at,payload_json) VALUES (?,?,?)",
                    (sport_key,payload["checked_at"],json.dumps(payload,ensure_ascii=False)))


def load_manual_quotes(event_id):
    with db() as con:
        return [dict(r) for r in con.execute("SELECT * FROM manual_ou_quotes WHERE event_id=? ORDER BY id DESC",(event_id,))]


def save_manual_quote(event_id, bookmaker, line, over, under):
    from manual_quotes import validate_quote, evaluate_manual
    quote = validate_quote(bookmaker,line,over,under)
    with db() as con:
        models = [dict(r) for r in con.execute("SELECT * FROM picks WHERE event_id=? ORDER BY scanned_at DESC,id DESC",(event_id,))]
        if not models:
            raise ValueError("ไม่พบคู่แข่งขันที่บันทึกไว้")
        identity = tuple(models[0].get(k) for k in ("home_team","away_team","league","kickoff_utc"))
        models = [r for r in models if tuple(r.get(k) for k in ("home_team","away_team","league","kickoff_utc")) == identity]
        now = datetime.now(UTC)
        result = {"available":False,"reason":"ไม่มีโมเดล B ที่ยืนยันความสดได้"}
        for model in models:
            candidate = evaluate_manual(model,line,over,under,now,score_matrix,evaluate_market_poisson)
            if candidate["available"]:
                result = candidate
                break
        cur = con.execute("""INSERT INTO manual_ou_quotes(event_id,bookmaker,point,odds_over,odds_under,created_at,source,model_result_json)
            VALUES (?,?,?,?,?,?,?,?)""",(event_id,quote["bookmaker"],quote["point"],quote["odds_over"],quote["odds_under"],
            now.isoformat(timespec="seconds"),"MANUAL",json.dumps(result,ensure_ascii=False,allow_nan=False)))
        return int(cur.lastrowid)


def render_data_health():
    from data_health import table_rows
    with st.expander("Data Health · ตรวจชื่อทีมแยกตามลีก"):
        with db() as con:
            audits = {r["sport_key"]:json.loads(r["payload_json"]) for r in con.execute(
                "SELECT * FROM league_health WHERE id IN (SELECT MAX(id) FROM league_health GROUP BY sport_key)")}
        report_path = Path(__file__).parent / "reports" / "league-source-audit.json"
        source = json.loads(report_path.read_text()) if report_path.exists() else {}
        st.caption("ตรวจเฉพาะทีม API ที่เคยสแกนและบันทึกไว้; 0 หมายถึงยังไม่มีชื่อให้ตรวจ ไม่ใช่ผ่านทั้งหมด · ไม่เรียก API เมื่อเปิดส่วนนี้")
        st.caption("ตรวจแหล่ง CSV/Understat เมื่อ: " + str(source.get("checked_at","ยังไม่มีรายงานสด")))
        if source.get("full_report_url"):
            st.link_button("รายงานชื่อทีม/แหล่งข้อมูลฉบับเต็มจาก CI",source["full_report_url"])
        st.dataframe(pd.DataFrame(table_rows(LEAGUES,UNDERSTAT_LEAGUES,audits,source)),hide_index=True,use_container_width=True)
        for context in source.get("context_only_sources",[]):
            st.write("แหล่งประกอบถ้วยเท่านั้น (ไม่ใช่ลีกที่เลือกสแกน)",context)
        for key,audit in audits.items():
            problems = [team for team in audit["teams"] if any(team[s]["status"] not in ("matched","unsupported") for s in ("csv","understat"))]
            if problems:
                st.write(audit["league"],problems)
        st.caption("ชื่อใหม่ในชุดข้อมูลฤดูกาลใหม่ต้องตรวจ identity เพิ่ม ไม่ถือว่าเลื่อนชั้นสำเร็จหรือจับคู่จากชื่อคล้าย")
        for league in source.get("leagues",[]):
            unresolved = [r for r in league.get("cross_source_results",[]) if r["status"]!="matched"]
            if unresolved or league.get("current_new_names"):
                st.write(league.get("league",league["key"]),{"ชื่อใหม่ใน CSV":league.get("current_new_names",[]),"ยังจับคู่ข้ามแหล่งไม่ได้":unresolved})


def render_mobile_cards(rows):
    from mobile_dashboard import DASHBOARD_CSS, fixture_dashboard_html, fixture_details_html
    from market_recommendations import RANKING, group_fixture_recommendations

    if rows.empty:
        return
    st.markdown(DASHBOARD_CSS, unsafe_allow_html=True)
    st.caption(RANKING)
    from ou_market import selection_counts
    fixtures = group_fixture_recommendations(rows.to_dict("records"), choose_recommendation)
    counts = selection_counts(fixtures)
    st.caption(f"สัดส่วนฝั่ง OU ที่แนะนำในหน้าปัจจุบัน: Over {counts['over']} | Under {counts['under']} | ประเมินแต่ไม่เลือกฝั่ง {counts['neutral']} | ไม่ยืนยัน Main {counts['missing_main']}; ไม่ใช่อัตราชนะ")
    if counts["over_pct"] is not None:
        st.caption(f"เฉพาะคำแนะนำที่เลือกฝั่ง: Over {counts['over_pct']:.1f}% / Under {counts['under_pct']:.1f}%")
    for fixture in fixtures:
        for entry in fixture["markets"].values():
            if entry is not None:
                entry["snapshot"] = lookup_quote_snapshot(entry["row"])
        if fixture.get("ou_main",{}).get("pair"):
            for side in ("over","under"):
                entry = fixture["ou_main"]["pair"][side]
                entry["snapshot"] = lookup_quote_snapshot(entry["row"])
        with st.container():
            st.markdown(fixture_dashboard_html(fixture), unsafe_allow_html=True)
            with st.expander("ดูรายละเอียดเพิ่มเติม"):
                st.markdown(fixture_details_html(fixture, RANKING), unsafe_allow_html=True)
                from mobile_dashboard import manual_quotes_html
                st.markdown(manual_quotes_html(load_manual_quotes(fixture["event_id"])),unsafe_allow_html=True)
                if not fixture["event_id"]:
                    st.caption("ไม่สามารถบันทึก Manual: ข้อมูลเก่าไม่มี event_id")
                    continue
                with st.form("manual_ou_" + str(fixture["event_id"])):
                    st.caption("Manual OU Quote · กรอกเอง แยกจาก API และบิลเดิม; ไม่ใช้ยืนยัน Main Line ของ API")
                    book = st.text_input("เว็บ/เจ้ามือ (Manual)")
                    line = st.number_input("เส้นสูง–ต่ำ (Manual)",min_value=0.0,max_value=20.0,value=None,step=.25)
                    over = st.number_input("ราคาสูง (Manual)",min_value=1.01,value=None,step=.01)
                    under = st.number_input("ราคาต่ำ (Manual)",min_value=1.01,value=None,step=.01)
                    if st.form_submit_button("บันทึก Manual OU Quote"):
                        try:
                            quote_id = save_manual_quote(fixture["event_id"],book,line,over,under)
                            st.success(f"บันทึก Manual #{quote_id} แล้ว — ไม่ใช่บิลเดิมพัน")
                            st.markdown(manual_quotes_html(load_manual_quotes(fixture["event_id"])[:1]),unsafe_allow_html=True)
                        except ValueError as exc:
                            st.error(str(exc))


def latest_saved_quotes(df):
    """OU retains each actual line; AH keeps its existing latest-side policy."""
    latest = df.sort_values("id",ascending=False)
    keys = ["event_id","market","side","bookmaker"]
    ou = latest[latest["market"] == "Over/Under"].drop_duplicates(subset=keys+["point"],keep="first")
    other = latest[latest["market"] != "Over/Under"].drop_duplicates(subset=keys,keep="first")
    return pd.concat([other,ou],ignore_index=True,sort=False)


def analysis_display_rows(df):
    """Do not relabel legacy blended scores as independent System A evidence."""
    view = df.copy()
    a = [saved_analysis(raw) for raw in view["analysis_a_json"]]
    b = [saved_analysis(raw) for raw in view["analysis_b_json"]]
    view["confidence_score"] = [item.get("score") for item in a]
    view["system_a_result"] = [item.get("result", "ข้อมูลเดิม: สแกนเพื่อสร้าง A/B") for item in a]
    view["system_b_status"] = [item.get("status", "ยังไม่มีผล B ที่บันทึกไว้") for item in b]
    from market_recommendations import assess
    entries = [assess(row, choose_recommendation) for row in view.to_dict("records")]
    view["recommendation"] = [entry['conclusion'] for entry in entries]
    view["ev_pct"] = [entry['ev'] for entry in entries]
    view["analysis_reason"] = [entry['reason'] for entry in entries]
    return view


# -------------------- SCAN --------------------
def scan_league(sport_key, league_name, csv_code, hours_limit, min_odds, max_odds, regions, api_key):
    matches, remaining, used = fetch_odds(sport_key, regions, api_key)
    receipt = getattr(matches, 'received_at', None) or (matches[0].get('_received_at') if matches else None)
    record_api_receipt(sport_key, receipt)
    history = fetch_understat_history(sport_key)
    score_history = fetch_history(csv_code)
    domestic_context = fetch_understat_all_history() if sport_key in EUROPEAN_CUPS else pd.DataFrame()
    now = datetime.now(UTC)
    from data_health import observe_league
    health = observe_league(
        sport_key, league_name, csv_code, sport_key in UNDERSTAT_LEAGUES, score_history, history, matches, now)
    record_data_health(sport_key, health)
    target = now + timedelta(hours=min(hours_limit, 24))
    rows, snapshot_rows = [], []
    first_prices = first_observed_quotes([m.get("id", "") for m in matches])
    for match in matches:
        try:
            kickoff = datetime.fromisoformat(match["commence_time"].replace("Z", "+00:00")).astimezone(UTC)
        except Exception:
            continue
        if not (now <= kickoff <= target):
            continue
        home, away = match.get("home_team", ""), match.get("away_team", "")
        kickoff_th = kickoff.astimezone(TH_TZ)
        h2h = get_h2h_history(score_history, home, away, kickoff, limit=5)
        metrics = estimate_goals(history, home, away, kickoff) if not history.empty else None
        context = estimate_domestic_context(domestic_context, home, away, kickoff) if sport_key in EUROPEAN_CUPS else {}
        if sport_key in EUROPEAN_CUPS:
            metrics = None
        matrix = score_matrix(metrics["home_lambda"], metrics["away_lambda"]) if metrics else None
        home_profile = team_form_profile(score_history, home, kickoff, venue="home")
        away_profile = team_form_profile(score_history, away, kickoff, venue="away")
        opponents = {"home": opponent_quality(score_history, home, kickoff, "home"),
                     "away": opponent_quality(score_history, away, kickoff, "away")}
        xg_evidence = actual_xg_evidence(domestic_context if sport_key in EUROPEAN_CUPS else history, home, away, kickoff)
        if sport_key in EUROPEAN_CUPS:
            xg_evidence["status"] += " — ลีกในประเทศเพื่อประกอบถ้วยเท่านั้น"
        rows_before_match = len(rows)
        for bm in match.get("bookmakers", []):
            book_name = bm.get("title", "Unknown")
            for market in bm.get("markets", []):
                m_key = market.get("key")
                if m_key not in ("spreads", "totals"):
                    continue
                provenance = {"received_at": match.get("_received_at"), "source_updated_at": market.get("last_update"),
                              "bookmaker_key": bm.get("key")}
                for outcome in market.get("outcomes", []):
                    try:
                        odds = float(outcome.get("price", 0))
                        line = float(outcome.get("point"))
                    except (TypeError, ValueError):
                        continue
                    if not math.isfinite(odds) or odds <= 1 or not math.isfinite(line) or abs(line*4-round(line*4)) > 1e-9:
                        continue
                    name = outcome.get("name")
                    if m_key == "spreads":
                        if name == home:
                            side, selection = "home", selection_spread(home, "home", line)
                        elif name == away:
                            side, selection = "away", selection_spread(away, "away", line)
                        else:
                            continue
                        market_label = "Asian Handicap"
                    else:
                        if name == "Over":
                            side, selection = "over", f"สูง (Over {line:g})"
                        elif name == "Under":
                            side, selection = "under", f"ต่ำ (Under {line:g})"
                        else:
                            continue
                        market_label = "Over/Under"
                    odds_in_range = bool(min_odds <= odds <= max_odds)
                    snapshot_rows.append({"event_id": match.get("id", ""), "match_name": f"{home} vs {away}",
                        "market": market_label, "side": side, "point": line, "odds": odds, "bookmaker": book_name, **provenance})
                    # Do not discard outcomes outside the odds filter: all available matches/quotes remain visible.
                    eval_res = evaluate_market_poisson(matrix, m_key, side, line, odds) if matrix else None
                    form5 = estimate_form_signal(score_history, home, away, kickoff, m_key, side, line, window=5)
                    form10 = estimate_form_signal(score_history, home, away, kickoff, m_key, side, line, window=10)
                    formseason = estimate_form_signal(score_history, home, away, kickoff, m_key, side, line, window=None)
                    price = price_evidence(first_prices.get((match.get("id", ""), market_label, side, book_name)),
                                           line, odds, provenance["received_at"] or now.astimezone(TH_TZ).isoformat(timespec="seconds"))
                    analysis_a = system_a_analysis(form5, form10, formseason, home_profile, away_profile,
                                                   opponents, xg_evidence, price)
                    ou_line = line if m_key == "totals" else 2.5
                    analysis_b = system_b_analysis(metrics, matrix, eval_res, split_line(ou_line), ou_line)
                    confidence = analysis_a["score"]
                    confidence_label, source_label = analysis_a["result"], "ระบบ A: หลักฐานเท่านั้น"
                    ev_pct = eval_res.get("ev_pct") if eval_res else None
                    recommendation = choose_recommendation(confidence, ev_pct, odds_in_range, confidence is not None)
                    reason_parts = [f"รับข้อมูล UTC: {provenance['received_at'] or 'ไม่ทราบ'} · เจ้ามืออัปเดตตลาด: {provenance['source_updated_at'] or 'ไม่ทราบ'}"]
                    if metrics:
                        reason_parts.append(f"xG คาดการณ์ {metrics['home_lambda']:.2f}-{metrics['away_lambda']:.2f}")
                    reason_parts.append(f"ผลย้อนหลังเทียบเส้น: 5 นัด {form5.get('form_rate') if form5.get('form_rate') is not None else 'n/a'}%, 10 นัด {form10.get('form_rate') if form10.get('form_rate') is not None else 'n/a'}%, ช่วงข้อมูลทั้งหมด {formseason.get('form_rate') if formseason.get('form_rate') is not None else 'n/a'}%")
                    if ev_pct is not None:
                        reason_parts.append(f"EV {ev_pct:+.2f}%")
                    else:
                        reason_parts.append("ยังคำนวณ EV ไม่ได้ เพราะไม่มี xG ที่รองรับเพียงพอ")
                    if not odds_in_range:
                        reason_parts.append(f"ค่าน้ำ {odds:.2f} อยู่นอกช่วง {min_odds:.2f}-{max_odds:.2f}")
                    rows.append({
                        "event_id": match.get("id", ""), "kickoff_utc": kickoff.isoformat(),
                        "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"),
                        "league": league_name, "home_team": home, "away_team": away,
                        "match": f"{home} vs {away}", "market": market_label, "side": side,
                        "point": line, "selection": selection, "odds": odds, "bookmaker": book_name, **provenance,
                        "lambda_home": metrics["home_lambda"] if metrics else None,
                        "lambda_away": metrics["away_lambda"] if metrics else None,
                        "history_matches": metrics["history_matches"] if metrics else 0,
                        "xg_home_avg": metrics.get("xg_home_avg") if metrics else None,
                        "xga_home_avg": metrics.get("xga_home_avg") if metrics else None,
                        "xg_away_avg": metrics.get("xg_away_avg") if metrics else None,
                        "xga_away_avg": metrics.get("xga_away_avg") if metrics else None,
                        "home_context_xg": context.get("home_context_xg"), "home_context_xga": context.get("home_context_xga"),
                        "away_context_xg": context.get("away_context_xg"), "away_context_xga": context.get("away_context_xga"),
                        "home_context_matches": context.get("home_context_matches", 0), "away_context_matches": context.get("away_context_matches", 0),
                        "context_status": context.get("context_status", ""),
                        "model_status": ("ถ้วยยุโรป: xG ลีกในประเทศเป็นข้อมูลประกอบเท่านั้น — ไม่มี EV เฉพาะถ้วย" if sport_key in EUROPEAN_CUPS else
                            ("Poisson จาก xG/xGA จริง (Understat)" if metrics else
                            ("ลีกนี้ไม่มี xG จากแหล่งที่ตั้งไว้ — ใช้ฟอร์มอย่างระมัดระวัง" if sport_key not in UNDERSTAT_LEAGUES else "xG ย้อนหลังไม่พอ/แหล่งข้อมูลไม่พร้อม — ใช้ฟอร์มอย่างระมัดระวัง"))),
                        "ev_pct": ev_pct, "p_win": eval_res.get("p_win") if eval_res else None,
                        "p_partial": eval_res.get("p_partial") if eval_res else None,
                        "p_loss": eval_res.get("p_loss") if eval_res else None,
                        "score": confidence, "confidence_score": confidence, "confidence_label": confidence_label,
                        "recommendation": recommendation, "analysis_reason": " | ".join(reason_parts),
                        "analysis_a_json": json.dumps(analysis_a, ensure_ascii=False, allow_nan=False),
                        "analysis_b_json": json.dumps(analysis_b, ensure_ascii=False, allow_nan=False),
                        "odds_in_range": int(odds_in_range),
                        "form_rate": form5.get("form_rate"), "form_rate_10": form10.get("form_rate"),
                        "form_rate_season": formseason.get("form_rate"), "form_sample": form5.get("form_sample", 0),
                        "form_signal": form5.get("form_signal", "ข้อมูลย้อนหลังไม่พอ"),
                        "form_summary": form5.get("form_summary", ""), "form_method": f"Football-Data line fit; 5/10/all previous matches; {source_label}",
                        "home_form_summary": home_profile.get("summary", ""), "away_form_summary": away_profile.get("summary", ""),
                        "h2h_summary": h2h["h2h_summary"], "h2h_detail": h2h["h2h_detail"], "h2h_sample": h2h["h2h_sample"],
                    })
        # Keep a fixture visible even when API has not supplied usable spreads/totals outcomes.
        if len(rows) == rows_before_match:
            rows.append({"event_id": match.get("id", ""), "kickoff_utc": kickoff.isoformat(),
                "kickoff_th": kickoff_th.strftime("%d/%m/%Y %H:%M"), "league": league_name,
                "home_team": home, "away_team": away, "match": f"{home} vs {away}",
                "market": "ยังไม่มีราคา", "received_at": receipt, "side": "none", "point": None, "selection": "ยังไม่มีราคาแฮนดิแคป/สูงต่ำ",
                "odds": None, "bookmaker": "-", "model_status": "API ไม่มีราคาในตลาดที่รองรับ",
                "ev_pct": None, "p_win": None, "p_partial": None, "p_loss": None,
                "score": None, "confidence_score": None, "confidence_label": "ข้อมูลไม่พอ",
                "recommendation": "รอราคา — ยังวิเคราะห์ตลาดไม่ได้", "analysis_reason": "พบโปรแกรมแข่งขัน แต่ API ไม่ส่งราคาตลาดที่รองรับ",
                "odds_in_range": 0, "form_rate": None, "form_rate_10": None, "form_rate_season": None,
                "form_sample": 0, "form_signal": "ข้อมูลย้อนหลังไม่พอ", "form_summary": "",
                "form_method": "ไม่มีราคา", "home_form_summary": home_profile.get("summary", ""),
                "away_form_summary": away_profile.get("summary", ""), "h2h_summary": h2h["h2h_summary"],
                "h2h_detail": h2h["h2h_detail"], "h2h_sample": h2h["h2h_sample"],
                "analysis_a_json": json.dumps(system_a_analysis({}, {}, {}, home_profile, away_profile,
                    opponents, xg_evidence, {}), ensure_ascii=False, allow_nan=False),
                "analysis_b_json": json.dumps({"available": False, "calibrated": False,
                    "status": "ยังไม่มีราคาตลาดสำหรับคำนวณ EV"}, ensure_ascii=False)})
    return rows, snapshot_rows, remaining, used

# -------------------- RESULT SETTLEMENT --------------------
def settle_pick(row, home_goals, away_goals):
    # Fixture placeholders without odds must never be settled as bets.
    if row.get("market") not in ("Asian Handicap", "Over/Under") or row.get("odds") is None or pd.isna(row.get("odds")):
        return None
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
        return None
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
    min_odds = st.number_input("ค่าน้ำต่ำสุด (ใช้แจ้งเตือน ไม่ซ่อนคู่)", 1.01, 5.00, 1.80, 0.05)
    max_odds = st.number_input("ค่าน้ำสูงสุด (ใช้แจ้งเตือน ไม่ซ่อนคู่)", 1.01, 10.00, 2.05, 0.05)
    min_confidence = st.slider("คะแนนหลักฐานขั้นต่ำสำหรับจัดอันดับบิล", 0, 100, 55, 1)
    scan_btn = st.button("🚀 สแกนราคาและวิเคราะห์", type="primary", use_container_width=True)

api_key = get_api_key()
tab_scan, tab_slips, tab_history, tab_help = st.tabs(["📊 ผลวิเคราะห์ราคา", "🎟️ ตัวเลือกสเต็ป", "📚 ราคาไหลและตรวจผล", "ℹ️ วิธีใช้"])

with tab_scan:
    st.subheader("📊 วิเคราะห์ทุกคู่ที่ดึงข้อมูลได้")
    render_data_health()
    analysis_view = st.radio("มุมมองผลวิเคราะห์", ["การ์ดมือถือ", "ตารางเดิม"], horizontal=True)
    st.caption("ระบบ A = คะแนนหลักฐาน | ระบบ B = Poisson/EV | ระบบ C = อ่านผลร่วมกัน; ยังไม่ผ่าน Calibration")
    if scan_btn:
        if not api_key:
            st.error("ไม่พบ API Key กรุณากรอกคีย์ในแถบซ้ายมือ")
        elif not selected_leagues:
            st.warning("เลือกลีกทางซ้ายก่อนกดสแกน")
        elif min_odds > max_odds:
            st.error("ค่าน้ำต่ำสุดต้องไม่มากกว่าค่าน้ำสูงสุด")
        else:
            all_rows, all_snapshots, errors, quota_info = [], [], [], []
            progress = st.progress(0)
            for i, key in enumerate(selected_leagues):
                league_name, csv_code = LEAGUES[key]
                try:
                    rows, snapshots, remaining, used = scan_league(key, league_name, csv_code, hours_limit, min_odds, max_odds, regions, api_key)
                    all_rows.extend(rows)
                    all_snapshots.extend(snapshots)
                    quota_info.append(f"{league_name}: ใช้ไป {used}, เหลือ {remaining}")
                except Exception as exc:
                    from odds_budget import ScanStopped
                    errors.append(f"{league_name}: {str(exc) if isinstance(exc, ScanStopped) else type(exc).__name__}")
                    if isinstance(exc, ScanStopped):
                        break
                progress.progress((i + 1) / len(selected_leagues))
            progress.empty()
            if all_rows or all_snapshots:
                count, snapshot_count = save_picks(all_rows, all_snapshots)
                fixture_count = len({r.get("event_id") for r in all_rows if r.get("event_id")})
                with_price = sum(1 for r in all_rows if r.get("market") != "ยังไม่มีราคา")
                st.success(f"พบ {fixture_count} คู่ | บันทึก/อัปเดต {count} รายการตลาด | เก็บราคา {snapshot_count} snapshot")
                st.info(f"มีราคาและตลาดให้วิเคราะห์ {with_price} รายการตลาด — ไม่ตัดคู่ทิ้งด้วย EV หรือช่วงค่าน้ำ")
            else:
                st.info("ไม่พบคู่ในช่วงเวลาที่เลือก หรือ API ไม่ส่งโปรแกรมแข่งขัน")
            for info in quota_info:
                st.caption("ℹ️ " + info)
            for err in errors:
                st.warning(err)

    from readiness import analysis_scope
    df = analysis_display_rows(analysis_scope(load_picks(limit=None), [LEAGUES[k][0] for k in selected_leagues], latest_receipts=latest_api_receipts()))
    if df.empty:
        st.info("ยังไม่มีข้อมูล เลือกลีกและกดสแกน")
    else:
        df["kickoff_parsed"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
        now_ts = pd.Timestamp.now(tz="UTC")
        for col in ("ev_pct", "odds", "p_win", "score", "confidence_score", "form_rate", "form_rate_10", "form_rate_season"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        dashboard_rows = df[df["kickoff_parsed"] >= now_ts].copy()
        df = latest_saved_quotes(df)
        upcoming = df[df["kickoff_parsed"] >= now_ts].copy()
        # Use the best available price for the same line/selection across bookmakers.
        quoted = upcoming[upcoming["odds"].notna()].copy()
        if not quoted.empty:
            from market_recommendations import ah_table_rows
            best_market = pd.DataFrame(ah_table_rows(dashboard_rows.to_dict("records"), choose_recommendation))
            no_price_rows = upcoming[upcoming["market"] == "ยังไม่มีราคา"].copy()
            if not no_price_rows.empty:
                best_market = pd.concat([best_market, no_price_rows], ignore_index=True, sort=False)
        else:
            best_market = upcoming[upcoming["market"] == "ยังไม่มีราคา"].copy()
        from market_recommendations import ou_table_rows
        ou_rows = ou_table_rows(dashboard_rows.to_dict("records"),choose_recommendation)
        if not best_market.empty:
            best_market = best_market[best_market["market"] != "Over/Under"].copy()
        if ou_rows:
            best_market = pd.concat([best_market,pd.DataFrame(ou_rows)],ignore_index=True,sort=False)
        fixtures = upcoming[["event_id", "match", "league", "kickoff_th"]].drop_duplicates("event_id")
        a, b, c = st.columns(3)
        a.metric("คู่ที่ยังไม่เริ่ม", len(fixtures))
        b.metric("คู่มีราคาตลาด", upcoming[upcoming["market"] != "ยังไม่มีราคา"]["event_id"].nunique())
        c.metric("คู่ที่ยังไม่มีราคา", upcoming[upcoming["market"] == "ยังไม่มีราคา"]["event_id"].nunique())
        if analysis_view == "การ์ดมือถือ":
            st.caption("หนึ่งการ์ดต่อ event_id · AH/OU จัดอันดับแยกกันจากแถวที่บันทึกไว้ · ระบุเจ้ามือ เส้น ราคา และ Snapshot แยกตลาด · ไม่เลือกเพราะค่าน้ำสูงสุด")
        else:
            st.caption("ทุกคู่ที่ API ส่งมาในช่วงเวลาที่เลือกจะแสดง แม้ EV ติดลบหรือข้อมูล xG ไม่ครบ OU แสดงราคาสองฝั่งของเส้นหลักที่ประมาณ; เมื่อยืนยันไม่ได้แสดงเป็นข้อมูลประเมิน ไม่เลือกฝั่ง; AH คงตารางเดิม")
        if analysis_view == "การ์ดมือถือ":
            render_mobile_cards(dashboard_rows)
        for market_label, heading in [("Asian Handicap", "อันดับแฮนดิแคป: ต่อ–รอง"), ("Over/Under", "อันดับสูง–ต่ำ")]:
            if analysis_view == "การ์ดมือถือ":
                continue
            st.markdown(f"### {heading}")
            market_view = best_market[best_market["market"] == market_label].copy() if not best_market.empty else pd.DataFrame()
            if market_view.empty:
                st.info("ไม่มีราคาตลาดนี้ในคู่ที่ดึงมา")
                continue
            market_view["confidence_score"] = pd.to_numeric(market_view["confidence_score"], errors="coerce")
            market_view["kickoff_sort"] = pd.to_datetime(market_view["kickoff_utc"], errors="coerce", utc=True)
            market_view = market_view.sort_values(
                ["kickoff_sort", "confidence_score", "ev_pct"],
                ascending=[True, False, False], na_position="last"
            )
            cols = ["kickoff_th", "league", "match", "selection", "odds", "confidence_score", "system_a_result", "system_b_status", "recommendation",
                    "ev_pct", "p_win", "form_rate", "form_rate_10", "form_rate_season", "xg_home_avg", "xga_home_avg",
                    "xg_away_avg", "xga_away_avg", "bookmaker", "model_status", "ou_main_status", "system_c_reason"]
            cols = [c for c in cols if c in market_view.columns]
            if analysis_view == "การ์ดมือถือ":
                render_mobile_cards(market_view)
            else:
                st.dataframe(market_view[cols].rename(columns={
                    "kickoff_th":"เวลาแข่ง (ไทย)", "league":"ลีก", "match":"คู่แข่งขัน", "selection":"คำแนะนำตลาด",
                    "odds":"ราคาที่บันทึก", "confidence_score":"คะแนนหลักฐาน A/100", "system_a_result":"ระบบ A", "system_b_status":"ระบบ B", "recommendation":"ระบบ C อ่าน A/B ร่วมกัน",
                    "ev_pct":"EV (%)", "p_win":"ชนะเต็มตามโมเดล (%)", "form_rate":"ฟอร์มเทียบเส้น 5 นัด (%)",
                    "form_rate_10":"ฟอร์มเทียบเส้น 10 นัด (%)", "form_rate_season":"ฟอร์มเทียบเส้นทั้งหมด (%)",
                    "xg_home_avg":"เหย้า xG เฉลี่ย", "xga_home_avg":"เหย้า xGA เฉลี่ย", "xg_away_avg":"เยือน xG เฉลี่ย",
                    "xga_away_avg":"เยือน xGA เฉลี่ย", "bookmaker":"เจ้ามือ", "model_status":"สถานะข้อมูล"
                }), use_container_width=True, hide_index=True)
        with st.expander("ดูเหตุผลวิเคราะห์และฟอร์มทีมแบบละเอียด"):
            detail = best_market.copy() if not best_market.empty else pd.DataFrame()
            if detail.empty:
                st.info("ไม่มีรายละเอียด")
            else:
                detail = detail.sort_values("confidence_score", ascending=False, na_position="last")
                for _, r in detail.iterrows():
                    st.markdown(f"**{r.get('match','')} — {r.get('market','')} — {r.get('selection','')}**")
                    st.write(f"คะแนน: {r.get('confidence_score', 'n/a')}/100 | {r.get('recommendation','')}")
                    st.write(r.get("analysis_reason") or "ไม่มีเหตุผลเชิงตัวเลขเพิ่มเติม")
                    st.caption("เหย้า: " + str(r.get("home_form_summary") or "ไม่มีข้อมูล") + " | เยือน: " + str(r.get("away_form_summary") or "ไม่มีข้อมูล"))
                    if r.get("h2h_summary"):
                        st.caption("H2H ประกอบเท่านั้น: " + str(r.get("h2h_summary")))
        with st.expander("ดูราคาทางเลือกอื่นและทุกเจ้ามือ"):
            if quoted.empty:
                st.info("ไม่มีราคาให้แสดง")
            else:
                alt_cols = [c for c in ["kickoff_th", "league", "match", "market", "selection", "point", "odds", "confidence_score", "ev_pct", "bookmaker", "odds_in_range"] if c in quoted.columns]
                st.dataframe(quoted[alt_cols].rename(columns={"kickoff_th":"เวลาแข่ง", "league":"ลีก", "match":"คู่แข่งขัน", "market":"ตลาด", "selection":"ตัวเลือก", "point":"เส้น", "odds":"ค่าน้ำ", "confidence_score":"คะแนนหลักฐาน A", "ev_pct":"EV (%)", "bookmaker":"เจ้ามือ", "odds_in_range":"อยู่ในช่วงค่าน้ำที่ตั้งไว้"}), use_container_width=True, hide_index=True)
        st.caption("คะแนน A มาจากหลักฐานฟอร์ม ไม่ใช่ความน่าจะเป็น; เปอร์เซ็นต์ของ B มาจาก Poisson และยังไม่ผ่าน Calibration; EV อาจคลาดเคลื่อนตามคุณภาพ xG")

with tab_slips:
    st.subheader("🎟️ จัดอันดับตัวเลือก — OU ต้องมีเส้นหลักและ EV ที่ยืนยันได้")
    from readiness import analysis_scope
    df = analysis_display_rows(analysis_scope(load_picks(limit=None), [LEAGUES[k][0] for k in selected_leagues], latest_receipts=latest_api_receipts()))
    if df.empty:
        st.info("สแกนราคาก่อน")
    else:
        df["kickoff_parsed"] = pd.to_datetime(df["kickoff_utc"], errors="coerce", utc=True)
        for col in ("ev_pct", "odds", "p_win", "score", "confidence_score"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        now_ts = pd.Timestamp.now(tz="UTC")
        df = latest_saved_quotes(df)
        candidates = df[(df["kickoff_parsed"] >= now_ts) & df["odds"].notna() &
            (df["status"] == "รอตรวจผล") & (df["confidence_score"] >= min_confidence)].copy()
        from market_recommendations import recommended_pick_ids
        upcoming_quotes = df[df["kickoff_parsed"] >= now_ts]
        eligible_ids = recommended_pick_ids(upcoming_quotes.to_dict("records"),choose_recommendation)
        candidates = candidates[candidates["id"].isin(eligible_ids)]
        candidates = candidates.sort_values(["ev_pct", "confidence_score"], ascending=False,na_position="last")
        candidates = candidates.drop_duplicates(subset=["event_id"], keep="first").reset_index(drop=True)
        st.write(f"พบ {len(candidates)} คู่ที่คะแนนตั้งแต่ {min_confidence}/100 ขึ้นไป โดยเลือกไม่เกิน 1 ตัวเลือกต่อคู่; OU ต้องผ่าน Main Line/A/B/EV ของการ์ด")
        if not candidates.empty:
            st.dataframe(candidates[["kickoff_th", "league", "match", "market", "selection", "odds", "confidence_score", "recommendation", "ev_pct", "p_win", "bookmaker"]].rename(columns={
                "kickoff_th":"เวลาแข่ง", "league":"ลีก", "match":"คู่แข่งขัน", "market":"ตลาด", "selection":"ตัวเลือก",
                "odds":"ค่าน้ำ", "confidence_score":"คะแนนหลักฐาน A/100", "recommendation":"คำแนะนำระบบ", "ev_pct":"EV (%)",
                "p_win":"ชนะเต็มตามโมเดล (%)", "bookmaker":"เจ้ามือ"
            }), use_container_width=True, hide_index=True)
            st.caption("OU ไม่มี EV หรือยังไม่ยืนยันเส้นหลักจะไม่เข้ารายการบิลใหม่; AH คงเงื่อนไขเดิม คะแนนสูงไม่ได้หมายถึงกำไรแน่นอน")
            sizes = [5, 9, 13, 20]
            for size in sizes:
                st.markdown(f"#### รายการ {size} คู่แรกตามคะแนน (จัดอันดับ ไม่ใช่คำรับรองให้แทงสเต็ป)")
                if len(candidates) < size:
                    st.caption(f"มี {len(candidates)} คู่ที่ผ่านคะแนนขั้นต่ำ จึงยังจัดรายการ {size} คู่ไม่ได้")
                    continue
                slip = candidates.head(size).copy()
                combined_odds = float(slip["odds"].prod())
                c1, c2 = st.columns(2)
                c1.metric("ค่าน้ำรวม (ชนะเต็มทุกคู่)", f"{combined_odds:,.2f}")
                if slip["p_win"].notna().all():
                    p_full = float((slip["p_win"].clip(0, 100) / 100).prod() * 100)
                    c2.metric("โอกาสชนะเต็มโดยประมาณจากโมเดล", f"{p_full:.3f}%")
                else:
                    c2.metric("โอกาสชนะเต็มโดยประมาณ", "คำนวณไม่ได้ครบทุกคู่")
                st.dataframe(slip[["league", "match", "market", "selection", "odds", "confidence_score", "ev_pct", "bookmaker"]].rename(columns={
                    "league":"ลีก", "match":"คู่แข่งขัน", "market":"ตลาด", "selection":"ตัวเลือก", "odds":"ค่าน้ำ",
                    "confidence_score":"คะแนนหลักฐาน A/100", "ev_pct":"EV (%)", "bookmaker":"เจ้ามือ"
                }), use_container_width=True, hide_index=True)
                save_key = f"save_slip_{size}_" + "_".join(str(int(x)) for x in slip["id"].tolist())
                can_settle = all(has_result_source(name) for name in slip["league"])
                if not can_settle:
                    st.caption("บิลนี้มีลีกที่ยังไม่มีแหล่งตรวจผลอัตโนมัติ จึงยังบันทึกไม่ได้")
                if st.button(f"💾 บันทึกบิล {size} คู่", key=save_key, disabled=not can_settle):
                    probability = float((slip["p_win"].clip(0, 100) / 100).prod()) if slip["p_win"].notna().all() else None
                    slip_id = save_slip_record(slip, combined_odds, probability, [LEAGUES[k][0] for k in selected_leagues])
                    st.success(f"บันทึกบิลหมายเลข #{slip_id} แล้ว — จะตรวจผลเมื่อผลของทุกคู่พร้อม")
        else:
            st.info("ยังไม่มีตัวเลือกถึงคะแนนขั้นต่ำนี้ — แต่คู่ทั้งหมดดูได้ในเมนูวิเคราะห์")

    saved_slips = load_slips()
    st.divider()
    st.markdown("#### 📋 บิลที่บันทึกไว้และสรุปผล")
    if saved_slips.empty:
        st.info("ยังไม่มีบิลที่บันทึกไว้ กดปุ่ม ‘บันทึกบิล’ ใต้ชุดที่ต้องการ")
    else:
        slip_cols = [c for c in ["id", "created_at", "slip_size", "combined_odds", "estimated_probability", "status", "profit_units", "final_summary"] if c in saved_slips.columns]
        st.dataframe(saved_slips[slip_cols].rename(columns={
            "id":"เลขที่บิล", "created_at":"เวลาบันทึก", "slip_size":"จำนวนคู่", "combined_odds":"ค่าน้ำรวมโดยประมาณ",
            "estimated_probability":"โอกาสชนะเต็มโดยประมาณ (โมเดล)", "status":"สถานะ", "profit_units":"กำไร/ขาดทุน (หน่วย)", "final_summary":"สรุปผล"
        }), use_container_width=True, hide_index=True)
        with st.expander("ดูรายละเอียดคู่ในบิลที่บันทึก"):
            for _, saved in saved_slips.iterrows():
                st.markdown(f"**บิล #{int(saved['id'])} — {saved['status']}**")
                try:
                    legs = read_slip_selections(saved["selections_json"])
                    if legs is None or any(slip_pick_id(leg) is None for leg in legs):
                        st.warning("อ่านรายละเอียดบิลนี้ไม่ได้ — เก็บข้อมูลเดิมไว้โดยไม่เปลี่ยนผลบิล")
                    else:
                        st.dataframe(pd.DataFrame(legs).rename(columns={"pick_id":"รหัสคำแนะนำ", "league":"ลีก", "match":"คู่แข่งขัน", "market":"ตลาด", "selection":"ตัวเลือก", "odds":"ค่าน้ำ"}), use_container_width=True, hide_index=True)
                except (ValueError, TypeError):
                    st.warning("อ่านรายละเอียดบิลนี้ไม่ได้")

with tab_history:
    st.subheader("📈 ประวัติราคาและตรวจผล")
    with db() as con:
        keys = pd.read_sql_query("""SELECT event_id, MAX(match_name) AS match_name, market, side, bookmaker,
            MAX(captured_at) AS last_seen FROM odds_snapshots
            GROUP BY event_id, market, side, bookmaker
            ORDER BY last_seen DESC LIMIT 500""", con)
        movement_rows = []
        for _, keyrow in keys.iterrows():
            rows = con.execute("""SELECT point, odds, captured_at FROM odds_snapshots
                WHERE event_id=? AND market=? AND side=? AND bookmaker=?
                ORDER BY julianday(captured_at) ASC, id ASC""", (keyrow["event_id"], keyrow["market"], keyrow["side"], keyrow["bookmaker"])).fetchall()
            if not rows:
                continue
            first, latest = rows[0], rows[-1]
            try:
                first_odds, latest_odds = float(first["odds"]), float(latest["odds"])
                first_point = float(first["point"] or 0)
                latest_point = float(latest["point"] or 0)
            except (TypeError, ValueError):
                continue
            point_changed = abs(latest_point - first_point) > 1e-9
            # Odds are comparable only if the handicap/total line stayed the same.
            odds_delta = round(latest_odds - first_odds, 3) if not point_changed else None
            if point_changed:
                trend = "แต้มต่อ/สูงต่ำเปลี่ยน — เทียบน้ำตรง ๆ ไม่ได้"
            else:
                trend = "ราคาเพิ่ม" if odds_delta > 0 else ("ราคาลด" if odds_delta < 0 else "ราคาน้ำคงที่")
            movement_rows.append({"คู่แข่งขัน":keyrow["match_name"], "ตลาด":keyrow["market"], "ฝั่ง":keyrow["side"], "เจ้ามือ":keyrow["bookmaker"], "First Observed Price (ราคาแรกที่ระบบพบ)":first_odds, "ราคาล่าสุด":latest_odds, "เปลี่ยนแปลงน้ำ (เมื่อเส้นเดิม)":odds_delta, "แต้มแรก":first_point, "แต้มล่าสุด":latest_point, "จำนวน snapshot":len(rows), "ตรวจครั้งแรก":first["captured_at"], "ตรวจล่าสุด":latest["captured_at"], "สัญญาณ":trend})
    if movement_rows:
        st.dataframe(pd.DataFrame(movement_rows), use_container_width=True, hide_index=True)
        st.caption("การเปลี่ยนแปลงราคาเป็นเพียงสัญญาณตลาด ไม่สามารถยืนยันได้ว่าเป็นราคาไหลหลอก")
    else:
        st.info("ยังไม่มีประวัติราคา กดสแกนมากกว่าหนึ่งครั้งในช่วงเวลาต่างกันเพื่อเริ่มเปรียบเทียบ")

    st.divider()
    st.markdown("#### ตรวจผลย้อนหลังจาก Football-Data (เฉพาะลีกที่มีรหัสและไฟล์ผลย้อนหลังใช้งานได้)")
    df = load_picks()
    if not df.empty:
        st.dataframe(df[["id","kickoff_th","league","match","selection","odds","ev_pct","model_status","status","final_score","settlement","profit_units"]], use_container_width=True, hide_index=True)
        if st.button("🔄 ตรวจและคำนวณผลที่หาเจอ", type="secondary"):
            all_picks = load_picks(limit=None)
            pending = all_picks[all_picks["status"] != "ตรวจแล้ว"].copy()
            checked = 0
            skipped_no_source = 0
            skipped_no_history = 0
            skipped_no_match = 0
            skipped_bad_data = 0
            errors = []
            history_cache = {}
            progress = st.progress(0)
            status_box = st.empty()
            total = len(pending)
            try:
                for index, (_, row) in enumerate(pending.iterrows(), start=1):
                    status_box.info(f"กำลังตรวจผล {index}/{total}: {row.get('match', 'ไม่ทราบคู่')}")
                    from readiness import result_time_ready
                    if not result_time_ready(row):
                        skipped_no_match += 1
                        progress.progress(index / max(total, 1))
                        continue
                    code = next((v[1] for v in LEAGUES.values() if v[0] == row["league"]), None)
                    if not code:
                        skipped_no_source += 1
                        progress.progress(index / max(total, 1))
                        continue
                    if code not in history_cache:
                        try:
                            history_cache[code] = fetch_history(code)
                        except Exception as exc:
                            history_cache[code] = pd.DataFrame()
                            errors.append(f"{row['league']}: ดาวน์โหลดผลย้อนหลังผิดพลาด ({type(exc).__name__})")
                    hist = history_cache[code]
                    if hist.empty:
                        record_result_review(row['id'], 'โหลดผลย้อนหลังไม่ได้/ไม่มีผลที่ตรวจสอบได้ — รอตรวจด้วยมือ',
                                             {'csv_code':code,'kickoff_utc':row.get('kickoff_utc')})
                        skipped_no_history += 1
                        progress.progress(index / max(total, 1))
                        continue
                    try:
                        kickoff = pd.to_datetime(row["kickoff_utc"], utc=True).tz_convert(None).normalize()
                        teams = sorted(set(hist["HomeTeam"].dropna()) | set(hist["AwayTeam"].dropna()))
                        home = map_team(row["home_team"], teams)
                        away = map_team(row["away_team"], teams)
                        if not home or not away:
                            record_result_review(row['id'], 'จับคู่ชื่อทีมกับแหล่งผลไม่ได้ — รอตรวจด้วยมือ',
                                                 {'csv_code':code,'home':row.get('home_team'),'away':row.get('away_team')})
                            skipped_no_match += 1
                            progress.progress(index / max(total, 1))
                            continue
                        from readiness import unique_result
                        result = unique_result(hist, home, away, row["kickoff_utc"])
                        if result is None:
                            evidence = hist[(hist['HomeTeam']==home) & (hist['AwayTeam']==away)]
                            record_result_review(row['id'], 'ผลไม่เป็นเอกลักษณ์/วันหรือหลักฐานไม่พอ — รอตรวจด้วยมือ', evidence.to_dict('records'))
                            skipped_no_match += 1
                            progress.progress(index / max(total, 1))
                            continue
                        hg, ag = int(result["FTHG"]), int(result["FTAG"])
                        settled_result = settle_pick(row, hg, ag)
                        if settled_result is None:
                            skipped_bad_data += 1
                        else:
                            label, profit, binary = settled_result
                            update_pick_result(row["id"], f"{hg}-{ag}", label, profit, binary)
                            checked += 1
                    except Exception as exc:
                        skipped_bad_data += 1
                        errors.append(f"{row.get('match', row['id'])}: {type(exc).__name__}")
                    progress.progress(index / max(total, 1))
            finally:
                status_box.empty()
            slips_updated = refresh_slip_settlements()
            st.success(f"ตรวจผลสำเร็จ {checked} รายการ | ไม่มีแหล่งข้อมูล {skipped_no_source} | ไม่มีผลย้อนหลัง {skipped_no_history} | จับคู่ผลไม่พบ {skipped_no_match} | ข้อมูลผิดรูปแบบ/คำนวณไม่ได้ {skipped_bad_data} | อัปเดตสรุปบิล {slips_updated} บิล")
            for message in errors[:10]:
                st.warning(message)
            if len(errors) > 10:
                st.caption(f"มีข้อผิดพลาดเพิ่มเติมอีก {len(errors)-10} รายการ")
            # Reload so the result table and summary reflect the changes immediately.
            df = load_picks()

        from readiness import result_time_ready
        unsupported = load_picks(limit=None)
        unsupported = unsupported[(unsupported["status"] != "ตรวจแล้ว") &
            unsupported.apply(result_time_ready, axis=1) &
            (~unsupported["league"].map(has_result_source) | unsupported["id"].isin(manual_review_ids())) &
            unsupported["market"].isin(["Asian Handicap", "Over/Under"])]
        if not unsupported.empty:
            with st.expander("กรอกผลด้วยตนเองสำหรับลีกที่ไม่มีแหล่งตรวจผล (รวมบิลเดิม)"):
                event_ids = unsupported["event_id"].drop_duplicates().tolist()
                event_id = st.selectbox("คู่แข่งขัน", event_ids,
                    format_func=lambda eid: str(unsupported.loc[unsupported["event_id"] == eid, "match"].iloc[0] or eid))
                hg = st.number_input("ประตูเจ้าบ้าน", min_value=0, step=1)
                ag = st.number_input("ประตูทีมเยือน", min_value=0, step=1)
                confirmed = st.checkbox("ยืนยันว่าเป็นผลจบการแข่งขันอย่างเป็นทางการ")
                if st.button("บันทึกผลที่ยืนยันแล้ว", disabled=not confirmed):
                    for _, pick in unsupported[unsupported["event_id"] == event_id].iterrows():
                        if not result_time_ready(pick):
                            continue
                        result = settle_pick(pick, int(hg), int(ag))
                        if result is not None:
                            label, profit, binary = result
                            update_pick_result(pick["id"], f"{int(hg)}-{int(ag)}", label, profit, binary)
                    refresh_slip_settlements()
                    st.success("บันทึกผลและอัปเดตบิลที่เกี่ยวข้องแล้ว")

        # Refresh pending slips as a safety net when the history tab is opened again.
        try:
            refresh_slip_settlements()
        except Exception:
            pass
        df = load_picks()

        all_picks = load_picks(limit=None)
        settled = all_picks[all_picks["status"] == "ตรวจแล้ว"].copy()
        if not settled.empty:
            settled["profit_units"] = pd.to_numeric(settled["profit_units"], errors="coerce")
            total_profit = settled["profit_units"].fillna(0).sum()
            n = len(settled)
            roi = total_profit / n * 100 if n else 0
            a, b, c = st.columns(3)
            a.metric("จำนวนคำแนะนำที่ตรวจผลแล้ว", n)
            b.metric("กำไร/ขาดทุนสุทธิ (หน่วยเดิมพัน)", f"{total_profit:+.2f}")
            c.metric("ROI ต่อเดิมพันที่ตรวจแล้ว", f"{roi:+.2f}%")
            st.caption("สถิตินี้เป็นผลของคำแนะนำที่บันทึกไว้ ไม่ใช่ backtest อิสระที่สมบูรณ์ และไม่ใช่ผลเดิมพันเงินจริง; หนึ่งหน่วยต่อรายการ ไม่รวมค่าคอมมิชชัน")
    st.divider()
    if DB_FILE.exists():
        from bill_backup import backup_bytes
        st.download_button("⬇️ ดาวน์โหลดสำรองฐานข้อมูล SQLite", data=backup_bytes(DB_FILE), file_name=DB_FILE.name, mime="application/octet-stream")

with tab_help:
    st.subheader("ℹ️ วิธีใช้และข้อจำกัด")
    st.markdown("""
- **แสดงทุกคู่:** ระบบไม่ตัดคู่ทิ้งเพราะ EV ติดลบหรือค่าน้ำอยู่นอกช่วง; ช่วงค่าน้ำใช้แจ้งเตือนเท่านั้น. คู่ที่ API ส่งมาแต่ไม่มีราคาในตลาดที่รองรับก็จะแสดงเป็นรอราคา
- **ฟอร์มย้อนหลัง:** เปรียบเทียบเส้นปัจจุบันกับผลย้อนหลัง 5 นัด, 10 นัด และข้อมูลทั้งหมดที่มีจาก Football-Data; ดูค่าเฉลี่ยยิง/เสียแยกเหย้า–เยือน. การเทียบเส้นปัจจุบันเป็นเพียงตัวชี้แนวโน้ม ไม่ใช่ประวัติการกินราคาจริง เพราะไม่มีราคาเก่าครบทุกนัด
- **ระบบ A:** คะแนนหลักฐานจากฟอร์มเทียบเส้น 5/10/ทั้งหมดและความสอดคล้อง ไม่ใช่โอกาสชนะ; ดูประตูได้เสียตามสนาม คุณภาพคู่แข่งจาก PPG ก่อนเกม xG/xGA จริง และ First Observed Price เป็นหลักฐานประกอบ ไม่มี xG ก็ยังดูฟอร์มได้
- **ระบบ B:** Poisson จาก xG จริงคำนวณประตูคาดการณ์ โอกาสเหย้าชนะ/เสมอ/เยือนชนะ ผลเต็ม/ครึ่ง/คืนทุนของ AH/OU และ EV. ยังไม่ผ่าน Calibration และไม่มีข้อมูลก็เว้นค่า ไม่สร้าง xG ทดแทน
- **ระบบ C:** อ่าน A/B ร่วมกันโดยไม่ผสมคะแนนหลักฐานเป็นความน่าจะเป็น. ถ้า EV ไม่มีค่าหรือไม่เป็นบวก จะไม่ใช้คำว่าตัวเลือกเด่นจากฟอร์มอย่างเดียว. ราคาไหลและคุณภาพคู่แข่งเป็นข้อมูลประกอบ ยังไม่ปรับน้ำหนักเชิงสถิติ
- **มือถือ:** เลือกการ์ดมือถือเพื่ออ่านผลหลักโดยไม่เลื่อนตารางแนวนอน หรือสลับตารางเดิมได้. การเปลี่ยนมุมมองอ่านข้อมูล SQLite ไม่เรียก Odds API. ข้อมูลเดิมต้องสแกนใหม่จึงได้ผล A/B แยกกัน
- **ความครอบคลุม xG:** โค้ดตั้งค่า Understat สำหรับพรีเมียร์ลีก, ลาลีกา, บุนเดสลีกา, เซเรียอา, ลีกเอิง และรัสเซีย หากเว็บปิดกั้น/เปลี่ยนรูปแบบ หรือไม่มีข้อมูลก่อนเตะ ระบบจะเว้น EV
- **UCL / Europa League / Conference League:** ดึงราคาได้ทั้งสามรายการ แสดง xG ลีกในประเทศของทีมที่จับคู่ข้อมูลได้เพื่อประกอบการดูฟอร์มเท่านั้น และไม่ใช้ข้อมูลลีกในประเทศคำนวณ EV ของถ้วยยุโรป
- **ลีกอื่น:** ยังดึงราคาได้ และหากมีผลย้อนหลัง Football-Data จะเห็นสัญญาณฟอร์มได้ แต่จะไม่แสดง EV จนกว่าจะมี xG ที่เชื่อถือได้สำหรับลีกนั้น ห้ามตีความว่าไม่มี EV แปลว่าไม่มี value
- **StatsBomb Open Data:** ไม่ใช้เป็นแหล่ง EV สดแบบครอบคลุม เพราะข้อมูลฟรีเป็นบางรายการแข่งขัน/บางฤดูกาลและไม่สม่ำเสมอ ไม่ควรนำข้อมูลเก่ามาอ้างเป็น xG ปัจจุบัน
- **ประวัติราคา:** เก็บราคาในช่วงเวลาที่สแกนโดยไม่ใช้ตัวกรองค่าน้ำของหน้าคัดคู่; “ราคาแรกที่ระบบพบ” ไม่ใช่ราคาเปิดตลาดจริง ต้องสแกนหลายครั้ง และเมื่อเส้นเปลี่ยนจะไม่เปรียบเทียบค่าน้ำราวกับเป็นเส้นเดียวกัน
- **โควตา The Odds API:** เลือกสแกนเฉพาะลีกที่สนใจ; แต่ละลีกใช้คำขอราคา 1 ครั้ง โดยขอ 2 ตลาดในภูมิภาคเดียว; ต้นทุนขึ้นกับการคิดเครดิตของแผนและตลาดที่ตอบกลับ ให้ยึด response headers เป็นหลัก
- **ข้อมูลบน Streamlit Cloud:** SQLite ในพื้นที่แอปอาจไม่ถาวรหลังรีสตาร์ตหรือ redeploy ควรดาวน์โหลดสำรองเป็นระยะ
- **ผลตรวจย้อนหลัง:** คำแนะนำที่ไม่ได้บันทึกเป็นบิลจะอัปเดตเป็นค่าล่าสุด; รายการที่อยู่ในบิลจะล็อกเส้นและค่าน้ำเดิม. การตรวจผลและ ROI ใช้ข้อมูลทั้งหมด แม้ตารางจะแสดงเพียง 5,000 รายการล่าสุด. สถิตินี้ไม่ใช่ backtest อิสระ
- **บิลและแหล่งผล:** บิลใหม่ต้องมีรหัสแหล่งผลอัตโนมัติครบทุกลีก; บิลเดิมในลีกที่ไม่มีแหล่งผลสามารถกรอกผลจบการแข่งขันที่ยืนยันแล้วในหน้าประวัติ
- **ความสดของ xG:** หากเกมล่าสุดในชุดเหย้าหรือเยือนเก่ากว่าวันแข่งเกิน 120 วัน ระบบจะเว้น EV; เกณฑ์นี้เผื่อช่วงปิดฤดูกาลและยังไม่ได้ปรับเทียบเชิงสถิติ
- **EV:** ใช้ประกอบการดูความคุ้มค่าของราคา ไม่ได้ใช้คัดคู่ตั้งแต่ต้น. EV ติดลบหมายถึงราคาที่ประเมินอาจไม่คุ้ม แม้ฟอร์มจะดูดี
""")

st.divider()
st.caption("Value Bet Pro xG Edition | confidence scores are rankings, not calibrated win probabilities; no model is guaranteed profitable")

