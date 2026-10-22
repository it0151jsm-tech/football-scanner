"""User-confirmed football statistics; no bookmaker or score-provider calls."""
import json
import math
import sqlite3
from datetime import datetime, timezone

def initialize(connection):
    connection.execute("""CREATE TABLE IF NOT EXISTS personal_match_stats (
        match_key TEXT PRIMARY KEY, kickoff_utc TEXT NOT NULL,
        home_team TEXT NOT NULL, away_team TEXT NOT NULL,
        home_goals INTEGER, away_goals INTEGER,
        source TEXT NOT NULL, stats_json TEXT NOT NULL,
        recorded_at TEXT NOT NULL, verified INTEGER NOT NULL DEFAULT 1
    )""")

def clean_stats(raw):
    """Accept only nonnegative finite numeric metrics, retaining arbitrary labels."""
    if not isinstance(raw, dict):
        raise ValueError("stats must be an object")
    result = {}
    for name, sides in raw.items():
        if not isinstance(name, str) or not name.strip() or len(name) > 120:
            raise ValueError("invalid metric name")
        if not isinstance(sides, dict):
            raise ValueError("invalid metric")
        clean = {}
        for side in ("home", "away"):
            value = sides.get(side)
            if value is None or value == "":
                clean[side] = None
            elif isinstance(value, bool):
                raise ValueError("invalid numeric value")
            else:
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    raise ValueError("invalid numeric value")
                if not math.isfinite(number) or number < 0:
                    raise ValueError("invalid numeric value")
                clean[side] = number
        result[name.strip()] = clean
    return result

def save_verified(connection, *, match_key, kickoff_utc, home_team, away_team,
                  home_goals, away_goals, source, stats):
    if not all(isinstance(v, str) and v.strip() for v in
               (match_key, kickoff_utc, home_team, away_team, source)):
        raise ValueError("missing match identity/source")
    kickoff = datetime.fromisoformat(kickoff_utc.replace("Z", "+00:00"))
    if kickoff.tzinfo is None or kickoff.astimezone(timezone.utc) > datetime.now(timezone.utc):
        raise ValueError("match must be completed and timezone-aware")
    for goal in (home_goals, away_goals):
        if goal is not None and (isinstance(goal, bool) or not isinstance(goal, int) or goal < 0):
            raise ValueError("invalid score")
    if (home_goals is None) != (away_goals is None):
        raise ValueError("score must have both sides")
    checked = clean_stats(stats)
    initialize(connection)
    connection.execute("""INSERT INTO personal_match_stats
        (match_key,kickoff_utc,home_team,away_team,home_goals,away_goals,
         source,stats_json,recorded_at,verified)
         VALUES (?,?,?,?,?,?,?,?,?,1)
         ON CONFLICT(match_key) DO UPDATE SET
         kickoff_utc=excluded.kickoff_utc,home_team=excluded.home_team,
         away_team=excluded.away_team,home_goals=excluded.home_goals,
         away_goals=excluded.away_goals,source=excluded.source,
         stats_json=excluded.stats_json,recorded_at=excluded.recorded_at,
         verified=1""", (match_key.strip(),kickoff.isoformat(),home_team.strip(),
                         away_team.strip(),home_goals,away_goals,source.strip(),
                         json.dumps(checked,ensure_ascii=False),
                         datetime.now(timezone.utc).isoformat()))
    connection.commit()

def read_history(connection, *, team=None, before=None):
    initialize(connection)
    query = "SELECT * FROM personal_match_stats WHERE verified=1"
    params = []
    if team:
        query += " AND (home_team=? OR away_team=?)"
        params.extend([team,team])
    if before:
        query += " AND kickoff_utc < ?"
        params.append(before)
    query += " ORDER BY kickoff_utc DESC"
    return [dict(row) for row in connection.execute(query,params).fetchall()]
