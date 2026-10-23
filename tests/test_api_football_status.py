"""Offline synthetic API-Football responses; no provider access or live identity claims."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import Mock

from api_football_data import (
    ApiFootballUnavailable, api_football_status_text, fetch_finished_fixtures,
)


class ApiFootballStatusTests(unittest.TestCase):
    key = "synthetic-api-football-test-secret"

    def fixture(self, fixture_id=1, home="Arsenal", away="Chelsea", home_id=42, away_id=49):
        when = datetime(2026, 9, 1, 18, tzinfo=timezone.utc)
        return {
            "fixture": {"id": fixture_id, "timestamp": int(when.timestamp()),
                        "date": when.isoformat(), "status": {"short": "FT"}},
            "league": {"id": 39, "season": 2026},
            "teams": {"home": {"id": home_id, "name": home},
                      "away": {"id": away_id, "name": away}},
            "goals": {"home": 2, "away": 1},
        }

    def fetch(self, records=None, payload=None, status=200):
        response = Mock(status_code=status)
        response.json.return_value = payload if payload is not None else {"errors": [], "response": records or []}
        get = Mock(return_value=response)
        frame, report = fetch_finished_fixtures("soccer_epl", 2026, self.key, get)
        self.assertNotIn(self.key, json.dumps(report))
        return frame, report, get

    def test_success_has_actual_receipt_checksum_source_and_numeric_ids(self):
        before = datetime.now(timezone.utc)
        frame, report, _ = self.fetch([self.fixture()])
        after = datetime.now(timezone.utc)
        self.assertEqual(report["state"], "success")
        self.assertTrue(report["request_attempted"])
        self.assertLessEqual(before, datetime.fromisoformat(report["received_at"]))
        self.assertLessEqual(datetime.fromisoformat(report["received_at"]), after)
        self.assertEqual(len(report["payload_sha256"]), 64)
        self.assertEqual(frame.iloc[0]["_provider_home_team_id"], 42)
        self.assertEqual(frame.iloc[0]["_provider_away_team_id"], 49)
        self.assertEqual(report["team_id_status"], "provider_ids_recorded")
        self.assertTrue(frame.iloc[0]["_provider_league_verified"])
        self.assertTrue(frame.iloc[0]["_provider_identity_verified"])
        self.assertEqual(report["fully_verified_rows"], 1)
        self.assertEqual(frame.iloc[0]["_history_received_at"], report["received_at"])
        self.assertEqual(frame.attrs["api_football_diagnostics"], report)
        self.assertFalse(any("xg" in c.lower() for c in frame.columns))
        self.assertIn("ใช้งานสำเร็จ", api_football_status_text(report))

    def test_plan_error_in_http_200_is_explicit_without_raw_message(self):
        frame, report, _ = self.fetch(payload={"errors": {"requests": "Free plans do not have access to this season " + self.key}, "response": []})
        self.assertTrue(frame.empty)
        self.assertEqual(report["status"], "provider_error")
        self.assertEqual(report["state"], "plan_unsupported")
        self.assertEqual(report["http_status"], 200)
        self.assertIn("แพ็กเกจไม่รองรับ", api_football_status_text(report))

    def test_generic_provider_error_is_not_assumed_plan_restriction(self):
        frame, report, _ = self.fetch(payload={"errors": {"requests": "daily quota " + self.key}, "response": []})
        self.assertTrue(frame.empty)
        self.assertEqual(report["state"], "provider_error")
        self.assertNotIn("daily quota", json.dumps(report))

    def test_missing_key_is_not_called_and_has_diagnostics(self):
        get = Mock()
        frame, report = fetch_finished_fixtures("soccer_epl", 2026, "", get)
        self.assertEqual(report["state"], "not_called")
        self.assertEqual(report["reason"], "missing_key")
        self.assertFalse(report["request_attempted"])
        self.assertNotIn("received_at", report)
        self.assertEqual(frame.attrs["api_football_diagnostics"], report)
        get.assert_not_called()
        self.assertIn("ยังไม่ได้ตั้งค่า", api_football_status_text(report))

    def test_unsupported_league_is_not_called(self):
        get = Mock()
        _, report = fetch_finished_fixtures("soccer_uefa_champs_league", 2026, self.key, get)
        self.assertEqual(report["state"], "not_called")
        self.assertEqual(report["reason"], "unsupported_league")
        get.assert_not_called()

    def test_boolean_season_is_rejected_before_request(self):
        get = Mock()
        _, report = fetch_finished_fixtures("soccer_epl", True, self.key, get)
        self.assertEqual(report["reason"], "invalid_season")
        get.assert_not_called()

    def test_http_errors_do_not_parse_body_or_expose_key(self):
        for code in (401, 403, 429, 500):
            with self.subTest(code=code):
                _, report, get = self.fetch(status=code)
                self.assertEqual(report["state"], "http_error")
                get.return_value.json.assert_not_called()
                self.assertIn(str(code), api_football_status_text(report))

    def test_network_error_has_safe_reason_only(self):
        get = Mock(side_effect=RuntimeError("header-key=" + self.key))
        frame, report = fetch_finished_fixtures("soccer_epl", 2026, self.key, get)
        self.assertTrue(frame.empty)
        self.assertEqual(report["state"], "download_error")
        self.assertNotIn(self.key, json.dumps(report))
        self.assertNotIn("header-key", json.dumps(report))

    def test_empty_response_is_explicit_and_keeps_receipt(self):
        frame, report, _ = self.fetch()
        self.assertTrue(frame.empty)
        self.assertEqual(report["state"], "empty")
        self.assertEqual(report["provider_results"], 0)
        self.assertIn("received_at", report)
        self.assertIn("ข้อมูลว่าง", api_football_status_text(report))

    def test_invalid_json_does_not_echo_exception(self):
        response = Mock(status_code=200)
        response.json.side_effect = ValueError(self.key)
        frame, report = fetch_finished_fixtures("soccer_epl", 2026, self.key, Mock(return_value=response))
        self.assertTrue(frame.empty)
        self.assertEqual(report["state"], "invalid_response")
        self.assertEqual(report["reason"], "invalid_json")
        self.assertNotIn(self.key, json.dumps(report))

    def test_invalid_payload_values_are_not_treated_as_success(self):
        _, report, _ = self.fetch(payload={"response": [], "meta": float("nan")})
        self.assertEqual(report["state"], "invalid_response")
        self.assertEqual(report["reason"], "invalid_payload_values")

    def test_incomplete_pagination_does_not_hidden_fetch(self):
        frame, report, get = self.fetch(payload={"response": [self.fixture()], "paging": {"current": 1, "total": 2}})
        self.assertTrue(frame.empty)
        self.assertEqual(report["reason"], "incomplete_pagination")
        self.assertEqual(get.call_count, 1)

    def test_timestamp_date_disagreement_is_quarantined(self):
        record = self.fixture()
        record["fixture"]["date"] = "2026-09-01T19:00:00+00:00"
        frame, report, _ = self.fetch([record])
        self.assertTrue(frame.empty)
        self.assertEqual(report["rejected_reasons"], {"timestamp_date_mismatch": 1})

    def test_naive_source_date_is_not_assumed_utc(self):
        record = self.fixture()
        record["fixture"]["date"] = "2026-09-01T18:00:00"
        frame, report, _ = self.fetch([record])
        self.assertTrue(frame.empty)
        self.assertEqual(report["rejected_reasons"], {"timestamp_date_mismatch": 1})

    def test_boolean_timestamp_is_not_epoch(self):
        record = self.fixture()
        record["fixture"]["timestamp"] = True
        frame, report, _ = self.fetch([record])
        self.assertTrue(frame.empty)
        self.assertEqual(report["rejected_reasons"], {"invalid_timestamp": 1})

    def test_wrong_league_or_season_is_quarantined(self):
        for key, value in (("id", 78), ("season", 2025)):
            with self.subTest(key=key):
                record = self.fixture()
                record["league"][key] = value
                frame, report, _ = self.fetch([record])
                self.assertTrue(frame.empty)
                self.assertEqual(report["rejected_reasons"], {"league_season_mismatch": 1})

    def test_date_outside_reported_season_is_quarantined(self):
        record = self.fixture()
        when = datetime(2026, 5, 1, 18, tzinfo=timezone.utc)
        record["fixture"].update(timestamp=int(when.timestamp()), date=when.isoformat())
        frame, report, _ = self.fetch([record])
        self.assertTrue(frame.empty)
        self.assertEqual(report["rejected_reasons"], {"date_season_mismatch": 1})

    def test_exact_duplicate_is_counted_once(self):
        record = self.fixture()
        frame, report, _ = self.fetch([record, deepcopy(record)])
        self.assertEqual(len(frame), 1)
        self.assertEqual(report["provider_results"], 2)
        self.assertEqual(report["conflicting_rows"], 0)

    def test_one_fixture_id_with_different_time_is_quarantined(self):
        record, other = self.fixture(), self.fixture()
        other["fixture"].update(timestamp=record["fixture"]["timestamp"] + 3600,
                                 date="2026-09-01T19:00:00+00:00")
        frame, report, _ = self.fetch([record, other])
        self.assertTrue(frame.empty)
        self.assertEqual(report["conflicting_rows"], 2)

    def test_shared_team_label_with_different_ids_is_not_pooled(self):
        first = self.fixture()
        second = self.fixture(fixture_id=2, away="Everton", home_id=99, away_id=45)
        frame, report, _ = self.fetch([first, second])
        self.assertTrue(frame.empty)
        self.assertEqual(report["ambiguous_identity_rows"], 2)

    def test_one_team_id_with_different_names_is_not_assumed_alias(self):
        first = self.fixture()
        second = self.fixture(fixture_id=2, home="Arsenal U21", away="Everton", away_id=45)
        frame, report, _ = self.fetch([first, second])
        self.assertTrue(frame.empty)
        self.assertEqual(report["ambiguous_identity_rows"], 2)

    def test_missing_team_ids_remain_unverified_not_invented(self):
        record = self.fixture(home_id=None, away_id=None)
        frame, report, _ = self.fetch([record])
        self.assertEqual(len(frame), 1)
        self.assertEqual(report["team_id_status"], "missing")
        self.assertIsNone(frame.iloc[0]["_provider_home_team_id"])
        self.assertIsNone(frame.iloc[0]["_provider_away_team_id"])

    def test_cache_description_preserves_provider_state_and_receipt(self):
        _, report, _ = self.fetch([self.fixture()])
        original = report["received_at"]
        report.update(state="cache", provider_state="success", cache_hit=True)
        text = api_football_status_text(report)
        self.assertIn("cache", text)
        self.assertIn("ใช้งานสำเร็จ", text)
        self.assertEqual(report["received_at"], original)

    def test_renderer_never_echoes_untrusted_diagnostic_strings(self):
        text = api_football_status_text({"state": self.key, "reason": self.key,
                                       "http_status": self.key, "usable_rows": self.key})
        self.assertNotIn(self.key, text)
        self.assertIn("สถานะยังยืนยันไม่ได้", text)

    def test_missing_metadata_is_compatible_but_explicitly_unverified(self):
        record = self.fixture(home_id=None, away_id=None)
        record.pop("league")
        frame, report, _ = self.fetch([record])
        self.assertEqual(len(frame), 1)
        self.assertFalse(frame.iloc[0]["_provider_league_verified"])
        self.assertFalse(frame.iloc[0]["_provider_identity_verified"])
        self.assertEqual(report["fully_verified_rows"], 0)
        self.assertEqual(report["league_verified_rows"], 0)
        self.assertEqual(report["identity_verified_rows"], 0)

    def test_non_string_or_blank_key_never_reaches_request_or_logger(self):
        for key in (True, 123, "   "):
            with self.subTest(key=key):
                get = Mock()
                _, report = fetch_finished_fixtures("soccer_epl", 2026, key, get)
                self.assertEqual(report["state"], "not_called")
                self.assertEqual(report["reason"], "invalid_key")
                get.assert_not_called()

    def test_renderer_handles_malformed_saved_state_without_echoing_it(self):
        text = api_football_status_text({"state": {"unsafe": self.key}, "reason": [self.key]})
        self.assertNotIn(self.key, text)
        self.assertIn("สถานะยังยืนยันไม่ได้", text)

    def test_failure_exception_retains_evidence_with_safe_message(self):
        frame, report, _ = self.fetch(status=403)
        error = ApiFootballUnavailable(frame, report)
        self.assertIs(error.frame, frame)
        self.assertIs(error.diagnostic, report)
        self.assertNotIn(self.key, str(error))


if __name__ == "__main__":
    unittest.main()
