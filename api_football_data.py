"""Optional API-Football score evidence for five major leagues; never supplies xG or EV.

Only verified, finished fixtures with integer scores are admitted. No secrets are
stored here; callers supply the key from Streamlit secrets/environment.
"""
from datetime import datetime, timezone
import pandas as pd

LEAGUE_IDS = {
    "soccer_epl": 39,
    "soccer_spain_la_liga": 140,
    "soccer_germany_bundesliga": 78,
    "soccer_italy_serie_a": 135,
    "soccer_france_ligue_one": 61,
}
BASE_URL = "https://v3.football.api-sports.io/fixtures"


def fetch_finished_fixtures(sport_key, season, api_key, get, timeout=(3, 8)):
    """One bounded league-season request; fail closed on provider/API errors."""
    league_id = LEAGUE_IDS.get(sport_key)
    diagnostic = {"provider": "api-football", "league_id": league_id, "season": season}
    empty = pd.DataFrame()
    if not league_id:
        return empty, {**diagnostic, "status": "unsupported"}
    if not api_key:
        return empty, {**diagnostic, "status": "not_configured"}
    if not isinstance(season, int) or not 2000 <= season <= 2100:
        return empty, {**diagnostic, "status": "invalid_season"}
    try:
        response = get(BASE_URL, headers={"x-apisports-key": api_key},
                       params={"league": league_id, "season": season, "status": "FT"},
                       timeout=timeout)
        if response.status_code != 200:
            return empty, {**diagnostic, "status": "http_error", "http_status": response.status_code}
        payload = response.json()
        if not isinstance(payload, dict) or payload.get("errors"):
            return empty, {**diagnostic, "status": "provider_error"}
        records = payload.get("response")
        if not isinstance(records, list):
            return empty, {**diagnostic, "status": "invalid_response"}
        rows = []
        for record in records:
            try:
                fixture, teams, goals = record["fixture"], record["teams"], record["goals"]
                if fixture.get("status", {}).get("short") != "FT":
                    continue
                if fixture.get("timestamp") is None:
                    continue
                when = datetime.fromtimestamp(int(fixture["timestamp"]), tz=timezone.utc)
                home, away = teams["home"]["name"], teams["away"]["name"]
                hg, ag = goals["home"], goals["away"]
                if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (hg, ag)):
                    continue
                if not all(isinstance(n, str) and n.strip() for n in (home, away)):
                    continue
                if when > datetime.now(timezone.utc):
                    continue
                rows.append({"Date_dt": pd.Timestamp(when).tz_localize(None),
                             "HomeTeam": home, "AwayTeam": away, "FTHG": hg, "FTAG": ag,
                             "_provider_fixture_id": fixture.get("id"),
                             "_history_season": f"{season % 100:02d}{(season + 1) % 100:02d}"})
            except (KeyError, TypeError, ValueError, OverflowError, AttributeError):
                continue
        frame = pd.DataFrame(rows)
        if frame.empty:
            return empty, {**diagnostic, "status": "empty_history", "provider_results": len(records)}
        # Conflicting fixture results must never be silently chosen.
        frame = frame.drop_duplicates()
        conflicts = frame.groupby(["Date_dt", "HomeTeam", "AwayTeam"])[["FTHG", "FTAG"]].transform("nunique").gt(1).any(axis=1)
        frame = frame.loc[~conflicts].drop_duplicates(["Date_dt", "HomeTeam", "AwayTeam"]).sort_values("Date_dt").reset_index(drop=True)
        frame.attrs["history_diagnostics"] = {"status": "ok", "provider": "api-football",
            "league_id": league_id, "season": season, "provider_results": len(records),
            "conflicting_rows": int(conflicts.sum())}
        return frame, {**diagnostic, "status": "ok" if not frame.empty else "conflicting_scores",
                       "usable_rows": len(frame), "conflicting_rows": int(conflicts.sum())}
    except Exception as exc:
        return empty, {**diagnostic, "status": "download_error", "error_type": type(exc).__name__}
