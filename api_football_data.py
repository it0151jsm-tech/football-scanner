"""Optional, fail-closed API-Football result evidence; never supplies xG or EV.

``status`` retains the original adapter contract. ``state`` is the presentation
state, so callers can distinguish a plan restriction from a generic API error.
No provider error text, request headers, or credential-bearing URLs are retained.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math

import pandas as pd

LEAGUE_IDS = {
    "soccer_epl": 39,
    "soccer_spain_la_liga": 140,
    "soccer_germany_bundesliga": 78,
    "soccer_italy_serie_a": 135,
    "soccer_france_ligue_one": 61,
}
BASE_URL = "https://v3.football.api-sports.io/fixtures"



class ApiFootballUnavailable(Exception):
    """Keep a short-lived failure outside the successful six-hour cache."""
    def __init__(self, frame, diagnostic):
        self.frame = frame
        self.diagnostic = diagnostic
        super().__init__("API-Football source unavailable")


def api_football_status_text(diagnostic):
    """Thai diagnostics from allowlisted states; never echo provider/key text."""
    report = diagnostic if isinstance(diagnostic, dict) else {}
    state = report.get("state", report.get("status", "not_called"))
    if not isinstance(state, str):
        state = "unknown"
    cached = state == "cache" or report.get("cache_hit") is True
    if state == "cache":
        state = report.get("provider_state", "unknown")
        if not isinstance(state, str):
            state = "unknown"
    messages = {
        "success": "ใช้งานสำเร็จ", "ok": "ใช้งานสำเร็จ",
        "not_called": "ไม่ได้เรียก", "not_configured": "ไม่ได้เรียก — ยังไม่ได้ตั้งค่า Key",
        "http_error": "HTTP error", "plan_unsupported": "แพ็กเกจไม่รองรับคำขอหรือฤดูกาลนี้",
        "provider_error": "ผู้ให้บริการตอบข้อผิดพลาด", "empty": "ข้อมูลว่างหรือไม่มีเกมที่ผ่านการตรวจสอบ",
        "empty_history": "ข้อมูลว่างหรือไม่มีเกมที่ผ่านการตรวจสอบ",
        "invalid_response": "รูปแบบข้อมูลไม่ผ่านการตรวจสอบ",
        "download_error": "โหลดข้อมูลไม่สำเร็จ", "conflicting_scores": "ผลการแข่งขันขัดแย้ง — ไม่ใช้ข้อมูล",
    }
    detail = messages.get(state, "สถานะยังยืนยันไม่ได้")
    reasons = {
        "invalid_key": "การตั้งค่า Key ไม่ถูกต้อง",
        "missing_key": "ยังไม่ได้ตั้งค่า Key", "not_configured": "ยังไม่ได้ตั้งค่า Key",
        "csv_sufficient": "ใช้ฟอร์ม CSV ที่พร้อมอยู่แล้ว", "csv_ready": "ใช้ฟอร์ม CSV ที่พร้อมอยู่แล้ว",
        "csv_names_matched": "ใช้ฟอร์ม CSV ที่จับคู่ชื่อได้อยู่แล้ว",
        "unsupported_league": "ลีกนี้ไม่มีตัวเชื่อม API-Football",
        "no_fixtures": "ไม่มีคู่ที่ผ่านตัวกรอง", "no_valid_fixtures": "ไม่มีคู่ที่ผ่านตัวกรอง",
        "invalid_season": "ฤดูกาลไม่ถูกต้อง", "names_unmatched": "จับคู่ชื่อทีมไม่สำเร็จ",
        "provider_names_unmatched": "จับคู่ชื่อทีมไม่สำเร็จ",
        "provider_metadata_unverified": "หลักฐานลีก ฤดูกาล หรือรหัสทีมไม่ครบ — ยังไม่ใช้คำนวณฟอร์ม",
        "incomplete_pagination": "ข้อมูลหลายหน้าไม่ครบ — ไม่เรียกหน้าถัดไปอัตโนมัติ",
        "conflicting_results": "ผลหรืออัตลักษณ์ทีมขัดแย้ง — ไม่ใช้ข้อมูล",
        "disabled_by_owner": "ปิด API-Football ตามการตั้งค่า — ไม่ใช้เครดิต",
        "plan_backoff": "พักการเรียกหลังแพ็กเกจไม่รองรับ — ไม่ใช้เครดิตซ้ำ",
        "auth_backoff": "พักการเรียกหลังปัญหาสิทธิ์เข้าถึง — ไม่ใช้เครดิตซ้ำ",
        "quota_backoff": "พักการเรียกหลังถูกจำกัดโควตา — ไม่ใช้เครดิตซ้ำ",
        "failure_backoff": "พักการเรียกหลังแหล่งข้อมูลล้มเหลว",
        "request_in_progress": "มีคำขอสำหรับลีกและฤดูกาลนี้กำลังทำงาน — ไม่เรียกซ้ำ",
        "backoff_store_unavailable": "ตรวจข้อจำกัดการเรียกไม่ได้ — หยุดเรียกเพื่อป้องกันเครดิต",
        "invalid_clock": "เวลาอ้างอิงไม่ถูกต้อง — หยุดเรียก",
        "invalid_backoff_policy": "การตั้งค่าช่วงพักไม่ถูกต้อง — หยุดเรียก",
    }
    reason_code = report.get("reason") or report.get("reason_code")
    reason = reasons.get(reason_code) if isinstance(reason_code, str) else None
    if reason:
        detail += " — " + reason
    http_status = report.get("http_status")
    if state == "http_error" and isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599:
        detail += " " + str(http_status)
    count = report.get("usable_rows")
    if state in {"success", "ok"} and isinstance(count, int) and not isinstance(count, bool) and count >= 0:
        detail += " (" + str(count) + " เกม)"
    if state == "not_called" and report.get("backoff_active") is True:
        previous = report.get("provider_state")
        previous_text = messages.get(previous) if isinstance(previous, str) else None
        if previous_text:
            detail += " — ผลจากครั้งก่อน: " + previous_text
            if previous == "http_error" and isinstance(http_status, int) and not isinstance(http_status, bool) and 100 <= http_status <= 599:
                detail += " " + str(http_status)
    next_retry = report.get("next_retry_at")
    if isinstance(next_retry, str) and len(next_retry) <= 64:
        try:
            when = datetime.fromisoformat(next_retry.replace("Z", "+00:00"))
            if when.tzinfo is not None:
                detail += " · ลองใหม่ได้ UTC: " + when.astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, OverflowError):
            pass
    quarantined = sum(v for v in (report.get("rejected_rows", 0), report.get("conflicting_rows", 0))
                      if isinstance(v, int) and not isinstance(v, bool) and v > 0)
    if quarantined:
        detail += " — ไม่ใช้ข้อมูล " + str(quarantined) + " แถวที่ไม่ผ่านตรวจ"
    return "API-Football: " + ("cache — " if cached else "") + detail


def _positive_id(value):
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _plan_restricted(errors):
    """Classify provider evidence without returning its possibly secret text."""
    if isinstance(errors, dict):
        if any(str(key).lower() in {"plan", "subscription"} for key in errors):
            return True
        values = errors.values()
    elif isinstance(errors, list):
        values = errors
    else:
        values = [errors]
    for value in values:
        text = str(value).lower()
        if any(phrase in text for phrase in (
            "free plan", "subscription", "plan does not", "plan do not",
            "plan doesn't", "plan don't", "not included in your plan",
            "not available in your plan", "upgrade your plan",
        )):
            return True
    return False


def fetch_finished_fixtures(sport_key, season, api_key, get, timeout=(3, 8)):
    """One bounded league-season request with UTC epoch and immutable receipt.

    Names/IDs remain exactly as supplied. No fuzzy identity matching is performed
    here; the caller must match against its reviewed identity roster. Cache hits
    must retain ``received_at`` and be reported separately by the caller.
    """
    league_id = LEAGUE_IDS.get(sport_key)
    diagnostic = {
        "provider": "api-football", "league_id": league_id, "season": season,
        "source_url": BASE_URL, "request_attempted": False,
    }
    empty = pd.DataFrame()

    def finish(frame, status, state=None, reason=None, **details):
        report = {**diagnostic, "status": status, "state": state or status,
                  "reason": reason or state or status, **details}
        # Keeping the report on empty frames also makes fallback failures visible.
        frame.attrs["history_diagnostics"] = report
        frame.attrs["api_football_diagnostics"] = report
        return frame, report

    if not league_id:
        return finish(empty, "unsupported", "not_called", "unsupported_league")
    if not api_key:
        return finish(empty, "not_configured", "not_called", "missing_key")
    if not isinstance(api_key, str) or not api_key.strip():
        return finish(empty, "not_configured", "not_called", "invalid_key")
    if not isinstance(season, int) or isinstance(season, bool) or not 2000 <= season <= 2100:
        return finish(empty, "invalid_season", "not_called", "invalid_season")
    # Register the supplied credential with the existing logging protection before
    # requests (including third-party debug logs) can observe it.
    from credential_redaction import protect
    protect(api_key)
    diagnostic["request_attempted"] = True
    diagnostic["requested_at"] = datetime.now(timezone.utc).isoformat()
    try:
        response = get(BASE_URL, headers={"x-apisports-key": api_key},
                       params={"league": league_id, "season": season, "status": "FT"},
                       timeout=timeout)
        diagnostic["received_at"] = datetime.now(timezone.utc).isoformat()
        diagnostic["http_status"] = response.status_code
        if response.status_code != 200:
            return finish(empty, "http_error")
        try:
            payload = response.json()
        except (ValueError, TypeError):
            return finish(empty, "invalid_response", reason="invalid_json")
        if not isinstance(payload, dict):
            return finish(empty, "invalid_response", reason="invalid_payload")
        # Hash only a canonical representation; never include raw bodies/errors.
        try:
            digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return finish(empty, "invalid_response", reason="invalid_payload_values")
        diagnostic["payload_sha256"] = digest
        if payload.get("errors"):
            state = "plan_unsupported" if _plan_restricted(payload["errors"]) else "provider_error"
            return finish(empty, "provider_error", state)
        records = payload.get("response")
        if not isinstance(records, list):
            return finish(empty, "invalid_response", reason="missing_response_list")
        pagination = payload.get("paging")
        if isinstance(pagination, dict) and pagination.get("total", 1) != 1:
            # This adapter promises exactly one bounded request, not hidden paging.
            return finish(empty, "invalid_response", reason="incomplete_pagination",
                          provider_results=len(records))
        rows, rejected = [], Counter()
        now = datetime.now(timezone.utc)
        for record in records:
            try:
                fixture, teams, goals = record["fixture"], record["teams"], record["goals"]
                if fixture.get("status", {}).get("short") != "FT":
                    rejected["not_finished"] += 1
                    continue
                fixture_id = fixture.get("id")
                if not _positive_id(fixture_id):
                    rejected["invalid_fixture_id"] += 1
                    continue
                stamp = fixture.get("timestamp")
                if (not isinstance(stamp, (int, float)) or isinstance(stamp, bool)
                        or not math.isfinite(stamp) or int(stamp) != stamp):
                    rejected["invalid_timestamp"] += 1
                    continue
                when = datetime.fromtimestamp(stamp, tz=timezone.utc)
                if when > now:
                    rejected["future_result"] += 1
                    continue
                reported_date = fixture.get("date")
                if reported_date is not None:
                    parsed = pd.Timestamp(reported_date)
                    if (pd.isna(parsed) or parsed.tzinfo is None
                            or parsed.tz_convert("UTC") != pd.Timestamp(when)):
                        rejected["timestamp_date_mismatch"] += 1
                        continue
                league = record.get("league")
                if league is not None and (not isinstance(league, dict)
                        or league.get("id") != league_id or league.get("season") != season):
                    rejected["league_season_mismatch"] += 1
                    continue
                if league is not None and not (datetime(season, 7, 1, tzinfo=timezone.utc) <= when
                        < datetime(season + 1, 7, 1, tzinfo=timezone.utc)):
                    rejected["date_season_mismatch"] += 1
                    continue
                home, away = teams["home"]["name"], teams["away"]["name"]
                if (not all(isinstance(n, str) and n.strip() for n in (home, away))
                        or home.strip().casefold() == away.strip().casefold()):
                    rejected["invalid_teams"] += 1
                    continue
                home_id, away_id = teams["home"].get("id"), teams["away"].get("id")
                if (any(v is not None and not _positive_id(v) for v in (home_id, away_id))
                        or home_id is not None and home_id == away_id):
                    rejected["invalid_team_ids"] += 1
                    continue
                hg, ag = goals["home"], goals["away"]
                if not all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in (hg, ag)):
                    rejected["invalid_scores"] += 1
                    continue
                rows.append({"Date_dt": pd.Timestamp(when).tz_localize(None),
                    "HomeTeam": home, "AwayTeam": away, "FTHG": hg, "FTAG": ag,
                    "_provider_fixture_id": fixture_id, "_provider_home_team_id": home_id,
                    "_provider_away_team_id": away_id,
                    "_provider_league_verified": league is not None,
                    "_provider_identity_verified": home_id is not None and away_id is not None,
                    "_history_season": f"{season % 100:02d}{(season + 1) % 100:02d}",
                    "_history_source_url": BASE_URL,
                    "_history_received_at": diagnostic["received_at"],
                    "_history_payload_sha256": digest,
                    "_history_time_basis": "provider_unix_timestamp_utc"})
            except (KeyError, TypeError, ValueError, OverflowError, OSError, AttributeError):
                rejected["invalid_record"] += 1
        counts = {"provider_results": len(records), "rejected_rows": sum(rejected.values()),
                  "rejected_reasons": dict(rejected)}
        frame = pd.DataFrame(rows)
        if frame.empty:
            return finish(empty, "empty_history", "empty", **counts, usable_rows=0)
        frame = frame.drop_duplicates()
        # One source ID cannot refer to different teams, times, or scores. Also
        # reject contradictory scores carrying different source IDs for one game.
        identity_columns = ["Date_dt", "HomeTeam", "AwayTeam", "FTHG", "FTAG",
                            "_provider_home_team_id", "_provider_away_team_id"]
        id_conflicts = frame.groupby("_provider_fixture_id")[identity_columns].transform("nunique").gt(1).any(axis=1)
        score_conflicts = frame.groupby(["Date_dt", "HomeTeam", "AwayTeam"])[["FTHG", "FTAG"]].transform("nunique").gt(1).any(axis=1)
        # A provider label is not a cross-provider ID bridge. Within this payload,
        # however, names shared by multiple numeric IDs (or IDs shared by multiple
        # names) are ambiguous; quarantine every affected fixture instead of pooling.
        name_ids, id_names = {}, {}
        for row in rows:
            for name_key, id_key in (("HomeTeam", "_provider_home_team_id"),
                                     ("AwayTeam", "_provider_away_team_id")):
                label, team_id = row[name_key].strip().casefold(), row[id_key]
                if team_id is not None:
                    name_ids.setdefault(label, set()).add(team_id)
                    id_names.setdefault(team_id, set()).add(label)
        ambiguous_names = {name for name, ids in name_ids.items() if len(ids) > 1}
        ambiguous_ids = {team_id for team_id, names in id_names.items() if len(names) > 1}
        def ambiguous(row):
            return any(str(row[name_key]).strip().casefold() in ambiguous_names
                       or row[id_key] in ambiguous_ids
                       for name_key, id_key in (("HomeTeam", "_provider_home_team_id"),
                                                ("AwayTeam", "_provider_away_team_id")))
        identity_conflicts = frame.apply(ambiguous, axis=1)
        conflicts = id_conflicts | score_conflicts | identity_conflicts
        conflicting_rows = int(conflicts.sum())
        frame = frame.loc[~conflicts].drop_duplicates(["Date_dt", "HomeTeam", "AwayTeam"]).sort_values("Date_dt").reset_index(drop=True)
        status = "ok" if not frame.empty else "conflicting_scores"
        return finish(frame, status, "success" if not frame.empty else "empty",
            reason="validated_results" if not frame.empty else "conflicting_results",
            **counts, usable_rows=len(frame), conflicting_rows=conflicting_rows,
            ambiguous_identity_rows=int(identity_conflicts.sum()),
            league_verified_rows=int(frame["_provider_league_verified"].sum()),
            identity_verified_rows=int(frame["_provider_identity_verified"].sum()),
            fully_verified_rows=int((frame["_provider_league_verified"] & frame["_provider_identity_verified"]).sum()),
            team_id_status=("missing" if not name_ids else
                "partial" if any(row[key] is None for row in rows for key in
                    ("_provider_home_team_id", "_provider_away_team_id")) else "provider_ids_recorded"))
    except Exception:
        # No exception message/type/provider body is exposed: all can contain keys.
        return finish(empty, "download_error", "download_error", "request_or_payload_error")
